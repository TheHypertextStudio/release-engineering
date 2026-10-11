import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio.candidate import CandidateError, write_candidate
from studio.ci_setup import setup
from studio.config import load_config


DECLARATION = '''schema: 1
product: example
repository: studio/example
owner_repository: studio/example
toolchain:
  python: ">=3.11"
  node: "24.20.0"
components:
  - id: app
    kind: swiftpm
    path: .
    checks:
      - [python3, -c, "print('ok')"]
    build:
      configuration: release
release:
  channels: [direct]
'''


class CISetupTests(unittest.TestCase):
    def prepare(self, root, product_root='landing'):
        product = root / 'landing'
        product.mkdir()
        (product / 'studio.yaml').write_text(DECLARATION)
        (product / 'studio.lock.json').write_text(json.dumps({'revision': 'b' * 40}) + '\n')
        config = load_config(product / 'studio.yaml')
        candidate = root / 'candidate'
        candidate.mkdir()
        (candidate / 'studio.yaml').write_text(DECLARATION)
        payload = b'candidate artifact'
        (candidate / 'app.zip').write_bytes(payload)
        data = {
            'schema': 1, 'id': '12-1', 'product': 'example', 'repository': 'studio/example',
            'product_root': product_root,
            'product_lock_sha256': hashlib.sha256((product / 'studio.lock.json').read_bytes()).hexdigest(),
            'source_sha': 'a' * 40, 'tooling_revision': 'b' * 40,
            'workflow_run_id': 12, 'workflow_run_attempt': 1, 'version': '1.0.1',
            'build_number': 12, 'toolchains': config['toolchain'],
            'artifacts': [{'component': 'app', 'channel': 'direct', 'path': 'app.zip',
                           'sha256': hashlib.sha256(payload).hexdigest(), 'size': len(payload)}],
            'checks': {'build': 'passed'}, 'prerequisites': {'tooling-pin': 'passed'},
            'compatibility': {},
        }
        write_candidate(candidate / 'candidate.json', data)
        return product, candidate

    def test_preflight_accepts_same_nested_source_lock_and_toolchain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            product, candidate = self.prepare(root)
            engine = root / 'engine'
            engine.mkdir()
            with patch.dict(os.environ, {'STUDIO_ENGINE_ROOT': str(engine)}, clear=False), \
                 patch('studio.ci_setup.repository_relative_root', return_value='landing'), \
                 patch('studio.ci_setup.execute', side_effect=['a' * 40, 'b' * 40]):
                config = setup(product, candidate_directory=candidate, validate_only=True)
            self.assertEqual(config.root, product.resolve())

    def test_mismatched_root_fails_before_engine_or_provider_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            product, candidate = self.prepare(root, product_root='.')
            with patch('studio.ci_setup.repository_relative_root', return_value='landing'), \
                 patch('studio.ci_setup.execute') as execute:
                with self.assertRaisesRegex(CandidateError, 'product root'):
                    setup(product, candidate_directory=candidate, validate_only=True)
            execute.assert_not_called()

    def test_validate_only_emits_exact_node_without_installing_tools_or_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            product, _ = self.prepare(root)
            output = root / 'output'
            with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output), 'NODE_AUTH_TOKEN': 'fake-token', 'RUNNER_TEMP': str(root)}, clear=True), \
                 patch('studio.ci_setup.execute') as execute:
                setup(product, validate_only=True)
            self.assertIn('node=24.20.0\n', output.read_text())
            self.assertFalse((root / 'studio-npmrc').exists())
            execute.assert_not_called()

    def test_node_range_cannot_select_an_unpinned_workflow_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            product, _ = self.prepare(root)
            declaration = product / 'studio.yaml'
            for value in ('">=24"', 'null', 'false', '0', '""'):
                with self.subTest(value=value):
                    declaration.write_text(DECLARATION.replace('"24.20.0"', value))
                    with self.assertRaisesRegex(CandidateError, 'exact Node'):
                        setup(product, validate_only=True)


if __name__ == '__main__':
    unittest.main()
