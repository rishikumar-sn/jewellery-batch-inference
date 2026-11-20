"""Owned persistent llama-server, loopback only, with CPU vision and decoding."""
from __future__ import annotations
import base64
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import threading
import urllib.error
import urllib.request
from PIL import Image, ImageOps
from .observations import GRAMMAR, PROMPT, PRESENCE_PROMPT, SCHEMA, VERSION, expand
from .process_job import ProcessJob
from .consistency import review, AUDIT_PROMPT, VERSION as CONSISTENCY_VERSION
from . import balanced, inspection, scale_reading

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = Path('D:/PROJECTS_BY_RISHI/Downloads/qwen')


def find_server():
    env = os.environ.get('LLAMA_SERVER')
    candidates = ([Path(env)] if env else []) + list((ROOT / 'tools/llama.cpp').rglob('llama-server.exe'))
    located = shutil.which('llama-server')
    if located:
        candidates.append(Path(located))
    return next((str(p.resolve()) for p in candidates if p.is_file()), '')


@dataclass(frozen=True)
class Settings:
    model: str = str(DEFAULT_MODELS / 'Qwen3VL-2B-Instruct-Q4_K_M.gguf')
    projector: str = str(DEFAULT_MODELS / 'mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf')
    threads: int = min(8, os.cpu_count() or 4)
    image_side: int = 512
    max_tokens: int = 192
    server: str = ''
    inference_mode: str = 'inspection'

    def signature(self):
        data = asdict(self)
        for key in ('model', 'projector'):
            p = Path(data[key]).resolve()
            stat = p.stat()
            data[key] = [str(p), stat.st_size, stat.st_mtime_ns]
        return hashlib.sha256(json.dumps([data, PROMPT, PRESENCE_PROMPT, AUDIT_PROMPT,
            SCHEMA, GRAMMAR, VERSION, CONSISTENCY_VERSION, balanced.PROMPT, balanced.COMPACT_AUDIT_PROMPT,
            balanced.GRAMMAR, balanced.VERSION, inspection.PROMPT, inspection.SCHEMA, inspection.VERSION,
            scale_reading.PROMPT], sort_keys=True).encode()).hexdigest()


class CpuReporter:
    log_directory = ROOT / "data/logs"

    def __init__(self, settings, progress=lambda text: None, cancelled=lambda: False):
        self.settings, self.process, self.log = settings, None, None
        self.job = None
        self._close_lock = threading.Lock()
        if settings.inference_mode not in ('inspection', 'balanced', 'thorough'):
            raise ValueError('Use inspection, balanced or thorough inference mode.')
        if settings.image_side not in (448, 512, 640) or not 60 <= settings.max_tokens <= 240 or settings.threads < 1:
            raise ValueError('Use 448/512/640 px, 60-240 tokens, and at least one CPU thread.')
        for path in (settings.model, settings.projector):
            if not Path(path).is_file():
                raise ValueError(f'Missing local GGUF: {path}')
        server = settings.server or find_server()
        if not server or not Path(server).is_file():
            raise ValueError('llama-server.exe missing. Set its path or install the Windows CPU llama.cpp release in tools/llama.cpp.')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        # Bypass system proxies for local image requests.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        logs = self.log_directory
        logs.mkdir(exist_ok=True)
        self.log_path = logs / f'llama-{os.getpid()}-{port}.log'
        self.log = self.log_path.open('w', encoding='utf-8')
        command = [server, '-m', settings.model, '--mmproj', settings.projector,
                   '--host', '127.0.0.1', '--port', str(port), '-ngl', '0',
                   '--no-mmproj-offload', '-t', str(settings.threads), '-tb', str(settings.threads),
                   '-c', '4096', '-np', '1', '--jinja']
        self.command = command
        started = time.perf_counter()
        try:
            self.process = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.job = ProcessJob(self.process)
            progress('Loading model...')
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if cancelled():
                    raise RuntimeError('Model loading stopped.')
                if self.process.poll() is not None:
                    raise RuntimeError(f'llama-server exited; see {self.log_path}')
                try:
                    if self.request('/health', timeout=2).get('status') == 'ok':
                        self.load_seconds = round(time.perf_counter() - started, 3)
                        progress('Model ready')
                        return
                except (OSError, ValueError, RuntimeError):
                    pass
                time.sleep(0.2)
            raise TimeoutError(f'Model loading timed out; see {self.log_path}')
        except BaseException:
            self.close()
            raise

    def request(self, route, payload=None, timeout=600):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + route, data=data, headers={'Content-Type': 'application/json'})
        try:
            with self.http.open(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors='replace')[:1200]
            raise RuntimeError(f'llama-server HTTP {exc.code}: {detail}') from exc

    def analyse(self, path, scope='visible_evidence_only'):
        if self.settings.inference_mode == 'inspection':
            return inspection.analyse(self, path, scope)
        if self.settings.inference_mode == 'balanced':
            return balanced.analyse(self, path, scope)
        return self._analyse_thorough(path, scope)

    def _analyse_thorough(self, path, scope='visible_evidence_only'):
        started = time.perf_counter()
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert('RGB')
            original_size = image.size
            image.thumbnail((self.settings.image_side,) * 2, Image.Resampling.LANCZOS)
            input_size = image.size
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
        uri = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
        # Ask independently, without exposing the detailed answer and anchoring on it.
        response = self.request('/v1/chat/completions', {
            'messages': [{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': uri}},
                {'type': 'text', 'text': PRESENCE_PROMPT}]}],
            'temperature': 0, 'max_tokens': 100, 'cache_prompt': True,
            'chat_template_kwargs': {'enable_thinking': False}, 'grammar': GRAMMAR})
        choice = response['choices'][0]
        presence_raw = choice['message'].get('content') or ''
        _, presence_report, presence_notes = expand(presence_raw, include_notes=True)
        presence = presence_report.get('jewellery_presence', 'unknown')
        if presence_notes or choice.get('finish_reason') == 'length' or not presence_report.get('description'):
            presence = 'unknown'
            presence_notes.append('Presence check incomplete or unsupported; manual review needed.')
        presence_check = dict(raw=presence_raw, presence=presence,
                              evidence=presence_report.get('description'), notes=presence_notes,
                              usage=response.get('usage'), finish_reason=choice.get('finish_reason'))
        if presence == 'absent':
            report, consistency_notes, consistency_review = review(self.request, uri, {
                'jewellery_presence': 'absent', 'description': presence_report['description']})
            return dict(status='generated', report=report,
                compact={}, attempts=[], presence_check=presence_check,
                consistency_review=consistency_review,
                partial_contract=True, observation_notes=presence_notes + consistency_notes,
                seconds=round(time.perf_counter() - started, 3),
                original_size=original_size, input_size=input_size)
        attempts = []
        error = ''
        for index in range(2):
            budget = self.settings.max_tokens if index == 0 else max(240, self.settings.max_tokens)
            prompt = PROMPT if index == 0 else PROMPT + '\nPrevious answer was invalid: ' + error + '\nReturn a complete corrected object.'
            if scope == 'context_and_weight_only':
                prompt += '\nQuality checkpoint: jewellery details cannot be assessed. Do not guess jewellery family or count: Omit types and count. Inspect only visible scale/packet context and readable display.'
            payload = {'messages': [{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': uri}}, {'type': 'text', 'text': prompt}]}],
                'temperature': 0, 'max_tokens': budget, 'cache_prompt': True,
                'chat_template_kwargs': {'enable_thinking': False},
                'grammar': GRAMMAR}
            if index:
                payload['messages'].extend([
                    {'role': 'assistant', 'content': attempts[-1]['raw']},
                    {'role': 'user', 'content': 'Correct this error using the photo: ' + error}])
            tick = time.perf_counter()
            response = self.request('/v1/chat/completions', payload)
            choice = response['choices'][0]
            raw = choice['message'].get('content') or ''
            attempt = {'raw': raw, 'seconds': round(time.perf_counter() - tick, 3),
                       'token_budget': budget, 'usage': response.get('usage'),
                       'timings': response.get('timings'), 'finish_reason': choice.get('finish_reason')}
            attempts.append(attempt)
            compact, report, notes = expand(raw, include_notes=True)
            attempt['normalizations'] = list(notes)
            if scope == 'context_and_weight_only':
                report.pop('possible_jewel_types', None)
                report.pop('jewel_count', None)
                notes.append('Type/count withheld by quality scope; raw predictions retained.')
            if choice.get('finish_reason') == 'length' and index == 0:
                error = 'JSON was truncated. Return a shorter object; omit fields as needed.'
                previous = (compact, report)
                continue
            if not report and index and previous[1]:
                compact, report = previous
                notes.append('Retained observations recovered from the first response.')
            notes.extend(presence_notes)
            detailed_presence = report.get('jewellery_presence')
            if presence == 'unknown' or detailed_presence in ('absent', 'unknown') or report.get('jewel_count') == 0:
                report['jewellery_presence'] = 'unknown'
                notes.append('Jewellery presence is uncertain or conflicting across observations; manual review needed.')
            else:
                report['jewellery_presence'] = 'present'
            report, consistency_notes, consistency_review = review(self.request, uri, report)
            notes.extend(consistency_notes)
            return dict(status='generated', compact=compact, report=report, attempts=attempts,
                        presence_check=presence_check,
                        consistency_review=consistency_review,
                        partial_contract=True, observation_notes=notes,
                        seconds=round(time.perf_counter() - started, 3),
                        original_size=original_size, input_size=input_size)

    def close(self):
        with self._close_lock:
            self._close()

    def _close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
        if self.job:
            self.job.close()
            self.job = None
        if self.log:
            self.log.close()
            self.log = None

