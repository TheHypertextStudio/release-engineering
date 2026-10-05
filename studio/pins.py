"""Update the launcher, reusable workflows, and Swift package as one pin."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.request import urlopen

from .ci import api

REPOSITORY='TheHypertextStudio/release-engineering'


def update(root,version=None):
    root=Path(root).resolve()
    release=api(f'repos/{REPOSITORY}/releases/tags/{version}' if version else f'repos/{REPOSITORY}/releases/latest')
    version=release['tag_name']
    if not re.fullmatch(r'v\d+\.\d+\.\d+',version) or release['draft'] or release['prerelease']:
        raise ValueError('Pin updates require a stable tooling release')
    assets={asset['name']:asset for asset in release['assets']}
    def read(name):
        with urlopen(assets[name]['browser_download_url'],timeout=30) as response:
            return response.read(16*1024*1024)
    archive=read('studio.pyz')
    expected=read('SHA256SUMS').decode().split()[0]
    if hashlib.sha256(archive).hexdigest()!=expected:
        raise ValueError('Tooling release checksum mismatch')
    reference=api(f'repos/{REPOSITORY}/git/ref/tags/{version}')['object']
    if reference['type']=='tag':
        reference=api(f'repos/{REPOSITORY}/git/tags/{reference["sha"]}')['object']
    revision=reference['sha']
    if reference['type']!='commit' or not re.fullmatch('[0-9a-f]{40}',revision):
        raise ValueError('Tooling tag does not resolve to an immutable commit')
    lock={'schema':1,'repository':REPOSITORY,'version':version,'revision':revision,'sha256':expected}
    root.joinpath('studio.lock.json').write_text(json.dumps(lock,indent=2)+'\n')
    launcher=root/'run'
    if launcher.exists():
        content=launcher.read_text()
        content=re.sub(r"(?m)^version='[^']*'$",f"version='{version}'",content)
        content=re.sub(r"(?m)^expected='[^']*'$",f"expected='{expected}'",content)
        launcher.write_text(content); launcher.chmod(0o755)
    for path in (root/'.github/workflows').glob('*.yml'):
        content=path.read_text()
        content=re.sub(re.escape(REPOSITORY)+r'/\.github/workflows/([\w.-]+)@[a-f0-9]{40}',lambda match:f'{REPOSITORY}/.github/workflows/{match[1]}@{revision}',content)
        content=re.sub(r"(?m)^(\s+tooling_revision:) ['\"]?[a-f0-9]{40}['\"]?$",rf'\g<1> {revision}',content)
        path.write_text(content)
    for path in root.glob('*.xcodeproj/project.pbxproj'):
        content=path.read_text()
        pattern=r'("?repositoryURL"?\s*=\s*"https://github.com/'+re.escape(REPOSITORY)+r'(?:\.git)?";\s+"?requirement"?\s*=\s*\{\s+"?kind"?\s*=\s*"?revision"?;\s+"?revision"?\s*=\s*"?)[a-f0-9]{40}'
        path.write_text(re.sub(pattern,rf'\g<1>{revision}',content))
    for path in (root/'infrastructure').glob('*.tf'):
        content=path.read_text()
        pattern=re.escape(REPOSITORY)+r'(\.git//[^"?]+\?ref=)[a-f0-9]{40}'
        path.write_text(re.sub(pattern,rf'{REPOSITORY}\g<1>{revision}',content))
    return lock

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,default=Path.cwd()); parser.add_argument('--version')
    args=parser.parse_args(); print(json.dumps(update(args.root,args.version)))
