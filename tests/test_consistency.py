import json
import unittest
from unittest.mock import Mock

from backend.consistency import enforce, review
from backend.risk import classify


class ConsistencyTests(unittest.TestCase):
    def test_scale_absent_cannot_keep_display_position_or_weight(self):
        report = {'jewellery_presence': 'present', 'scale': {
            'presence': 'absent', 'display': 'readable', 'weight_text': '12.3 g',
            'weight_value': '12.3', 'weight_unit': 'g', 'jewellery_position': 'on_pan'}}
        notes = enforce(report)
        self.assertEqual(report['scale'], {'presence': 'absent'})
        self.assertEqual(classify(report, notes=notes)['tier'], 'unassessed')
        self.assertIn('contradictions', classify(report, notes=notes)['categories'])

    def test_absent_packet_visibility_and_zero_count_types_are_withheld(self):
        report = {'packet': {'placement': 'absent', 'visibility': 'clear'},
                  'jewel_count': 0, 'possible_jewel_types': [{'type': 'bangle'}]}
        self.assertTrue(enforce(report))
        self.assertNotIn('visibility', report['packet'])
        self.assertNotIn('jewel_count', report)
        self.assertNotIn('possible_jewel_types', report)

    def test_unreadable_weight_is_not_kept_as_numeric_prediction(self):
        for text in ('12.3 g', 'unreadable'):
            report = {'scale': {'presence': 'present', 'display': 'unreadable', 'weight_text': text}}
            self.assertTrue(enforce(report))
            self.assertNotIn('weight_text', report['scale'])

    def test_description_and_every_column_use_only_reviewed_observations(self):
        source = {'description': 'An open packet with indistinct contents.',
                  'jewellery_presence': 'present', 'jewel_count': 1,
                  'possible_jewel_types': [{'type': 'finger_ring', 'evidence': 'a circle'}],
                  'packet': {'placement': 'on_or_near', 'visibility': 'clear'},
                  'scale': {'presence': 'absent'},
                  'visual_details': {'black_beads': 'present', 'threads': 'absent'}}
        raw = json.dumps({'packet': 'Open plastic packet on table.', 'scale': 'No weighing scale in view.'})
        request = Mock(return_value={'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}]})
        report, notes, audit = review(request, 'data:image/png;base64,test', source)
        self.assertEqual(report['jewellery_presence'], 'unknown')
        self.assertNotIn('jewel_count', report)
        self.assertNotIn('possible_jewel_types', report)
        self.assertNotIn('black_beads', report.get('visual_details', {}))
        self.assertNotIn('visibility', report['packet'])
        self.assertIn('Jewellery presence could not be established', report['description'])
        self.assertIn('Scale presence: absent', report['description'])
        self.assertNotIn('finger ring', report['description'])
        self.assertEqual(audit['source_description'], source['description'])
        self.assertTrue(notes)
        self.assertEqual(source['jewellery_presence'], 'present')  # Source remains auditable.

    def test_supported_readable_weight_retains_exact_decimals(self):
        report = {'scale': {'presence': 'present', 'display': 'readable', 'weight_text': '0.00 g'}}
        self.assertEqual(enforce(report), [])
        self.assertEqual(report['scale']['weight_text'], '0.00 g')

    def test_display_requires_supported_scale_presence(self):
        report = {'scale': {'display': 'unreadable'}}
        self.assertTrue(enforce(report))
        self.assertNotIn('display', report['scale'])

    def test_incomplete_review_never_defaults_to_supported(self):
        request = Mock(return_value={'choices': [{'message': {'content': '{"presence":'}, 'finish_reason': 'length'}]})
        report, notes, _ = review(request, 'test', {'jewellery_presence': 'present'})
        self.assertEqual(report['jewellery_presence'], 'unknown')
        self.assertTrue(notes)


if __name__ == '__main__':
    unittest.main()
