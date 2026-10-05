import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.pins import update

class PinConsistencyTests(unittest.TestCase):
    def test_pin_update_changes_quoted_xcode_and_yaml_revisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); project=root/'App.xcodeproj'; project.mkdir()
            pbx=project/'project.pbxproj'
            pbx.write_text('"repositoryURL" = "https://github.com/TheHypertextStudio/release-engineering.git";\n"requirement" = {\n"kind" = "revision";\n"revision" = "'+'0'*40+'";\n};')
            resolved=project/'project.xcworkspace/xcshareddata/swiftpm/Package.resolved'
            resolved.parent.mkdir(parents=True)
            resolved.write_text(json.dumps({'version':3,'pins':[{'identity':'release-engineering','location':'https://github.com/TheHypertextStudio/release-engineering.git','state':{'revision':'0'*40}}]}))
            workflows=root/'.github/workflows'; workflows.mkdir(parents=True)
            (workflows/'candidate.yml').write_text("tooling_revision: '"+'0'*40+"'\n")
            archive=b'archive'; import hashlib
            digest=hashlib.sha256(archive).hexdigest()
            release={'tag_name':'v0.1.0','draft':False,'prerelease':False,'assets':[{'name':'studio.pyz','browser_download_url':'https://example/archive'},{'name':'SHA256SUMS','browser_download_url':'https://example/digest'}]}
            class Response:
                def __init__(self,data): self.data=data
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def read(self,*args): return self.data
            with patch('studio.pins.api',side_effect=[release,{'object':{'type':'commit','sha':'a'*40}}]),patch('studio.pins.urlopen',side_effect=[Response(archive),Response((digest+'  studio.pyz').encode())]):
                update(root)
            self.assertNotIn('0'*40,pbx.read_text())
            self.assertIn('a'*40,pbx.read_text())
            self.assertEqual(json.loads(resolved.read_text())['pins'][0]['state'],{'revision':'a'*40})
