import base64
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from studio.macos import _appcast
from studio.config import Config

class NativeSparkleTests(unittest.TestCase):
    def test_official_sparkle_signs_and_verifies_the_final_archive(self):
        tools=Path(__file__).resolve().parents[1]/'.build/artifacts/sparkle/Sparkle/bin'
        self.assertTrue((tools/'sign_update').is_file(),'Run swift package resolve before native adapter tests')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); app=root/'Native.app'; (app/'Contents/MacOS').mkdir(parents=True)
            shutil.copyfile('/usr/bin/true',app/'Contents/MacOS/Native')
            (app/'Contents/MacOS/Native').chmod(0o755)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'studio.hypertext.native-fixture','CFBundleName':'Native','CFBundleExecutable':'Native','CFBundleVersion':'12001','CFBundleShortVersionString':'1.2.3','LSMinimumSystemVersion':'13.0','CFBundlePackageType':'APPL'}))
            key=root/'key.pem'
            subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(key)],check=True,capture_output=True)
            private=subprocess.run(['openssl','pkey','-in',str(key),'-outform','DER'],check=True,capture_output=True).stdout[-32:]
            public=subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-outform','DER'],check=True,capture_output=True).stdout[-32:]
            info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
            info.update(SUPublicEDKey=base64.b64encode(public).decode(),SUFeedURL='https://example.com/native/appcast.xml')
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
            subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True,capture_output=True)
            archive=root/'Native.zip'
            subprocess.run(['ditto','-c','-k','--keepParent',str(app),str(archive)],check=True,capture_output=True)
            config=Config(root,{'release':{'macos':{'sparkle':{'feed_url':'https://example.com/native/appcast.xml','download_url':'https://example.com/native/releases/12-1/','public_key':base64.b64encode(public).decode(),'tools_path':str(tools)}}}})
            from unittest.mock import patch
            with patch.dict(os.environ,{'SPARKLE_PRIVATE_KEY':base64.b64encode(private).decode()}):
                def execute(argv,cwd,**kwargs):
                    result=subprocess.run(argv,cwd=cwd,capture_output=True,text=True,**kwargs)
                    if result.returncode:
                        self.fail((result.stdout+result.stderr).replace(os.environ['SPARKLE_PRIVATE_KEY'],'REDACTED'))
                    return result.stdout
                try:
                    feed=_appcast(config,{'id':'app'},archive,'1.2.3',12001,runner=execute)
                except ValueError:
                    self.fail((root/'appcast.xml').read_text())
            self.assertIn('Native.zip',feed.read_text())
            self.assertIn('12001',feed.read_text())
