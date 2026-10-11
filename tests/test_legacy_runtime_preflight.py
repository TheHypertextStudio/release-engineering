"""Run dispatcher preflight and retained v0.1.7 modules in an isolated interpreter."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import venv

from studio.candidate import write_candidate


REPO = Path(__file__).resolve().parents[1]
LEGACY = '0e6c0313904316e893a8e606a2557f016571412e'
FIXTURE = REPO / 'tests/fixtures/legacy-runtime'
DECLARATION = '''schema: 1
product: example
repository: studio/example
owner_repository: studio/example
toolchain:
  node: "24.20.0"
components:
  - id: site
    kind: static-site
    path: .
    checks: []
    build:
      output: .artifact
release:
  channels: [direct]
'''


class LegacyRuntimePreflightTests(unittest.TestCase):
    def run_native(self, command, *, cwd=None, env=None, input=None):
        return subprocess.run(command, cwd=cwd, env=env, input=input,
                              text=True, capture_output=True, check=True, timeout=60).stdout.strip()

    def make_fixture(self, root):
        interpreter = root / 'python'
        venv.EnvBuilder(system_site_packages=True, with_pip=False).create(interpreter)
        python = interpreter / 'bin/python'
        site = Path(self.run_native([str(python), '-I', '-c', 'import sysconfig;print(sysconfig.get_path("purelib"))']))
        shutil.copytree(REPO / 'studio', site / 'studio', ignore=shutil.ignore_patterns('__pycache__'))
        legacy_sha = LEGACY
        engine = root / 'engine'
        engine.mkdir()
        self.run_native(['git', 'init'], cwd=engine)
        commit = (FIXTURE / 'v0.1.7.commit').read_text()
        self.assertEqual(self.run_native(['git', 'hash-object', '-w', '-t', 'commit', '--stdin'],
                                         cwd=engine, input=commit), legacy_sha)
        self.run_native(['git', 'update-ref', 'HEAD', legacy_sha], cwd=engine)
        source = root / 'source'
        source.mkdir()
        self.run_native(['git', 'init', '-b', 'main'], cwd=source)
        (source / 'studio.yaml').write_text(DECLARATION)
        (source / 'studio.lock.json').write_text(json.dumps({'revision': legacy_sha}) + '\n')
        entries = []
        for name in ('studio.lock.json', 'studio.yaml'):
            digest = self.run_native(['git', 'hash-object', '-w', name], cwd=source)
            entries.append(f'100644 blob {digest}\t{name}\n')
        tree = self.run_native(['git', 'mktree'], cwd=source, input=''.join(entries))
        identity = {**os.environ, 'GIT_AUTHOR_NAME': 'CI Test', 'GIT_COMMITTER_NAME': 'CI Test',
                    'GIT_AUTHOR_EMAIL': 'ci@example.test', 'GIT_COMMITTER_EMAIL': 'ci@example.test'}
        message = root / 'message'
        message.write_text('Fixture source provenance\n')
        source_sha = self.run_native(['git', 'commit-tree', tree, '-F', str(message)], cwd=source, env=identity)
        self.run_native(['git', 'update-ref', 'refs/heads/main', source_sha], cwd=source)
        # A product package named studio must never override the installed engine.
        (source / 'studio').mkdir()
        (source / 'studio/__init__.py').write_text('raise RuntimeError("source package shadowed runtime")\n')
        candidate = root / 'candidate'
        candidate.mkdir()
        (candidate / 'studio.yaml').write_text(DECLARATION)
        payload = b'retained worker archive'
        (candidate / 'site.zip').write_bytes(payload)
        data = {
            'schema': 1, 'id': '12-1', 'product': 'example', 'repository': 'studio/example',
            'source_sha': source_sha, 'tooling_revision': legacy_sha,
            'workflow_run_id': 12, 'workflow_run_attempt': 1, 'version': '1.0.1', 'build_number': 12,
            'toolchains': {'node': '24.20.0'},
            'artifacts': [{'component': 'site', 'channel': 'direct', 'path': 'site.zip',
                           'sha256': hashlib.sha256(payload).hexdigest(), 'size': len(payload)}],
            'checks': {'build': 'passed'}, 'prerequisites': {}, 'compatibility': {},
        }
        manifest = write_candidate(candidate / 'candidate.json', data)
        output = root / 'output'
        env = {**os.environ, 'STUDIO_ENGINE_ROOT': str(engine), 'GITHUB_OUTPUT': str(output)}
        return python, site, source, candidate, output, env, manifest

    def test_default_root_legacy_candidate_passes_before_restored_runtime_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python, site, source, candidate, output, env, _ = self.make_fixture(root)
            self.run_native([str(python), '-I', '-m', 'studio.product_root', '--checkout', str(source), '.'], cwd=source, env=env)
            self.run_native([str(python), '-I', '-m', 'studio.ci_setup', '--candidate', str(candidate),
                             '--source', str(source), '--validate-only'], cwd=source, env=env)
            self.assertIn('node=24.20.0\n', output.read_text())
            shutil.rmtree(site / 'studio')
            archive = (FIXTURE / 'v0.1.7.tar.gz').read_bytes()
            self.assertEqual(hashlib.sha256(archive).hexdigest(),
                             '05bc76e372eab2e5cba895a6b34906b5b02014783becafda73afcbc88a785223')
            with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as retained:
                for member in retained.getmembers():
                    if member.isfile():
                        target = site / member.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(retained.extractfile(member).read())
            self.assertFalse((site / 'studio/product_root.py').exists())
            # Only old supported flags are used once the legacy runtime is active.
            self.run_native([str(python), '-I', '-m', 'studio.ci_setup', '--candidate', str(candidate),
                             '--source', str(source)], cwd=source, env=env)
            help_text = self.run_native([str(python), '-I', '-m', 'studio.ci', '--help'], cwd=source, env=env)
            self.assertIn('--manifest-sha256', help_text)

    def test_legacy_candidate_cannot_claim_a_nested_product_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python, _, source, candidate, output, env, _ = self.make_fixture(root)
            nested = source / 'landing'
            nested.mkdir()
            for name in ('studio.yaml', 'studio.lock.json'):
                shutil.copyfile(source / name, nested / name)
            result = subprocess.run([str(python), '-I', '-m', 'studio.ci_setup', '--candidate', str(candidate),
                                     '--source', str(nested), '--validate-only'], cwd=source, env=env,
                                    text=True, capture_output=True, timeout=60)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('product root', result.stderr)
            self.assertFalse(output.exists())
