import tempfile
import unittest
from pathlib import Path

from studio.config import ConfigError, load_config

CONFIG = '''schema: 1
product: example
repository: studio/example
owner_repository: studio/release-engineering
toolchain:
  python: ">=3.11"
components:
  - id: app
    kind: swiftpm
    path: .
    checks:
      - [python3, -c, "print('checked')"]
    build:
      configuration: release
release:
  channels: [direct]
  required_checks: [app]
  required_evidence: [installation]
  version:
    build_offset: 100
'''

class ConfigTests(unittest.TestCase):
    def load(self, content):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'studio.yaml'
            path.write_text(content)
            return load_config(path)

    def test_loads_native_contract(self):
        config = self.load(CONFIG)
        self.assertEqual(config.product, 'example')
        self.assertEqual(config.components[0]['kind'], 'swiftpm')

    def test_rejects_release_hooks(self):
        with self.assertRaises(ConfigError):
            self.load(CONFIG.replace('  channels:', '  hooks: ["sh -c deploy"]\n  channels:'))

    def test_rejects_secret_values(self):
        with self.assertRaises(ConfigError):
            self.load(CONFIG.replace('  python:', '  api_token: secret\n  python:'))

    def test_rejects_shell_and_path_escape(self):
        for content in (CONFIG.replace('python3, -c', 'sh, -c'), CONFIG.replace('path: .', 'path: ../outside')):
            with self.subTest(content=content), self.assertRaises(ConfigError):
                self.load(content)

    def test_rejects_duplicate_components(self):
        with self.assertRaises(ConfigError):
            self.load(CONFIG.replace('release:', '  - id: app\n    kind: pnpm\n    path: .\n    checks: []\nrelease:'))

    def test_allows_only_resource_references_for_credential_bindings(self):
        binding = '  credential_bindings:\n    API_TOKEN: projects/test/secrets/token/versions/latest\n'
        self.assertEqual(self.load(CONFIG.replace('  channels:', binding + '  channels:')).data['release']['credential_bindings']['API_TOKEN'], 'projects/test/secrets/token/versions/latest')
        with self.assertRaises(ConfigError):
            self.load(CONFIG.replace('  channels:', binding.replace('projects/test/secrets/token/versions/latest', 'secret-value') + '  channels:'))

    def test_duplicate_yaml_keys_fail_closed(self):
        with self.assertRaises(ConfigError):
            self.load(CONFIG + 'product: other\n')

    def test_release_argv_cannot_override_shared_policy(self):
        with self.assertRaises(ConfigError):
            self.load(CONFIG.replace('  channels:', '  custom:\n    argv: [native, deploy]\n  channels:'))
