import json
from pathlib import Path
import tempfile
import unittest

from studio.bindings import snapshot
from studio.config import Config


class BindingSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / 'candidate'
        self.output.mkdir()
        self.component = {'id': 'package', 'path': '.', 'kind': 'pnpm'}
        self.calls = []

    def config(self):
        return Config(self.root, {'repository': 'Studio/packages', 'components': [self.component]})

    def unavailable_api(self, route):
        self.calls.append(route)
        raise PermissionError('configuration variables are unavailable')

    def api(self, route):
        self.calls.append(route)
        return {'variables': [{'name': 'SERVICE', 'value': 'staging-service' if '/environments/' not in route else 'production-service'}]}

    def template(self, contents):
        (self.root / 'environment.yaml').write_text(contents)
        self.component['deploy'] = {'environments': {'production': {'env_file_template': 'environment.yaml'}}}

    def test_package_without_variable_expressions_never_reads_provider_variables(self):
        resolved, files = snapshot(self.config(), self.output, 'abc123', self.unavailable_api)
        self.assertEqual(self.calls, [])
        self.assertEqual(resolved, [self.component])
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text())['components'], resolved)

    def test_source_sha_expression_does_not_require_variable_api(self):
        self.component['source'] = '${{ github.sha }}'
        resolved, _ = snapshot(self.config(), self.output, 'abc123', self.unavailable_api)
        self.assertEqual(resolved[0]['source'], 'abc123')
        self.assertEqual(self.calls, [])

    def test_literal_template_does_not_require_variable_api(self):
        self.template('SERVICE: fixed-service\nSOURCE: ${{ github.sha }}\n')
        resolved, files = snapshot(self.config(), self.output, 'abc123', self.unavailable_api)
        self.assertEqual(self.calls, [])
        self.assertEqual(resolved[0]['deploy']['environments']['production']['env_file'], 'bindings/package/production.env.yaml')
        self.assertIn('SOURCE: abc123', files[0].read_text())
        self.assertIn('STUDIO_SOURCE_SHA: abc123', files[0].read_text())

    def test_template_variable_loads_repository_and_production_overrides(self):
        self.template('SERVICE: ${{ vars.SERVICE }}\n')
        _, files = snapshot(self.config(), self.output, 'abc123', self.api)
        self.assertEqual(len(self.calls), 2)
        self.assertIn('SERVICE: production-service', files[0].read_text())

    def test_component_variable_still_requires_provider_access(self):
        self.component['deploy'] = {'service': '${{ vars.SERVICE }}'}
        with self.assertRaises(PermissionError):
            snapshot(self.config(), self.output, 'abc123', self.unavailable_api)
        self.assertEqual(len(self.calls), 1)

    def test_component_variable_resolves_before_template_path(self):
        (self.root / 'production-service.yaml').write_text('SERVICE: fixed-service\n')
        self.component['deploy'] = {'environments': {'production': {'env_file_template': '${{ vars.SERVICE }}.yaml'}}}
        _, files = snapshot(self.config(), self.output, 'abc123', self.api)
        self.assertEqual(len(self.calls), 2)
        self.assertIn('SERVICE: fixed-service', files[0].read_text())

    def test_template_variable_denied_access_does_not_fall_back_silently(self):
        self.template('SERVICE: ${{ vars.SERVICE }}\n')
        with self.assertRaises(PermissionError):
            snapshot(self.config(), self.output, 'abc123', self.unavailable_api)

    def test_template_path_escape_rejected_before_api_access(self):
        self.component['deploy'] = {'environments': {'production': {'env_file_template': '../outside.yaml'}}}
        with self.assertRaisesRegex(ValueError, 'escaped repository'):
            snapshot(self.config(), self.output, 'abc123', self.unavailable_api)
        self.assertEqual(self.calls, [])
