import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.providers import deploy_cloud_run, deploy_worker, deploy_site

class DeploymentBehaviorTests(unittest.TestCase):
    def test_image_and_health_match_before_traffic_switch(self):
        calls=[]
        digest='registry/api@sha256:'+'a'*64
        component={'id':'api','deploy':{'project':'project','region':'region','service':'api'}}
        def run(command,**kwargs):
            calls.append(command)
            if 'services' in command and 'describe' in command:
                return json.dumps({'status':{'traffic':[{'tag':'studio-review','revisionName':'api-42','url':'https://preview.example'}]}})
            if 'revisions' in command:
                return json.dumps({'status':{'imageDigest':digest}})
        with patch('studio.providers.probe') as probe:
            result=deploy_cloud_run(component,{'image':digest,'revision':'api-42','source_sha':'b'*40},'production',runner=run)
            self.assertEqual(result['state'],'completed')
            self.assertEqual(probe.call_args.kwargs['expected_sha'],'b'*40)
        with patch('studio.providers.probe',side_effect=RuntimeError('bad health')):
            calls.clear()
            with self.assertRaises(RuntimeError): deploy_cloud_run(component,{'image':digest,'revision':'api-42'},'production',runner=run)
            self.assertFalse(any('update-traffic' in call for call in calls))
    def test_worker_preserves_toml_and_does_not_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'wrangler.toml').write_text('name="worker"')
            (root/'index.js').write_text('export default {}')
            component={'id':'sync','path':'.','deploy':{'config':'wrangler.toml','entrypoint':'index.js','environments':{'production':'default'},'health_urls':{'production':'https://worker.example/health'}}}
            calls=[]
            with patch('studio.providers.probe'):
                deploy_worker(component,{'source_sha':'a'*40,'sha256':'b'*64},'production',root,root,'4.120.1',runner=lambda command,**kw:calls.append(command))
            command=calls[0]
            self.assertIn('--no-bundle',command)
            self.assertNotIn('--env',command)
            self.assertTrue(command[command.index('--config')+1].endswith('.toml'))
    def test_site_rejects_missing_staging_project_before_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError): deploy_site({'id':'site','deploy':{'provider':'pages','project':'prod'}},{},'staging',Path(directory),{},runner=lambda *a,**kw:self.fail('must not deploy'))
