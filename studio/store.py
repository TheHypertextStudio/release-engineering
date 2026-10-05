"""App Store packages and candidate-bound App Store Connect review lifecycle."""
import base64
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import macos
from .candidate import load_candidate, CandidateError


class StoreError(macos.MacOSError):
    pass


def _settings(config, component):
    if config.product.lower() == 'curfew':
        raise StoreError('Curfew is direct-only')
    if 'app-store' not in config['release'].get('channels', []):
        raise StoreError('App Store channel is not declared')
    policy, facts = macos._settings(config, component)
    settings = policy.get('store', {})
    if not isinstance(settings, dict):
        raise StoreError('App Store settings must be a mapping')
    return settings, facts


def _credentials():
    for name in ('APPLE_API_PRIVATE_KEY', 'APPLE_API_KEY_ID', 'APPLE_API_ISSUER'):
        if not os.environ.get(name):
            raise StoreError(f'{name} is missing')
    if not re.fullmatch(r'[A-Za-z0-9]+', os.environ['APPLE_API_KEY_ID']):
        raise StoreError('Invalid Apple API key identifier')


def preflight(config, component, *, runner=None):
    runner = runner or macos.execute
    settings, facts = _settings(config, component)
    try:
        macos.entitlements(config, facts.get('store_entitlements'), store=True)
    except macos.MacOSError as error:
        raise StoreError(str(error)) from error
    if not facts.get('store_provisioning_profiles'):
        raise StoreError('Store export requires explicit provisioning profiles')
    for name in ('app_id', 'application_identity', 'installer_identity'):
        if not isinstance(settings.get(name), str) or not settings[name]:
            raise StoreError(f'App Store configuration requires {name}')
    _credentials()
    identities = runner(['security', 'find-identity', '-v', '-p', 'codesigning'], config.root)
    if settings['application_identity'] not in identities:
        raise StoreError('App Store distribution identity is not installed')
    # Installer certificates are not classified as codesigning identities.
    runner(['security', 'find-certificate', '-c', settings['installer_identity'], '-p'], config.root)
    return {'store-signing':'passed','sandbox':'passed'}


def _validate_app(config, component, app, *, runner):
    if any(path.name == 'Sparkle.framework' for path in app.rglob('*')):
        raise StoreError('App Store app cannot embed Sparkle')
    info = plistlib.loads((app / 'Contents' / 'Info.plist').read_bytes())
    if any(key.startswith('SU') and key in {'SUFeedURL','SUPublicEDKey','SUEnableInstallerLauncherService','SUEnableDownloaderService'} for key in info):
        raise StoreError('App Store app cannot contain Sparkle configuration')
    runner(['codesign','--verify','--deep','--strict',str(app)],config.root)
    _, facts = _settings(config,component)
    for target in [app, *[p for p in macos._code_targets(app) if p.suffix in {'.app','.xpc','.appex'}]]:
        actual = runner(['codesign','-d','--entitlements',':-',str(target)],config.root)
        try:
            macos.validate_entitlements(plistlib.loads(actual.encode()),store=True)
        except (plistlib.InvalidFileException,macos.MacOSError) as error:
            raise StoreError(f'Exported Store executable has invalid sandbox entitlements: {target.name}') from error
        if target != app and str(target.relative_to(app)) not in facts.get('nested_entitlements',{}):
            raise StoreError('Store nested helper lacks declared entitlements')


def prepare(config, component, output, version, build_number, channel='app-store', *, runner=None):
    if channel != 'app-store':
        raise StoreError('Store adapter requires app-store channel')
    runner = runner or macos.execute
    preflight(config,component,runner=runner)
    output = Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    app = macos.archive(config,component,output / 'work',version,build_number,runner=runner,store=True)
    _validate_app(config,component,app,runner=runner)
    settings,facts = _settings(config,component)
    package = output / f'{facts["app_name"]}-{version}-{build_number}.pkg'
    runner(['productbuild','--component',str(app),'/Applications','--sign',settings['installer_identity'],str(package)],config.root)
    runner(['pkgutil','--check-signature',str(package)],config.root)
    if not package.is_file():
        raise StoreError('Store packaging did not produce a PKG')
    return [package]


def upload(config, candidate, component, *, authorized_digest, runner=None, client=None):
    package = _authorized(config,candidate,component,authorized_digest)
    _, existing, _ = _lookup(config, candidate, component, client or AppStoreClient())
    if existing is not None:
        if existing['attributes'].get('processingState') in {'VALID', 'PROCESSING'}:
            return {'state': 'completed', 'uploaded': True, 'already_present': True, 'manifest_sha256': candidate.digest}
        raise StoreError('Existing Apple build is invalid and cannot be replaced')
    _credentials()
    runner = runner or macos.execute
    with tempfile.TemporaryDirectory(prefix='studio-store-key-') as directory:
        key = Path(directory) / f'AuthKey_{os.environ["APPLE_API_KEY_ID"]}.p8'
        key.write_text(os.environ['APPLE_API_PRIVATE_KEY'])
        key.chmod(0o600)
        environment = {**os.environ,'API_PRIVATE_KEYS_DIR':directory}
        args = ['--file',str(package),'--type','macos','--apiKey',os.environ['APPLE_API_KEY_ID'],'--apiIssuer',os.environ['APPLE_API_ISSUER'],'--output-format','json']
        runner(['xcrun','altool','--validate-app',*args],config.root,env=environment)
        runner(['xcrun','altool','--upload-app',*args],config.root,env=environment)
    return {'state':'completed','uploaded':True,'manifest_sha256':candidate.digest}


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _raw_es256(der):
    """Convert OpenSSL's ASN.1 integer pair into JWT's fixed-width R || S."""
    if len(der) < 8 or der[0] != 0x30 or der[1] != len(der)-2:
        raise StoreError('OpenSSL returned invalid ES256 signature')
    offset,values = 2,[]
    for _ in range(2):
        if offset+2 > len(der) or der[offset] != 2:
            raise StoreError('OpenSSL returned invalid ES256 integer')
        length = der[offset+1]
        integer = der[offset+2:offset+2+length]
        offset += 2+length
        if not integer or integer[0]&0x80 or len(integer.lstrip(b'\x00')) > 32:
            raise StoreError('OpenSSL returned invalid ES256 integer size')
        values.append(integer.lstrip(b'\x00').rjust(32,b'\x00'))
    if offset != len(der):
        raise StoreError('OpenSSL returned trailing ES256 bytes')
    return b''.join(values)


def jwt(*, now=None):
    _credentials()
    now = int(time.time()) if now is None else now
    header = _b64(json.dumps({'alg':'ES256','kid':os.environ['APPLE_API_KEY_ID'],'typ':'JWT'},separators=(',',':')).encode())
    payload = _b64(json.dumps({'iss':os.environ['APPLE_API_ISSUER'],'iat':now,'exp':now+600,'aud':'appstoreconnect-v1'},separators=(',',':')).encode())
    signing = f'{header}.{payload}'.encode()
    with tempfile.TemporaryDirectory(prefix='studio-jwt-') as temporary:
        key = Path(temporary) / 'AuthKey.p8'
        key.write_text(os.environ['APPLE_API_PRIVATE_KEY'])
        key.chmod(0o600)
        result = subprocess.run(['openssl','dgst','-sha256','-sign',str(key)],input=signing,capture_output=True,check=False)
        if result.returncode:
            raise StoreError('OpenSSL could not sign the App Store Connect JWT')
    return f'{header}.{payload}.{_b64(_raw_es256(result.stdout))}'


class AppStoreClient:
    def request(self,method,path,data=None):
        if not path.startswith('/v1/'):
            raise StoreError('App Store API path is invalid')
        request = Request('https://api.appstoreconnect.apple.com'+path,method=method,data=json.dumps(data).encode() if data is not None else None,headers={'Authorization':'Bearer '+jwt(),'Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=60) as response:
                content = response.read()
                return json.loads(content) if content else {}
        except HTTPError as error:
            raise StoreError(f'App Store Connect request failed (HTTP {error.code})') from error
        except (URLError,json.JSONDecodeError) as error:
            raise StoreError('App Store Connect request did not return valid JSON') from error


def _authorized(config,candidate,component,digest):
    if digest != candidate.digest:
        raise StoreError('App Store action requires the authorized candidate digest')
    _settings(config,component)
    if config['repository'] != config.data.get('owner_repository'):
        raise StoreError('Only the owner repository may publish App Store candidates')
    try:
        verified = load_candidate(candidate.path,expected_digest=digest,expected_product=config.product,expected_repository=config['repository'])
    except CandidateError as error:
        raise StoreError(str(error)) from error
    if verified.data != candidate.data:
        raise StoreError('Candidate data differs from authorized manifest')
    records = [a for a in candidate.data['artifacts'] if a['component']==component['id'] and a['channel']=='app-store' and Path(a['path']).suffix=='.pkg']
    if len(records)!=1:
        raise StoreError('Authorized Store candidate requires exactly one PKG')
    return candidate.path.parent / records[0]['path']


def _lookup(config,candidate,component,client):
    settings,_ = _settings(config,component)
    app = settings.get('app_id')
    if not isinstance(app,str) or not re.fullmatch(r'\d+',app):
        raise StoreError('Store app_id must be a numeric App Store Connect identifier')
    query = urlencode({'filter[app]':app,'filter[version]':str(candidate.data['build_number']),'filter[preReleaseVersion.version]':candidate.data['version'],'include':'preReleaseVersion','limit':'200'})
    response = client.request('GET','/v1/builds?'+query)
    matching = []
    versions = {v['id']:v.get('attributes',{}) for v in response.get('included',[]) if v.get('type')=='preReleaseVersions'}
    for build in response.get('data',[]):
        attributes = build.get('attributes',{})
        prerelease = versions.get(build.get('relationships',{}).get('preReleaseVersion',{}).get('data',{}).get('id'),{})
        if attributes.get('version') == str(candidate.data['build_number']) and prerelease.get('version')==candidate.data['version'] and prerelease.get('platform')=='MAC_OS':
            matching.append(build)
    if not matching:
        if response.get('data'):
            raise StoreError('Apple build differs from authorized candidate')
        return app,None,None
    if len(matching)!=1:
        raise StoreError('Apple returned ambiguous candidate builds')
    build = matching[0]
    query = urlencode({'filter[versionString]':candidate.data['version'],'filter[platform]':'MAC_OS','limit':'200'})
    versions = client.request('GET',f'/v1/apps/{app}/appStoreVersions?'+query).get('data',[])
    exact = [v for v in versions if v.get('attributes',{}).get('versionString')==candidate.data['version'] and v.get('attributes',{}).get('platform')=='MAC_OS']
    if len(exact)>1:
        raise StoreError('Apple returned ambiguous Store versions')
    return app,build,exact[0] if exact else None


def _status(candidate,apple_state,*,state='apple-pending',**extra):
    return {'state':state,'apple_state':apple_state,'manifest_sha256':candidate.digest,'version':candidate.data['version'],'build_number':candidate.data['build_number'],**extra}


def _bound_build(client,version,build,candidate):
    linked = client.request('GET',f'/v1/appStoreVersions/{version["id"]}/build').get('data')
    if not linked or linked.get('id') != build['id'] or linked.get('attributes',{}).get('version') != str(candidate.data['build_number']):
        raise StoreError('Released or submitted Apple build differs from authorized candidate')


def submit(config,candidate,component,*,authorized_digest,client=None,runner=None):
    _authorized(config,candidate,component,authorized_digest)
    client = client or AppStoreClient()
    app,build,version = _lookup(config,candidate,component,client)
    if not build or build['attributes'].get('processingState')=='PROCESSING':
        return _status(candidate,'BUILD_PROCESSING')
    if build['attributes'].get('processingState')!='VALID':
        return _status(candidate,'BUILD_INVALID',state='failed')
    if version is None:
        version = client.request('POST','/v1/appStoreVersions',{'data':{'type':'appStoreVersions','attributes':{'platform':'MAC_OS','versionString':candidate.data['version'],'releaseType':'MANUAL'},'relationships':{'app':{'data':{'type':'apps','id':app}}}}})['data']
    state = version.get('attributes',{}).get('appStoreState') or version.get('attributes',{}).get('appVersionState')
    if state in {'DEVELOPER_REJECTED','REJECTED','METADATA_REJECTED','INVALID_BINARY'}:
        return _status(candidate,state,state='failed',app_store_version_id=version['id'])
    if state not in {'PREPARE_FOR_SUBMISSION',None}:
        _bound_build(client,version,build,candidate)
        return _status(candidate,state,state='completed' if state in {'READY_FOR_SALE','READY_FOR_DISTRIBUTION'} else 'apple-pending',app_store_version_id=version['id'])
    client.request('PATCH',f'/v1/appStoreVersions/{version["id"]}/relationships/build',{'data':{'type':'builds','id':build['id']}})
    query = urlencode({'filter[app]':app,'filter[platform]':'MAC_OS','include':'items','limit':'200'})
    drafts = client.request('GET','/v1/reviewSubmissions?'+query)
    candidates = [r for r in drafts.get('data',[]) if r.get('attributes',{}).get('state')=='READY_FOR_REVIEW']
    if len(candidates)>1:
        raise StoreError('Multiple Apple review drafts require explicit reconciliation')
    if candidates:
        review = candidates[0]
        included = drafts.get('included',[])
        item_ids = {i['id'] for i in review.get('relationships',{}).get('items',{}).get('data',[])}
        items = [i for i in included if i.get('id') in item_ids and i.get('type')=='reviewSubmissionItems']
        if len(items)!=len(item_ids) or any(i.get('relationships',{}).get('appStoreVersion',{}).get('data',{}).get('id') != version['id'] for i in items):
            raise StoreError('Existing Apple review draft contains unrelated items')
        attached = bool(items)
    else:
        review = client.request('POST','/v1/reviewSubmissions',{'data':{'type':'reviewSubmissions','attributes':{'platform':'MAC_OS'},'relationships':{'app':{'data':{'type':'apps','id':app}}}}})['data']
        attached = False
    if not attached:
        client.request('POST','/v1/reviewSubmissionItems',{'data':{'type':'reviewSubmissionItems','relationships':{'reviewSubmission':{'data':{'type':'reviewSubmissions','id':review['id']}},'appStoreVersion':{'data':{'type':'appStoreVersions','id':version['id']}}}}})
    client.request('PATCH',f'/v1/reviewSubmissions/{review["id"]}',{'data':{'type':'reviewSubmissions','id':review['id'],'attributes':{'submitted':True}}})
    return _status(candidate,'WAITING_FOR_REVIEW',app_store_version_id=version['id'],review_submission_id=review['id'])


def reconcile(config,candidate,component,*,authorized_digest,client=None,authorize=None):
    _authorized(config,candidate,component,authorized_digest)
    client = client or AppStoreClient()
    _,build,version = _lookup(config,candidate,component,client)
    if not build or not version:
        return _status(candidate,'BUILD_PROCESSING' if not build else 'VERSION_NOT_CREATED')
    _bound_build(client,version,build,candidate)
    state = version.get('attributes',{}).get('appStoreState') or version.get('attributes',{}).get('appVersionState')
    if state == 'PENDING_DEVELOPER_RELEASE' and authorize is not None:
        if authorize(candidate) is False:
            raise StoreError('Current Store release authorization was revoked')
        _authorized(config,candidate,component,authorized_digest)
        _bound_build(client,version,build,candidate)
        client.request('POST','/v1/appStoreVersionReleaseRequests',{'data':{'type':'appStoreVersionReleaseRequests','relationships':{'appStoreVersion':{'data':{'type':'appStoreVersions','id':version['id']}}}}})
        return _status(candidate,'RELEASE_REQUESTED',app_store_version_id=version['id'])
    if state in {'READY_FOR_SALE','READY_FOR_DISTRIBUTION'}:
        return _status(candidate,state,state='completed',app_store_version_id=version['id'])
    if state in {'REJECTED','METADATA_REJECTED','INVALID_BINARY','DEVELOPER_REJECTED'}:
        return _status(candidate,state,state='failed',app_store_version_id=version['id'])
    return _status(candidate,state or 'UNKNOWN',app_store_version_id=version['id'])
