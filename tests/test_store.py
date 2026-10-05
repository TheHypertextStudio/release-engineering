import base64
import hashlib
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
from studio import store
from studio.candidate import write_candidate
from test_macos import fixture, NativeRunner


def candidate(root):
    (root / 'app.pkg').write_bytes(b'package')
    return write_candidate(root / 'candidate.json', dict(schema=1,id='candidate-42',product='app',repository='studio/app',source_sha='a'*40,workflow_run_id=42,workflow_run_attempt=1,tooling_revision='b'*40,version='1.2.3',build_number=42,artifacts=[dict(component='app',channel='app-store',path='app.pkg',sha256=hashlib.sha256(b'package').hexdigest(),size=7)],checks={'app':'passed'},prerequisites={'store-signed':'passed'},compatibility={}))


class AppleAPI:
    def __init__(self, state='PREPARE_FOR_SUBMISSION', build='42'):
        self.state, self.build, self.calls = state, build, []

    def request(self, method, path, data=None):
        self.calls.append((method,path,data))
        if path.startswith('/v1/builds?'):
            return {'data':[{'id':'build-id','attributes':{'version':self.build,'processingState':'VALID'},'relationships':{'preReleaseVersion':{'data':{'id':'pre-id'}}}}], 'included':[{'id':'pre-id','type':'preReleaseVersions','attributes':{'version':'1.2.3','platform':'MAC_OS'}}]}
        if '/appStoreVersions?' in path:
            return {'data':[{'id':'version-id','attributes':{'versionString':'1.2.3','platform':'MAC_OS','appStoreState':self.state}}]}
        if path.endswith('/build') and method == 'GET':
            return {'data':{'id':'build-id','attributes':{'version':self.build}}}
        if path.startswith('/v1/reviewSubmissions?'):
            return {'data':[]}
        if path == '/v1/reviewSubmissions' and method == 'POST':
            return {'data':{'id':'review-id','attributes':{'state':'READY_FOR_REVIEW'}}}
        return {'data':{'id':'item-id'}}


class StoreTests(unittest.TestCase):
    def test_store_rejects_direct_only_curfew_and_unsigned_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, component = fixture(root)
            config.data['product'] = 'curfew'
            with self.assertRaisesRegex(store.StoreError, 'direct-only'):
                store.preflight(config, component)
            config.data['product'] = 'app'
            config.data['release']['channels'] = ['app-store']
            component['macos']['store_entitlements'] = 'app.entitlements'
            with self.assertRaisesRegex(store.StoreError, 'app-sandbox'):
                store.preflight(config, component)

    def test_submit_requires_digest_authorization_and_exact_apple_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, component = fixture(root)
            config.data.update(repository='studio/app', owner_repository='studio/app')
            config.data['release']['channels'] = ['app-store']
            config.data['release']['macos']['store'] = {'app_id':'123'}
            artifact = candidate(root)
            with self.assertRaisesRegex(store.StoreError, 'authorized'):
                store.submit(config, artifact, component, authorized_digest='0'*64, client=AppleAPI())
            apple = AppleAPI(build='41')
            with self.assertRaisesRegex(store.StoreError, 'build'):
                store.submit(config, artifact, component, authorized_digest=artifact.digest, client=apple)
            self.assertFalse(any(c[0] in ('POST','PATCH') for c in apple.calls))

    def test_submit_enters_review_and_reconcile_requires_same_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, component = fixture(root)
            config.data.update(repository='studio/app', owner_repository='studio/app')
            config.data['release']['channels'] = ['app-store']
            config.data['release']['macos']['store'] = {'app_id':'123'}
            artifact = candidate(root)
            apple = AppleAPI()
            result = store.submit(config, artifact, component, authorized_digest=artifact.digest, client=apple)
            self.assertEqual(result.get('state'), 'apple-pending')
            self.assertTrue(any(c[0]=='PATCH' and c[1]=='/v1/reviewSubmissions/review-id' and c[2]['data']['attributes']['submitted'] for c in apple.calls))
            result = store.reconcile(config, artifact, component, authorized_digest=artifact.digest, client=AppleAPI(state='READY_FOR_DISTRIBUTION'))
            self.assertEqual(result.get('state'), 'completed')
            self.assertEqual(result['manifest_sha256'], artifact.digest)
            with self.assertRaisesRegex(store.StoreError, 'build'):
                store.reconcile(config, artifact, component, authorized_digest=artifact.digest, client=AppleAPI(state='READY_FOR_DISTRIBUTION',build='41'))

    def test_unprocessed_build_preserves_pending(self):
        class PendingAPI(AppleAPI):
            def request(self, method, path, data=None):
                result = super().request(method,path,data)
                if path.startswith('/v1/builds?'):
                    result['data'][0]['attributes']['processingState'] = 'PROCESSING'
                return result
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, component = fixture(root)
            config.data.update(repository='studio/app', owner_repository='studio/app')
            config.data['release']['channels'] = ['app-store']
            config.data['release']['macos']['store'] = {'app_id':'123'}
            artifact = candidate(root)
            api = PendingAPI()
            result = store.submit(config,artifact,component,authorized_digest=artifact.digest,client=api)
            self.assertEqual(result.get('state'),'apple-pending')
            self.assertFalse(any(c[0] in ('POST','PATCH') for c in api.calls))

    def test_jwt_uses_real_openssl_es256_signature_without_python_crypto(self):
        import json
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory)/'key.p8'
            subprocess.run(['openssl','genpkey','-algorithm','EC','-pkeyopt','ec_paramgen_curve:P-256','-out',str(key)],check=True,capture_output=True)
            with patch.dict(os.environ,{'APPLE_API_PRIVATE_KEY':key.read_text(),'APPLE_API_KEY_ID':'ABC123','APPLE_API_ISSUER':'issuer'}):
                token = store.jwt(now=1000)
            header,payload,signature = token.split('.')
            decode = lambda text: base64.urlsafe_b64decode(text+'='*((4-len(text)%4)%4))
            self.assertEqual(json.loads(decode(header))['alg'],'ES256')
            self.assertEqual(json.loads(decode(payload))['exp'],1600)
            raw = decode(signature)
            self.assertEqual(len(raw),64)
            integers = []
            for half in (raw[:32],raw[32:]):
                half = half.lstrip(b'\x00') or b'\x00'
                if half[0]&128:
                    half=b'\x00'+half
                integers.append(b'\x02'+bytes([len(half)])+half)
            content=b''.join(integers)
            sig = Path(directory)/'signature.der'
            sig.write_bytes(b'\x30'+bytes([len(content)])+content)
            public = Path(directory)/'public.pem'
            subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(public)],check=True,capture_output=True)
            result = subprocess.run(['openssl','dgst','-sha256','-verify',str(public),'-signature',str(sig)],input=f'{header}.{payload}'.encode(),capture_output=True)
            self.assertEqual(result.returncode,0)

    def test_store_build_has_no_notarization_or_sparkle(self):
        class StoreRunner(NativeRunner):
            def __call__(self,command,cwd,**kwargs):
                result = super().__call__(command,cwd,**kwargs)
                if command[:2]==['security','find-identity']:
                    return 'Apple Distribution: Studio (TEAM)'
                if '-exportArchive' in command:
                    app = Path(command[command.index('-exportPath')+1])/'App.app'
                    path=app/'Contents'/'Info.plist'
                    info=plistlib.loads(path.read_bytes())
                    info.pop('SUFeedURL');info.pop('SUPublicEDKey')
                    path.write_bytes(plistlib.dumps(info))
                if command[:2]==['codesign','-d']:
                    return plistlib.dumps({'com.apple.security.app-sandbox':True}).decode()
                if command[0]=='productbuild':
                    Path(command[-1]).write_bytes(b'pkg')
                return result
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config,component=fixture(root)
            config.data['release']['channels']=['app-store']
            config.data['release']['macos']['store']={'app_id':'123','application_identity':'Apple Distribution: Studio (TEAM)','installer_identity':'3rd Party Mac Developer Installer: Studio (TEAM)'}
            (root/'store.entitlements').write_bytes(plistlib.dumps({'com.apple.security.app-sandbox':True}))
            component['macos'].update(store_entitlements='store.entitlements',store_provisioning_profiles={'dev.williecubed.app':'profile'})
            runner=StoreRunner(root,config)
            with patch.dict(os.environ,{'APPLE_API_PRIVATE_KEY':'pem','APPLE_API_KEY_ID':'ABC','APPLE_API_ISSUER':'issuer'}):
                artifacts=store.prepare(config,component,root/'out','1.2.3',42,runner=runner)
            self.assertEqual([p.suffix for p in artifacts],['.pkg'])
            self.assertFalse(any('notarytool' in c or c[0].endswith('/sign_update') for c in runner.calls))
            archive=next(c for c in runner.calls if c[0]=='xcodebuild' and 'archive' in c)
            self.assertIn('AppStore',archive)

    def test_rejected_version_is_not_resubmitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config,component=fixture(root)
            config.data.update(repository='studio/app',owner_repository='studio/app')
            config.data['release']['channels']=['app-store']
            config.data['release']['macos']['store']={'app_id':'123'}
            artifact=candidate(root)
            api=AppleAPI(state='REJECTED')
            result=store.submit(config,artifact,component,authorized_digest=artifact.digest,client=api)
            self.assertEqual(result.get('state'),'failed')
            self.assertFalse(any(c[0] in ('POST','PATCH') for c in api.calls))

    def test_manual_release_rechecks_authorization_before_apple_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config,component=fixture(root)
            config.data.update(repository='studio/app',owner_repository='studio/app')
            config.data['release']['channels']=['app-store']
            config.data['release']['macos']['store']={'app_id':'123'}
            artifact=candidate(root)
            api=AppleAPI(state='PENDING_DEVELOPER_RELEASE')
            result=store.reconcile(config,artifact,component,authorized_digest=artifact.digest,client=api)
            self.assertEqual(result.get('state'),'apple-pending')
            self.assertFalse(any(c[0]=='POST' for c in api.calls))

    def test_manual_release_uses_fresh_callback_and_keeps_apple_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config,component=fixture(root)
            config.data.update(repository='studio/app',owner_repository='studio/app')
            config.data['release']['channels']=['app-store']
            config.data['release']['macos']['store']={'app_id':'123'}
            artifact=candidate(root)
            api=AppleAPI(state='PENDING_DEVELOPER_RELEASE')
            checks=[]
            result=store.reconcile(config,artifact,component,authorized_digest=artifact.digest,client=api,authorize=lambda current: checks.append(current.digest))
            self.assertEqual(checks,[artifact.digest])
            self.assertEqual(result['state'],'apple-pending')
            self.assertTrue(any(c[0]=='POST' and c[1]=='/v1/appStoreVersionReleaseRequests' for c in api.calls))
            denied=AppleAPI(state='PENDING_DEVELOPER_RELEASE')
            with self.assertRaisesRegex(store.StoreError,'revoked'):
                store.reconcile(config,artifact,component,authorized_digest=artifact.digest,client=denied,authorize=lambda current:False)
            self.assertFalse(any(c[0]=='POST' for c in denied.calls))

    def test_upload_resumes_existing_processed_build_without_native_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config,component=fixture(root)
            config.data.update(repository='studio/app',owner_repository='studio/app')
            config.data['release']['channels']=['app-store']
            config.data['release']['macos']['store']={'app_id':'123'}
            artifact=candidate(root)
            native=[]
            result=store.upload(config,artifact,component,authorized_digest=artifact.digest,client=AppleAPI(),runner=lambda *args,**kwargs:native.append(args))
            self.assertEqual(result['state'],'completed')
            self.assertTrue(result['already_present'])
            self.assertEqual(native,[])
