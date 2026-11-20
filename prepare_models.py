"""Verify the exact model/projector/runtime used by the testing application."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_testing_artifacts(config):
    manifest = json.loads((ROOT / 'reference/testing-artifacts.json').read_text(encoding='utf-8'))
    for key in ('model', 'projector', 'server'):
        path = Path(config[key])
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise RuntimeError(f'Missing testing {key}: {path}. Restore the original testing file; the app does not substitute another model.')
        if digest(path) != manifest[key]['sha256']:
            raise RuntimeError(f'Testing {key} differs from the recorded baseline: {path}. Check the testing setup before changing the baseline.')


if __name__ == '__main__':
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    verify_testing_artifacts(config)
    print('Verified original testing model, projector and runtime. No replacement downloads.')
