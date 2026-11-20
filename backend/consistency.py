"""Evidence review and deterministic checks shared by every prediction column."""
from copy import deepcopy
import json
import re

from .observations import decode, GRAMMAR

VERSION = 4
COMPACT_AUDIT_PROMPT = '''Independently check each candidate prediction against the photo and description. Return only {"supported":["field_name",...]}, listing the exact names of supported predictions. Omit unsupported or contradictory predictions. Empty supported list is allowed. Do not add predictions or repeat their values.
Ignore worn watches and background objects. Do not assume packets contain jewellery. Type requires construction evidence. Weight must be visible on a scale, not paperwork. Count only complete traceable ornaments. Description and columns must agree.
'''
AUDIT_PROMPT = '''Check each proposed prediction against the photo and description. Return a FLAT JSON object: each key is a supported prediction name and each value is a short sentence of visible evidence, at least 3 words. Omit predictions that are unsupported or contradict the description. Empty {} is allowed.
Example evidence: "Only paper inside open packet" supports absent presence; "Gold ornament inside the packet" supports present presence. Never use "absent" or "present" alone as evidence.
Do not copy the input object, output a description key, or return predicted values. Return ONLY evidence sentences. Do not assume a packet contains jewellery. Ignore the worn watch and image-edge background. Jewellery type needs construction evidence; round shape alone is insufficient. Read weight only from a visible scale display.
Description and predictions to check: '''

PATHS = {
    'presence': ('jewellery_presence',), 'types': ('possible_jewel_types',),
    'count': ('jewel_count',), 'arrangement': ('arrangement',),
    'packet': ('packet', 'placement'), 'packet_visibility': ('packet', 'visibility'),
    'scale': ('scale', 'presence'), 'scale_position': ('scale', 'jewellery_position'),
    'display': ('scale', 'display'), 'weight': ('scale', 'weight_text'),
    'threads': ('visual_details', 'threads'), 'black_beads': ('visual_details', 'black_beads'),
    'coloured_stones': ('visual_details', 'coloured_stones_or_beads'),
}


def fields(report):
    values = {}
    for key, path in PATHS.items():
        value = report
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if value is not None and value != 'unknown':
            values[key] = [item['type'] for item in value] if key == 'types' else value
    return values


def withhold(report, key):
    path = PATHS[key]
    target = report if len(path) == 1 else report.get(path[0], {})
    target.pop(path[-1], None)
    if key == 'weight':
        target.pop('weight_value', None)
        target.pop('weight_unit', None)


def enforce(report):
    """Withhold incompatible observations; never choose a fabricated replacement."""
    notes = []
    def reject(keys, message):
        for key in keys:
            withhold(report, key)
        notes.append('Consistency: ' + message)

    values = fields(report)
    presence = values.get('presence')
    if presence == 'present' and values.get('count') == 0:
        reject(('presence', 'count'), 'Jewellery present conflicts with zero count.')
        report['jewellery_presence'] = 'unknown'
    elif presence == 'absent':
        if values.get('count', 0) != 0 or values.get('types') or values.get('arrangement') not in (None, 'not_applicable'):
            reject(('presence', 'types', 'count', 'arrangement'), 'Absent jewellery conflicts with type, count or arrangement.')
            report['jewellery_presence'] = 'unknown'
        if any(values.get(key) == 'present' for key in ('threads', 'black_beads', 'coloured_stones')):
            reject(('threads', 'black_beads', 'coloured_stones'), 'Jewellery details supplied without jewellery.')
    if values.get('arrangement') == 'not_applicable' and (values.get('count') or 0) > 0:
        reject(('arrangement', 'count'), 'Positive count conflicts with not-applicable arrangement.')
    if values.get('scale') == 'absent':
        invalid = [key for key in ('display', 'scale_position', 'weight')
                   if key in values and values[key] not in ('not_applicable', 'not_on_scale')]
        if invalid:
            reject(invalid, 'Scale absent conflicts with display, position or weight.')
    if values.get('weight') and (values.get('display') != 'readable' or values.get('scale') != 'present'):
        reject(('weight',), 'Weight lacks a present scale with readable display.')
    if values.get('scale') != 'present':
        dependent = [key for key in ('display', 'scale_position')
                     if key in fields(report) and values.get(key) not in ('not_applicable', 'not_on_scale')]
        if dependent:
            reject(dependent, 'Scale display or position lacks supported scale presence.')
    if values.get('weight') and not re.fullmatch(r'\s*[+-]?\d+(?:[.,]\d+)?\s*(?:kg|mg|g|oz|ct)?\s*', values['weight'], re.I):
        reject(('weight',), 'Weight is not a visible numeric reading.')
    if values.get('display') == 'readable' and not report.get('scale', {}).get('weight_text'):
        reject(('display',), 'Readable display has no supported weight reading.')
    if values.get('packet') == 'absent' and values.get('packet_visibility') not in (None, 'not_applicable'):
        reject(('packet_visibility',), 'Absent packet cannot have assessed visibility.')
    if values.get('types') and values.get('count') == 0:
        reject(('types', 'count'), 'Jewellery types conflict with zero count.')
    return notes


def describe(report):
    """Describe the final reviewed values so the UI cannot display a rival narrative."""
    labels = {'presence': 'Jewellery presence', 'types': 'Visible jewellery types',
              'count': 'Complete item count', 'arrangement': 'Arrangement',
              'packet': 'Jewellery relative to packet', 'packet_visibility': 'Packet visibility',
              'scale': 'Scale presence', 'scale_position': 'Jewellery relative to scale',
              'display': 'Scale display', 'weight': 'Visible scale reading',
              'threads': 'Threads', 'black_beads': 'Black beads', 'coloured_stones': 'Coloured stones or beads'}
    sentences = []
    for key, value in fields(report).items():
        if isinstance(value, list):
            value = ', '.join(value) if value else 'none'
        sentences.append(f'{labels[key]}: {str(value).replace("_", " ")}.')
    if report.get('jewellery_presence') in (None, 'unknown'):
        sentences.insert(0, 'Jewellery presence could not be established.')
    return ' '.join(sentences) or 'No supported observations; manual review needed.'


def review(request, uri, source, compact=False):
    report = deepcopy(source)
    notes = enforce(report)
    proposed = fields(report)
    # Constrain the reviewer to evidence strings, preventing a copied prediction
    # object from being mistaken for an independent evidence review.
    keys = ' | '.join(json.dumps(json.dumps(key)) for key in proposed)
    audit_grammar = GRAMMAR.replace('root ::= object', 'root ::= auditobject')
    audit_grammar += '\nauditobject ::= "{" ws (auditpair (ws "," ws auditpair)*)? ws "}"\n'
    audit_grammar += f'auditpair ::= ({keys or chr(34) + chr(34)}) ws ":" ws evidence\n'
    audit_grammar += r'''
evidence ::= "\"" evidenceword " " evidenceword " " evidencechar+ "\""
evidenceword ::= [a-zA-Z0-9-]+
evidencechar ::= [a-zA-Z0-9 ,./()%+-]
'''
    if compact:
        audit_grammar = GRAMMAR.replace('root ::= object', 'root ::= verdicts')
        audit_grammar += r'''
verdicts ::= "{" ws "\"supported\"" ws ":" ws "[" ws (fieldname (ws "," ws fieldname)*)? ws "]" ws "}"
'''
        audit_grammar += f'fieldname ::= {keys or chr(34) + chr(34)}\n'
    response = request('/v1/chat/completions', {
        'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': uri}},
            {'type': 'text', 'text': (COMPACT_AUDIT_PROMPT if compact else AUDIT_PROMPT) + '\nDescription: ' + (source.get('description') or 'Unassessed')
             + '\nCandidate predictions:\n' + '\n'.join(f'{key} = {value}' for key, value in proposed.items())}]}],
        'temperature': 0, 'max_tokens': 96 if compact else 240, 'cache_prompt': True,
        'chat_template_kwargs': {'enable_thinking': False}, 'grammar': audit_grammar})
    choice = response['choices'][0]
    raw = choice['message'].get('content') or ''
    evidence, decode_notes = decode(raw)
    evidence = evidence if isinstance(evidence, dict) else {}
    if decode_notes or choice.get('finish_reason') == 'length':
        notes.append('Consistency: Evidence review incomplete; unsupported fields withheld.')
        evidence = {}
    if compact:
        supported = evidence.get('supported', [])
        supported = supported if isinstance(supported, list) else []
        evidence = {key: source.get('description') or 'Independently confirmed against the photograph.'
                    for key in supported if isinstance(key, str) and key in proposed}
    accepted = {key: value.strip() for key, value in evidence.items()
                if key in proposed and isinstance(value, str) and value.strip()
                and (compact or len(value.split()) >= 3)
                and value.strip().lower() not in ('unknown', 'unsupported', 'uncertain', 'not visible', 'n/a')}
    for key in proposed:
        if key not in accepted:
            withhold(report, key)
            notes.append(f'Consistency: {key} lacks supporting evidence or conflicts with the description; withheld.')
    if report.get('jewellery_presence') is None:
        report['jewellery_presence'] = 'unknown'
    if report.get('jewellery_presence') != 'present':
        for key in ('types', 'count', 'arrangement', 'threads', 'black_beads', 'coloured_stones'):
            if report['jewellery_presence'] == 'unknown':
                withhold(report, key)
    notes.extend(enforce(report))
    if report.get('jewellery_presence') == 'absent':
        report.update(jewel_count=0, possible_jewel_types=[], arrangement='not_applicable')
    final_keys = fields(report)
    accepted = {key: value for key, value in accepted.items() if key in final_keys}
    if 'types' in final_keys:
        for item in report['possible_jewel_types']:
            item['evidence'] = accepted.get('types', '')
    report['description'] = describe(report)
    for section in ('packet', 'scale', 'visual_details'):
        if section in report and not report[section]:
            report.pop(section)
    return report, notes, dict(raw=raw, evidence=accepted,
                               supported_fields=list(accepted),
                               source_description=source.get('description'),
                               finish_reason=choice.get('finish_reason'), usage=response.get('usage'))
