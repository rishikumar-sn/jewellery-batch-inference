"""Regression fixtures captured from the user's actual testing pipeline."""
from collections import Counter
import json
from pathlib import Path
import unittest
from backend.observations import expand
from backend.risk import quality
from backend.testing_policy import priority

ROOT = Path(__file__).resolve().parents[1]


class TestingParity(unittest.TestCase):
    def test_legacy_policy_and_decoder_preserve_84_original_fixtures(self):
        reference = json.loads((ROOT / 'reference/testing-qwen35.json').read_text(encoding='utf-8'))
        counts = Counter()
        for row in reference['reports']:
            with self.subTest(filename=row['filename']):
                risk = priority(row['quality_gate'], row.get('report'))
                self.assertEqual(risk['tier'], row['triage']['tier'])
                self.assertEqual(risk['severity'], row['triage']['severity'])
                self.assertEqual(risk['reasons'], row['triage']['reasons'])
                counts[risk['tier']] += 1
                if row.get('attempts'):
                    # Actual stored raw response, rather than a fabricated prompt fixture.
                    _, decoded, _ = expand(row['attempts'][-1]['raw'], include_notes=True)
                    self.assertEqual(decoded, row['report'])
        self.assertEqual(counts, {'critical':21, 'high':19, 'medium':30, 'low':14})

    def test_current_quality_measurements_match_testing_images(self):
        reference = json.loads((ROOT / 'reference/testing-qwen35.json').read_text(encoding='utf-8'))
        for row in reference['reports']:
            path = Path(row['source_path'])
            if not path.exists():
                self.skipTest('Original images unavailable for live quality parity')
            measured = quality(path)
            self.assertEqual(measured, row['quality_gate'], row['filename'])


if __name__ == '__main__':
    unittest.main()
