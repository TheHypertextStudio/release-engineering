"""Bind product-scoped secret resources to ephemeral CI credentials."""
import base64
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile

from .config import load_config
from .providers import execute


def prepare(config, *, signing=None):
    if signing is None: signing=os.environ.get('GITHUB_EVENT_NAME') not in {'workflow_dispatch','schedule'}
    bindings = config['release'].get('credential_bindings',{})
    for variable, resource in bindings.items():
        if not signing and variable in {'APPLE_CERTIFICATE_BASE64','APPLE_CERTIFICATE_PASSWORD','SPARKLE_PRIVATE_KEY','APPLE_PROVISIONING_PROFILES_BASE64'}:
            continue
        segments = resource.split('/')
        value = execute(['gcloud','secrets','versions','access',segments[-1],'--secret',segments[3],'--project',segments[1]],capture=True)
        # The runner must mask a secret before it writes it to the job environment.
        print(f'::add-mask::{value.replace(chr(10), "%0A").replace(chr(13), "%0D")}')
        os.environ[variable] = value
        if os.environ.get('GITHUB_ENV'):
            delimiter = 'STUDIO_' + os.urandom(16).hex()
            with open(os.environ['GITHUB_ENV'],'a') as target:
                target.write(f'{variable}<<{delimiter}\n{value}\n{delimiter}\n')
    certificate = os.environ.get('APPLE_CERTIFICATE_BASE64') if signing else None
    if not certificate:
        return
    password = os.environ.get('APPLE_CERTIFICATE_PASSWORD')
    if not password:
        raise ValueError('Signing certificate requires its import password')
    directory = Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir())) / 'studio-signing'
    directory.mkdir(mode=0o700,exist_ok=True)
    install_profiles(config, directory)
    keychain = directory / 'signing.keychain-db'
    p12 = directory / 'certificate.p12'
    p12.write_bytes(base64.b64decode(certificate,validate=True)); p12.chmod(0o600)
    keychain_password = os.urandom(32).hex()
    print(f'::add-mask::{keychain_password}')
    def security(*args):
        try:
            subprocess.run(['security',*args],check=True,capture_output=True,text=True)
        except (OSError,subprocess.CalledProcessError):
            raise RuntimeError('Ephemeral signing credential preparation failed') from None
    security('create-keychain','-p',keychain_password,str(keychain))
    security('set-keychain-settings','-lut','21600',str(keychain))
    security('unlock-keychain','-p',keychain_password,str(keychain))
    try:
        security('import',str(p12),'-k',str(keychain),'-P',password,'-T','/usr/bin/codesign','-T','/usr/bin/productbuild')
        security('set-key-partition-list','-S','apple-tool:,apple:,codesign:','-s','-k',keychain_password,str(keychain))
        existing = execute(['security','list-keychains','-d','user'],capture=True)
        import shlex
        security('list-keychains','-d','user','-s',str(keychain),*shlex.split(existing))
    finally:
        p12.unlink(missing_ok=True)
    api_key = os.environ.get('APPLE_API_PRIVATE_KEY')
    if api_key:
        key_path = directory / f'AuthKey_{os.environ["APPLE_API_KEY_ID"]}.p8'
        key_path.write_text(api_key); key_path.chmod(0o600)
        with open(os.environ['GITHUB_ENV'],'a') as target:
            target.write(f'APPLE_API_KEY_PATH={key_path}\n')


def install_profiles(config, directory):
    expected={}
    channels=config['release'].get('channels',['direct'])
    for component in config.components:
        if 'direct' in channels:
            for bundle,profile in component.get('direct_provisioning_profiles',{}).items():
                expected.setdefault(bundle,set()).add(profile['specifier'])
        if 'app-store' in channels:
            for bundle,specifier in component.get('store_provisioning_profiles',{}).items():
                expected.setdefault(bundle,set()).add(specifier)
    if not expected:
        return
    raw=os.environ.get('APPLE_PROVISIONING_PROFILES_BASE64')
    if not raw:
        raise ValueError('Declared Developer ID profiles require their credential binding')
    payload=json.loads(raw)
    if not isinstance(payload,list) or not payload:
        raise ValueError('Provisioning credential must contain a nonempty list of profiles')
    destination=Path.home()/'Library/Developer/Xcode/UserData/Provisioning Profiles'
    destination.mkdir(parents=True,exist_ok=True)
    installed=[]; found=set()
    record=directory/'installed-profiles.json'
    for encoded in payload:
        data=base64.b64decode(encoded,validate=True)
        temporary=directory/'profile.provisionprofile'
        temporary.write_bytes(data); temporary.chmod(0o600)
        try:
            decoded=subprocess.run(['security','cms','-D','-i',str(temporary)],check=True,capture_output=True).stdout
            profile=plistlib.loads(decoded)
        finally:
            temporary.unlink(missing_ok=True)
        team=config['release']['macos']['team_id']
        identity=profile.get('Entitlements',{}).get('com.apple.application-identifier','')
        bundle=identity.removeprefix(team+'.')
        if team not in profile.get('TeamIdentifier',[]) or bundle not in expected or profile.get('Name') not in expected[bundle]:
            raise ValueError('Provisioning profile does not match its product, team and declared name')
        identifier=profile.get('UUID','')
        if not re.fullmatch(r'[0-9A-Fa-f-]{36}',identifier):
            raise ValueError('Provisioning profile UUID is invalid')
        path=destination/(identifier+'.provisionprofile')
        if path.exists():
            if path.read_bytes()!=data:
                raise ValueError('An installed provisioning profile has conflicting bytes')
        else:
            path.write_bytes(data); path.chmod(0o600); installed.append(str(path))
            record.write_text(json.dumps(installed)); record.chmod(0o600)
        found.add((bundle,profile['Name']))
    if found != {(bundle,name) for bundle,names in expected.items() for name in names}:
        raise ValueError('The credential is missing a declared provisioning profile')


def cleanup():
    directory = Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir())) / 'studio-signing'
    keychain = directory / 'signing.keychain-db'
    if keychain.exists():
        subprocess.run(['security','delete-keychain',str(keychain)],check=True,capture_output=True)
    record=directory/'installed-profiles.json'
    if record.exists():
        expected=Path.home()/'Library/Developer/Xcode/UserData/Provisioning Profiles'
        for name in json.loads(record.read_text()):
            path=Path(name)
            if path.parent != expected or path.suffix != '.provisionprofile':
                raise ValueError('Invalid ephemeral provisioning profile cleanup path')
            path.unlink(missing_ok=True)
    import shutil
    shutil.rmtree(directory,ignore_errors=False) if directory.exists() else None

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',nargs='?',choices=['prepare','cleanup'],default='prepare')
    parser.add_argument('--root',type=Path,default=Path.cwd())
    args=parser.parse_args()
    cleanup() if args.mode == 'cleanup' else prepare(load_config(args.root / 'studio.yaml'))
