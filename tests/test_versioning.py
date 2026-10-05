import unittest
from studio.versioning import next_version, build_number, version_from_git
from pathlib import Path
import subprocess
import tempfile

class VersioningTests(unittest.TestCase):
    def test_conventional_commit_bumps(self):
        self.assertEqual(next_version('1.2.3', ['fix(ui): Repair selection']), '1.2.4')
        self.assertEqual(next_version('1.2.3', ['feat(ui): Add export']), '1.3.0')
        self.assertEqual(next_version('1.2.3', ['feat(ui)!: Replace export']), '2.0.0')
        self.assertEqual(next_version('1.2.3', ['chore: Change\n\nBREAKING CHANGE: New API']), '2.0.0')

    def test_override_must_advance_version(self):
        self.assertEqual(next_version('1.2.3', [], '1.5.0'), '1.5.0')
        with self.assertRaises(ValueError):
            next_version('1.2.3', [], '1.2.3')

    def test_maintenance_push_creates_patch_candidate(self):
        self.assertEqual(next_version('1.2.3', ['docs: Explain API']), '1.2.4')

    def test_numeric_build_sequence(self):
        self.assertEqual(build_number(100, 23), 123)
        for args in ((0, 0), (-1, 2), (0, 'foo')):
            with self.assertRaises(ValueError):
                build_number(*args)

    def test_non_integral_build_sequences_are_invalid(self):
        with self.assertRaises(ValueError):
            build_number(0, 1.5)

    def test_version_uses_recorded_production_instead_of_an_older_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.run(['git',*args],cwd=directory,check=True,capture_output=True,text=True).stdout.strip()
            git('init'); git('config','user.email','test@example.com'); git('config','user.name','Test')
            path=Path(directory)/'app'; path.write_text('first')
            git('add','app')
            message=Path(directory)/'message'; message.write_text('feat: Add app\n')
            git('commit','--file',str(message)); git('tag','v0.1.0')
            path.write_text('second'); git('add','app'); message.write_text('feat: Add another feature\n'); git('commit','--file',str(message))
            production=git('rev-parse','HEAD')
            path.write_text('third'); git('add','app'); message.write_text('fix: Correct behavior\n'); git('commit','--file',str(message))
            self.assertEqual(version_from_git(directory,previous={'version':'0.2.0','source_sha':production}),'0.2.1')
