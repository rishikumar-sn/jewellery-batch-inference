import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PIL import Image
from backend.inference import CpuReporter
from backend.risk import classify


def response(data, finish='stop'):
    return {'choices': [{'message': {'content': data if isinstance(data, str) else json.dumps(data)}, 'finish_reason': finish}]}


class BalancedTests(unittest.TestCase):
    def analyse(self, replies, scope='visible_evidence_only'):
        reporter = CpuReporter.__new__(CpuReporter)
        reporter.settings = SimpleNamespace(image_side=512, max_tokens=192, inference_mode='balanced')
        reporter.request = Mock(side_effect=replies)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.png'
            Image.new('RGB', (640, 480)).save(path)
            result = reporter.analyse(path, scope)
        return result, reporter.request

    def test_confirmed_empty_image_uses_one_request_and_stays_high_priority(self):
        result, request = self.analyse([response({'presence': 'absent', 'note': 'Only paper in open packet.'})])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result['report']['jewel_count'], 0)
        self.assertEqual(classify(result['report'])['tier'], 'high')

    def test_present_image_retains_independent_field_review(self):
        result, request = self.analyse([
            response({'presence': 'present', 'note': 'Gold bangle inside packet.'}),
            response({'p': 'present', 'n': 'One gold bangle inside packet.', 't': ['bangle'], 'c': 1, 'k': 'inside'}),
            response({'supported': ['presence', 'types', 'count', 'packet']})])
        self.assertEqual(request.call_count, 3)
        self.assertEqual(result['report']['jewel_count'], 1)
        self.assertEqual(result['report']['possible_jewel_types'][0]['type'], 'bangle')
        self.assertTrue(result['consistency_review']['independent_review'])
        self.assertEqual([call.args[1]['max_tokens'] for call in request.call_args_list], [80, 192, 96])

    def test_unsupported_columns_are_withheld(self):
        result, _ = self.analyse([
            response({'presence': 'present', 'note': 'Visible gold ornament.'}),
            response({'p': 'present', 'n': 'Gold ornament with indistinct construction.', 't': ['bangle'], 'c': 2}),
            response({'supported': ['presence']})])
        self.assertNotIn('jewel_count', result['report'])
        self.assertNotIn('possible_jewel_types', result['report'])
        self.assertTrue(result['observation_notes'])

    def test_unknown_presence_cannot_be_upgraded_by_detailed_predictions(self):
        result, _ = self.analyse([
            response({'presence': 'unknown', 'note': 'Contents hidden by packet.'}),
            response({'p': 'present', 'n': 'Gold ring in packet.', 't': ['finger_ring'], 'c': 1}),
            response({'supported': ['types', 'count']})])
        self.assertEqual(result['report']['jewellery_presence'], 'unknown')
        self.assertNotIn('jewel_count', result['report'])
        self.assertEqual(classify(result['report'])['tier'], 'high')

    def test_truncated_review_cannot_confirm_fields(self):
        result, _ = self.analyse([
            response({'presence': 'present', 'note': 'Visible gold bangle.'}),
            response({'p': 'present', 'n': 'Visible gold bangle.', 't': ['bangle']}),
            response('{"supported":["presence",', 'length')])
        self.assertEqual(result['report']['jewellery_presence'], 'unknown')
        self.assertNotIn('possible_jewel_types', result['report'])

    def test_truncated_details_are_reviewed_without_full_regeneration(self):
        result, request = self.analyse([
            response({'presence': 'present', 'note': 'Visible gold bangle.'}),
            response('{"p":"present","n":"Visible gold bangle.","t":["bangle"],', 'length'),
            response({'supported': ['presence', 'types']})])
        self.assertEqual(request.call_count, 3)
        self.assertEqual(result['report']['possible_jewel_types'][0]['type'], 'bangle')
        self.assertTrue(result['observation_notes'])


if __name__ == '__main__':
    unittest.main()
