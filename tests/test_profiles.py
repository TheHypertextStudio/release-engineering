import tempfile
import unittest
from pathlib import Path
from studio.lifecycle import native_command
from studio.providers import worker_plan
from studio.bindings import render

class ProfileTests(unittest.TestCase):
    def test_worker_build_is_native_and_has_explicit_output(self):
        command=native_command({'kind':'cloudflare-worker','deploy':{'config':'wrangler.toml','environments':{'staging':'preview'}},'build':{'profile':'cloudflare-worker','environment':'staging','output':'dist'}},'build')
        self.assertIn('--dry-run',command)
        self.assertIn('--outdir',command)
        self.assertIn('preview',command)
    def test_default_worker_environment_does_not_select_named_environment(self):
        command=worker_plan({'id':'runner','deploy':{'config':'wrangler.toml','environments':{'production':'default'}}},{'entrypoint':'index.js'},'production')
        self.assertNotIn('--env',command)
    def test_package_build_produces_immutable_tarball(self):
        command=native_command({'kind':'pnpm','build':{'profile':'npm-package','output':'dist'}},'build')
        self.assertEqual(command,['pnpm','pack','--pack-destination','dist'])
    def test_binding_renderer_resolves_facts_and_blocks_unknown_expressions(self):
        self.assertEqual(render('${{ vars.API_URL }}/health',{'API_URL':'https://api.example'},'a'*40),'https://api.example/health')
        self.assertEqual(render("${{ vars.MISSING || 'false' }}",{},'a'*40),'false')
        with self.assertRaises(ValueError): render('${{ secrets.TOKEN }}',{},'a'*40)
        with self.assertRaises(ValueError): render('${{ vars.MISSING }}',{},'a'*40)
