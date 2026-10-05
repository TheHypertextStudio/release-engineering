"""Report credential presence and public distribution identity without secret bytes."""
import json
import os
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

if __name__=='__main__':
    report(Path('.studio/prerequisites.json'))
