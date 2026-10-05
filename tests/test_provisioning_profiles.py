import base64
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

from studio.config import Config
from studio.credentials import install_profiles


class ProvisioningProfileTests(unittest.TestCase):
    def fixture(self, root):
        return Config(root, {'components':[{'direct_provisioning_profiles':{'studio.hypertext.app':{'specifier':'Studio App Direct','build_setting':'STUDIO_APP_PROFILE'}}}], 'release':{'macos':{'team_id':'TEAM'}}})

    def test_matching_profile_installs_and_records_only_owned_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); profile={'Name':'Studio App Direct','UUID':'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE','TeamIdentifier':['TEAM'],'Entitlements':{'com.apple.application-identifier':'TEAM.studio.hypertext.app'}}
            with patch.dict(os.environ, {'APPLE_PROVISIONING_PROFILES_BASE64':json.dumps([base64.b64encode(b'signed-profile').decode()])}), patch('studio.credentials.Path.home',return_value=root), patch('studio.credentials.subprocess.run') as cms:
                cms.return_value.stdout=plistlib.dumps(profile)
                install_profiles(self.fixture(root),root)
                installed=json.loads((root/'installed-profiles.json').read_text())
                self.assertEqual(Path(installed[0]).read_bytes(),b'signed-profile')
                install_profiles(self.fixture(root),root)
                self.assertEqual(json.loads((root/'installed-profiles.json').read_text()),installed)

    def test_another_team_cannot_supply_the_product_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            profile={'Name':'Studio App Direct','UUID':'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE','TeamIdentifier':['OTHER'],'Entitlements':{'com.apple.application-identifier':'OTHER.studio.hypertext.app'}}
            with patch.dict(os.environ, {'APPLE_PROVISIONING_PROFILES_BASE64':json.dumps([base64.b64encode(b'signed-profile').decode()])}), patch('studio.credentials.Path.home',return_value=root), patch('studio.credentials.subprocess.run') as cms:
                cms.return_value.stdout=plistlib.dumps(profile)
                with self.assertRaisesRegex(ValueError,'product, team'):
                    install_profiles(self.fixture(root),root)
                self.assertFalse((root/'installed-profiles.json').exists())

    def test_declared_profiles_fail_closed_without_the_binding(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'credential binding'):
                install_profiles(self.fixture(Path(directory)),Path(directory))
