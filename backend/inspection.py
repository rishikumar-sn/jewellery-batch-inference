"""One visual assessment, with a bounded second look for essential uncertainty."""
import base64
import io
import json
import re
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from .observations import PRESENCE_PROMPT, GRAMMAR
from .quality_gate import assess_image, QualitySettings
from .scale_reading import DISPLAY_BANDS, read_scale

VERSION = 7
PACKET = re.compile(r'\b(packet|packaging|pouch|bag|plastic|wrist|watch)\b', re.I)
NON_JEWELLERY = re.compile(r'\b(glass|loose beads?|beads?|cups?|vials?|containers?|weights?|fixtures?|holders?|calibration|hardware|discs?|disks?|washers?|coins?|wooden)\b', re.I)
ORNAMENT = re.compile(r'\b(bangles?|bracelets?|necklaces?|chains?|chokers?|earrings?|pendants?|finger rings?|anklets?|nosepins?)\b', re.I)
BACKGROUND = re.compile(r'\b(handcuffs?|wristwatches?|worn watch|illustrations?|printed (?:objects?|ornaments?|jewellery|images?))\b', re.I)
FEATURE_EVIDENCE = {
    'threads': r'\b(?:threads?|cords?|strings?|tassels?|textile|fabric)\b',
    'black_beads': r'\bblack beads?\b',
    'coloured_stones_or_beads': r'\b(?:(?:colou?red|multicolou?red|red|green|blue|pink|purple|white|orange|yellow) (?:stones?|beads?)|gemstones?)\b',
}
FAMILY_EVIDENCE = {
    'necklace_chain': r'\b(?:necklaces?|chokers?|neck chains?)\b',
    'earring_nosepin': r'\b(?:earrings?|nose ?(?:pins?|rings?|studs?))\b',
    'finger_ring': r'\b(?:finger rings?|rings? for (?:a |the )?finger)\b',
    'toe_ring': r'\btoe rings?\b',
    'bangle': r'\bbangles?\b', 'bracelet': r'\bbracelets?\b',
    'pendant': r'\b(?:loose|detached|separate) (?P<name>pendants?|lockets?)\b',
    'anklet': r'\banklets?\b',
}
APPEARANCE = {
    'gold_coloured': 'gold-coloured metal', 'silver_coloured': 'silver-coloured metal',
    'linked_strands': 'linked strands', 'coiled_strands': 'coiled strands', 'attached_pendant': 'an attached pendant',
    'tangled_strands': 'tangled strands', 'beaded_decoration': 'beaded decoration', 'decorated_band': 'a decorated band',
}
APPEARANCE_EVIDENCE = {
    'gold_coloured': r'\bgold(?:en)?\b', 'silver_coloured': r'\bsilver\b',
    'linked_strands': r'\b(?:chains?|links?|linked strands?)\b',
    'coiled_strands': r'\b(?:coiled|coils?)\b',
    'tangled_strands': r'\btangled\b[^.,;]*\b(?:chains?|strands?|links?)\b',
    'attached_pendant': r'\b(?:necklace|chain)\b[^.,;]*\b(?:with|has)\b[^.,;]*\bpendant\b',
    'beaded_decoration': r'\b(?:beaded (?:chains?|strands?|edge|trim|design)|beadwork|gold beads?)\b',
    'decorated_band': r'\b(?:decorated|ornate|engraved|carved) (?:wide |broad )?(?:band|arc)\b',
}
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
    'note': {'type': 'string', 'maxLength': 180},
    'subject': {'type': 'string', 'enum': ['packet_contents', 'tray_pan', 'other', 'unknown']},
    **{name: {'type': 'string', 'enum': FIELDS[key][1]} for key, name in WIRE_NAMES.items()},
    'c': {'type': 'integer', 'minimum': 0, 'maximum': 100},
    'scale': {'type': 'string', 'enum': ['present', 'absent', 'unknown']},
    'display_area': {'type': 'string', 'enum': [*DISPLAY_BANDS, 'unknown']},
}, 'required': ['note', 'subject', *WIRE_NAMES.values(), 'scale', 'display_area'], 'additionalProperties': False}
PROMPT = (Path(__file__).with_name('prompts') / 'inspection.txt').read_text(encoding='utf-8')
PRESENCE_CHECK = PRESENCE_PROMPT + (
    '\nInspect only the presented contents of the packet or pan. Loose beads, glass cups, '
    'round inserts in a holder, calibration weights, coins and hardware are not jewellery. '
    'If only these are visible, answer absent. A circular object alone is not a ring. '
    'Printed photos, drawings, handcuffs and reflections are not physical ornaments. '
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
    if wire.get('subject') not in ('packet_contents', 'tray_pan', 'other', 'unknown'):
        return None, ['Missing inspection subject location']
    wire = {**wire, **{key: wire.get(name) for key, name in WIRE_NAMES.items()}}
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
        record_details(report, wire['note'])
        count = wire.get('c')
        minimum = sum(2 if item['name'].endswith('s') else 1 for item in report['possible_jewel_types'])
        if type(count) is int:
            if count < max(1, minimum) or wire['s'] == 'limits_inspection':
                report['count_conflict'] = True
            elif (count <= 100 and wire['v'] not in ('unknown', 'unusable')
                    and not re.search(r'\b(?:tangled|pile|bundled)\b', wire['note'], re.I)):
                report['jewel_count'] = count
        if wire['v'] == 'unknown' or any(wire[k] == 'unknown' for k in ('f', 'o', 'b', 'e', 's')):
            problems.append('Essential jewellery visibility is uncertain')
    elif wire['p'] == 'absent':
        report.update(jewel_count=0, possible_jewel_types=[])
        if wire.get('c', 0) != 0:
            problems.append('Absent jewellery conflicts with item observations')
    else:
        problems.append('Jewellery presence is uncertain')
    if wire.get('scale') in ('present', 'absent', 'unknown'):
        report['scale'] = {'presence': wire['scale']}
    if wire.get('display_area') in tuple(DISPLAY_BANDS):
        report['display_area'] = wire['display_area']
    return report, problems


def needs_presence_check(report):
    evidence = report.get('visual_evidence', '')
    return (report.get('inspection_subject') in ('packet_contents', 'unknown')
            or PACKET.search(evidence)
            or not (any(item['type'] != 'not_known' for item in report.get('possible_jewel_types', [])) or ORNAMENT.search(evidence))
            or (NON_JEWELLERY.search(evidence) and not ORNAMENT.search(evidence)))


def describe(report):
    presence = report['jewellery_presence']
    if presence == 'absent':
        return 'No jewellery visible in the inspection area.'
    if presence == 'unknown':
        return 'Jewellery could not be identified.'
    locations = {'tray_pan': ' on the tray or pan', 'packet_contents': ' inside the packet'}
    location = locations.get(report.get('inspection_subject'), '')
    names = [item.get('name') or item['type'].replace('_', ' ') for item in report.get('possible_jewel_types', [])]
    description = 'Jewellery visible' + location + '.'
    if names:
        description += ' Visible types include ' + ', '.join(names) + '.'
    cues = [APPEARANCE[cue] for cue in report.get('jewel_appearance', [])]
    features = {'threads': 'thread or cord', 'black_beads': 'black beads',
                'coloured_stones_or_beads': 'coloured stones or beads'}
    cues.extend(name for key, name in features.items() if report.get('visual_details', {}).get(key) == 'present')
    if cues:
        description += ' Visible details include ' + ', '.join(cues) + '.'
    return description


def record_details(report, note):
    """Build optional findings from the physical observations in the grounding note."""
    clauses = re.split(r'[,.;]', note)
    positive = '. '.join(clause for clause in clauses
                         if not re.search(r'\b(?:no|not|without|unclear|possibly|maybe|unknown|or)\b', clause, re.I))
    physical_details = re.sub(r'\b(?:loose|scattered) (?:black|colou?red) beads?\b', '', positive, flags=re.I)
    report['visual_details'] = {feature: 'present' if re.search(pattern, physical_details, re.I) else 'unknown'
                                for feature, pattern in FEATURE_EVIDENCE.items()}
    types = []
    for family, pattern in FAMILY_EVIDENCE.items():
        match = re.search(pattern, positive, re.I)
        if match:
            types.append({'type': family, 'name': match.groupdict().get('name', match[0]).lower()})
    report['possible_jewel_types'] = types
    report['jewel_appearance'] = [cue for cue, pattern in APPEARANCE_EVIDENCE.items()
                                  if re.search(pattern, positive, re.I)]


def analyse(reporter, path, scope=None):
    started = time.perf_counter()
    attempts = []
    report, notes = None, []
    scale_seen, scale_area = False, None
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
            prompt += '\nSecond look: independently resolve these uncertainties: ' + '; '.join(notes) + '. Inspect the image again; use unknown if unresolved.'
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
            evidence = candidate.get('visual_evidence', '')
            if (candidate.get('scale', {}).get('presence') == 'present'
                    or re.search(r'\b(?:scale pan|weighing scale|precision (?:scale|balance)|digital scale)\b', evidence, re.I)):
                scale_seen = True
                scale_area = candidate.get('display_area', scale_area)
            # A second look cannot silently erase a disagreement about presence.
            if report and report['jewellery_presence'] != candidate['jewellery_presence']:
                candidate['jewellery_presence'] = 'unknown'
                candidate.pop('jewel_count', None)
                candidate.pop('possible_jewel_types', None)
                candidate.pop('visual_details', None)
                candidate.pop('jewel_appearance', None)
                issues.append('Jewellery presence disagrees between visual passes')
            elif report and candidate['jewellery_presence'] == 'present':
                previous_types = {item['type'] for item in report['possible_jewel_types']}
                current_types = {item['type'] for item in candidate['possible_jewel_types']}
                previous_evidence = report['visual_evidence']
                if (current_types < previous_types and not BACKGROUND.search(previous_evidence)
                        and not BACKGROUND.search(evidence)):
                    # A retry aimed at visibility may describe fewer items.
                    for field in ('possible_jewel_types', 'visual_details', 'jewel_appearance'):
                        candidate[field] = report[field]
                    minimum = sum(2 if item['name'].endswith('s') else 1 for item in candidate['possible_jewel_types'])
                    if candidate.get('jewel_count', 100) < minimum:
                        candidate.pop('jewel_count', None)
                        candidate['count_conflict'] = True
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
        if index == 0 and report and report['jewellery_presence'] == 'present' and BACKGROUND.search(evidence):
            notes.append('Trace physical jewellery separately from images printed under it')
            continue
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
                    report.pop('jewel_appearance', None)
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
                report.pop('jewel_appearance', None)
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
        if report['jewellery_presence'] == 'present' and BACKGROUND.search(report.get('visual_evidence', '')):
            for field in ('jewel_count', 'possible_jewel_types', 'visual_details', 'jewel_appearance'):
                report.pop(field, None)
            notes.append('Consistency: Jewellery details could not be separated from background imagery.')
        if report.pop('count_conflict', False):
            notes.append('Consistency: Item count could not be confirmed.')
        evidence = ' '.join((report.pop('visual_evidence', ''), report.pop('presence_evidence', '')))
        subject = report['inspection_subject']
        if ((subject == 'packet_contents' and not re.search(r'\b(?:packet|pouch|bag|packaging)\b', evidence, re.I))
                or (subject == 'tray_pan' and not re.search(r'\b(?:tray|pan|scale)\b', evidence, re.I))):
            report['inspection_subject'] = 'unknown'
        report.pop('display_area', None)
        if scale_seen:
            report['scale'] = read_scale(reporter, original, scale_area, attempts)
        report['assessment_complete'] = processing == 'completed'
        report['description'] = describe(report)
    return dict(status='generated' if report else 'error', report=report, processing_status=processing,
        observation_notes=notes, attempts=attempts, stage_seconds={a['stage']: a['seconds'] for a in attempts},
        seconds=round(time.perf_counter()-started, 3), original_size=original.size,
        input_size=attempts[0].get('input_size'), inference_mode='inspection', assessment_version=VERSION)
