"""External publisher evidence must never masquerade as a Workers candidate."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from test_config import CONFIG
from studio.config import load_config
from studio import documentation

SHA = 'a' * 40
SETTINGS = {
    'provider': 'mintlify', 'organization': 'studio', 'project': 'manual',
    'project_id': 'provider-project-1', 'path': 'docs', 'branch': 'main',
    'upstream': 'https://manual.mintlify.site', 'public_url': 'https://example.com/docs',
    'required_checks': ['Docs validation'],
}
UPDATE = {
    '_id': 'update-1', 'projectId': 'provider-project-1',
    'createdAt': '2026-10-10T20:00:00Z', 'endedAt': '2026-10-10T20:01:00Z',
    'status': 'success', 'summary': 'Deployment complete', 'logs': ['internal log'],
    'subdomain': 'manual', 'commit': {'sha': SHA, 'ref': 'refs/heads/main', 'message': 'Docs update',
        'filesChanged': {'added': [], 'modified': ['docs/index.mdx'], 'removed': []}},
    'source': 'github', 'author': {'name': 'Maintainer'},
}
CHECK = {'name': 'Docs validation', 'head_sha': SHA, 'status': 'completed',
    'conclusion': 'success', 'details_url': 'https://github.com/studio/example/actions/runs/12/job/34',
    'app': {'slug': 'github-actions'}, 'id': 34}

class GitHubFixture:
    def __init__(self):
        self.checks = [copy.deepcopy(CHECK)]
        self.ancestry = 'ahead'
    def request(self, path):
        if path == f'repos/studio/example/compare/{SHA}...main':
            return {'status': self.ancestry, 'head_commit': {'sha': 'b' * 40}}
        if path == f'repos/studio/example/commits/{SHA}/check-runs?filter=latest&per_page=100':
            return [{'check_runs': self.checks, 'total_count': len(self.checks)}]
        raise AssertionError('Unexpected GitHub endpoint: ' + path)

class MintlifyFixture:
    def __init__(self):
        self.update = copy.deepcopy(UPDATE)
    def deployment(self, identity):
        if identity != 'update-1': raise AssertionError('Wrong provider deployment queried')
        return self.update

class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'docs').mkdir()
        declaration = CONFIG + '\ndocumentation:\n  manual: ' + json.dumps(SETTINGS) + '\n'
        (self.root / 'studio.yaml').write_text(declaration)
        self.config = load_config(self.root / 'studio.yaml')
        self.github = GitHubFixture()
        self.mintlify = MintlifyFixture()
    def inspect(self):
        return documentation.inspect(self.config, 'manual', SHA, 'update-1', github=self.github, mintlify=self.mintlify)

    def test_success_records_exact_source_checks_and_external_publisher_identity(self):
        result = self.inspect()
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['source_sha'], SHA)
        self.assertEqual(result['deployment_id'], 'update-1')
        self.assertEqual(result['project_id'], 'provider-project-1')
        self.assertEqual(result['checks'], [{'name': 'Docs validation', 'id': 34, 'status': 'passed', 'url': CHECK['details_url']}])
        self.assertEqual(result['public_url'], 'https://example.com/docs')
        self.assertEqual(result['provider_status'], 'success')
        for forbidden in ('artifact_sha256', 'candidate_id', 'logs', 'summary', 'author'):
            self.assertNotIn(forbidden, result)
        self.assertNotIn('internal log', json.dumps(result))

    def test_provider_pending_and_failure_remain_visible(self):
        for status, state in [('queued', 'provider-pending'), ('in_progress', 'provider-pending'), ('failure', 'failed')]:
            with self.subTest(status=status):
                self.mintlify.update['status'] = status
                self.assertEqual(self.inspect()['state'], state)
                self.assertEqual(self.inspect()['provider_status'], status)

    def test_wrong_project_deployment_source_branch_or_subdomain_is_rejected(self):
        mutations = [('_id', 'other'), ('projectId', 'other'), ('subdomain', 'other'),
            ('commit', {'sha': 'c' * 40, 'ref': 'refs/heads/main'}),
            ('commit', {'sha': SHA, 'ref': 'refs/heads/preview'}), ('commit', None), ('status', 'unknown')]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                self.mintlify.update = copy.deepcopy(UPDATE)
                self.mintlify.update[field] = value
                with self.assertRaises(ValueError): self.inspect()

    def test_required_checks_cannot_be_skipped_failed_missing_or_from_another_source(self):
        for checks in [[], [{**CHECK, 'conclusion': 'skipped'}], [{**CHECK, 'conclusion': 'failure'}],
            [{**CHECK, 'status': 'in_progress'}], [{**CHECK, 'head_sha': 'c' * 40}],
            [{**CHECK, 'app': {'slug': 'untrusted-app'}}], [CHECK, {**CHECK, 'id': 35}]]:
            with self.subTest(checks=checks):
                self.github.checks = checks
                with self.assertRaises(ValueError): self.inspect()

    def test_all_pages_of_required_check_results_are_consumed(self):
        original = self.github.request
        def request(path):
            if '/check-runs?' in path: return [{'check_runs': [], 'total_count': 1}, {'check_runs': [CHECK], 'total_count': 1}]
            return original(path)
        self.github.request = request
        self.assertEqual(self.inspect()['state'], 'completed')

    def test_source_must_belong_to_the_configured_deployment_branch(self):
        for status in ('behind', 'diverged'):
            self.github.ancestry = status
            with self.assertRaises(ValueError): self.inspect()
        self.github.ancestry = 'identical'
        self.assertEqual(self.inspect()['state'], 'completed')

    def test_invalid_binding_or_traversal_fails_before_provider_access(self):
        for key, value in [('path', '../outside'), ('path', '/tmp'), ('project_id', ''), ('branch', '../main'),
            ('required_checks', []), ('required_checks', ['Docs validation', 'Docs validation']),
            ('upstream', 'https://example.com'), ('upstream', 'https://manual.mintlify.site/path'),
            ('upstream', 'http://manual.mintlify.site'), ('public_url', 'https://user:password@example.com/docs')]:
            with self.subTest(key=key, value=value):
                self.config.data['documentation']['manual'] = {**SETTINGS, key: value}
                with self.assertRaises(ValueError): self.inspect()
        self.config.data['documentation']['manual'] = copy.deepcopy(SETTINGS)
        for source, identity in [('main', 'update-1'), (SHA, '../update-1'), (SHA, 'a?token=value')]:
            with self.subTest(source=source, identity=identity):
                with self.assertRaises(ValueError):
                    documentation.inspect(self.config, 'manual', source, identity, github=self.github, mintlify=self.mintlify)

    def test_observation_history_preserves_pending_failed_and_completed_results(self):
        for status in ('queued', 'failure', 'success'):
            self.mintlify.update['status'] = status
            result = self.inspect()
            path = documentation.record(self.config, result)
        stored = json.loads(path.read_text())
        self.assertEqual([entry['provider_status'] for entry in stored['observations']], ['queued', 'failure', 'success'])
        self.assertEqual(stored['state'], 'completed')
        self.assertTrue(path.is_relative_to(self.root.resolve() / '.studio' / 'documentation'))
        self.assertNotIn('internal log', path.read_text())

    def test_existing_record_with_a_different_identity_is_not_overwritten(self):
        result = self.inspect()
        path = documentation.record(self.config, result)
        previous = json.loads(path.read_text())
        previous['source_sha'] = 'c' * 40
        path.write_text(json.dumps(previous))
        with self.assertRaises(ValueError): documentation.record(self.config, result)
        self.assertEqual(json.loads(path.read_text())['source_sha'], 'c' * 40)

    def test_symlinked_record_directory_cannot_escape_repository(self):
        with tempfile.TemporaryDirectory() as external:
            (self.root / '.studio').symlink_to(external, target_is_directory=True)
            with self.assertRaises(ValueError): documentation.record(self.config, self.inspect())
            self.assertEqual(list(Path(external).iterdir()), [])

    def test_cli_records_provider_state_and_returns_failure_until_success(self):
        from studio.__main__ import main
        args = ['--root', str(self.root), 'docs', 'inspect', '--project', 'manual',
            '--source-sha', SHA, '--deployment-id', 'update-1']
        with patch.object(documentation, 'GitHubClient', return_value=self.github), patch.object(documentation, 'MintlifyClient', return_value=self.mintlify):
            for status, code in [('queued', 2), ('failure', 1), ('success', 0)]:
                self.mintlify.update['status'] = status
                output = io.StringIO()
                with redirect_stdout(output): self.assertEqual(main(args), code)
                result = json.loads(output.getvalue())
                self.assertTrue(Path(result['record']).is_file())
                self.assertEqual(result['provider_status'], status)

    def test_mintlify_client_uses_read_only_fixed_api_host_without_redirects(self):
        from urllib.error import HTTPError
        with patch.dict(os.environ, {'MINTLIFY_API_KEY': 'secret-never-print'}):
            client = documentation.MintlifyClient()
            class Reply:
                def __enter__(self): return io.BytesIO(json.dumps(UPDATE).encode())
                def __exit__(self, *args): pass
            with patch('studio.documentation.build_opener') as factory:
                factory.return_value.open.return_value = Reply()
                self.assertEqual(client.deployment('update-1')['_id'], 'update-1')
                request = factory.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, 'https://api.mintlify.com/v1/project/update-status/update-1')
                self.assertEqual(request.get_method(), 'GET')
                self.assertEqual(request.get_header('Authorization'), 'Bearer secret-never-print')
                factory.return_value.open.side_effect = HTTPError(request.full_url, 403, 'secret-never-print', {}, None)
                with self.assertRaisesRegex(ValueError, 'HTTP 403') as error: client.deployment('update-1')
                self.assertNotIn('secret-never-print', str(error.exception))
            redirect = documentation.NoRedirect()
            with self.assertRaises(ValueError): redirect.redirect_request(None, None, 302, 'Moved', {}, 'https://other.example')

    def test_github_client_runs_paginated_read_only_lookup_and_redacts_errors(self):
        import sys
        path = f'repos/studio/example/commits/{SHA}/check-runs?filter=latest&per_page=100'
        executable = self.root / 'gh'
        response = [{'check_runs': [CHECK], 'total_count': 1}]
        executable.write_text('#!' + sys.executable + '\nimport json,sys\n' +
            'if sys.argv[1:] != ' + repr(['api', path, '--paginate', '--slurp']) + ': sys.exit(9)\n' +
            'print(' + repr(json.dumps(response)) + ')\n')
        executable.chmod(0o755)
        with patch.dict(os.environ, {'PATH': str(self.root) + os.pathsep + os.environ['PATH']}):
            self.assertEqual(documentation.GitHubClient().request(path), response)
            executable.write_text('#!' + sys.executable + '\nimport sys\nprint("private-provider-error",file=sys.stderr)\nsys.exit(1)\n')
            with self.assertRaises(ValueError) as error: documentation.GitHubClient().request(path)
            self.assertNotIn('private-provider-error', str(error.exception))

    def test_malformed_admin_key_is_rejected_without_echoing_or_transmitting_it(self):
        class Reply:
            def __enter__(self): return io.BytesIO(json.dumps(UPDATE).encode())
            def __exit__(self, *args): pass
        for value in ('private\ncredential', 'private credential', 'private\rcredential'):
            with self.subTest(value=value), patch.dict(os.environ, {'MINTLIFY_API_KEY': value}), patch('studio.documentation.build_opener') as factory:
                factory.return_value.open.return_value = Reply()
                with self.assertRaises(ValueError) as error: documentation.MintlifyClient().deployment('update-1')
                self.assertNotIn('private', str(error.exception))
                self.assertNotIn('credential', str(error.exception))

    def test_missing_admin_key_reports_entitlement_and_env_name_without_value(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'MINTLIFY_API_KEY'):
                documentation.MintlifyClient().deployment('update-1')
