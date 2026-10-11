"""Retained OpenNext bytes seed only the reviewed environment's R2 bucket."""
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from studio.candidate import PromotionJournal
from studio.providers import deploy_site
from studio.websites import workers_site_inputs


class OpenNextCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.build = self.root / 'build'
        self.bindings = self.root / 'bindings'
        self.build.mkdir(); self.bindings.mkdir()
        (self.build / 'worker.js').write_text('export default {}')
        (self.build / 'assets').mkdir()
        (self.build / 'assets/index.html').write_text('reviewed asset')
        (self.build / 'assets/BUILD_ID').write_text('build-1')
        self.cache = self.build / '.open-next/cache'
        (self.cache / 'build-1/nested').mkdir(parents=True)
        (self.cache / 'build-1/nested/page.cache').write_text('{"html":"reviewed"}')
        (self.cache / '__fetch/build-1').mkdir(parents=True)
        (self.cache / '__fetch/build-1/request').write_text('{"value":"reviewed fetch"}')
        self.config = {'account_id': 'a' * 32, 'compatibility_date': '2026-10-10', 'env': {
            env: {'name': f'site-{env}', 'images': {'binding': 'IMAGES'},
                  'r2_buckets': [{'binding': 'NEXT_INC_CACHE_R2_BUCKET', 'bucket_name': f'cache-{env}'}]}
            for env in ('staging', 'production')}}
        self.component = {'id': 'web', 'path': '.', 'deploy': {
            'provider': 'workers', 'config': 'wrangler.json', 'entrypoint': 'worker.js', 'assets': 'assets',
            'opennext_cache': {'directory': '.open-next/cache'},
            'environments': {'staging': 'staging', 'production': 'production'},
            'health_urls': {env: f'https://{env}.example.com/__studio/release' for env in ('staging','production')}}}
        self.artifact = {'source_sha': 'a' * 40, 'sha256': 'b' * 64, 'candidate_id': '123-1'}
        self.calls = []
        self.objects = {}
        self.journal = PromotionJournal(self.root / 'journal.json', 'c' * 64)
        self.write_config()

    def write_config(self):
        (self.bindings / 'wrangler.json').write_text(json.dumps(self.config))

    def runner(self, command, *, cwd=None, capture=False):
        self.calls.append(command)
        if command[3:6] == ['r2', 'object', 'put']:
            self.objects[command[6]] = Path(command[command.index('--file')+1]).read_bytes()
        elif command[3:6] == ['r2', 'object', 'get']:
            Path(command[command.index('--file')+1]).write_bytes(self.objects[command[6]])

    def deploy(self, environment='staging', runner=None, authorize=None):
        with patch('studio.providers.probe'):
            return deploy_site(self.component, self.artifact, environment, self.build,
                               {'wrangler': '4.148.0'}, bindings_root=self.bindings,
                               runner=runner or self.runner, journal=self.journal, authorize=authorize)

    def test_seeds_raw_reviewed_files_with_native_keys_before_deploy_and_resumes(self):
        self.deploy()
        expected = 'cache-staging/incremental-cache/build-1/' + hashlib.sha256(b'/nested/page').hexdigest() + '.cache'
        self.assertEqual(self.objects[expected], (self.cache / 'build-1/nested/page.cache').read_bytes())
        fetch = 'cache-staging/incremental-cache/build-1/' + hashlib.sha256(b'/request').hexdigest() + '.fetch'
        self.assertEqual(self.objects[fetch], (self.cache / '__fetch/build-1/request').read_bytes())
        self.assertTrue(all('--remote' in command and '--env-file' in command for command in self.calls[:-1]))
        self.assertEqual(self.calls[-1][3], 'deploy')
        self.journal = PromotionJournal(self.journal.path, 'c' * 64)
        self.calls.clear()
        self.deploy()
        self.assertEqual(len(self.calls), 1)
        self.deploy('production')
        self.assertEqual(len([key for key in self.objects if key.startswith('cache-production/')]), 2)

    def test_partial_seed_retries_and_persists_completed_objects_before_deployment(self):
        def fail_second(command, **kwargs):
            if command[3:6] == ['r2', 'object', 'put'] and '.cache' in command[6]:
                raise subprocess.CalledProcessError(1, command)
            return self.runner(command, **kwargs)
        with self.assertRaises(subprocess.CalledProcessError):
            self.deploy(runner=fail_second)
        record = json.loads(self.journal.path.read_text())
        self.assertTrue(any(row['state'] == 'completed' for row in record['operations'].values()))
        self.assertTrue(any(row['state'] == 'failed' for row in record['operations'].values()))
        self.assertFalse(any(command[3] == 'deploy' for command in self.calls))
        self.journal = PromotionJournal(self.journal.path, 'c' * 64)
        self.calls.clear()
        self.deploy()
        self.assertEqual(len([command for command in self.calls if command[3:6] == ['r2','object','put']]), 1)

    def test_wrong_readback_never_deploys(self):
        def corrupt(command, **kwargs):
            result = self.runner(command, **kwargs)
            if command[3:6] == ['r2','object','get']:
                Path(command[command.index('--file')+1]).write_bytes(b'wrong bytes')
            return result
        with self.assertRaisesRegex(ValueError, 'digest'):
            self.deploy(runner=corrupt)
        self.assertFalse(any(command[3] == 'deploy' for command in self.calls))

    def test_rejects_unreviewed_cache_and_binding_shapes_before_any_write(self):
        for change in ('escape','shared-preview','missing-binding','invalid-file','missing-declaration','inherited-images','invalid-images','wrong-build','invalid-jurisdiction'):
            with self.subTest(change=change):
                saved = json.loads(json.dumps(self.config)); deploy = dict(self.component['deploy'])
                bad = self.cache / 'invalid.txt'
                if change == 'escape': self.component['deploy']['opennext_cache'] = {'directory': '../outside'}
                if change == 'shared-preview': self.config['env']['staging']['r2_buckets'][0]['preview_bucket_name'] = 'cache-production'
                if change == 'missing-binding': self.config['env']['production']['r2_buckets'] = []
                if change == 'invalid-file': bad.write_text('invalid')
                if change == 'invalid-jurisdiction': self.config['env']['staging']['r2_buckets'][0]['jurisdiction'] = '../outside'
                if change == 'wrong-build': (self.build / 'assets/BUILD_ID').write_text('other-build')
                if change == 'missing-declaration': del self.component['deploy']['opennext_cache']
                if change == 'inherited-images': self.config['images'] = self.config['env']['production'].pop('images')
                if change == 'invalid-images': self.config['env']['staging']['images']['path'] = '/outside'
                self.write_config()
                with self.assertRaises(ValueError): self.deploy()
                self.assertEqual(self.calls, [])
                self.config = saved; self.component['deploy'] = deploy
                bad.unlink(missing_ok=True)
                (self.build / 'assets/BUILD_ID').write_text('build-1')

    def test_changed_identity_cannot_resume_prior_object_receipts(self):
        self.deploy()
        self.config['env']['staging']['r2_buckets'][0]['bucket_name'] = 'different-staging-cache'
        self.write_config()
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.deploy()
        self.assertEqual(self.calls, [])

    def test_native_prefix_and_jurisdiction_pass_to_selected_bucket_unchanged(self):
        scope = self.config['env']['staging']
        scope['vars'] = {'NEXT_INC_CACHE_R2_PREFIX': 'reviewed/cache'}
        scope['r2_buckets'][0]['jurisdiction'] = 'eu'
        self.write_config()
        self.deploy()
        self.assertTrue(all(key.startswith('cache-staging/reviewed/cache/') for key in self.objects))
        self.assertTrue(all(command[command.index('--jurisdiction')+1] == 'eu' for command in self.calls[:-1]))

    def test_ambiguous_put_retries_identical_bytes_and_finishes(self):
        ambiguous = True
        def fail_after_write(command, **kwargs):
            nonlocal ambiguous
            result = self.runner(command, **kwargs)
            if command[3:6] == ['r2','object','put'] and ambiguous:
                ambiguous = False
                raise subprocess.CalledProcessError(1, command)
            return result
        self.deploy(runner=fail_after_write)
        uploads = [command for command in self.calls if command[3:6] == ['r2','object','put']]
        self.assertEqual(len(uploads), 3)
        self.assertEqual(uploads[0], uploads[1])
        self.assertEqual(self.calls[-1][3], 'deploy')

    def test_two_candidate_identities_keep_separate_receipts_in_one_journal(self):
        self.deploy()
        self.artifact['candidate_id'] = '124-1'
        self.calls.clear()
        self.deploy()
        self.assertEqual(len(self.journal.data['website_caches']), 2)
        self.assertEqual(len([command for command in self.calls if command[3:6] == ['r2','object','put']]), 2)

    def test_revoked_authorization_stops_next_object_and_worker_upload(self):
        allowed = True
        def authorize():
            if not allowed:
                raise ValueError('approval revoked')
        def revoke_after_first(command, **kwargs):
            nonlocal allowed
            result = self.runner(command, **kwargs)
            if command[3:6] == ['r2','object','get']:
                allowed = False
            return result
        with self.assertRaisesRegex(ValueError, 'approval revoked'):
            self.deploy(runner=revoke_after_first, authorize=authorize)
        self.assertEqual(len(self.objects), 1)
        self.assertTrue(any(row['state'] == 'completed' for row in self.journal.data['operations'].values()))
        self.assertFalse(any(command[3] == 'deploy' for command in self.calls))

    def test_revoked_authorization_stops_retry_after_an_ambiguous_write(self):
        allowed = True
        def authorize():
            if not allowed:
                raise ValueError('approval revoked')
        def revoke_after_write(command, **kwargs):
            nonlocal allowed
            result = self.runner(command, **kwargs)
            if command[3:6] == ['r2','object','put']:
                allowed = False
                raise subprocess.CalledProcessError(1, command)
            return result
        with self.assertRaisesRegex(ValueError, 'approval revoked'):
            self.deploy(runner=revoke_after_write, authorize=authorize)
        self.assertEqual(len(self.calls), 1)

    def test_revoked_authorization_after_cache_stops_final_worker_upload(self):
        allowed = True
        def authorize():
            if not allowed:
                raise ValueError('approval revoked')
        def revoke_after_last(command, **kwargs):
            nonlocal allowed
            result = self.runner(command, **kwargs)
            if command[3:6] == ['r2','object','get'] and len(self.objects) == 2:
                allowed = False
            return result
        with self.assertRaisesRegex(ValueError, 'approval revoked'):
            self.deploy(runner=revoke_after_last, authorize=authorize)
        self.assertEqual(len(self.objects), 2)
        self.assertFalse(any(command[3] == 'deploy' for command in self.calls))


@unittest.skipUnless(os.environ.get('STUDIO_NATIVE_WORKERS_TESTS') == '1',
                     'Set STUDIO_NATIVE_WORKERS_TESTS=1 for pinned Wrangler R2 acceptance')
class NativeOpenNextCacheTests(unittest.TestCase):
    def test_pinned_native_keys_and_local_r2_roundtrip_from_detached_archive(self):
        from studio.ci import pack_tree, unpack
        from studio.websites import seed_workers_site_cache
        fixture = OpenNextCacheTests()
        fixture.setUp()
        self.addCleanup(fixture.temporary.cleanup)
        archive = fixture.root / 'artifact.zip'
        pack_tree(fixture.build, archive)
        shipping = fixture.root / 'shipping'
        unpack(archive, shipping)
        # The source build is unavailable throughout shipping acceptance.
        shutil.rmtree(fixture.build)
        args, metadata = workers_site_inputs(fixture.component, fixture.artifact, 'staging', shipping, fixture.bindings)
        config = args[args.index('--config')+1]
        fixtures = Path(__file__).parent / 'fixtures/websites'
        cli = [str((fixtures / 'node_modules/.bin/wrangler').resolve())]
        def native(command, **kwargs):
            result = subprocess.run(command, **kwargs, check=True, text=True, capture_output=True,
                                    env={**os.environ, 'WRANGLER_SEND_METRICS': 'false', 'CI': 'true'})
            return result.stdout
        receipt = seed_workers_site_cache(fixture.component, metadata, 'staging', shipping, config,
                                          cli, runner=native, target='local')
        self.assertEqual(receipt['entries'], 2)
        # Compare Python derivation against the actual pinned native implementation.
        module = (fixtures / 'next/node_modules/@opennextjs/cloudflare/dist/api/overrides/internal.js').resolve().as_uri()
        script = f"import {{computeCacheKey}} from {json.dumps(module)}; console.log(computeCacheKey('/nested/page', {{buildId:'build-1',cacheType:'cache'}}))"
        native_key = subprocess.run(['pnpm','exec','node','--input-type=module','-e',script], cwd=fixtures,
                                    check=True, text=True, capture_output=True).stdout.strip().splitlines()[-1]
        state = json.loads((shipping / '.studio-cache/web-staging-local.json').read_text())
        self.assertTrue(any(row.get('result', {}).get('key') == native_key for row in state['operations'].values()))
        result = subprocess.run([*cli, *args, '--dry-run', '--outdir', str(fixture.root / 'out')],
                                cwd=shipping, text=True, capture_output=True,
                                env={**os.environ, 'WRANGLER_SEND_METRICS': 'false', 'CI': 'true'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
