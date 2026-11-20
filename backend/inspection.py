"""One visual assessment, with a bounded second look for essential uncertainty."""
import base64
import io
import json
import re
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from .taxonomy import TYPES
from .observations import PRESENCE_PROMPT, GRAMMAR
from .quality_gate import assess_image, QualitySettings

VERSION = 6
PACKET = re.compile(r'\b(packet|packaging|pouch|bag|plastic|wrist|watch)\b', re.I)
NON_JEWELLERY = re.compile(r'\b(glass|loose beads?|beads?|cups?|vials?|containers?|weights?|fixtures?|holders?|calibration|hardware|discs?|disks?|washers?|coins?|wooden)\b', re.I)
ORNAMENT = re.compile(r'\b(bangle|bracelet|necklace|chain|earrings?|pendant|finger ring|anklet|nosepin)\b', re.I)
DETAILS = ('threads', 'black_beads', 'coloured_stones_or_beads')
FIELDS = {
    'p': ('jewellery_presence', ['present', 'absent', 'unknown']),
    'v': ('inspectability', ['adequate', 'limited', 'unusable', 'unknown']),
    'f': ('cropping', ['none', 'local', 'substantial', 'unknown']),
    'o': ('obstruction', ['none', 'local', 'substantial', 'unknown']),
    'b': ('blur', ['none', 'local', 'substantial', 'unknown']),
    'e': ('glare_exposure', ['none', 'local', 'substantial', 'unknown']),
    's': ('item_separation', ['adequate', 'limits_inspection', 'not_applicable', 'unknown']),
}
WIRE_NAMES = dict(p='presence', f='cropping', o='obstruction', b='blur', e='glare', s='separation')
SCHEMA = {'type': 'object', 'properties': {
    'note': {'type': 'string', 'maxLength': 120},
    'subject': {'type': 'string', 'enum': ['packet_contents', 'tray_pan', 'other', 'unknown']},
    **{name: {'type': 'string', 'enum': FIELDS[key][1]} for key, name in WIRE_NAMES.items()},
    'details': {'type': 'array', 'items': {'type': 'string', 'enum': list(DETAILS)}, 'maxItems': len(DETAILS)},
    't': {'type': 'array', 'items': {'type': 'string', 'enum': sorted(TYPES)}, 'maxItems': 3},
    'c': {'type': 'integer', 'minimum': 0, 'maximum': 100},
    'scale': {'type': 'string', 'enum': ['present', 'absent', 'unknown']},
}, 'required': ['note', 'subject', *WIRE_NAMES.values(), 'details'], 'additionalProperties': False}
PROMPT = (Path(__file__).with_name('prompts') / 'inspection.txt').read_text(encoding='utf-8')
PRESENCE_CHECK = PRESENCE_PROMPT + (
    '\nInspect only the presented contents of the packet or pan. Loose beads, glass cups, '
    'round inserts in a holder, calibration weights, coins and hardware are not jewellery. '
    'If only these are visible, answer absent. A circular object alone is not a ring. '
    'Use unknown if the object cannot be identified. Return only note (at most 12 words) and presence.')


def decode(raw, finish):
    try:
        wire = json.loads(raw)
    except (ValueError, TypeError):
        return None, ['Invalid assessment JSON']
    if finish != 'stop' or not isinstance(wire, dict):
        return None, ['Incomplete assessment response']
    if 'presence' not in wire:
        return None, ['Missing essential presence answer']
    # Long descriptive wire names make the small VLM's task easier; local fields stay stable.
    if 'presence' in wire:
        if wire.get('subject') not in ('packet_contents', 'tray_pan', 'other', 'unknown'):
            return None, ['Missing inspection subject location']
        wire = {**wire, **{key: wire.get(name) for key, name in WIRE_NAMES.items()}}
        # Item separation measures overlap between distinct ornaments.
        if type(wire.get('c')) is int and wire['c'] == 1 and len(wire.get('t') or []) <= 1:
            wire['s'] = 'not_applicable'
        levels = [wire.get(k) for k in ('f', 'o', 'b', 'e')]
        wire['v'] = ('unusable' if 'substantial' in levels else 'limited' if 'local' in levels or wire.get('s') == 'limits_inspection'
                     else 'unknown' if 'unknown' in levels or wire.get('s') == 'unknown' else 'adequate')
        if not isinstance(wire.get('note'), str) or not wire['note'].strip() or wire['note'].strip().lower() in ('packet_contents', 'tray_pan', 'other', 'unknown', 'present', 'absent', 'none'):
            return None, ['Missing visual grounding note']
    if any(wire.get(k) not in values for k, (_, values) in FIELDS.items()):
        return None, ['Missing or invalid essential assessment answers']
    report = {'jewellery_presence': wire['p'], 'assessment': {
        name: wire[key] for key, (name, _) in FIELDS.items() if key != 'p'}}
    if wire.get('note'):
        report['visual_evidence'] = wire['note']
        report['inspection_subject'] = wire.get('subject', 'unknown')
    problems = []
    if wire['p'] == 'present':
        observed = wire.get('details')
        if isinstance(observed, list):
            report['visual_details'] = {
                key: 'present' if key in observed else 'unknown' for key in DETAILS
            }
        if wire.get('c') == 0:
            problems.append('Jewellery presence conflicts with zero count')
        if wire['v'] == 'adequate' and (any(wire[k] in ('local', 'substantial') for k in ('f', 'o', 'b', 'e')) or wire['s'] == 'limits_inspection'):
            problems.append('Inspectability conflicts with reported visibility limitations')
        if wire['v'] == 'unknown' or any(wire[k] == 'unknown' for k in ('f', 'o', 'b', 'e', 's')):
            problems.append('Essential jewellery visibility is uncertain')
        families = wire.get('t')
        if isinstance(families, list) and all(isinstance(t, str) and t in TYPES for t in families):
            report['possible_jewel_types'] = [{'type': t} for t in dict.fromkeys(families)]
        if type(wire.get('c')) is int and 1 <= wire['c'] <= 100:
            report['jewel_count'] = wire['c']
    elif wire['p'] == 'absent':
        report.update(jewel_count=0, possible_jewel_types=[])
        if wire.get('c', 0) != 0 or wire.get('t'):
            problems.append('Absent jewellery conflicts with item observations')
    else:
        problems.append('Jewellery presence is uncertain')
    if wire.get('scale') in ('present', 'absent', 'unknown'):
        report['scale'] = {'presence': wire['scale']}
    return report, problems


def needs_presence_check(report):
    evidence = report.get('visual_evidence', '')
    return (report.get('inspection_subject') in ('packet_contents', 'unknown')
            or PACKET.search(evidence)
            or not (report.get('possible_jewel_types') or ORNAMENT.search(evidence))
            or (NON_JEWELLERY.search(evidence) and not ORNAMENT.search(evidence)))


def analyse(reporter, path, scope=None):
    started = time.perf_counter()
    attempts = []
    report, notes = None, []
    quality_metrics = assess_image(path).metrics
    softness_warning = quality_metrics.get('focus_score', float('inf')) < QualitySettings().blur_threshold
    with Image.open(path) as source:
        original = ImageOps.exif_transpose(source).convert('RGB')
    # Near-white pixels in the central inspection area can be missed by
    # whole-frame exposure statistics. Use this signal only to request review.
    width, height = original.size
    centre = original.crop((width // 4, height // 4, width - width // 4, height - height // 4))
    centre.thumbnail((320, 320))
    exposure_warning = (np.asarray(centre).min(axis=2) >= 250).mean() >= .65
    for index in range(2):
        image = original.copy()
        image.thumbnail((reporter.settings.image_side if index == 0 else 640,) * 2, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        uri = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
        prompt = PROMPT
        if index:
            prompt += '\nSecond look: independently resolve these essential uncertainties: ' + '; '.join(notes) + '. Inspect the image again; use unknown if unresolved.'
        tick = time.perf_counter()
        try:
            response = reporter.request('/v1/chat/completions', {
                'messages': [{'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': uri}}, {'type': 'text', 'text': prompt}]}],
                'temperature': 0, 'max_tokens': reporter.settings.max_tokens,
                'cache_prompt': True, 'chat_template_kwargs': {'enable_thinking': False},
                'response_format': {'type': 'json_schema', 'schema': SCHEMA}})
            choice = response['choices'][0]
            raw = choice['message'].get('content') or ''
            candidate, issues = decode(raw, choice.get('finish_reason'))
            attempts.append(dict(stage='assessment' if not index else 'verification', raw=raw,
                seconds=round(time.perf_counter()-tick, 3), input_size=image.size,
                usage=response.get('usage'), timings=response.get('timings'), finish_reason=choice.get('finish_reason')))
        except Exception as exc:
            candidate, issues = None, ['Assessment request failed: ' + str(exc)]
            attempts.append(dict(stage='assessment' if not index else 'verification', error=str(exc),
                seconds=round(time.perf_counter()-tick, 3)))
        if candidate is not None:
            # A second look cannot silently erase a disagreement about presence.
            if report and report['jewellery_presence'] != candidate['jewellery_presence']:
                candidate['jewellery_presence'] = 'unknown'
                candidate.pop('jewel_count', None)
                candidate.pop('possible_jewel_types', None)
                candidate.pop('visual_details', None)
                issues.append('Jewellery presence disagrees between visual passes')
            report = candidate
        notes = issues
        if report and report['jewellery_presence'] == 'present' and report['assessment']['blur'] == 'none' and softness_warning:
            notes.append('Sharpness measurement conflicts with an unrestricted visibility assessment; inspect jewellery edge/detail clarity independently')
        if report and report['jewellery_presence'] == 'present' and report['assessment']['glare_exposure'] in ('none', 'unknown') and exposure_warning:
            notes.append('Much of the central inspection area is near white; check whether washed-out jewellery has lost detail')
        # Packet contents are a demonstrated failure case for this small VLM:
        # its longer checklist can hallucinate an ornament or use a worn watch.
        # Verify presence independently, without sending the first prediction.
        evidence = (report or {}).get('visual_evidence', '')
        non_jewellery_evidence = NON_JEWELLERY.search(evidence) and not ORNAMENT.search(evidence)
        if (index == 0 and report and report['jewellery_presence'] == 'present'
                and (non_jewellery_evidence or (not notes and needs_presence_check(report)))):
            tick = time.perf_counter()
            try:
                response = reporter.request('/v1/chat/completions', {
                    'messages': [{'role': 'user', 'content': [
                        {'type': 'image_url', 'image_url': {'url': uri}},
                        {'type': 'text', 'text': PRESENCE_CHECK}]}],
                    'temperature': 0, 'max_tokens': 80, 'cache_prompt': True,
                    'chat_template_kwargs': {'enable_thinking': False}, 'grammar': GRAMMAR})
                choice = response['choices'][0]
                raw = choice['message'].get('content') or ''
                attempts.append(dict(stage='presence_verification', raw=raw,
                    seconds=round(time.perf_counter()-tick, 3), input_size=image.size,
                    usage=response.get('usage'), timings=response.get('timings'), finish_reason=choice.get('finish_reason')))
                check = json.loads(raw)
                confirmed = check.get('presence') if isinstance(check, dict) else None
                if choice.get('finish_reason') != 'stop' or confirmed not in ('present', 'absent', 'unknown') or not check.get('note'):
                    raise ValueError('Incomplete packet presence verification')
                if confirmed == 'present' and NON_JEWELLERY.search(check['note']) and not ORNAMENT.search(check['note']):
                    confirmed = 'unknown'
                report['presence_evidence'] = check['note']
                report['jewellery_presence'] = confirmed
                if confirmed != 'present':
                    report.pop('possible_jewel_types', None)
                    report.pop('jewel_count', None)
                    report.pop('visual_details', None)
                    if confirmed == 'absent':
                        notes = []
                        report.update(jewel_count=0, possible_jewel_types=[])
                        report['visual_evidence'] = check['note']
                        report['assessment'] = {name: 'unknown' for key, (name, _) in FIELDS.items() if key != 'p'}
                    else:
                        notes = ['Jewellery could not be identified by the independent presence check']
            except Exception as exc:
                report['jewellery_presence'] = 'unknown'
                report.pop('jewel_count', None)
                report.pop('possible_jewel_types', None)
                report.pop('visual_details', None)
                notes = ['Presence verification failed: ' + str(exc)]
                if attempts[-1]['stage'] != 'presence_verification':
                    attempts.append(dict(stage='presence_verification', error=str(exc), seconds=round(time.perf_counter()-tick, 3)))
            break  # At most two visual requests, including this focused check.
        if not notes:
            if index and report and report['jewellery_presence'] == 'present' and needs_presence_check(report):
                notes = ['The assessment lacks independently verified evidence of jewellery in the inspection area']
            break
    processing = 'failed' if report is None else 'needs_verification' if notes else 'completed'
    if report:
        evidence = report.get('visual_evidence', '')
        if report.get('scale', {}).get('presence') in (None, 'unknown') and re.search(
                r'\b(?:on|inside)\s+(?:an?\s+|the\s+)?(?:precision\s+|weighing\s+|digital\s+)?(?:balance|scale)\b',
                evidence, re.I):
            report['scale'] = {'presence': 'present'}
        report['assessment_complete'] = processing == 'completed'
        presence = report['jewellery_presence']
        report['description'] = ('No jewellery visible in the inspection area.' if presence == 'absent' else
            'Jewellery presence is uncertain.' if presence == 'unknown' else
            'Jewellery present. Overall inspectability: ' + report['assessment']['inspectability'] + '.')
    return dict(status='generated' if report else 'error', report=report, processing_status=processing,
        observation_notes=notes, attempts=attempts, stage_seconds={a['stage']: a['seconds'] for a in attempts},
        seconds=round(time.perf_counter()-started, 3), original_size=original.size,
        input_size=attempts[0].get('input_size'), inference_mode='inspection', assessment_version=VERSION)
