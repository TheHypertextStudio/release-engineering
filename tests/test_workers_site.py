import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
import yaml

from studio.providers import deploy_site, probe
from studio.ci import create, promote_component
from studio.config import Config


class WorkersSiteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.build = self.root / 'build'
        self.bindings = self.root / 'candidate'
        self.build.mkdir()
        self.bindings.mkdir()
        (self.build / 'worker.js').write_text('export default {fetch(){return new Response("ok")}}')
        (self.build / 'assets').mkdir()
        (self.build / 'assets/index.html').write_text('<h1>Reviewed website</h1>')
        self.config = {
            'account_id': 'a' * 32,
            'name': 'site', 'compatibility_date': '2026-10-10',
            'assets': {'binding': 'ASSETS', 'run_worker_first': ['/__studio/release']},
            'env': {'staging': {'name': 'site-staging'}, 'production': {'name': 'site-production'}},
        }
        self.component = {
            'id': 'web', 'path': '.',
            'deploy': {
                'provider': 'workers', 'config': 'wrangler.json', 'entrypoint': 'worker.js', 'assets': 'assets',
                'environments': {'staging': 'staging', 'production': 'production'},
                'health_urls': {'staging': 'https://staging.example/__studio/release', 'production': 'https://example.com/__studio/release'},
            },
        }
        self.artifact = {'source_sha': 'a' * 40, 'sha256': 'b' * 64, 'candidate_id': '123-1'}
        self.calls = []
        self.write_config()

    def write_config(self):
        (self.bindings / 'wrangler.json').write_text(json.dumps(self.config))

    def deploy(self, environment='production'):
        try:
            return deploy_site(self.component, self.artifact, environment, self.build,
                               {'wrangler': '4.126.0'}, bindings_root=self.bindings,
                               runner=lambda command, **kwargs: self.calls.append(command))
        except TypeError as error:
            self.fail(f'Workers site candidate API is unavailable: {error}')

    def test_prebuilt_worker_and_assets_use_the_selected_candidate(self):
        # Detect a rebuild, omitted assets, or use of mutable repository config.
        with patch('studio.providers.probe') as health:
            result = self.deploy()
        self.assertEqual(result['state'], 'completed')
        command = self.calls[0]
        self.assertEqual(command[:4], ['pnpm', 'dlx', 'wrangler@4.126.0', 'deploy'])
        self.assertIn('--no-bundle', command)
        self.assertIn('--autoconfig=false', command)
        self.assertEqual(command[command.index('--assets') + 1], str(self.build / 'assets'))
        self.assertEqual(command[command.index('--env') + 1], 'production')
        self.assertIn('STUDIO_CANDIDATE_ID:123-1', command)
        copied = Path(command[command.index('--config') + 1])
        self.assertEqual(json.loads(copied.read_text()), self.config)
        self.assertEqual(health.call_args.kwargs['expected_metadata'], {
            'sourceSha': 'a' * 40, 'artifactSha256': 'b' * 64, 'candidateId': '123-1',
        })

    def test_staging_cannot_select_the_production_worker(self):
        self.config['env']['staging']['name'] = 'site-production'
        self.write_config()
        with patch('studio.providers.probe'), self.assertRaisesRegex(ValueError, 'isolated'):
            self.deploy('staging')
        self.assertEqual(self.calls, [])

    def test_module_roots_and_blob_inputs_cannot_leave_the_archive(self):
        for key, value in [('base_dir', str(self.bindings)), ('base_dir', '../candidate'),
                           ('text_blobs', {'TEXT': str(self.bindings / 'wrangler.json')}),
                           ('data_blobs', {'DATA': '../candidate/wrangler.json'}),
                           ('wasm_modules', {'WASM': '/tmp/unreviewed.wasm'})]:
            with self.subTest(key=key, value=value):
                config = copy.deepcopy(self.config)
                config[key] = value
                (self.bindings / 'wrangler.json').write_text(json.dumps(config))
                with patch('studio.providers.probe'), self.assertRaisesRegex(ValueError, 'archive'):
                    self.deploy()
        self.assertEqual(self.calls, [])

    def test_relative_module_and_blob_paths_keep_artifact_semantics(self):
        (self.build / 'message.txt').write_text('reviewed text')
        self.config.update(base_dir='.', find_additional_modules=True,
                           rules=[{'type': 'ESModule', 'globs': ['**/*.js']}],
                           text_blobs={'TEXT': 'message.txt'})
        self.write_config()
        with patch('studio.providers.probe'):
            self.deploy()
        command = self.calls[0]
        copied = json.loads(Path(command[command.index('--config') + 1]).read_text())
        self.assertEqual(Path(copied['base_dir']), self.build)
        self.assertEqual(Path(copied['text_blobs']['TEXT']).read_text(), 'reviewed text')
        self.assertEqual(json.loads((self.bindings / 'wrangler.json').read_text()), self.config)

    def test_unsupported_build_and_filesystem_options_fail_before_deployment(self):
        for key, value in [('tsconfig', '/tmp/tsconfig.json'), ('site', {'bucket': '/tmp'}),
                           ('unsafe', {'bindings': []}), ('containers', []),
                           ('upload_source_maps', True)]:
            with self.subTest(key=key):
                self.config[key] = value
                self.write_config()
                with patch('studio.providers.probe'), self.assertRaisesRegex(ValueError, 'unsupported'):
                    self.deploy()
                del self.config[key]
        self.assertEqual(self.calls, [])

    def test_staging_cannot_share_production_write_resource_identities(self):
        resources = [
            ('d1_databases', [{'binding': 'DB', 'database_id': 'prod-db'}]),
            ('r2_buckets', [{'binding': 'BUCKET', 'bucket_name': 'prod-bucket'}]),
            ('kv_namespaces', [{'binding': 'KV', 'id': 'prod-kv'}]),
            ('services', [{'binding': 'API', 'service': 'prod-api'}]),
            ('services', [{'binding': 'SELF', 'service': 'site-production'}]),
            ('vectorize', [{'binding': 'INDEX', 'index_name': 'prod-index'}]),
            ('hyperdrive', [{'binding': 'DB', 'id': 'prod-hyperdrive'}]),
            ('queues', {'producers': [{'binding': 'QUEUE', 'queue': 'prod-queue'}]}),
            ('queues', {'consumers': [{'queue': 'prod-queue'}]}),
            ('durable_objects', {'bindings': [{'name': 'DO', 'class_name': 'Cache', 'script_name': 'prod-cache'}]}),
            ('analytics_engine_datasets', [{'binding': 'METRICS', 'dataset': 'prod-metrics'}]),
            ('workflows', [{'binding': 'JOB', 'name': 'prod-job', 'class_name': 'Job'}]),
            ('pipelines', [{'binding': 'PIPE', 'stream': 'prod-stream'}]),
            ('artifacts', [{'binding': 'FILES', 'namespace': 'prod-files'}]),
            ('dispatch_namespaces', [{'binding': 'DISPATCH', 'namespace': 'prod-dispatch'}]),
            ('secrets_store_secrets', [{'binding': 'KEY', 'store_id': 'store', 'secret_name': 'prod-key'}]),
            ('ratelimits', [{'name': 'LIMIT', 'namespace_id': '123', 'simple': {'limit': 10, 'period': 60}}]),
            ('tail_consumers', [{'service': 'prod-tail'}]),
        ]
        for key, value in resources:
            with self.subTest(resource=key, value=value):
                config = copy.deepcopy(self.config)
                for scope in ['staging', 'production']:
                    config['env'][scope][key] = value
                (self.bindings / 'wrangler.json').write_text(json.dumps(config))
                with patch('studio.providers.probe'), self.assertRaisesRegex(ValueError, 'isolated'):
                    self.deploy('staging')
        self.assertEqual(self.calls, [])

    def test_resources_require_explicit_environment_bindings_and_identities(self):
        for config in [
            {**self.config, 'd1_databases': [{'binding': 'DB', 'database_id': 'prod-db'}]},
            {**self.config, 'env': {'staging': self.config['env']['staging'],
                                  'production': {**self.config['env']['production'], 'r2_buckets': [{'binding': 'CACHE', 'bucket_name': 'prod-cache'}]}}},
            {**self.config, 'env': {'staging': {**self.config['env']['staging'], 'kv_namespaces': [{'binding': 'KV'}]},
                                  'production': self.config['env']['production']}},
        ]:
            with self.subTest(config=config):
                (self.bindings / 'wrangler.json').write_text(json.dumps(config))
                with patch('studio.providers.probe'), self.assertRaisesRegex(ValueError, 'explicit'):
                    self.deploy('staging')
        self.assertEqual(self.calls, [])

    def test_provider_identity_case_cannot_bypass_isolation(self):
        self.config['env']['production']['d1_databases'] = [{'binding': 'DB', 'database_id': 'abcd-1234'}]
        self.config['env']['staging']['d1_databases'] = [{'binding': 'DB', 'database_id': 'ABCD-1234'}]
        self.write_config()
        with patch('studio.providers.probe'), self.assertRaisesRegex(ValueError, 'isolated'):
            self.deploy('staging')
        self.assertEqual(self.calls, [])

    def test_separate_resources_and_worker_local_durable_objects_are_supported(self):
        for scope in ['staging', 'production']:
            self.config['env'][scope].update(
                d1_databases=[{'binding': 'DB', 'database_id': scope + '-db'}],
                r2_buckets=[{'binding': 'CACHE', 'bucket_name': scope + '-cache'}],
                services=[{'binding': 'API', 'service': scope + '-api'}],
                durable_objects={'bindings': [{'name': 'DO', 'class_name': 'Cache'}]})
        self.write_config()
        with patch('studio.providers.probe'):
            self.deploy('staging')
        self.assertEqual(len(self.calls), 1)

    def test_malformed_environment_mappings_fail_before_deployment(self):
        for value in [None, [], 'production']:
            with self.subTest(value=value):
                self.config['env'] = value
                self.write_config()
                with self.assertRaisesRegex(ValueError, 'environment'):
                    self.deploy()
        self.assertEqual(self.calls, [])

    def test_staging_cannot_bind_a_production_route(self):
        route = {'pattern': 'example.com/*', 'zone_name': 'example.com'}
        self.config['env']['staging']['routes'] = [route]
        self.config['env']['production']['routes'] = [route]
        self.write_config()
        with patch('studio.providers.probe'), self.assertRaisesRegex(ValueError, 'isolated'):
            self.deploy('staging')
        self.assertEqual(self.calls, [])

    def test_missing_probe_fails_before_deployment(self):
        del self.component['deploy']['health_urls']['production']
        with self.assertRaisesRegex(ValueError, 'probe'):
            self.deploy()
        self.assertEqual(self.calls, [])

    def test_missing_account_binding_fails_before_deployment(self):
        del self.config['account_id']
        self.write_config()
        with patch('studio.providers.probe'), self.assertRaisesRegex(ValueError, 'account'):
            self.deploy()
        self.assertEqual(self.calls, [])

    def test_environment_cannot_override_the_reviewed_account(self):
        self.config['env']['staging']['account_id'] = 'c' * 32
        self.write_config()
        with patch('studio.providers.probe'), self.assertRaisesRegex(ValueError, 'account'):
            self.deploy('staging')
        self.assertEqual(self.calls, [])

    def test_website_only_assembly_stages_and_promotes_the_same_archive(self):
        product = self.root / 'product'
        product.mkdir()
        source = product / 'prebuilt'
        source.mkdir()
        (source / 'worker.js').write_text('export default {fetch(){return new Response("reviewed")}}')
        (source / 'assets').mkdir()
        (source / 'assets/index.html').write_text('<h1>Reviewed</h1>')
        (product / 'wrangler.json').write_text(json.dumps(self.config))
        component = {**copy.deepcopy(self.component), 'kind': 'static-site', 'checks': [],
                     'build': {'profile': 'static-copy', 'source': 'prebuilt', 'output': 'output'}}
        data = {'schema': 1, 'product': 'website', 'repository': 'Studio/website',
                'owner_repository': 'Studio/website', 'toolchain': {'wrangler': '4.126.0'},
                'components': [component], 'release': {'channels': ['web']}}
        (product / 'studio.yaml').write_text(yaml.safe_dump(data))
        (product / 'studio.lock.json').write_text(json.dumps({
            'repository': 'TheHypertextStudio/release-engineering', 'revision': 'b' * 40,
            'version': 'v0.1.6', 'sha256': 'c' * 64}))
        (product / 'run').write_text("version='v0.1.6'\nexpected='" + 'c' * 64 + "'\n")
        config = Config(product, data)
        identities = []

        def api(path):
            if path == 'repos/Studio/website':
                return {'default_branch': 'main'}
            return [] if '/releases?' in path else {'variables': []}

        def git(command, **kwargs):
            self.assertEqual(command[0], 'git', 'Assembly unexpectedly invoked a provider or native app tool')
            return 'a' * 40 if command[1] == 'rev-parse' else ''

        def deploy(declared, artifact, environment, build, toolchain, *, bindings_root):
            self.assertEqual((build / 'assets/index.html').read_text(), '<h1>Reviewed</h1>')
            self.assertEqual(json.loads((bindings_root / 'wrangler.json').read_text()), self.config)
            identities.append((environment, artifact['sha256'], artifact['candidate_id']))
            return {'state': 'completed'}

        environment = {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
                       'GITHUB_REPOSITORY': 'Studio/website', 'GITHUB_SHA': 'a' * 40,
                       'GITHUB_RUN_ID': '123', 'GITHUB_RUN_NUMBER': '1', 'GITHUB_RUN_ATTEMPT': '1'}
        with patch.dict(os.environ, environment), patch('studio.ci.api', side_effect=api), \
                patch('studio.ci.execute', side_effect=git), patch('studio.ci.production_baseline', return_value=None), \
                patch('studio.ci.version_from_git', return_value='0.1.0'), patch('studio.ci.deploy_site', side_effect=deploy):
            candidate = create(config, self.root / 'assembled', 'b' * 40)
            # Removing the mutable build proves promotion consumes the candidate archive.
            import shutil
            shutil.rmtree(product / 'output')
            shutil.rmtree(source)
            promote_component(config, candidate, component, 'production')
        self.assertEqual([entry[0] for entry in identities], ['staging', 'production'])
        self.assertEqual(identities[0][1:], identities[1][1:])

    def test_custom_build_and_environment_build_are_rejected(self):
        for location in ['top', 'environment']:
            with self.subTest(location=location):
                config = copy.deepcopy(self.config)
                target = config if location == 'top' else config['env']['production']
                target['build'] = {'command': 'pnpm build'}
                (self.bindings / 'wrangler.json').write_text(json.dumps(config))
                with self.assertRaisesRegex(ValueError, 'build'):
                    self.deploy()
        self.assertEqual(self.calls, [])

    def test_prebuilt_paths_cannot_escape_the_archive(self):
        for key, value in [('entrypoint', '../outside.js'), ('assets', '/outside')]:
            with self.subTest(key=key):
                original = self.component['deploy'][key]
                self.component['deploy'][key] = value
                with self.assertRaisesRegex(ValueError, 'inside'):
                    self.deploy()
                self.component['deploy'][key] = original
        self.assertEqual(self.calls, [])

    def test_asset_symlink_cannot_read_outside_the_archive(self):
        outside = self.root / 'outside.txt'
        outside.write_text('must not upload')
        (self.build / 'assets/private.txt').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.deploy()
        self.assertEqual(self.calls, [])

    def test_unknown_or_unpinned_identity_fails_before_deployment(self):
        for key, value in [('candidate_id', ''), ('sha256', 'not-a-digest'), ('source_sha', 'main')]:
            with self.subTest(key=key):
                original = self.artifact[key]
                self.artifact[key] = value
                with self.assertRaisesRegex(ValueError, 'identity'):
                    self.deploy()
                self.artifact[key] = original
        self.assertEqual(self.calls, [])

    def test_wrong_artifact_metadata_does_not_pass_a_health_probe(self):
        response = io.BytesIO(json.dumps({'sourceSha': 'a' * 40, 'artifactSha256': 'c' * 64, 'candidateId': '123-1'}).encode())
        response.geturl = lambda: 'https://example.com/__studio/release'
        with patch('studio.providers.urllib.request.urlopen', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'probe failed'):
                try:
                    probe('https://example.com/__studio/release', attempts=1,
                          expected_metadata={'sourceSha': 'a' * 40, 'artifactSha256': 'b' * 64, 'candidateId': '123-1'})
                except TypeError as error:
                    self.fail(f'Full release metadata verification is unavailable: {error}')


if __name__ == '__main__':
    unittest.main()
