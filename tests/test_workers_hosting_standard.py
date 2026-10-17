"""Retired hosting paths fail before native commands or credential access."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio.config import Config, ConfigError, load_config
from studio.lifecycle import native_command
from studio.providers import deploy_site
from studio.credentials import prepare


class WorkersHostingTests(unittest.TestCase):
    def test_retired_site_adapters_cannot_invoke_provider_commands(self):
        for provider in ('pages', 'vercel'):
            with self.subTest(provider=provider), self.assertRaisesRegex(ValueError, 'native.*Workers'):
                deploy_site({'id': 'site', 'deploy': {'provider': provider, 'environments': {'production': {'project': 'old'}}, 'health_urls': {'production': 'https://example.test'}}},
                            {'source_sha': 'a' * 40, 'sha256': 'b' * 64}, 'production', Path('.'), {'wrangler': '4.120.1', 'vercel': '50.28.1'},
                            runner=lambda *a, **kw: self.fail('must not invoke a retired provider'))

    def test_retired_build_profile_fails_without_fallback(self):
        with self.assertRaisesRegex(ConfigError, 'native.*Workers'):
            native_command({'kind': 'static-site', 'build': {'profile': 'vercel-prebuilt'}}, 'build')

    def test_retired_credential_binding_is_rejected_before_cloud_access(self):
        config = Config(Path('.'), {'release': {'credential_bindings': {'VERCEL_TOKEN': 'projects/example/secrets/old/versions/latest'}}})
        with patch('studio.credentials.execute', side_effect=AssertionError('must not fetch')):
            with self.assertRaisesRegex(ValueError, 'Retired hosting credential'):
                prepare(config, signing=False)

    def test_stale_repository_token_is_not_forwarded_to_later_steps(self):
        config = Config(Path('.'), {'release': {}})
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'VERCEL_TOKEN': 'obsolete', 'GITHUB_ENV': str(Path(directory) / 'env')}, clear=True), patch('builtins.print') as output:
            prepare(config, signing=False)
            self.assertFalse((Path(directory) / 'env').exists())
            output.assert_not_called()

    def test_load_rejects_retired_profiles_and_bindings_before_lifecycle(self):
        declaration = """schema: 1
product: example
repository: TheHypertextStudio/example
owner_repository: TheHypertextStudio/release-engineering
toolchain: {}
components:
  - id: site
    kind: static-site
    path: .
    checks: []
    build:
      profile: vercel-prebuilt
release: {}
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'studio.yaml'
            for content in (declaration, declaration.replace('    build:\n      profile: vercel-prebuilt', '    deploy:\n      provider: pages'), declaration.replace('    build:\n      profile: vercel-prebuilt', '    deploy: invalid')):
                path.write_text(content)
                with self.assertRaises(ConfigError): load_config(path)
