"""Short presence and observation passes with a compact independent review."""
import base64
import io
import time

from PIL import Image, ImageOps
from .observations import PRESENCE_PROMPT, PROMPT as OBSERVATION_PROMPT, GRAMMAR, expand
from .consistency import fields, withhold, enforce, describe, review, COMPACT_AUDIT_PROMPT

VERSION = 3
PROMPT = OBSERVATION_PROMPT + '''
Keep note under 20 words and include important visible evidence such as a scale reading. Omit unknown, null and not_applicable fields rather than filling them. If scale is absent, omit display, scale_position and weight. If exact scale digits are visible, display must be readable; never emit digits with unreadable display. A wrist-sized rigid band is a bangle, not a finger ring.'''


def unpack(raw):
    _, report, notes = expand(raw, include_notes=True)
    return report, {}, notes


def analyse(reporter, path, scope):
    started = time.perf_counter()
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        original_size = image.size
        image.thumbnail((reporter.settings.image_side,) * 2, Image.Resampling.LANCZOS)
        input_size = image.size
        buffer = io.BytesIO(); image.save(buffer, format='PNG')
    uri = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
    timings, attempts = {}, []

    def generate(prompt, budget, grammar, stage):
        tick = time.perf_counter()
        response = reporter.request('/v1/chat/completions', {
            'messages': [{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': uri}}, {'type': 'text', 'text': prompt}]}],
            'temperature': 0, 'max_tokens': budget, 'cache_prompt': True,
            'chat_template_kwargs': {'enable_thinking': False}, 'grammar': grammar})
        choice = response['choices'][0]
        attempt = dict(stage=stage, raw=choice['message'].get('content') or '',
                       seconds=round(time.perf_counter()-tick, 3), token_budget=budget,
                       finish_reason=choice.get('finish_reason'), usage=response.get('usage'), timings=response.get('timings'))
        attempts.append(attempt)
        timings[stage] = attempt['seconds']
        return attempt

    check = generate(PRESENCE_PROMPT + '\nUse at most 12 words in note. Return only note and presence.', 80, GRAMMAR, 'presence')
    _, presence_report, notes = expand(check['raw'], include_notes=True)
    presence = presence_report.get('jewellery_presence', 'unknown')
    if notes or check['finish_reason'] == 'length' or not presence_report.get('description'):
        presence = 'unknown'
        notes.append('Presence check incomplete or unsupported; manual review needed.')
    presence_check = {**check, 'presence': presence, 'evidence': presence_report.get('description'), 'notes': list(notes)}
    if presence == 'absent':
        report = {'jewellery_presence': 'absent', 'jewel_count': 0,
                  'possible_jewel_types': [], 'arrangement': 'not_applicable'}
        evidence = {'presence': presence_report['description']}
        audit = {'raw': None, 'evidence': evidence, 'supported_fields': ['presence'],
                 'source_description': presence_report['description']}
    else:
        prompt = PROMPT
        if scope == 'context_and_weight_only':
            prompt += '\nQuality limits inspection: omit types and count; inspect only visible context and scale readings.'
        detail = generate(prompt, reporter.settings.max_tokens, GRAMMAR, 'observations')
        report, evidence, detail_notes = unpack(detail['raw'])
        if not fields(report):
            detail_notes.append('Consistency: No usable detailed observations were returned; manual review needed.')
        # Preserve complete fields, then independently audit them without a full regeneration.
        notes.extend(detail_notes)
        if detail['finish_reason'] == 'length':
            notes.append('Consistency: Observation response truncated; only complete fields retained for review.')
        if scope == 'context_and_weight_only':
            for key in ('types', 'count'):
                withhold(report, key); evidence.pop(key, None)
        if presence == 'unknown' or report.get('jewellery_presence') in ('absent', 'unknown') or report.get('jewel_count') == 0:
            report['jewellery_presence'] = 'unknown'
            notes.append('Consistency: Jewellery presence uncertain or conflicting; manual review needed.')
        else:
            report['jewellery_presence'] = 'present'
            evidence['presence'] = presence_report['description']
        issues = enforce(report)
        notes.extend(issues)
        tick = time.perf_counter()
        report, review_notes, audit = review(reporter.request, uri, report, compact=True)
        timings['review'] = round(time.perf_counter()-tick, 3)
        notes.extend(review_notes)
        evidence = audit['evidence']
        if report.get('jewellery_presence') != 'present':
            for key in ('types', 'count', 'arrangement', 'threads', 'black_beads', 'coloured_stones'):
                withhold(report, key)
    evidence = {key: value for key, value in evidence.items() if key in fields(report)}
    for item in report.get('possible_jewel_types', []):
        item['evidence'] = evidence.get('types', '')
    report['description'] = describe(report)
    for section in ('packet', 'scale', 'visual_details'):
        if section in report and not report[section]:
            report.pop(section)
    return dict(status='generated', report=report, compact={}, attempts=attempts,
                presence_check=presence_check, consistency_review={
                    **audit, 'mode': 'compact_independent_review', 'evidence': evidence,
                    'independent_review': 'review' in timings},
                observation_notes=list(dict.fromkeys(notes)), partial_contract=True,
                seconds=round(time.perf_counter()-started, 3), stage_seconds=timings,
                inference_mode='balanced', original_size=original_size, input_size=input_size)
