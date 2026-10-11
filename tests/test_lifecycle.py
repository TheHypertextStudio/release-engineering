import tempfile
import unittest
from pathlib import Path
from studio.config import load_config
from studio.lifecycle import native_command, run
from test_config import CONFIG

MULTI_COMPONENT_CONFIG = '''schema: 1
product: example
repository: studio/example
owner_repository: studio/release-engineering
toolchain:
  python: ">=3.11"
components:
  - id: web-config
    kind: pnpm
    path: .
    checks: []
    dev: [python3, -c, "print('web-config')"]
    build:
      script: build
  - id: web-tokens
    kind: pnpm
    path: packages/tokens
    checks: []
    dev: [python3, -c, "print('web-tokens')"]
    build:
      script: build
development:
  component: web-config
release:
  channels: [direct]
'''

class LifecycleTests(unittest.TestCase):
    def test_validation_build_does_not_require_a_signing_credential(self):
        command=native_command({'kind':'macos','project':'App.xcodeproj','scheme':'App','build':{'configuration':'Release'}},'build')
        self.assertIn('CODE_SIGNING_ALLOWED=NO',command)
        self.assertIn('CODE_SIGNING_REQUIRED=NO',command)
        self.assertIn('Release',command)

    def test_native_build_commands_bound_parallelism(self):
        self.assertEqual(native_command({'kind': 'swiftpm', 'build': {'configuration': 'release'}}, 'build'), ['swift', 'build', '-c', 'release', '--jobs', '2'])
        self.assertEqual(native_command({'kind': 'pnpm', 'build': {'script': 'build'}}, 'build'), ['pnpm', 'run', 'build'])
        self.assertIn('--max-workers=2', native_command({'kind': 'gradle', 'build': {'tasks': ['assemble']}}, 'build'))

    def test_check_dispatches_argv_at_component_root_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'studio.yaml'
            path.write_text(CONFIG)
            config = load_config(path)
            calls = []
            def execute(argv, cwd):
                calls.append((argv, cwd))
                return 0
            run(config, 'check', runner=execute)
            self.assertEqual(calls[0][1], Path(root).resolve())
            self.assertEqual(calls[0][0][0], 'python3')
            with self.assertRaises(RuntimeError):
                run(config, 'check', runner=lambda argv, cwd: 1)

    def _load_multi_component_config(self, root, declaration=MULTI_COMPONENT_CONFIG):
        (root / 'packages' / 'tokens').mkdir(parents=True)
        path = root / 'studio.yaml'
        path.write_text(declaration)
        return load_config(path)

    def test_dev_uses_declared_development_component_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._load_multi_component_config(root)
            calls = []
            run(config, 'dev', runner=lambda argv, cwd: calls.append((argv, cwd)) or 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], ['python3', '-c', "print('web-config')"])
            self.assertEqual(calls[0][1], root.resolve())

    def test_explicit_component_overrides_development_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._load_multi_component_config(root)
            calls = []
            run(config, 'dev', component='web-tokens', runner=lambda argv, cwd: calls.append((argv, cwd)) or 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], ['python3', '-c', "print('web-tokens')"])
            self.assertEqual(calls[0][1], (root / 'packages' / 'tokens').resolve())

    def test_dev_without_selector_preserves_all_component_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            declaration = MULTI_COMPONENT_CONFIG.replace('development:\n  component: web-config\n', '')
            config = self._load_multi_component_config(root, declaration)
            calls = []
            run(config, 'dev', runner=lambda argv, cwd: calls.append((argv, cwd)) or 0)
            self.assertEqual(len(calls), 2)

    def test_build_still_runs_all_components_with_development_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._load_multi_component_config(root)
            calls = []
            run(config, 'build', runner=lambda argv, cwd: calls.append((argv, cwd)) or 0)
            self.assertEqual(len(calls), 2)
            self.assertEqual([call[0] for call in calls], [['pnpm', 'run', 'build'], ['pnpm', 'run', 'build']])

    def test_provision_requires_environment(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'studio.yaml'
            path.write_text(CONFIG)
            with self.assertRaises(ValueError):
                run(load_config(path), 'provision')

    def test_provision_stops_when_terraform_init_returns_failure(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            infrastructure = root / 'infrastructure'
            infrastructure.mkdir()
            (infrastructure / 'production.tfvars').write_text('')
            path = root / 'studio.yaml'
            path.write_text(CONFIG.replace('release:', 'infrastructure:\n  path: infrastructure\n  variable_files:\n    production: production.tfvars\nrelease:'))
            calls = []

            def failed_init(argv, cwd):
                calls.append(argv)
                return 1

            with self.assertRaises(RuntimeError):
                run(load_config(path), 'provision', env='production', runner=failed_init)
            self.assertEqual(calls, [['terraform', 'init', '-input=false']])

    def test_native_check_runs_real_process_in_component_directory(self):
        import sys
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'studio.yaml'
            declaration = CONFIG.replace('python3, -c, "print(\'checked\')"', sys.executable + ', -c, "from pathlib import Path; Path(\'checked.txt\').write_text(\'done\')"')
            path.write_text(declaration)
            run(load_config(path), 'check')
            self.assertEqual((Path(root) / 'checked.txt').read_text(), 'done')

    def test_cli_promotion_dispatches_immutable_candidate_and_resumes(self):
        import hashlib
        import io
        import json
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from studio.candidate import write_candidate
        from studio.__main__ import main
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'studio.yaml').write_text(CONFIG.replace('owner_repository: studio/release-engineering', 'owner_repository: studio/example'))
            (root / 'app.zip').write_bytes(b'app')
            candidate = write_candidate(root / 'candidate.json', dict(schema=1,id='candidate-1',product='example',repository='studio/example',source_sha='a'*40,tooling_revision='b'*40,workflow_run_id=12,workflow_run_attempt=1,version='1.0.1',build_number=12,artifacts=[dict(component='app',channel='direct',path='app.zip',sha256=hashlib.sha256(b'app').hexdigest(),size=3)],checks={'app':'passed'},prerequisites={'signed':'passed'},compatibility={}))
            (root / 'review.json').write_text(json.dumps({'schema':1,'manifest_sha256':candidate.digest,'reviewer':'willie','checks':[{'name':'installation','status':'passed','url':'https://example.com/evidence'}]}))
            args = ['--root', str(root), 'release', 'promote', '--candidate', 'candidate.json', '--evidence', 'review.json']
            with patch('studio.__main__.subprocess.run') as dispatch, redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 0)
                self.assertEqual(dispatch.call_count, 2)
                command = dispatch.call_args.args[0]
                self.assertIn('candidate_id=candidate-1', command)
                self.assertIn('manifest_sha256=' + candidate.digest, command)
                self.assertNotIn('build', command)
