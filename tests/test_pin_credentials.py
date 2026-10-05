import io
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from studio.pin_credentials import prepare


class PinCredentialTests(unittest.TestCase):
    def test_secret_is_masked_before_output_and_never_logged_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            secret = 'private%key\nsecond line'
            log = io.StringIO()
            with patch.dict(os.environ, {'STUDIO_PIN_UPDATE_SECRET': 'projects/curfew-release/secrets/curfew-pin-key/versions/latest', 'GITHUB_OUTPUT': str(output)}), patch('studio.pin_credentials.execute', return_value=secret) as access, redirect_stdout(log):
                prepare()
            access.assert_called_once_with(['gcloud', 'secrets', 'versions', 'access', 'latest', '--secret', 'curfew-pin-key', '--project', 'curfew-release'], capture=True)
            self.assertEqual(log.getvalue(), '::add-mask::private%25key%0Asecond line\n')
            self.assertIn(secret, output.read_text())

    def test_invalid_binding_cannot_select_cli_options(self):
        for value in ('', 'projects/x/secrets/--help/versions/latest', 'projects/x/secrets/key/versions/latest/extra'):
            with patch.dict(os.environ, {'STUDIO_PIN_UPDATE_SECRET': value}), patch('studio.pin_credentials.execute') as access:
                with self.assertRaises(ValueError):
                    prepare()
                access.assert_not_called()
