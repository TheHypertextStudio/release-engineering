import json
from pathlib import Path
import tempfile
import unittest
from studio.conformance import check
from studio.config import Config

class ConformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=Config(self.root,{'product':'app','repository':'Studio/app','components':[],'release':{}})
        (self.root/'studio.lock.json').write_text(json.dumps({'repository':'TheHypertextStudio/release-engineering','revision':'a'*40,'version':'v0.1.0','sha256':'b'*64}))
        (self.root/'run').write_text("version='v0.1.0'\nexpected='"+'b'*64+"'\n")
    def test_rejects_product_signing_and_moving_workflow_refs(self):
        workflows=self.root/'.github/workflows'; workflows.mkdir(parents=True)
        (workflows/'candidate.yml').write_text('jobs:\n  build:\n    uses: TheHypertextStudio/release-engineering/.github/workflows/candidate.yml@main\n')
        with self.assertRaises(ValueError): check(self.config)
        (workflows/'candidate.yml').write_text('jobs:\n  build:\n    uses: TheHypertextStudio/release-engineering/.github/workflows/candidate.yml@'+'a'*40+'\n')
        check(self.config)
        (self.root/'release.sh').write_text('codesign --force app.app')
        with self.assertRaises(ValueError): check(self.config)
    def test_legacy_exception_requires_identical_disabled_file(self):
        workflows=self.root/'.github/workflows'; workflows.mkdir(parents=True)
        legacy=workflows/'release.yml'; legacy.write_text('on: [push]\njobs: {}\n')
        self.config.data['release']['legacy_files']=['.github/workflows/release.yml']
        with self.assertRaises(ValueError): check(self.config)

    def test_placeholder_pins_are_not_releases(self):
        path=self.root/'studio.lock.json'
        original=json.loads(path.read_text())
        for key,length in [('revision',40),('sha256',64)]:
            path.write_text(json.dumps({**original,key:'0'*length}))
            with self.assertRaises(ValueError): check(self.config)

    def test_sdk_and_workflow_input_must_match_lock(self):
        workflow=self.root/'.github/workflows/candidate.yml'
        workflow.parent.mkdir(parents=True)
        workflow.write_text("tooling_revision: '"+'c'*40+"'\n")
        with self.assertRaises(ValueError): check(self.config)

        workflow.unlink()
        project=self.root/'App.xcodeproj/project.pbxproj'
        project.parent.mkdir()
        project.write_text('repositoryURL = "https://github.com/TheHypertextStudio/release-engineering.git"; requirement = {kind = revision; revision = "'+'c'*40+'";};')
        with self.assertRaises(ValueError): check(self.config)

    def test_declared_apple_team_rejects_a_personal_debug_override(self):
        project=self.root/'App.xcodeproj/project.pbxproj'
        project.parent.mkdir()
        self.config.data['components']=[{'id':'app','kind':'macos','path':'.','project':'App.xcodeproj'}]
        self.config.data['release']['macos']={'team_id':'T95VDD3A4W'}
        project.write_text('DEVELOPMENT_TEAM = T95VDD3A4W;\n"DEVELOPMENT_TEAM" = "39AB9DY3K8";\n')
        with self.assertRaisesRegex(ValueError,'team'): check(self.config)
        project.write_text('DEVELOPMENT_TEAM = T95VDD3A4W;\n')
        self.assertEqual(check(self.config)['status'],'passed')

    def test_declared_apple_team_rejects_unbound_or_empty_project_team(self):
        project=self.root/'App.xcodeproj/project.pbxproj'
        project.parent.mkdir()
        self.config.data['components']=[{'id':'app','kind':'macos','path':'.','project':'App.xcodeproj'}]
        self.config.data['release']['macos']={'team_id':'T95VDD3A4W'}
        for content in ('CODE_SIGN_STYLE = Automatic;\n','DEVELOPMENT_TEAM = "";\n'):
            with self.subTest(content=content):
                project.write_text(content)
                with self.assertRaisesRegex(ValueError,'team'): check(self.config)
