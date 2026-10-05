"""Report credential presence and public distribution identity without secret bytes."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from .macos import sparkle_public_key

def report(path):
    variables=('APPLE_CERTIFICATE_BASE64','APPLE_CERTIFICATE_PASSWORD','APPLE_API_PRIVATE_KEY','APPLE_API_KEY_ID','APPLE_API_ISSUER','SPARKLE_PRIVATE_KEY')
    data={'schema':1,'credentials':{name:bool(os.environ.get(name)) for name in variables}}
    if os.environ.get('SPARKLE_PRIVATE_KEY'):
        data['sparkle_public_key']=sparkle_public_key(os.environ['SPARKLE_PRIVATE_KEY'])
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps(data,indent=2))
    return data


def migrate_sparkle(public_key,output):
    """Encrypt the existing updater key for its owner's scoped secret-store migration."""
    if not public_key: return
    value=os.environ.get('SPARKLE_PRIVATE_KEY')
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
    report(Path('.studio/prerequisites.json'))
    migrate_sparkle(os.environ.get('STUDIO_MIGRATION_PUBLIC_KEY'),Path('.studio/sparkle-migration.enc'))
