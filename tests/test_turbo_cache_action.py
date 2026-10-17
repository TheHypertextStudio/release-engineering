"""The shared cache action validates inputs and forwards only ephemeral native auth."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class TurboCacheActionTests(unittest.TestCase):
    def setUp(self):
        self.action = yaml.load((ROOT / 'actions/turbo-cache/action.yml').read_text(), Loader=yaml.BaseLoader)
        self.steps = self.action['runs']['steps']

    def test_only_trusted_studio_main_callers_reach_google_auth(self):
        validate = self.steps[0]
        for condition in ("github.repository_owner == 'TheHypertextStudio'", "github.ref == 'refs/heads/main'", "github.event_name == 'push'", "github.event_name == 'workflow_dispatch'", "format('{0}/.github/workflows/', github.repository)", "endsWith(github.workflow_ref, '@refs/heads/main')", "env.TURBO_REMOTE_CACHE_SIGNATURE_KEY != ''"):
            self.assertIn(condition, validate['if'])
        for name in self.action['inputs']:
            self.assertIn('inputs.' + name + " != ''", validate['if'])
        self.assertEqual("steps.validate.outputs.enabled == 'true'", self.steps[1]['if'])

    def test_google_action_is_pinned_and_never_creates_a_credential_file(self):
        auth = self.steps[1]
        self.assertEqual('google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093', auth['uses'])
        self.assertEqual('id_token', auth['with']['token_format'])
        self.assertEqual('${{ inputs.api }}', auth['with']['id_token_audience'])
        self.assertEqual('false', auth['with']['create_credentials_file'])

    def test_input_validation_rejects_invalid_values_before_emitting_enablement(self):
        good = {'CACHE_API': 'https://turbo-cache.hypertext.studio', 'CACHE_TEAM': 'logdate-web',
                'CACHE_SERVICE_ACCOUNT': 'studio-cache-logdate@athena-services.iam.gserviceaccount.com',
                'CACHE_WIF_PROVIDER': 'projects/123/locations/global/workloadIdentityPools/studio-cache/providers/logdate'}
        cases = [({}, True), ({'CACHE_API': 'http://other.example'}, False),
                 ({'CACHE_API': 'https://other.example\nINJECT=value'}, False),
                 ({'CACHE_TEAM': 'other/repo'}, False), ({'CACHE_SERVICE_ACCOUNT': 'owner@example.com'}, False),
                 ({'CACHE_WIF_PROVIDER': 'other'}, False)]
        for changed, valid in cases:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'output'
                result = subprocess.run(['bash', '-euo', 'pipefail', '-c', self.steps[0]['run']],
                    env={**os.environ, **good, **changed, 'GITHUB_OUTPUT': str(output)}, capture_output=True, text=True)
                self.assertEqual(valid, result.returncode == 0)
                self.assertEqual('enabled=true\n' if valid else '', output.read_text() if output.exists() else '')

    def test_success_forwards_masked_short_lived_token_to_turbo_only(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'env'
            env = {**os.environ, 'CACHE_TOKEN': 'disposable-fixture-token', 'CACHE_API': 'https://turbo-cache.hypertext.studio',
                   'CACHE_TEAM': 'curfew', 'GITHUB_ENV': str(output)}
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', self.steps[2]['run']], env=env, capture_output=True, text=True, check=True)
            self.assertIn('::add-mask::disposable-fixture-token', result.stdout)
            self.assertEqual(['TURBO_TOKEN=disposable-fixture-token', 'TURBO_API=https://turbo-cache.hypertext.studio',
                              'TURBO_TEAM=curfew', 'TURBO_CACHE=local:rw,remote:rw'], output.read_text().splitlines())
