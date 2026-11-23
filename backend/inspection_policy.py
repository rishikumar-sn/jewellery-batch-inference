"""Jewellery visibility priority; optional metadata never contributes severity."""
VERSION = 7
TIERS = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1, 'unassessed': 0}
VISIBILITY_REASONS = {'cropping': 'Jewellery cropped', 'obstruction': 'Jewellery obscured',
                      'blur': 'Blur limits jewellery detail',
                      'glare_exposure': 'Glare or exposure limits jewellery detail'}
# Near-zero sharpness only corroborates an unusable image after the visual assessment.
NEAR_ZERO_FOCUS = 8
SEVERE_SOFTNESS_FOCUS = 75


def priority(quality, report=None):
    metrics = quality.get('automatic_metrics') or {}
    # Only decode failures or effectively blank frames can bypass visual assessment.
    hard = 'Unreadable image' in (quality.get('automatic_reasons') or [])
    hard = hard or metrics.get('dark_fraction', 0) >= .995 or metrics.get('bright_fraction', 0) >= .995
    if hard:
        return result('critical', ['Image provides no usable visual evidence'], 'skip_inference')
    if not report:
        return result('unassessed', ['Assessment unavailable; technical review required'])
    presence = report.get('jewellery_presence')
    assessment = report.get('assessment') or {}
    detail = assessment.get('detail')
    if assessment.get('capture') == 'reproduced':
        return result('critical', ['Jewellery is shown in a reproduced photo; direct evidence is required'])
    if presence == 'unknown' and (detail == 'none' or (
            assessment.get('inspectability') == 'unusable' and assessment.get('blur') == 'substantial'
            and metrics.get('focus_score', float('inf')) <= NEAR_ZERO_FOCUS)):
        return result('critical', ['Photo provides essentially no usable view of the jewellery'])
    if presence == 'absent':
        return result('high', ['No jewellery visible in inspection area'])
    if presence == 'unknown':
        return result('high', ['Jewellery presence cannot be established'])
    if presence != 'present':
        return result('unassessed', ['Jewellery presence was not assessed'])
    if not assessment:
        visibility = report.get('packet', {}).get('visibility')
        if visibility in ('obscured', 'partly_obscured'):
            return result('high' if visibility == 'obscured' else 'medium', ['Packaging limits jewellery visibility'])
        return result('unassessed', ['Jewellery visibility was not assessed; use the inspection workflow'])
    substantial = [text for key, text in VISIBILITY_REASONS.items() if assessment.get(key) == 'substantial']
    local = [text for key, text in VISIBILITY_REASONS.items() if assessment.get(key) == 'local']
    separation = assessment.get('item_separation')
    inspectability = assessment.get('inspectability')
    packet_blocked = (report.get('inspection_subject') == 'packet_contents'
                      and (assessment.get('obstruction') == 'substantial' or assessment.get('glare_exposure') == 'substantial'))
    unresolved_softness = (detail == 'limited' and report.get('assessment_complete') is False
                           and metrics.get('focus_score', float('inf')) < SEVERE_SOFTNESS_FOCUS)
    if unresolved_softness:
        return result('high', substantial + ['Severe softness and unresolved jewellery visibility warrant review'])
    if detail in ('poor', 'none') or packet_blocked or (
            detail != 'limited' and (substantial or inspectability == 'unusable')):
        reasons = substantial + ['Most jewellery construction cannot be inspected']
        if separation == 'limits_inspection' and detail == 'poor':
            reasons.insert(0, 'A pile or overlap hides jewellery construction')
        return result('high', reasons)
    if detail == 'limited' or local or (detail != 'clear' and (separation == 'limits_inspection' or inspectability == 'limited')):
        reasons = substantial + local
        if separation == 'limits_inspection':
            reasons.append('Overlap hides jewellery construction' if detail == 'limited' else 'Overlapping items limit inspection')
        return result('medium', reasons or ['Some jewellery construction cannot be inspected'])
    if (report.get('assessment_complete') is False or assessment.get('capture') == 'unknown'
            or any(assessment.get(key) != 'none' for key in VISIBILITY_REASONS)
            or (detail != 'clear' and (inspectability != 'adequate' or separation not in ('adequate', 'not_applicable')))):
        return result('unassessed', ['Essential visibility assessment needs verification'])
    if report.get('jewel_count') == 0:
        return result('unassessed', ['Jewellery presence conflicts with zero count'])
    return result('low', ['Jewellery present and adequately visible'])


def result(tier, reasons, action='infer'):
    return dict(policy_version=VERSION, tier=tier, severity=TIERS[tier], action=action,
                inference_scope='none' if action == 'skip_inference' else 'visible_evidence_only', reasons=reasons)
