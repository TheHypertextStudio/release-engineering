import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from studio.diagnostics import migrate_sparkle

class CredentialMigrationTests(unittest.TestCase):
    def test_encrypted_migration_round_trips_without_plaintext_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); private=root/'private.pem'; public=root/'public.pem'; output=root/'migration.enc'
            subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(private)],check=True,capture_output=True)
            subprocess.run(['openssl','pkey','-in',str(private),'-pubout','-out',str(public)],check=True,capture_output=True)
            with patch.dict(os.environ,{'SPARKLE_PRIVATE_KEY':'existing-updater-credential'}):
                migrate_sparkle(public.read_text(),output)
            self.assertNotIn(b'existing-updater-credential',output.read_bytes())
            result=subprocess.run(['openssl','pkeyutl','-decrypt','-inkey',str(private),'-pkeyopt','rsa_padding_mode:oaep','-pkeyopt','rsa_oaep_md:sha256'],input=output.read_bytes(),check=True,capture_output=True)
            self.assertEqual(result.stdout,b'existing-updater-credential')
