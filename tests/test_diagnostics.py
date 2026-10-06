import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from studio.config import Config
from studio.diagnostics import migrate_sparkle, report

class CredentialReportTests(unittest.TestCase):
    def test_report_checks_declared_secret_manager_bindings_without_writing_values(self):
        config=Config(Path('.'),{'release':{'credential_bindings':{
            'APPLE_CERTIFICATE_BASE64':'projects/studio/secrets/certificate/versions/latest',
            'APPLE_PROVISIONING_PROFILES_BASE64':'projects/studio/secrets/profiles/versions/latest',
        }}})
        def read_secret(argv,**kwargs):
            name=argv[argv.index('--secret')+1]
            return subprocess.CompletedProcess(argv,0,stdout='private-certificate' if name=='certificate' else 'profile-bytes',stderr='')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{},clear=True), patch('studio.diagnostics.subprocess.run',side_effect=read_secret):
            path=Path(directory)/'prerequisites.json'
            data=report(path,config)
            self.assertTrue(data['credentials']['APPLE_CERTIFICATE_BASE64'])
            self.assertTrue(data['credentials']['APPLE_PROVISIONING_PROFILES_BASE64'])
            self.assertEqual(data['sources']['APPLE_CERTIFICATE_BASE64'],'secret-manager')
            self.assertNotIn('private-certificate',path.read_text())
            self.assertNotIn('profile-bytes',path.read_text())

    def test_report_marks_inaccessible_binding_unavailable(self):
        config=Config(Path('.'),{'release':{'credential_bindings':{'APPLE_API_PRIVATE_KEY':'projects/studio/secrets/apple-key/versions/latest'}}})
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{},clear=True), patch('studio.diagnostics.subprocess.run',return_value=subprocess.CompletedProcess([],1,stdout='',stderr='auth failed')):
            data=report(Path(directory)/'prerequisites.json',config)
            self.assertFalse(data['credentials']['APPLE_API_PRIVATE_KEY'])
            self.assertEqual(data['sources']['APPLE_API_PRIVATE_KEY'],'secret-manager-unavailable')

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
