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
    for path in root.glob('*.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved'):
        resolved=json.loads(path.read_text())
        for pin in resolved.get('pins',[]):
            if pin.get('location','').removesuffix('.git') == f'https://github.com/{REPOSITORY}':
                pin['state']={'revision':revision}
        path.write_text(json.dumps(resolved,indent=2)+'\n')
    for path in (root/'infrastructure').glob('*.tf'):
        content=path.read_text()
        pattern=re.escape(REPOSITORY)+r'(\.git//[^"?]+\?ref=)[a-f0-9]{40}'
        path.write_text(re.sub(pattern,rf'{REPOSITORY}\g<1>{revision}',content))
    return lock


def resolve_native(root):
    from .config import load_config
    from .lifecycle import native_command
    config=load_config(Path(root)/'studio.yaml')
    for component in config.components:
        if component['kind']=='macos':
            subprocess.run([*native_command(component,'setup'),'-jobs','2'],cwd=config.root/component['path'],check=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,default=Path.cwd()); parser.add_argument('--version'); parser.add_argument('--resolve-native',action='store_true')
    args=parser.parse_args(); print(json.dumps(update(args.root,args.version)))
    if args.resolve_native: resolve_native(args.root)
