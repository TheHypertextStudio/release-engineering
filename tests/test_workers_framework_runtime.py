"""Opt-in real framework builds, archive extraction, and local workerd acceptance.

This never deploys to Cloudflare. Provider lifecycle and product authentication
remain separate gates. Install the frozen fixture workspace before opting in.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from studio.candidate import sha256
from studio.ci import pack_tree, unpack
from studio.websites import workers_site_inputs, seed_workers_site_cache


FIXTURES = Path(__file__).parent / 'fixtures/websites'


def request(origin, path, *, cookie=None):
    headers = {'Cookie': cookie} if cookie else {}
    try:
        response = urllib.request.urlopen(urllib.request.Request(origin + path, headers=headers), timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read(2_000_000)


@contextlib.contextmanager
def workerd(build, component, artifact, environment, directory):
    arguments, expected = workers_site_inputs(component, artifact, environment, build, FIXTURES / component['id'])
    if component['id'] == 'next':
        def native_cache(command, **kwargs):
            result = subprocess.run(command, **kwargs, text=True, capture_output=True,
                env={**os.environ, 'WRANGLER_SEND_METRICS': 'false', 'CI': 'true'})
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        seed_workers_site_cache(component, expected, environment, build,
            arguments[arguments.index('--config') + 1],
            [str((FIXTURES / 'node_modules/.bin/wrangler').resolve())],
            runner=native_cache, target='local')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    log_path = directory / f'{component["id"]}-{environment}.log'
    with log_path.open('w+') as log:
        process = subprocess.Popen([
            'pnpm', 'exec', 'wrangler', 'dev',
            *[argument for argument in arguments[1:] if argument != '--autoconfig=false'], '--local',
            '--ip', '127.0.0.1', '--port', str(port), '--inspector-port', '0',
        ], cwd=FIXTURES, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            env={**os.environ, 'WRANGLER_SEND_METRICS': 'false', 'CI': 'true'})
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    request(origin, '/__studio/release')
                    yield origin, expected
                    return
                except (OSError, TimeoutError):
                    time.sleep(.2)
            log.seek(0)
            raise AssertionError('Packaged workerd did not start:\n' + log.read()[-10_000:])
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


@unittest.skipUnless(os.environ.get('STUDIO_FRAMEWORK_WORKERS_TESTS') == '1',
                     'Set STUDIO_FRAMEWORK_WORKERS_TESTS=1 for real Astro/Next workerd acceptance')
class FrameworkWorkersRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(['pnpm', 'build'], cwd=FIXTURES, text=True, capture_output=True,
                                timeout=600, env={**os.environ, 'WRANGLER_SEND_METRICS': 'false',
                                                 'NEXT_TELEMETRY_DISABLED': '1', 'CI': 'true'})
        if result.returncode:
            raise AssertionError('Native fixture build failed:\n' + (result.stdout + result.stderr)[-16_000:])

    def test_both_framework_archives_keep_identity_html_modules_assets_and_404s(self):
        for framework in ('astro', 'next'):
            with self.subTest(framework=framework), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / 'web.zip'
                pack_tree(FIXTURES / framework / '.artifact', archive)
                digest = sha256(archive)
                artifact = {'source_sha': 'a' * 40, 'sha256': digest, 'candidate_id': '123-1'}
                component = {'id': framework, 'path': '.', 'deploy': {
                    'config': 'wrangler.json', 'entrypoint': 'worker.js',
                    'assets': 'assets' if framework == 'astro' else '.open-next/assets',
                    'environments': {'staging': 'staging', 'production': 'production'},
                    **({'opennext_cache': {'directory': '.open-next/cache'}} if framework == 'next' else {}),
                }}
                # Two independently extracted shipping roots use the same zip bytes.
                # Promotion never invokes a framework build or uses node_modules.
                for environment in ('staging', 'production'):
                    build = root / environment
                    unpack(archive, build)
                    reviewed = {path.relative_to(build): sha256(path) for path in build.rglob('*') if path.is_file()}
                    self.assertFalse(any(path.name == 'node_modules' for path in build.rglob('*')))
                    with workerd(build, component, artifact, environment, root) as (origin, expected):
                        status, headers, body = request(origin, '/__studio/release')
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(body), expected)
                        self.assertIn('no-store', headers['Cache-Control'])
                        status, _, body = request(origin, '/')
                        self.assertEqual(status, 200)
                        self.assertIn(f'{framework} shipping fixture'.encode(), body)
                        self.assertEqual(request(origin, '/no-such-route')[0], 404)
                        self.assertEqual(request(origin, '/fixture.txt')[2], b'reviewed static asset\n')
                        if framework == 'astro':
                            self.assertNotIn(b'<script', body)
                            self.assertIn(b'astro fixture missing', request(origin, '/no-such-route')[2])
                        else:
                            modules = list((build / '.open-next/assets/_next/static').rglob('*.js'))
                            self.assertGreater(len(modules), 0)
                            module = modules[0]
                            url = '/' + module.relative_to(build / '.open-next/assets').as_posix()
                            delivered = request(origin, url)
                            self.assertEqual(delivered[0], 200)
                            self.assertEqual(hashlib.sha256(delivered[2]).hexdigest(), sha256(module))
                    for path, expected_digest in reviewed.items():
                        self.assertEqual(sha256(build / path), expected_digest, str(path))
                    self.assertEqual(sha256(archive), digest)

    def test_next_server_rendering_keeps_viewers_and_forwarded_request_state_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'next.zip'
            pack_tree(FIXTURES / 'next/.artifact', archive)
            build = root / 'shipping'
            unpack(archive, build)
            component = {'id': 'next', 'path': '.', 'deploy': {
                'config': 'wrangler.json', 'entrypoint': 'worker.js', 'assets': '.open-next/assets',
                'opennext_cache': {'directory': '.open-next/cache'},
                'environments': {'staging': 'staging', 'production': 'production'},
            }}
            artifact = {'source_sha': 'b' * 40, 'sha256': sha256(archive), 'candidate_id': '124-1'}
            with workerd(build, component, artifact, 'staging', root) as (origin, _):
                for viewer in ('alice', 'bob', None, 'alice'):
                    status, headers, body = request(origin, '/viewer', cookie=f'fixture_viewer={viewer}' if viewer else None)
                    self.assertEqual(status, 200)
                    self.assertIn(f'Viewer: {viewer or "anonymous"}'.encode(), body)
                    for other in {'alice', 'bob', 'anonymous'} - {viewer or 'anonymous'}:
                        self.assertNotIn(f'Viewer: {other}'.encode(), body)
                    self.assertRegex(headers['Cache-Control'], r'private|no-store')
                status, _, body = request(origin, '/api/echo?value=one%20two', cookie='fixture_viewer=bob')
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {'viewer': 'bob', 'value': 'one two'})
