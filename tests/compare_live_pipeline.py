"""Fresh, cache-bypassing comparison of testing and FinalApp inference wrappers."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / 'AU-Image-Analysis'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SOURCE))
from jewellery_qwen35.backend import CpuReporter as TestingReporter, Settings as TestingSettings
from backend.inference import CpuReporter as AppReporter, Settings as AppSettings


config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
output = ROOT / 'test-output/fresh-pipeline-comparison'
output.mkdir(parents=True, exist_ok=True)
images = [SOURCE / 'AU-images-v1' / filename for filename in ('25660000502452.jpg', '25660000506405-2.jpg')]
all_results = {}
for name, reporter_type, settings_type in [('testing', TestingReporter, TestingSettings), ('finalapp', AppReporter, AppSettings)]:
    reporter_type.log_directory = output
    reporter = reporter_type(settings_type(**config))
    try:
        results = {path.name: reporter.analyse(path) for path in images}
        all_results[name] = results
        for filename, result in results.items():
            print(name, filename, 'report_fields', len(result.get('report') or {}), 'seconds', result['seconds'], flush=True)
    finally:
        reporter.close()
(output / 'results.json').write_text(json.dumps(all_results, indent=2), encoding='utf-8')
for path in images:
    old, new = (all_results[name][path.name] for name in ('testing', 'finalapp'))
    assert old['report'] == new['report'], path.name
    assert [a['raw'] for a in old['attempts']] == [a['raw'] for a in new['attempts']], path.name
print('Fresh raw responses and decoded observations match on both images.', flush=True)
