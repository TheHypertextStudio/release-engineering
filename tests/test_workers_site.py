import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
import yaml

from studio.providers import deploy_site, probe
from studio.websites import (capture_workers_site_recovery, restore_workers_site_recovery,
                              workers_site_routing_digest)
from studio.ci import create, promote_component
from studio.config import Config


class RecoveryApi:
    def __init__(self, artifact_sha256):
        self.artifact_sha256 = artifact_sha256
        self.current_version = '11111111-1111-1111-1111-111111111111'
        self.mutations = []
        self.route = {'id': 'route-1', 'pattern': 'example.com/*',
                      'script': 'site-production', 'zone_name': 'example.com'}
        self.domain = {'id': 'domain-1', 'hostname': 'example.com',
                       'service': 'site-production', 'environment': 'production'}
        self.subdomain = {'enabled': True, 'previews_enabled': False}
        self.route_reads = 0
        self.fail_at = None
        self.fail_after_mutation = True
        self.drift_after = None
        self.version_drift_after = None
        self.deployment_reads = 0
        self.version_drift_on_deployment_read = None
        self.request_headers = []

    def get(self, path):
        if path.endswith('/deployments'):
            self.deployment_reads += 1
            if self.deployment_reads == self.version_drift_on_deployment_read:
                self.current_version = '44444444-4444-4444-4444-444444444444'
            return {'deployments': [{'versions': [{'version_id': self.current_version, 'percentage': 100}]}]}
        if '/versions/' in path:
            return {'id': self.current_version, 'resources': {'bindings': [
                {'name': 'STUDIO_ARTIFACT_SHA256', 'type': 'plain_text', 'text': self.artifact_sha256}]}}
        if path.endswith('/workers/services/site-production'):
            return {'default_environment': {'environment': 'production'}}
        if path.endswith('/workers/services/site-staging'):
            return {'default_environment': {'environment': 'production'}}
        if path.endswith('/routes?show_zonename=true'):
            self.route_reads += 1
            return [self.route]
        if '/domains/records?' in path:
            return {'result': [self.domain], 'result_info': {'page': 0, 'total_pages': 1}}
        if path.endswith('/subdomain'):
            return self.subdomain
        raise AssertionError(f'Unexpected read-only provider request: {path}')

    def request(self, method, path, data=None, headers=None):
        self.mutations.append((method, path, data))
        self.request_headers.append(headers or {})
        scope = ('routes' if path.endswith('/routes') else 'domains' if '/domains/records?' in path
                 else 'subdomain' if path.endswith('/subdomain') else None)
        if scope == self.fail_at and not self.fail_after_mutation:
            self.fail_at = None
            raise OSError(f'simulated {scope} failure before applying')
        if scope == 'routes':
            self.route = {**data[0], 'id': 'route-restored', 'script': 'site-production'}
        elif scope == 'domains':
            origins = data.get('origins', [])
            self.domain = ({**origins[0], 'id': 'domain-restored', 'service': 'site-production',
                            'environment': 'production'} if origins else None)
        elif scope == 'subdomain':
            self.subdomain = data
        if scope == self.drift_after:
            self.subdomain = {'enabled': False, 'previews_enabled': False}
            self.drift_after = None
        if scope == self.version_drift_after:
            self.current_version = '44444444-4444-4444-4444-444444444444'
            self.version_drift_after = None
        if scope == self.fail_at:
            self.fail_at = None
            raise OSError(f'simulated {scope} response lost after applying')


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
                'account_id': 'a' * 32,
                'environments': {'staging': 'staging', 'production': 'production'},
                'health_urls': {'staging': 'https://staging.example/__studio/release', 'production': 'https://example.com/__studio/release'},
            },
        }
        self.artifact = {'source_sha': 'a' * 40, 'sha256': 'b' * 64, 'candidate_id': '123-1'}
        self.calls = []
        self.write_config()

    def write_config(self):
        (self.bindings / 'wrangler.json').write_text(json.dumps(self.config))

    def test_recovery_snapshot_keeps_prior_release_routes_domains_and_subdomain(self):
        archive = self.root / 'prior.zip'
        archive.write_bytes(b'prior complete worker archive')
        snapshot = self.root / 'recovery.json'
        api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
        record = capture_workers_site_recovery(
            self.component, 'production', self.bindings / 'wrangler.json', archive,
            snapshot, api=api)
        self.assertEqual(record['logical_environment'], 'production')
        self.assertEqual(record['service_environment'], 'production')
        self.assertEqual(record['routes'][0]['script'], record['worker'])
        self.assertEqual(record['prior_version_id'], '11111111-1111-1111-1111-111111111111')
        self.assertEqual(record['routes'], [{'id': 'route-1', 'pattern': 'example.com/*',
                                             'script': 'site-production', 'zone_name': 'example.com'}])
        self.assertEqual(record['custom_domains'], [{'id': 'domain-1', 'hostname': 'example.com',
                                                     'service': 'site-production', 'environment': 'production'}])
        self.assertEqual(record['subdomain'], {'enabled': True, 'previews_enabled': False})
        self.assertTrue((snapshot.parent / record['archive_file']).is_file())
        self.assertTrue((snapshot.parent / record['config_file']).is_file())

    def test_recovery_snapshot_reads_all_custom_domain_pages(self):
        archive = self.root / 'prior.zip'
        archive.write_bytes(b'prior complete worker archive')
        snapshot = self.root / 'recovery.json'
        api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
        original_get = api.get

        def paginated(path):
            if '/domains/records?' in path:
                page = int(path.split('page=')[1].split('&')[0])
                if page == 0:
                    return {'result': [api.domain], 'result_info': {'page': 0, 'total_pages': 2}}
                domain = {**api.domain, 'id': 'domain-2', 'hostname': 'www.example.com'}
                return {'result': [domain], 'result_info': {'page': 1, 'total_pages': 2}}
            return original_get(path)

        api.get = paginated
        record = capture_workers_site_recovery(
            self.component, 'production', self.bindings / 'wrangler.json', archive,
            snapshot, api=api)
        self.assertEqual([row['id'] for row in record['custom_domains']], ['domain-1', 'domain-2'])

    def test_recovery_snapshot_cannot_overwrite_existing_evidence(self):
        archive = self.root / 'prior.zip'
        archive.write_bytes(b'prior complete worker archive')
        snapshot = self.root / 'recovery.json'
        api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
        capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                      archive, snapshot, api=api)
        saved = snapshot.read_bytes()
        with self.assertRaisesRegex(ValueError, 'cannot be replaced'):
            capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                          archive, snapshot, api=api)
        self.assertEqual(snapshot.read_bytes(), saved)

    def test_recovery_refuses_routing_changes_after_the_expected_version_snapshot(self):
        archive = self.root / 'prior.zip'
        archive.write_bytes(b'prior complete worker archive')
        snapshot = self.root / 'recovery.json'
        api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
        record = capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                               archive, snapshot, api=api)
        api.current_version = '33333333-3333-3333-3333-333333333333'
        api.route = {**api.route, 'pattern': 'other.example.com/*'}
        calls = []
        with self.assertRaisesRegex(ValueError, 'routes or domains changed'):
            restore_workers_site_recovery(snapshot, self.component, 'production', current_config_path=self.bindings / 'wrangler.json',
                expected_current_version=api.current_version,
                expected_current_routing_sha256=record['prior_routing_sha256'],
                confirmation=f'restore:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:site-production:production:production:11111111-1111-1111-1111-111111111111:{json.loads(snapshot.read_text())["record_sha256"]}',
                toolchain={'wrangler': '4.126.0'}, api=api,
                runner=lambda command, **kwargs: calls.append(command))
        self.assertEqual(calls, [])
        self.assertEqual(api.mutations, [])

    def test_recovery_restore_requires_live_version_guard_before_any_mutation(self):
        archive = self.root / 'prior.zip'
        archive.write_bytes(b'prior complete worker archive')
        snapshot = self.root / 'recovery.json'
        api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
        record = capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                               archive, snapshot, api=api)
        api.current_version = '22222222-2222-2222-2222-222222222222'
        calls = []
        with self.assertRaisesRegex(ValueError, 'current Worker version'):
            restore_workers_site_recovery(snapshot, self.component, 'production', current_config_path=self.bindings / 'wrangler.json',
                expected_current_version='33333333-3333-3333-3333-333333333333',
                expected_current_routing_sha256='0' * 64,
                confirmation=f'restore:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:site-production:production:production:11111111-1111-1111-1111-111111111111:{record["record_sha256"]}',
                toolchain={'wrangler': '4.126.0'}, api=api,
                runner=lambda command, **kwargs: calls.append(command))
        self.assertEqual(calls, [])
        self.assertEqual(api.mutations, [])

    def test_recovery_restores_exact_version_and_scoped_live_bindings(self):
        archive = self.root / 'prior.zip'
        archive.write_bytes(b'prior complete worker archive')
        snapshot = self.root / 'recovery.json'
        api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
        record = capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                               archive, snapshot, api=api)
        api.current_version = '33333333-3333-3333-3333-333333333333'
        api.route = {**api.route, 'pattern': 'candidate.example/*'}
        api.domain = {**api.domain, 'hostname': 'candidate.example'}
        api.subdomain = {'enabled': False, 'previews_enabled': True}
        calls = []

        def rollback(command, **kwargs):
            calls.append(command)
            api.current_version = '11111111-1111-1111-1111-111111111111'

        restore_workers_site_recovery(snapshot, self.component, 'production', current_config_path=self.bindings / 'wrangler.json',
            expected_current_version='33333333-3333-3333-3333-333333333333',
            expected_current_routing_sha256=workers_site_routing_digest(
                [api.route], [api.domain], api.subdomain),
            confirmation=f'restore:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:site-production:production:production:11111111-1111-1111-1111-111111111111:{record["record_sha256"]}',
            toolchain={'wrangler': '4.126.0'}, api=api,
            runner=rollback)
        self.assertEqual(calls[0][:5], ['pnpm', 'dlx', 'wrangler@4.126.0', 'rollback', '11111111-1111-1111-1111-111111111111'])
        self.assertEqual(api.mutations[0], ('PUT', '/accounts/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/workers/services/site-production/environments/production/routes', [{'pattern': 'example.com/*', 'zone_name': 'example.com'}]))
        self.assertEqual(api.mutations[1][0:2], ('PUT', '/accounts/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/workers/scripts/site-production/domains/records?replace_state=true'))
        self.assertEqual(api.mutations[1][2], {
            'override_scope': True, 'override_existing_origin': False,
            'override_existing_dns_record': False,
            'origins': [{'hostname': 'example.com'}],
        })
        self.assertEqual(api.mutations[2], ('POST', '/accounts/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/workers/scripts/site-production/subdomain', record['subdomain']))
        self.assertEqual(api.request_headers[2], {'Cloudflare-Workers-Script-Api-Date': '2025-08-01'})

    def test_recovery_rejects_record_for_another_component_worker(self):
        archive = self.root / 'prior.zip'
        archive.write_bytes(b'prior complete worker archive')
        snapshot = self.root / 'recovery.json'
        api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
        record = capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                               archive, snapshot, api=api)
        other = copy.deepcopy(self.component)
        other['deploy']['config'] = 'other.json'
        other['deploy']['account_id'] = 'c' * 32
        self.config['account_id'] = 'c' * 32
        self.config['env']['production']['name'] = 'other-production'
        other_config = self.bindings / 'other.json'
        other_config.write_text(json.dumps(self.config))
        calls = []
        with self.assertRaisesRegex(ValueError, 'current reviewed config'):
            restore_workers_site_recovery(snapshot, other, 'production', current_config_path=other_config,
                expected_current_version=api.current_version,
                expected_current_routing_sha256=record['prior_routing_sha256'],
                confirmation=f'restore:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:site-production:production:11111111-1111-1111-1111-111111111111:{record["record_sha256"]}',
                toolchain={'wrangler': '4.126.0'}, api=api,
                runner=lambda command, **kwargs: calls.append(command))
        self.assertEqual(calls, [])
        self.assertEqual(api.mutations, [])

    def test_routing_digest_ignores_provider_array_order(self):
        routes = [{'pattern': 'a.example/*', 'zone_id': '1'},
                  {'pattern': 'b.example/*', 'zone_id': '2'}]
        domains = [{'hostname': 'a.example', 'service': 'site-production', 'environment': 'production'},
                   {'hostname': 'b.example', 'service': 'site-production', 'environment': 'production'}]
        digest = workers_site_routing_digest(routes, domains, {'enabled': True, 'previews_enabled': False})
        self.assertEqual(digest, workers_site_routing_digest(list(reversed(routes)), list(reversed(domains)),
                                                             {'enabled': True, 'previews_enabled': False}))

    def _prepare_recovery_live_state(self, api, record):
        api.current_version = '33333333-3333-3333-3333-333333333333'
        api.route = {**api.route, 'pattern': 'candidate.example/*'}
        api.domain = {**api.domain, 'hostname': 'candidate.example'}
        api.subdomain = {'enabled': False, 'previews_enabled': True}
        return workers_site_routing_digest([api.route], [api.domain], api.subdomain)

    def _restore_args(self, snapshot, record, api, start_routing, runner):
        return restore_workers_site_recovery(
            snapshot, self.component, 'production', current_config_path=self.bindings / 'wrangler.json',
            expected_current_version='33333333-3333-3333-3333-333333333333',
            expected_current_routing_sha256=start_routing,
            confirmation=f'restore:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:site-production:production:production:11111111-1111-1111-1111-111111111111:{record["record_sha256"]}',
            toolchain={'wrangler': '4.126.0'}, api=api, runner=runner)

    def test_recovery_journal_resumes_failures_after_every_provider_step(self):
        for scenario, failed_step, failure_after_mutation in (
                ('rollback', 'rollback', True), ('routes-after', 'routes', True),
                ('routes-before', 'routes', False), ('domains-after', 'domains', True),
                ('subdomain-after', 'subdomain', True)):
            with self.subTest(scenario=scenario):
                directory = Path(tempfile.mkdtemp(dir=self.root))
                archive = directory / 'prior.zip'
                archive.write_bytes(b'prior complete worker archive')
                snapshot = directory / 'recovery.json'
                api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
                record = capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                                       archive, snapshot, api=api)
                start_routing = self._prepare_recovery_live_state(api, record)
                rollback_calls = []

                def fail_runner(command, **kwargs):
                    rollback_calls.append(command)
                    api.current_version = '11111111-1111-1111-1111-111111111111'
                    if failed_step == 'rollback':
                        raise OSError('simulated lost rollback response')

                if failed_step != 'rollback':
                    api.fail_at = failed_step
                api.fail_after_mutation = failure_after_mutation
                with self.assertRaisesRegex((OSError, RuntimeError), 'simulated'):
                    self._restore_args(snapshot, record, api, start_routing, fail_runner)
                journal_path = Path(str(snapshot) + '.restore.json')
                journal_after_failure = json.loads(journal_path.read_text())
                self.assertEqual(journal_after_failure['manifest_sha256'], record['record_sha256'])
                self.assertEqual(journal_after_failure['state'], 'partial')
                self.assertEqual(journal_after_failure['recovery_context']['start_version'],
                                 '33333333-3333-3333-3333-333333333333')
                self.assertEqual(journal_after_failure['operations'][
                    'version' if failed_step == 'rollback' else failed_step]['state'], 'failed')
                route_mutations_before_retry = sum(path.endswith('/routes') for _, path, _ in api.mutations)
                domain_mutations_before_retry = sum('/domains/records?' in path for _, path, _ in api.mutations)
                subdomain_mutations_before_retry = sum(path.endswith('/subdomain') for _, path, _ in api.mutations)

                def successful_runner(command, **kwargs):
                    rollback_calls.append(command)
                    api.current_version = '11111111-1111-1111-1111-111111111111'

                result = self._restore_args(snapshot, record, api, start_routing, successful_runner)
                self.assertEqual(result['state'], 'completed')
                self.assertEqual(len(rollback_calls), 1)
                journal = json.loads(journal_path.read_text())
                self.assertEqual(journal['state'], 'completed')
                for step in ('version', 'routes', 'domains', 'subdomain'):
                    self.assertEqual(journal['operations'][step]['state'], 'completed')
                if scenario in ('routes-after', 'domains-after', 'subdomain-after'):
                    self.assertEqual(sum(path.endswith('/routes') for _, path, _ in api.mutations),
                                     route_mutations_before_retry)
                elif scenario == 'routes-before':
                    self.assertEqual(sum(path.endswith('/routes') for _, path, _ in api.mutations),
                                     route_mutations_before_retry + 1)
                if failed_step in ('domains', 'subdomain'):
                    self.assertEqual(sum('/domains/records?' in path for _, path, _ in api.mutations),
                                     domain_mutations_before_retry)
                if failed_step == 'subdomain':
                    self.assertEqual(sum(path.endswith('/subdomain') for _, path, _ in api.mutations),
                                     subdomain_mutations_before_retry)

    def test_recovery_journal_rejects_third_version_or_unrelated_route_drift(self):
        for drift in ('version', 'route'):
            with self.subTest(drift=drift):
                directory = Path(tempfile.mkdtemp(dir=self.root))
                archive = directory / 'prior.zip'
                archive.write_bytes(b'prior complete worker archive')
                snapshot = directory / 'recovery.json'
                api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
                record = capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                                       archive, snapshot, api=api)
                start_routing = self._prepare_recovery_live_state(api, record)
                api.fail_at = 'routes'
                api.fail_after_mutation = False

                def runner(command, **kwargs):
                    api.current_version = '11111111-1111-1111-1111-111111111111'

                with self.assertRaisesRegex(OSError, 'simulated routes failure'):
                    self._restore_args(snapshot, record, api, start_routing, runner)
                api.fail_at = None
                if drift == 'version':
                    api.current_version = '44444444-4444-4444-4444-444444444444'
                else:
                    api.route = {**api.route, 'pattern': 'unrelated.example/*'}
                mutation_count = len(api.mutations)
                with self.assertRaisesRegex(ValueError, 'unexpected live Worker state'):
                    self._restore_args(snapshot, record, api, start_routing, runner)
                self.assertEqual(len(api.mutations), mutation_count)

    def test_recovery_journal_binds_current_reviewed_config_identity(self):
        archive = self.root / 'prior.zip'
        archive.write_bytes(b'prior complete worker archive')
        snapshot = self.root / 'recovery.json'
        api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
        record = capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                               archive, snapshot, api=api)
        start_routing = self._prepare_recovery_live_state(api, record)
        api.fail_at = 'routes'
        api.fail_after_mutation = False

        def runner(command, **kwargs):
            api.current_version = '11111111-1111-1111-1111-111111111111'

        with self.assertRaisesRegex(OSError, 'simulated routes failure'):
            self._restore_args(snapshot, record, api, start_routing, runner)
        config = json.loads((self.bindings / 'wrangler.json').read_text())
        config['compatibility_date'] = '2026-10-11'
        (self.bindings / 'wrangler.json').write_text(json.dumps(config))
        mutations = len(api.mutations)
        with self.assertRaisesRegex(ValueError, 'original reviewed start state and config identity'):
            self._restore_args(snapshot, record, api, start_routing, runner)
        self.assertEqual(len(api.mutations), mutations)

    def test_recovery_rechecks_all_scopes_before_each_provider_write(self):
        for drift in ('subdomain', 'version'):
            with self.subTest(drift=drift):
                directory = Path(tempfile.mkdtemp(dir=self.root))
                archive = directory / 'prior.zip'
                archive.write_bytes(b'prior complete worker archive')
                snapshot = directory / 'recovery.json'
                api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
                record = capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                                       archive, snapshot, api=api)
                start_routing = self._prepare_recovery_live_state(api, record)
                if drift == 'subdomain':
                    api.drift_after = 'routes'
                    message = 'unexpected live Worker state: subdomain changed'
                else:
                    api.version_drift_after = 'routes'
                    message = 'unexpected live Worker state: deployment version changed'

                def runner(command, **kwargs):
                    api.current_version = '11111111-1111-1111-1111-111111111111'

                with self.assertRaisesRegex(ValueError, message):
                    self._restore_args(snapshot, record, api, start_routing, runner)
                self.assertEqual(sum(path.endswith('/routes') for _, path, _ in api.mutations), 1)
                self.assertEqual(sum('/domains/records?' in path for _, path, _ in api.mutations), 0)
                self.assertEqual(sum(path.endswith('/subdomain') for _, path, _ in api.mutations), 0)
                journal = json.loads(Path(str(snapshot) + '.restore.json').read_text())
                self.assertEqual(journal['state'], 'partial')
                self.assertEqual(journal['operations']['routes']['state'], 'completed')
                self.assertEqual(journal['operations']['domains']['state'], 'failed')

    def test_recovery_rejects_third_version_after_rollback_before_routes_write(self):
        archive = self.root / 'prior.zip'
        archive.write_bytes(b'prior complete worker archive')
        snapshot = self.root / 'recovery.json'
        api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
        record = capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                               archive, snapshot, api=api)
        start_routing = self._prepare_recovery_live_state(api, record)

        def runner(command, **kwargs):
            api.current_version = '11111111-1111-1111-1111-111111111111'
            # Rollback verification is the next read; inject drift into the shared guard that
            # runs immediately afterward and before the first route write.
            api.version_drift_on_deployment_read = api.deployment_reads + 2

        with self.assertRaisesRegex(ValueError, 'unexpected live Worker state: deployment version changed'):
            self._restore_args(snapshot, record, api, start_routing, runner)
        self.assertEqual(api.current_version, '44444444-4444-4444-4444-444444444444')
        self.assertEqual(api.mutations, [])
        journal = json.loads(Path(str(snapshot) + '.restore.json').read_text())
        self.assertEqual(journal['operations']['version']['state'], 'completed')
        self.assertEqual(journal['operations']['routes']['state'], 'failed')

    def test_recovery_capture_rejects_route_change_during_snapshot(self):
        archive = self.root / 'prior.zip'
        archive.write_bytes(b'prior complete worker archive')
        snapshot = self.root / 'recovery.json'
        api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
        original_get = api.get

        def race(path):
            if path.endswith('/routes?show_zonename=true'):
                api.route_reads += 1
                if api.route_reads == 2:
                    return [{**api.route, 'pattern': 'changed.example/*'}]
                return [api.route]
            return original_get(path)

        api.get = race
        with self.assertRaisesRegex(ValueError, 'changed while the recovery snapshot was captured'):
            capture_workers_site_recovery(self.component, 'production', self.bindings / 'wrangler.json',
                                          archive, snapshot, api=api)
        self.assertFalse(snapshot.exists())

    def test_named_script_env_uses_api_service_environment_from_wrangler_metadata(self):
        archive = self.root / 'prior.zip'
        archive.write_bytes(b'prior complete worker archive')
        snapshot = self.root / 'staging-recovery.json'
        api = RecoveryApi(hashlib.sha256(archive.read_bytes()).hexdigest())
        api.route = {**api.route, 'script': 'site-staging'}
        api.domain = {**api.domain, 'service': 'site-staging'}
        record = capture_workers_site_recovery(self.component, 'staging', self.bindings / 'wrangler.json',
                                               archive, snapshot, api=api)
        self.assertEqual(record['logical_environment'], 'staging')
        self.assertEqual(record['service_environment'], 'production')
        self.assertEqual(record['worker'], 'site-staging')

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
