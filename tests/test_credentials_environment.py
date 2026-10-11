import base64
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from studio.config import Config
from studio.credentials import prepare


class CredentialEnvironmentTests(unittest.TestCase):
    def test_repository_fallback_is_exported_for_later_steps_without_plaintext_logs(self):
        config = Config(Path('.'), {'components': [], 'release': {}})
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'env'
            log = io.StringIO()
            with patch.dict(os.environ, {'GITHUB_ENV': str(output), 'CLOUDFLARE_API_TOKEN': 'fallback%token\nsecond'}, clear=True), redirect_stdout(log):
                prepare(config, signing=False)
            self.assertIn('CLOUDFLARE_API_TOKEN<<STUDIO_', output.read_text())
            self.assertIn('fallback%token\nsecond', output.read_text())
            self.assertEqual(log.getvalue(), '::add-mask::fallback%25token%0Asecond\n')

    def test_product_binding_overrides_repository_fallback_and_exports_once(self):
        config = Config(Path('.'), {'components': [], 'release': {'credential_bindings': {
            'CLOUDFLARE_API_TOKEN': 'projects/product/secrets/worker-token/versions/latest'}}})
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'env'
            with patch.dict(os.environ, {'GITHUB_ENV': str(output), 'CLOUDFLARE_API_TOKEN': 'stale-repository'}, clear=True), \
                 patch('studio.credentials.execute', return_value='managed-product'), redirect_stdout(io.StringIO()):
                prepare(config, signing=False)
                self.assertEqual(os.environ['CLOUDFLARE_API_TOKEN'], 'managed-product')
            self.assertNotIn('stale-repository', output.read_text())
            self.assertEqual(output.read_text().count('CLOUDFLARE_API_TOKEN<<'), 1)

    def test_repository_certificate_is_prepared_and_visible_to_diagnostics_step(self):
        config = Config(Path('.'), {'components': [], 'release': {}})
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'env'
            env = {'GITHUB_ENV': str(output), 'RUNNER_TEMP': temporary,
                   'APPLE_CERTIFICATE_BASE64': base64.b64encode(b'fake test certificate').decode(),
                   'APPLE_CERTIFICATE_PASSWORD': 'fake test password'}
            with patch.dict(os.environ, env, clear=True), patch('studio.credentials.execute', return_value=''), \
                 patch('studio.credentials.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as security, \
                 redirect_stdout(io.StringIO()):
                prepare(config, signing=True)
            self.assertTrue(any(call.args[0][1] == 'import' for call in security.call_args_list))
            self.assertIn('APPLE_CERTIFICATE_BASE64<<', output.read_text())
            self.assertIn('APPLE_CERTIFICATE_PASSWORD<<', output.read_text())

    def test_declared_binding_denial_does_not_export_stale_repository_fallback(self):
        config = Config(Path('.'), {'components': [], 'release': {'credential_bindings': {
            'CLOUDFLARE_API_TOKEN': 'projects/product/secrets/worker-token/versions/latest'}}})
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'env'
            with patch.dict(os.environ, {'GITHUB_ENV': str(output), 'CLOUDFLARE_API_TOKEN': 'stale-repository'}, clear=True), \
                 patch('studio.credentials.execute', side_effect=subprocess.CalledProcessError(1, ['gcloud'])), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):
                    prepare(config, signing=False)
            self.assertFalse(output.exists())
