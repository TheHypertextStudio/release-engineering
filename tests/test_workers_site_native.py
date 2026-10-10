"""Opt-in pinned Wrangler acceptance; dry runs never access a provider account."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from studio.websites import workers_site_inputs


@unittest.skipUnless(os.environ.get('STUDIO_NATIVE_WORKERS_TESTS') == '1',
                     'Set STUDIO_NATIVE_WORKERS_TESTS=1 to run pinned Wrangler packaging')
class NativeWorkersSiteTests(unittest.TestCase):
    def test_relative_module_root_packages_reviewed_chunks_without_rebuilding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            build = root / 'build'
            bindings = root / 'bindings'
            build.mkdir()
            bindings.mkdir()
            (build / 'assets').mkdir()
            (build / 'assets/index.html').write_text('<h1>reviewed asset</h1>')
            worker = "import {message} from './chunk.js'; export default {fetch(){return new Response(message)}};"
            chunk = "export const message = 'reviewed chunk';"
            (build / 'worker.js').write_text(worker)
            (build / 'chunk.js').write_text(chunk)
            (root / 'outside.js').write_text("export const secret = 'outside artifact';")
            (bindings / 'wrangler.json').write_text(json.dumps({
                'account_id': 'a' * 32, 'compatibility_date': '2026-10-10',
                'base_dir': '.', 'find_additional_modules': True,
                'rules': [{'type': 'ESModule', 'globs': ['**/*.js']}],
                'env': {'staging': {'name': 'fixture-staging'}, 'production': {'name': 'fixture-production'}},
            }))
            component = {'path': '.', 'deploy': {
                'config': 'wrangler.json', 'entrypoint': 'worker.js', 'assets': 'assets',
                'environments': {'staging': 'staging', 'production': 'production'},
            }}
            args, _ = workers_site_inputs(component, {
                'source_sha': 'a' * 40, 'sha256': 'b' * 64, 'candidate_id': '123-1',
            }, 'staging', build, bindings)
            result = subprocess.run(['pnpm', 'dlx', 'wrangler@4.126.0', *args, '--dry-run',
                                     '--outdir', str(root / 'output')],
                                    cwd=build, text=True, capture_output=True, timeout=180,
                                    env={**os.environ, 'WRANGLER_SEND_METRICS': 'false'})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((root / 'output/chunk.js').read_text(), chunk)
            self.assertEqual((root / 'output/worker.js').read_text(), worker)
            self.assertFalse((root / 'output/outside.js').exists())
