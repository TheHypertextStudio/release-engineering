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

    def test_native_cache_acceptance_runs_with_installed_framework_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.load((root / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
        steps = workflow['jobs']['framework-runtime']['steps']
        install = next(i for i, step in enumerate(steps) if step.get('run') == 'pnpm install --frozen-lockfile')
        cache = next((i for i, step in enumerate(steps) if 'test_opennext_cache.py' in step.get('run', '')), None)
        self.assertIsNotNone(cache, 'Native R2 acceptance requires the installed OpenNext/Wrangler fixture')
        self.assertLess(install, cache)
        self.assertEqual(steps[cache]['env']['STUDIO_FRAMEWORK_WORKERS_TESTS'], '1')
        import os
        import runpy
        from unittest.mock import patch
        with patch.dict(os.environ, {'STUDIO_NATIVE_WORKERS_TESTS': '1', 'STUDIO_FRAMEWORK_WORKERS_TESTS': '0'}):
            namespace = runpy.run_path(str(root / 'tests/test_opennext_cache.py'))
        self.assertTrue(namespace['NativeOpenNextCacheTests'].__unittest_skip__,
                        'Native-only jobs do not install the framework fixture')

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

    def test_candidate_prepares_private_registry_and_signing_before_assembly(self):
        workflow = yaml.load((Path(__file__).resolve().parents[1] / '.github/workflows/candidate.yml').read_text(), Loader=yaml.BaseLoader)
        steps = workflow['jobs']['candidate']['steps']
        toolchain = next(step for step in steps if step.get('name') == 'Prepare the native toolchain')
        self.assertEqual(toolchain['env']['NODE_AUTH_TOKEN'], '${{ github.token }}')
        prepare = next(step for step in steps if step.get('name') == 'Prepare the declared credential bindings')
        assembly = next(step for step in steps if step.get('name') == 'Assemble and stage the immutable candidate')
        for variable in ('APPLE_CERTIFICATE_BASE64','APPLE_CERTIFICATE_PASSWORD','APPLE_API_PRIVATE_KEY',
                         'APPLE_API_KEY_ID','APPLE_API_ISSUER','APPLE_PROVISIONING_PROFILES_BASE64',
                         'SPARKLE_PRIVATE_KEY','VERCEL_TOKEN','CLOUDFLARE_API_TOKEN'):
            self.assertEqual(prepare['env'][variable], '${{ secrets.' + variable + ' }}')
            self.assertNotIn(variable, assembly['env'], 'Fetched product credentials must survive into assembly')
        diagnostics = next(step for step in steps if step.get('name') == 'Record release prerequisites')
        self.assertNotIn('secrets.', str(diagnostics.get('env', {})))

    def test_dispatcher_preflight_precedes_legacy_runtime_install_and_credentials(self):
        workflow = yaml.load((Path(__file__).resolve().parents[1] / '.github/workflows/promote.yml').read_text(), Loader=yaml.BaseLoader)
        steps = workflow['jobs']['promote']['steps']
        preflight = next(i for i, step in enumerate(steps) if step.get('name') == 'Validate reviewed policy and toolchain before credentials')
        restore = next(i for i, step in enumerate(steps) if step.get('name') == 'Restore the candidate implementation')
        install = next(i for i, step in enumerate(steps) if step.get('name') == 'Install the exact candidate runtime')
        credentials = next(i for i, step in enumerate(steps) if step.get('name') == 'Prepare the declared credential bindings')
        self.assertLess(restore, preflight)
        self.assertLess(preflight, credentials)
        self.assertLess(credentials, install)
        self.assertIn('python -I -m studio.ci_setup', steps[preflight]['run'])
        for step in steps[install+1:]:
            self.assertNotIn('studio.product_root', step.get('run', ''))
            self.assertNotIn('--validate-only', step.get('run', ''))
            self.assertNotIn('secrets.', str(step.get('env', {})))

    def test_every_lifecycle_workflow_selects_node_from_validated_policy(self):
        for name, job in (('candidate.yml','candidate'),('promote.yml','promote'),('validate.yml','validate')):
            workflow = yaml.load((Path(__file__).resolve().parents[1] / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)
            steps = workflow['jobs'][job]['steps']
            node_index = next(i for i, step in enumerate(steps) if 'actions/setup-node@' in step.get('uses',''))
            node = steps[node_index]
            self.assertIn('steps.', node['with']['node-version'])
            self.assertIn('.outputs.node', node['with']['node-version'])
            selector_id = node['with']['node-version'].split('steps.')[1].split('.outputs')[0]
            selector_index = next(i for i, step in enumerate(steps) if step.get('id') == selector_id)
            self.assertLess(selector_index, node_index)
            self.assertIn('--validate-only', steps[selector_index]['run'])
