"""Presence decisions must remain reviewable and cannot default to present."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
from backend.inference import CpuReporter
from backend.risk import classify
from backend.consistency import PATHS


def answer(data, finish='stop'):
    return {'choices': [{'message': {'content': json.dumps(data)}, 'finish_reason': finish}]}


class PresenceTests(unittest.TestCase):
    def analyse(self, check, detail=None):
        reporter = CpuReporter.__new__(CpuReporter)
        reporter.settings = SimpleNamespace(image_side=512, max_tokens=120, inference_mode='thorough')
        audit = answer({key: 'Visible evidence for this test observation.' for key in PATHS})
        responses = [answer(check)]
        if check.get('presence') != 'absent':
            responses.append(answer(detail or {}))
        responses.append(audit)
        reporter.request = Mock(side_effect=responses)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'photo.png'
            Image.new('RGB', (640, 480)).save(path)
            return reporter.analyse(path), reporter.request

    def test_empty_packet_has_no_invented_types_or_details(self):
        result, request = self.analyse({'presence': 'absent', 'note': 'Only paper inside the open packet.'})
        self.assertEqual(result['report']['jewellery_presence'], 'absent')
        self.assertEqual(result['report']['possible_jewel_types'], [])
        self.assertEqual(classify(result['report'])['tier'], 'high')
        self.assertEqual(request.call_count, 2)
        self.assertIn('Only paper', result['presence_check']['raw'])

    def test_legacy_presence_alone_does_not_establish_visibility(self):
        result, _ = self.analyse({'presence': 'present', 'note': 'Gold bangle inside the packet.'},
                                 {'presence': 'present', 'count': 1, 'types': ['bangle']})
        self.assertEqual(result['report']['jewellery_presence'], 'present')
        self.assertEqual(classify(result['report'])['tier'], 'unassessed')

    def test_unclear_or_missing_check_cannot_be_overridden_by_details(self):
        for check in ({}, {'presence': 'present'}, {'presence': 'unknown', 'note': 'Contents obscured.'}):
            with self.subTest(check=check):
                result, _ = self.analyse(check, {'presence': 'present', 'count': 1})
                self.assertEqual(result['report']['jewellery_presence'], 'unknown')
                self.assertTrue(result['observation_notes'])
                self.assertEqual(classify(result['report'])['tier'], 'high')

    def test_zero_count_or_disagreement_requires_review(self):
        for detail in ({'presence': 'present', 'count': 0}, {'presence': 'absent'}):
            result, _ = self.analyse({'presence': 'present', 'note': 'A visible ornament.'}, detail)
            self.assertEqual(result['report']['jewellery_presence'], 'unknown')
            self.assertTrue(result['observation_notes'])


if __name__ == '__main__':
    unittest.main()
