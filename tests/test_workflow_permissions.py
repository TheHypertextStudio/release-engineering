from pathlib import Path
import unittest
import yaml


class WorkflowPermissionsTests(unittest.TestCase):
    def test_framework_workerd_acceptance_runs_without_provider_credentials(self):
        root = Path(__file__).resolve().parents[1] / '.github/workflows'
        workflow = yaml.load((root / 'ci.yml').read_text(), Loader=yaml.BaseLoader)
        job = workflow['jobs'].get('framework-runtime')
        self.assertIsNotNone(job, 'Real framework/workerd acceptance must run in hosted CI')
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        install = next(step for step in job['steps'] if step.get('run') == 'pnpm install --frozen-lockfile')
        self.assertEqual(install['working-directory'], 'tests/fixtures/websites')
        acceptance = next(step for step in job['steps'] if 'test_workers_framework_runtime.py' in step.get('run', ''))
        self.assertEqual(acceptance['env']['STUDIO_FRAMEWORK_WORKERS_TESTS'], '1')
        self.assertEqual(acceptance['env']['WRANGLER_SEND_METRICS'], 'false')
        self.assertNotIn('secrets.', str(job))

    def test_package_write_is_available_only_to_production_promotion(self):
        root = Path(__file__).resolve().parents[1] / '.github/workflows'
        promotion = yaml.load((root / 'promote.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(promotion['permissions']['packages'], 'write')
        self.assertEqual(promotion['jobs']['promote']['environment'], 'production')
        self.assertEqual(promotion['jobs']['promote']['if'], "github.event_name == 'workflow_dispatch'")
        for name in ['candidate.yml', 'validate.yml']:
            workflow = yaml.load((root / name).read_text(), Loader=yaml.BaseLoader)
            self.assertEqual(workflow['permissions']['packages'], 'read')

    def test_reusable_lifecycle_workflows_bind_the_selected_root_before_secrets(self):
        root = Path(__file__).resolve().parents[1] / '.github/workflows'
        for name in ('candidate.yml', 'promote.yml'):
            workflow = yaml.load((root / name).read_text(), Loader=yaml.BaseLoader)
            inputs = workflow['on']['workflow_call']['inputs']
            self.assertEqual(inputs['product_root']['default'], '.')
            steps = workflow['jobs']['candidate' if name == 'candidate.yml' else 'promote']['steps']
            resolver = next(i for i, step in enumerate(steps) if step.get('id') in {'product-root', 'dispatch-root'})
            credential_steps = [i for i, step in enumerate(steps) if 'secrets.' in str(step.get('env', {}))]
            self.assertTrue(credential_steps)
            self.assertLess(resolver, min(credential_steps))
        candidate = yaml.load((root / 'candidate.yml').read_text(), Loader=yaml.BaseLoader)
        promote = yaml.load((root / 'promote.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertIn('$GITHUB_WORKSPACE/.studio/candidate', str(candidate))
        self.assertIn('$GITHUB_WORKSPACE/.studio/candidate', str(promote))
