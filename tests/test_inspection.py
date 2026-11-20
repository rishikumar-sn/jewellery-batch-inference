import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw
from backend.inspection import analyse, WIRE_NAMES
from backend.risk import classify, quality


GOOD = dict(p='present', v='adequate', f='none', o='none', b='none', e='none', s='adequate', t=['bangle'], c=1)


def answer(data, finish='stop'):
    if 'p' in data:
        data = {**{name: data[key] for key, name in WIRE_NAMES.items()},
                **{k: v for k, v in data.items() if k not in (*WIRE_NAMES, 'v')},
                'note': data.get('note', 'One visible bangle on the inspection pan'),
                'subject': data.get('subject', 'tray_pan')}
    return {'choices': [{'message': {'content': json.dumps(data)}, 'finish_reason': finish}]}

def scale_answer(reading, finish='stop'):
    return {'choices': [{'message': {'content': reading}, 'finish_reason': finish}]}


class InspectionTests(unittest.TestCase):
    def run_model(self, *replies, focus=300, bright_centre=False):
        reporter = SimpleNamespace(settings=SimpleNamespace(image_side=512, max_tokens=160), request=Mock(side_effect=replies))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'photo.png'
            photo = Image.new('RGB', (1080, 1920), '#777777')
            if bright_centre:
                ImageDraw.Draw(photo).rectangle((270, 480, 810, 1440), fill='white')
            photo.save(path)
            with patch('backend.inspection.assess_image', return_value=SimpleNamespace(metrics={'focus_score': focus})):
                result = analyse(reporter, path)
        return result, reporter.request

    def test_good_image_needs_one_pass_and_no_weight(self):
        result, request = self.run_model(answer({**GOOD, 'scale': 'absent'}))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result['processing_status'], 'completed')
        self.assertEqual(classify(result['report'])['tier'], 'low')
        self.assertNotIn('weight_text', result['report']['scale'])
        self.assertIn('response_format', request.call_args.args[1])

    def test_detail_findings_are_available_in_one_pass(self):
        result, request = self.run_model(answer({**GOOD, 'details': ['threads', 'black_beads'], 'note': 'Bangle with a thread and black beads'}))
        self.assertEqual(request.call_count, 1)
        details = result['report']['visual_details']
        self.assertEqual(details['coloured_stones_or_beads'], 'unknown')
        risk = classify(result['report'])
        self.assertEqual(risk['tier'], 'low')
        self.assertTrue({'threads', 'black_beads', 'possible_mangalsutra'} <= set(risk['categories']))

    def test_overlap_with_single_item_prediction_withholds_count(self):
        result, request = self.run_model(answer({**GOOD, 's': 'limits_inspection', 'note': 'Gold bangle on precision scale'}),
                                         scale_answer('not_visible'), scale_answer('not_visible'))
        self.assertEqual(request.call_count, 3)
        self.assertNotIn('jewel_count', result['report'])
        risk = classify(result['report'])
        self.assertEqual(risk['tier'], 'medium')
        self.assertIn('scale_present', risk['categories'])

    def test_scale_reading_requires_matching_digits_and_exports_grams(self):
        for first, second, expected in [('44.52', '44.52', '44.52 g'),
                                        ('11.720g', '11.720 g', '11.720 g'),
                                        ('0.012 kg', '0.012kg', '12.000 g'),
                                        ('44.52', '44.53', None)]:
            with self.subTest(first=first, second=second):
                result, request = self.run_model(answer({**GOOD, 'scale': 'present', 'display_area': 'bottom'}),
                                                 scale_answer(first), scale_answer(second))
                scale = result['report']['scale']
                risk = classify(result['report'])
                self.assertEqual(scale.get('weight_text'), expected)
                self.assertEqual(risk['tier'], 'low')
                self.assertIn('weight_present' if expected else 'weight_unreadable', risk['categories'])
                self.assertEqual(request.call_count, 3)

    def test_unresolved_printed_background_is_excluded_from_findings(self):
        prediction = answer({**GOOD, 'note': 'Bangle and gold handcuffs', 'details': ['black_beads']})
        result, request = self.run_model(prediction, prediction)
        self.assertEqual(request.call_count, 2)
        report = result['report']
        self.assertNotIn('handcuffs', report['description'])
        self.assertNotIn('possible_jewel_types', report)
        self.assertNotIn('visual_details', report)
        self.assertIn('contradictions', classify(report, notes=result['observation_notes'])['categories'])

    def test_findings_without_matching_visual_evidence_are_withheld(self):
        result, _ = self.run_model(answer({**GOOD, 'note': 'Coiled gold chain on weighing scale',
                                          't': ['toe_ring'], 'details': ['threads', 'coloured_stones_or_beads']}),
                                   scale_answer('44.52'), scale_answer('44.52'))
        report = result['report']
        self.assertFalse(report['possible_jewel_types'])
        self.assertEqual(set(report['visual_details'].values()), {'unknown'})
        self.assertNotIn('toe ring', report['description'])
        self.assertNotIn('stones', report['description'])

    def test_description_and_types_share_the_same_grounded_findings(self):
        result, _ = self.run_model(answer({**GOOD, 'c': 2,
            'note': 'Gold choker with beaded trim and a pair of earrings, no coloured stones'}))
        report = result['report']
        self.assertEqual([item['name'] for item in report['possible_jewel_types']], ['choker', 'earrings'])
        self.assertNotIn('jewel_count', report)
        for term in ('choker', 'earrings', 'beaded decoration'):
            self.assertIn(term, report['description'])
        self.assertNotIn('stones', report['description'])
        self.assertEqual(classify(report, notes=result['observation_notes'])['tier'], 'low')

    def test_unconfirmed_location_is_omitted_from_description(self):
        result, _ = self.run_model(answer({**GOOD, 'note': 'A gold bangle is visible.'}))
        self.assertEqual(result['report']['inspection_subject'], 'unknown')
        self.assertNotIn('tray', result['report']['description'])

    def test_glass_holders_are_checked_even_on_scale_pan(self):
        result, request = self.run_model(
            answer({**GOOD, 's': 'unknown', 'note': 'Two glass beads on wooden holder', 'details': ['black_beads']}),
            answer({'presence': 'absent', 'note': 'Glass inserts in holder on scale pan'}))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(classify(result['report'])['tier'], 'high')
        self.assertNotIn('visual_details', result['report'])

    def test_washed_out_centre_cannot_pass_without_review(self):
        result, request = self.run_model(answer(GOOD), answer(GOOD), bright_centre=True)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result['processing_status'], 'needs_verification')
        self.assertEqual(classify(result['report'])['tier'], 'unassessed')

    def test_absent_subject_is_high_even_with_unknown_visibility(self):
        result, request = self.run_model(answer(dict(p='absent', v='unknown', f='unknown', o='unknown', b='unknown', e='unknown', s='unknown')))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(classify(result['report'])['tier'], 'high')
        self.assertEqual(result['report']['jewel_count'], 0)

    def test_local_and_substantial_limitations_do_not_accumulate(self):
        for severity, expected in [('local', 'medium'), ('substantial', 'high')]:
            with self.subTest(severity=severity):
                result, _ = self.run_model(answer({**GOOD, 'v': 'limited' if severity == 'local' else 'unusable', 'o': severity, 'b': severity}))
                self.assertEqual(classify(result['report'])['tier'], expected)

    def test_essential_uncertainty_uses_one_larger_second_look(self):
        result, request = self.run_model(answer({**GOOD, 'b': 'unknown'}), answer(GOOD))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result['attempts'][1]['input_size'], (360, 640))
        self.assertEqual(result['processing_status'], 'completed')

    def test_unresolved_visibility_never_low(self):
        bad = {**GOOD, 'b': 'unknown'}
        result, request = self.run_model(answer(bad), answer(bad))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result['processing_status'], 'needs_verification')
        risk = classify(result['report'])
        self.assertEqual(risk['tier'], 'unassessed')
        self.assertIn('needs_review', risk['categories'])

    def test_failed_or_truncated_assessment_is_technical_review(self):
        for reply in [answer({}, 'stop'), answer(GOOD, 'length'), RuntimeError('timeout')]:
            with self.subTest(reply=reply):
                result, request = self.run_model(reply, reply)
                self.assertEqual(request.call_count, 2)
                self.assertEqual(result['processing_status'], 'failed')
                self.assertEqual(classify(result['report'], status='error')['tier'], 'unassessed')

    def test_presence_disagreement_cannot_silently_become_low(self):
        result, _ = self.run_model(answer({**GOOD, 'b': 'unknown'}), answer({**GOOD, 'p': 'absent', 't': [], 'c': 0}))
        self.assertEqual(result['report']['jewellery_presence'], 'unknown')
        self.assertNotIn('jewel_count', result['report'])
        self.assertEqual(classify(result['report'])['tier'], 'high')

    def test_packet_subject_uses_independent_presence_check_without_anchoring(self):
        result, request = self.run_model(answer({**GOOD, 'subject': 'packet_contents'}),
            answer({'presence': 'absent', 'note': 'Only paper in the open packet.'}))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result['report']['jewellery_presence'], 'absent')
        self.assertEqual(result['report']['jewel_count'], 0)
        self.assertEqual(classify(result['report'])['tier'], 'high')
        self.assertEqual(result['processing_status'], 'completed')
        self.assertIn('packet_present', classify(result['report'])['categories'])
        self.assertEqual(result['attempts'][1]['stage'], 'presence_verification')
        self.assertEqual(len(request.call_args.args[1]['messages']), 1)

    def test_failed_packet_verification_cannot_be_low(self):
        result, request = self.run_model(answer({**GOOD, 'subject': 'packet_contents'}), RuntimeError('timeout'))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result['processing_status'], 'needs_verification')
        self.assertNotEqual(classify(result['report'])['tier'], 'low')

    def test_error_status_overrides_apparently_good_report(self):
        result, _ = self.run_model(answer(GOOD))
        self.assertEqual(classify(result['report'], status='error')['tier'], 'unassessed')

    def test_severe_sharpness_disagreement_cannot_silently_be_low(self):
        result, request = self.run_model(answer(GOOD), answer(GOOD), focus=3)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result['processing_status'], 'needs_verification')
        self.assertEqual(classify(result['report'])['tier'], 'unassessed')

    def test_sharpness_supports_model_confirmed_visibility_limitation(self):
        result, _ = self.run_model(answer(GOOD), answer({**GOOD, 'b': 'substantial'}), focus=3)
        self.assertEqual(classify(result['report'])['tier'], 'high')

    def test_low_audit_sample_does_not_change_priority(self):
        result, _ = self.run_model(answer(GOOD))
        selected = classify(result['report'], {'sha256': '0' * 64})
        ordinary = classify(result['report'], {'sha256': '0' * 63 + '1'})
        self.assertEqual(selected['tier'], 'low')
        self.assertIn('audit_sample', selected['categories'])
        self.assertNotIn('audit_sample', ordinary['categories'])

    def test_opaque_packet_unknown_presence_is_high(self):
        unknown = {**GOOD, 'p': 'unknown', 'v': 'unusable', 'o': 'substantial'}
        result, _ = self.run_model(answer(unknown), answer(unknown))
        self.assertEqual(classify(result['report'])['tier'], 'high')

    def test_optional_conflicts_and_weight_never_raise_priority(self):
        result, _ = self.run_model(answer(GOOD))
        report = result['report']
        baseline = classify(report)
        report.update(scale={'presence': 'present', 'display': 'unreadable'}, arrangement='bundled',
                      visual_details={'threads': 'present', 'black_beads': 'present'})
        risk = classify(report, notes=['Consistency: optional packet field withheld'])
        self.assertEqual(risk['tier'], baseline['tier'])
        self.assertEqual(risk['score'], baseline['score'])
        self.assertNotIn('needs_review', risk['categories'])

    def test_global_low_focus_does_not_skip_or_raise_priority(self):
        result, _ = self.run_model(answer(GOOD))
        q = dict(automatic_reject=True, automatic_reasons=['Blur / low pixel detail', 'Low resolution'], automatic_metrics={'focus_score': 3})
        risk = classify(result['report'], q)
        self.assertEqual(risk['tier'], 'low')
        self.assertEqual(risk['action'], 'infer')

    def test_blank_and_corrupt_images_are_critical(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'blank.png'
            for colour in ('black', 'white'):
                Image.new('RGB', (300, 300), colour).save(path)
                risk = classify(quality=quality(path))
                self.assertEqual(risk['tier'], 'critical')
                self.assertEqual(risk['action'], 'skip_inference')
            path.write_bytes(b'corrupt')
            self.assertEqual(classify(quality=quality(path))['tier'], 'critical')
