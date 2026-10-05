"""Bind product-scoped secret resources to ephemeral CI credentials."""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .config import load_config
from .providers import execute


def prepare(config, *, signing=None):
    if signing is None: signing=os.environ.get('GITHUB_EVENT_NAME') not in {'workflow_dispatch','schedule'}
    bindings = config['release'].get('credential_bindings',{})
    for variable, resource in bindings.items():
        if not signing and variable in {'APPLE_CERTIFICATE_BASE64','APPLE_CERTIFICATE_PASSWORD','SPARKLE_PRIVATE_KEY'}:
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


def cleanup():
    directory = Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir())) / 'studio-signing'
    keychain = directory / 'signing.keychain-db'
    if keychain.exists():
        subprocess.run(['security','delete-keychain',str(keychain)],check=True,capture_output=True)
    import shutil
    shutil.rmtree(directory,ignore_errors=False) if directory.exists() else None

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',nargs='?',choices=['prepare','cleanup'],default='prepare')
    parser.add_argument('--root',type=Path,default=Path.cwd())
    args=parser.parse_args()
    cleanup() if args.mode == 'cleanup' else prepare(load_config(args.root / 'studio.yaml'))
