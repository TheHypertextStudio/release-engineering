import base64
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
from studio.config import Config
from studio import macos

PUB = base64.b64encode(b'p' * 32).decode()
KEY = base64.b64encode(b's' * 64 + b'p' * 32).decode()


def fixture(root):
    (root / 'app.entitlements').write_bytes(plistlib.dumps({'com.apple.security.get-task-allow': False}))
    component = {'id': 'app', 'kind': 'macos', 'path': '.', 'project': 'App.xcodeproj', 'scheme': 'App', 'macos': {'app_name': 'App', 'bundle_id': 'dev.williecubed.app', 'entitlements': 'app.entitlements', 'nested_entitlements': {}}}
    config = Config(root, {'product': 'app', 'release': {'channels': ['direct'], 'macos': {'team_id': 'TEAM', 'developer_id': 'Developer ID Application: Studio (TEAM)', 'notary_profile': 'studio-notary', 'sparkle': {'feed_url': 'https://example.com/appcast.xml', 'download_url': 'https://example.com/releases/', 'public_key': PUB, 'tools_path': str(root / 'tools')}}}})
    (root / 'tools').mkdir()
    for name in ('sign_update', 'generate_appcast'):
        file = root / 'tools' / name
        file.write_text('tool')
        file.chmod(0o755)
    return config, component


class NativeRunner:
    def __init__(self, root, config, *, accepted=True):
        self.root, self.config, self.calls, self.accepted = root, config, [], accepted

    def __call__(self, command, cwd, **kwargs):
        self.calls.append(command)
        if command[:2] == ['security', 'find-identity']:
            return self.config['release']['macos']['developer_id']
        if 'archive' in command and command[0] == 'xcodebuild':
            return ''
        if '-exportArchive' in command:
            app = Path(command[command.index('-exportPath') + 1]) / 'App.app'
            (app / 'Contents' / 'MacOS').mkdir(parents=True)
            (app / 'Contents' / 'MacOS' / 'App').write_bytes(b'\xcf\xfa\xed\xfe' + b'binary')
            info = {'CFBundleIdentifier': 'dev.williecubed.app', 'CFBundleShortVersionString': '1.2.3', 'CFBundleVersion': '42', 'SUFeedURL': 'https://example.com/appcast.xml', 'SUPublicEDKey': PUB}
            (app / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(info))
        if command[0] == 'ditto':
            Path(command[-1]).write_bytes(b'zip-final')
        if command[0] == 'hdiutil':
            Path(command[-1]).write_bytes(b'dmg-final')
        if command[:3] == ['xcrun', 'notarytool', 'submit']:
            return json.dumps({'status': 'Accepted' if self.accepted else 'Invalid', 'id': 'request'})
        if command[0].endswith('/sign_update') and '--verify' not in command:
            return base64.b64encode(b'a' * 64).decode()
        if command[0].endswith('/generate_appcast'):
            feed = Path(command[command.index('-o') + 1])
            zipfile = next(Path(command[-1]).glob('*.zip'))
            feed.write_text(f'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><sparkle:version>42</sparkle:version><sparkle:shortVersionString>1.2.3</sparkle:shortVersionString><enclosure url="https://example.com/releases/{zipfile.name}" length="{zipfile.stat().st_size}" sparkle:edSignature="{base64.b64encode(bytes([97])*64).decode()}" /></item></channel></rss>')
        if command[:2] == ['codesign', '-d']:
            return plistlib.dumps({'com.apple.security.get-task-allow': False}).decode()
        return ''


class MacOSTests(unittest.TestCase):
    def setUp(self):
        mocked=patch('studio.tools.verified_sparkle',side_effect=lambda declared=None:Path(declared))
        mocked.start(); self.addCleanup(mocked.stop)

    def test_missing_sparkle_blocks_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, component = fixture(root)
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(macos.MacOSError, 'SPARKLE_PRIVATE_KEY'):
                    macos.prepare(config, component, root / 'out', '1.2.3', 42, 'direct', runner=lambda *a, **k: '')
            self.assertFalse((root / 'out').exists())

    def test_direct_artifacts_notarize_zip_and_dmg_before_signing_final_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, component = fixture(root)
            runner = NativeRunner(root, config)
            with patch.dict(os.environ, {'SPARKLE_PRIVATE_KEY': KEY}):
                paths = macos.prepare(config, component, root / 'out', '1.2.3', 42, 'direct', runner=runner)
            self.assertEqual({p.suffix for p in paths}, {'.zip', '.dmg', '.xml'})
            archives = [c[3] for c in runner.calls if c[:3] == ['xcrun', 'notarytool', 'submit']]
            self.assertEqual([Path(p).suffix for p in archives], ['.zip', '.dmg'])
            archive = next(c for c in runner.calls if c[0] == 'xcodebuild' and 'archive' in c)
            self.assertIn('MARKETING_VERSION=1.2.3', archive)
            self.assertIn('CURRENT_PROJECT_VERSION=42', archive)
            self.assertFalse(any(argument.startswith('CODE_SIGN_ENTITLEMENTS=') for argument in archive))
            sign_index = next(i for i,c in enumerate(runner.calls) if c[0].endswith('/sign_update') and '--verify' not in c)
            self.assertGreater(sign_index, max(i for i,c in enumerate(runner.calls) if c[:3] == ['xcrun', 'stapler', 'staple']))
            self.assertNotIn(KEY, str(runner.calls))
            codesign = [c for c in runner.calls if c[0] == 'codesign' and '--sign' in c and not c[-1].endswith('.dmg')]
            self.assertGreaterEqual(len(codesign), 2)
            self.assertTrue(all('runtime' in c and '--timestamp' in c for c in codesign))

    def test_direct_profiles_select_native_targets_and_export_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config,component=fixture(root)
            component['direct_provisioning_profiles']={'dev.williecubed.app':{'specifier':'Studio App Direct','build_setting':'STUDIO_APP_PROFILE'},'dev.williecubed.app.widget':{'specifier':'Studio Widget Direct','build_setting':'STUDIO_WIDGET_PROFILE'}}
            runner=NativeRunner(root,config)
            macos.archive(config,component,root/'archive','1.2.3',42,runner=runner)
            command=next(c for c in runner.calls if 'archive' in c)
            self.assertIn('STUDIO_APP_PROFILE=Studio App Direct',command)
            self.assertIn('STUDIO_WIDGET_PROFILE=Studio Widget Direct',command)
            options=plistlib.loads((root/'archive/ExportOptions.plist').read_bytes())
            self.assertEqual(options['provisioningProfiles'],{'dev.williecubed.app':'Studio App Direct','dev.williecubed.app.widget':'Studio Widget Direct'})

    def test_resigning_preserves_resolved_identity_before_leaf_signing_changes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config,component=fixture(root)
            app=root/'App.app'; (app/'Contents/MacOS').mkdir(parents=True)
            (app/'Contents/MacOS/App').write_bytes(b'\xcf\xfa\xed\xfebinary')
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'dev.williecubed.app'}))
            (app/'Contents/embedded.provisionprofile').write_bytes(b'profile')
            component['direct_provisioning_profiles']={'dev.williecubed.app':{'specifier':'Studio App Direct','build_setting':'STUDIO_APP_PROFILE'}}
            resolved={'com.apple.security.get-task-allow':False,'com.apple.application-identifier':'TEAM.dev.williecubed.app','com.apple.developer.team-identifier':'TEAM'}
            current=dict(resolved); captured=[]
            def runner(command,cwd):
                nonlocal current
                if command[:2]==['security','cms']:
                    return plistlib.dumps({'Name':'Studio App Direct','TeamIdentifier':['TEAM'],'Entitlements':{'com.apple.application-identifier':'TEAM.dev.williecubed.app'}}).decode()
                if command[:2]==['codesign','-d']:
                    return plistlib.dumps(current).decode()
                if command[0]=='codesign' and '--force' in command:
                    if '--entitlements' in command:
                        current=plistlib.loads(Path(command[command.index('--entitlements')+1]).read_bytes()); captured.append(dict(current))
                    else:
                        current={}
                return ''
            macos.sign(config,component,app,runner=runner)
            self.assertEqual(captured,[resolved])

    def test_profile_identity_mismatch_blocks_before_resigning(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config,component=fixture(root); app=root/'App.app'; (app/'Contents').mkdir(parents=True)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'dev.williecubed.app'}))
            component['direct_provisioning_profiles']={'dev.williecubed.app':{'specifier':'Studio App Direct','build_setting':'STUDIO_APP_PROFILE'}}
            calls=[]
            def runner(command,cwd):
                calls.append(command)
                return plistlib.dumps({'com.apple.security.get-task-allow':False,'com.apple.application-identifier':'OTHER.dev.williecubed.app','com.apple.developer.team-identifier':'OTHER'}).decode()
            with self.assertRaisesRegex(macos.MacOSError,'signing identity'):
                macos.sign(config,component,app,runner=runner)
            self.assertFalse(any('--force' in command for command in calls))

    def test_notary_rejection_stops_before_appcast(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, component = fixture(root)
            runner = NativeRunner(root, config, accepted=False)
            with patch.dict(os.environ, {'SPARKLE_PRIVATE_KEY': KEY}):
                with self.assertRaisesRegex(macos.MacOSError, 'notarization'):
                    macos.prepare(config, component, root / 'out', '1.2.3', 42, 'direct', runner=runner)
            self.assertFalse(any(c[0].endswith('/generate_appcast') for c in runner.calls))

    def test_public_private_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            config, component = fixture(Path(directory))
            with patch.dict(os.environ, {'SPARKLE_PRIVATE_KEY': base64.b64encode(b'x' * 96).decode()}):
                with self.assertRaisesRegex(macos.MacOSError, 'public key'):
                    macos.preflight(config, component, runner=lambda *a, **k: '')

    def test_entitlements_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, component = fixture(root)
            (root / 'app.entitlements').write_bytes(plistlib.dumps({'com.apple.security.get-task-allow': True}))
            with patch.dict(os.environ, {'SPARKLE_PRIVATE_KEY': KEY}):
                with self.assertRaisesRegex(macos.MacOSError, 'get-task-allow'):
                    macos.preflight(config, component, runner=lambda *a, **k: '')

    def test_root_product_facts_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, component = fixture(root)
            facts = component.pop('macos')
            component.update(app_name=facts['app_name'],bundle_id=facts['bundle_id'],entitlements={'direct':facts['entitlements']},nested_binaries={})
            with patch.dict(os.environ, {'SPARKLE_PRIVATE_KEY': KEY}):
                result = macos.preflight(config,component,runner=NativeRunner(root,config))
            self.assertEqual(result['signing'],'passed')

    def test_entitlements_in_signed_app_must_match_declaration(self):
        class WrongEntitlements(NativeRunner):
            def __call__(self,command,cwd,**kwargs):
                if command[:2]==['codesign','-d']:
                    return plistlib.dumps({'com.apple.security.get-task-allow':False}).decode()
                return super().__call__(command,cwd,**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config,component = fixture(root)
            (root / 'app.entitlements').write_bytes(plistlib.dumps({'com.apple.security.get-task-allow':False,'com.apple.security.automation.apple-events':True}))
            with patch.dict(os.environ, {'SPARKLE_PRIVATE_KEY':KEY}):
                with self.assertRaisesRegex(macos.MacOSError,'declared entitlements'):
                    macos.prepare(config,component,root/'out','1.2.3',42,runner=WrongEntitlements(root,config))

    def test_export_architectures_must_match_product_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config,component=fixture(root)
            component['architectures']=['arm64','x86_64']
            with patch.dict(os.environ, {'SPARKLE_PRIVATE_KEY':KEY}):
                with self.assertRaisesRegex(macos.MacOSError,'architectures'):
                    macos.prepare(config,component,root/'out','1.2.3',42,runner=NativeRunner(root,config))
