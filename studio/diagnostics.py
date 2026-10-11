"""Report credential presence and public distribution identity without secret bytes."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from .macos import sparkle_public_key

def _read_binding(resource):
    parts=resource.split('/')
    if len(parts)!=6 or parts[0]!='projects' or parts[2]!='secrets' or parts[4]!='versions':
        return None
    try:
        result=subprocess.run(['gcloud','secrets','versions','access',parts[5],
                               '--secret',parts[3],'--project',parts[1]],
                              capture_output=True,text=True,check=False)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode==0 else None

def report(path,config=None,*,migration_public_key=None,migration_output=None):
    variables=('APPLE_CERTIFICATE_BASE64','APPLE_CERTIFICATE_PASSWORD',
               'APPLE_API_PRIVATE_KEY','APPLE_API_KEY_ID','APPLE_API_ISSUER',
               'APPLE_PROVISIONING_PROFILES_BASE64','SPARKLE_PRIVATE_KEY')
    bindings=config['release'].get('credential_bindings',{}) if config is not None else {}
    data={'schema':1,'credentials':{},'sources':{}}
    sparkle_key=None
    for name in variables:
        if name in bindings:
            value=_read_binding(bindings[name])
            source='secret-manager' if value else 'secret-manager-unavailable'
        else:
            value=os.environ.get(name)
            source='repository-secret' if value else 'unconfigured'
        data['credentials'][name]=bool(value)
        data['sources'][name]=source
        if name=='SPARKLE_PRIVATE_KEY': sparkle_key=value
    if sparkle_key:
        data['sparkle_public_key']=sparkle_public_key(sparkle_key)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps(data,indent=2))
    if migration_public_key:
        migrate_sparkle(migration_public_key,migration_output,private_key=sparkle_key)
    return data


def migrate_sparkle(public_key,output,*,private_key=None):
    """Encrypt the existing updater key for its owner's scoped secret-store migration."""
    if not public_key: return
    value=private_key if private_key is not None else os.environ.get('SPARKLE_PRIVATE_KEY')
    if not value: raise ValueError('There is no existing updater credential to migrate')
    with tempfile.TemporaryDirectory(prefix='studio-key-migration-') as temporary:
        recipient=Path(temporary)/'recipient.pem'; recipient.write_text(public_key)
        result=subprocess.run(['openssl','pkeyutl','-encrypt','-pubin','-inkey',str(recipient),
                               '-pkeyopt','rsa_padding_mode:oaep','-pkeyopt','rsa_oaep_md:sha256'],
                              input=value.encode(),capture_output=True)
        if result.returncode: raise ValueError('Updater migration requires a valid RSA OAEP recipient key')
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_bytes(result.stdout)

if __name__=='__main__':
    import argparse
    from .config import load_config
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--output',type=Path,default=Path('.studio/prerequisites.json'))
    args=parser.parse_args()
    root=args.root.resolve()
    manifest=root/'studio.yaml'
    config=load_config(manifest) if manifest.exists() else None
    report(args.output,config,
           migration_public_key=os.environ.get('STUDIO_MIGRATION_PUBLIC_KEY'),
           migration_output=args.output.parent/'sparkle-migration.enc')
