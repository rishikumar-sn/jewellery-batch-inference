"""Jewellery visibility priority, with independent informational finding filters."""
import hashlib
import re
from .quality_gate import assess_image
from .inspection_policy import priority, TIERS, VERSION as POLICY_VERSION


def quality(path):
    assessment = assess_image(path)
    with open(path, 'rb') as stream:
        sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(sha256=sha256, bucket='poor' if assessment.reject else 'ok',
                source='algorithm', automatic_reject=assessment.reject,
                automatic_reasons=list(assessment.reasons), automatic_metrics=assessment.metrics)


def classify(report=None, quality=None, status='done', notes=None):
    """Finding filters never add severity or alter the visibility policy's tier."""
    risk = priority(quality or {}, report)
    if status == 'error' and risk['tier'] != 'critical':
        risk = {**risk, 'tier': 'unassessed', 'severity': 0,
                'reasons': ['Assessment failed; technical review required']}
    categories = []
    conflicts = [note for note in (notes or []) if note.startswith('Consistency:') or 'conflict' in note.lower() or 'disagree' in note.lower()]
    if conflicts:
        categories.append('contradictions')
    report = report or {}
    assessment = report.get('assessment', {})
    digest = (quality or {}).get('sha256', '')
    if risk['tier'] == 'low' and len(digest) == 64:
        try:
            if int(digest, 16) % 20 == 0:
                categories.append('audit_sample')
        except ValueError:
            pass
    packet, scale, details = (report.get(key, {}) for key in ('packet', 'scale', 'visual_details'))
    if report.get('jewellery_presence') == 'absent':
        categories.append('jewellery_absent')
    if (quality or {}).get('automatic_reject'):
        categories.append('poor_quality')
    for key in ('threads', 'black_beads', 'coloured_stones_or_beads'):
        if details.get(key) == 'present':
            categories.append(key)
    if details.get('black_beads') == 'present' and report.get('jewellery_presence') == 'present':
        categories.append('possible_mangalsutra')
    if report.get('inspection_subject') == 'packet_contents' or packet.get('placement') in ('inside', 'on_or_near'):
        categories.append('packet_present')
    if packet.get('placement') == 'inside' or (report.get('inspection_subject') == 'packet_contents' and report.get('jewellery_presence') == 'present'):
        categories.append('packet_inside')
    if packet.get('visibility') in ('partly_obscured', 'obscured'):
        categories.append('obscured')
    if assessment.get('obstruction') in ('local', 'substantial'):
        categories.append('obscured')
    if assessment.get('item_separation') == 'limits_inspection':
        categories.append('bundled')
    if report.get('arrangement') in ('bundled', 'mixed'):
        categories.append('bundled')
    if scale.get('presence') == 'present':
        categories.append('scale_present')
        if scale.get('display') in ('unreadable', 'unknown', 'not_visible'):
            categories.append('weight_unreadable')
    weight = scale.get('weight_text')
    if isinstance(weight, str) and re.fullmatch(r'\s*[+-]?\d+(?:[.,]\d+)?\s*(?:kg|mg|g|oz|ct)?\s*', weight, re.I):
        categories.append('weight_present')
    if status == 'error' or risk['tier'] == 'unassessed' or report.get('assessment_complete') is False or report.get('jewellery_presence') != 'present' or report.get('jewel_count') == 0:
        categories.append('needs_review')
    return {**risk, 'score': risk['severity'], 'categories': sorted(set(categories))}
