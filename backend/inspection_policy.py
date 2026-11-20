"""Jewellery visibility priority; optional metadata never contributes severity."""
VERSION = 4
TIERS = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1, 'unassessed': 0}


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
    if presence == 'absent':
        return result('high', ['No jewellery visible in inspection area'])
    if presence == 'unknown':
        return result('high', ['Jewellery presence cannot be established'])
    if presence != 'present':
        return result('unassessed', ['Jewellery presence was not assessed'])
    assessment = report.get('assessment', {})
    if not assessment:
        visibility = report.get('packet', {}).get('visibility')
        if visibility in ('obscured', 'partly_obscured'):
            return result('high' if visibility == 'obscured' else 'medium', ['Packaging limits jewellery visibility'])
        return result('unassessed', ['Jewellery visibility was not assessed; use the inspection workflow'])
    names = {'cropping': 'Jewellery cropped', 'obstruction': 'Jewellery obscured',
             'blur': 'Blur limits jewellery detail', 'glare_exposure': 'Glare or exposure limits jewellery detail'}
    substantial = [text for key, text in names.items() if assessment.get(key) == 'substantial']
    local = [text for key, text in names.items() if assessment.get(key) == 'local']
    if assessment.get('inspectability') == 'unusable':
        substantial.append('Jewellery cannot be adequately inspected')
    if substantial:
        return result('high', substantial)
    if assessment.get('item_separation') == 'limits_inspection':
        local.append('Overlapping items limit inspection')
    if assessment.get('inspectability') == 'limited':
        local.append('Meaningful jewellery details cannot be fully inspected')
    if local:
        return result('medium', list(dict.fromkeys(local)))
    if report.get('assessment_complete') is False or (assessment and (
        assessment.get('inspectability') != 'adequate' or any(assessment.get(key) not in ('none',) for key in names)
        or assessment.get('item_separation') not in ('adequate', 'not_applicable'))):
        return result('unassessed', ['Essential visibility assessment needs verification'])
    if report.get('jewel_count') == 0:
        return result('unassessed', ['Jewellery presence conflicts with zero count'])
    return result('low', ['Jewellery present and adequately visible'])


def result(tier, reasons, action='infer'):
    return dict(policy_version=VERSION, tier=tier, severity=TIERS[tier], action=action,
                inference_scope='none' if action == 'skip_inference' else 'visible_evidence_only', reasons=reasons)
