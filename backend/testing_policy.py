"""Inspection priority: image quality and jewellery availability are independent."""
VERSION=3
TIERS={"critical":4,"high":3,"medium":2,"low":1}
SEVERE_FOCUS_LIMIT=10.0
MARGINAL_FOCUS_LIMIT=310.0

def priority(quality, report=None):
    metrics = quality.get('automatic_metrics') or {}
    checkpoints = quality.get('automatic_reasons') or []
    focus = metrics.get('focus_score')
    reasons, severity = [], 0
    hard = any(r in checkpoints for r in ('Unreadable image', 'Too dark', 'Overexposed / nearly blank', 'Low resolution'))
    if focus is not None and focus < SEVERE_FOCUS_LIMIT:
        hard = True
        reasons.append('Severe blur / insufficient image detail')
    reasons.extend(r for r in checkpoints if r not in reasons
                   and not (hard and focus is not None and focus < SEVERE_FOCUS_LIMIT and r == 'Blur / low pixel detail'))
    if hard:
        return dict(policy_version=VERSION, tier='critical', severity=10, action='skip_inference',
                    inference_scope='none', reasons=reasons or ['Image cannot be assessed'])
    if quality.get('automatic_reject'):
        severity += 4
    elif focus is not None and focus < MARGINAL_FOCUS_LIMIT:
        severity += 1
        reasons.append('Limited sharpness')
    if not report or report.get('jewellery_presence') != 'present':
        severity += 4
        reasons.append('No jewellery visible in inspection area' if (report or {}).get('jewellery_presence') == 'absent'
                       else 'Jewellery presence unclear')
    elif report.get('jewel_count') == 0:
        severity += 4
        reasons.append('Conflicting jewellery presence: reported present with zero items')
    if report:
        if any(item['type'] == 'not_known' for item in report.get('possible_jewel_types', [])):
            severity += 2
            reasons.append('Jewellery type unclear')
        if report.get('arrangement') in ('bundled', 'mixed'):
            severity += 2
            reasons.append('Bundled / overlapping jewellery')
        if report.get('jewellery_presence') == 'present' and 'jewel_count' in report and report.get('jewel_count') is None:
            severity += 2
            reasons.append('Item count cannot be established')
        if report.get('packet', {}).get('visibility') in ('partly_obscured', 'obscured'):
            severity += 2
            reasons.append('Packet limits visibility')
        if report.get('scale', {}).get('presence') == 'present' and report.get('scale', {}).get('display') in ('unreadable', 'unknown'):
            severity += 1
            reasons.append('Scale display unreadable')
    tier = 'high' if severity >= 4 else ('medium' if severity else 'low')
    return dict(policy_version=VERSION, tier=tier, severity=severity,
                action='limited_inference' if quality.get('automatic_reject') else 'infer',
                inference_scope='visible_evidence_only', reasons=reasons or ['No detected quality issues'])
