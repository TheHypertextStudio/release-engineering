"""Select the declared native toolchain without installing product release policy."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from .candidate import load_candidate, sha256, CandidateError
from .config import load_config
from .providers import execute


def setup(root, *, candidate_directory=None):
    config = load_config(root / 'studio.yaml')
    if candidate_directory:
        candidate = load_candidate(candidate_directory / 'candidate.json')
        if sha256(root / 'studio.yaml') != sha256(candidate_directory / 'studio.yaml'):
            raise CandidateError('Candidate source policy does not match the reviewed policy')
        lock = json.loads((root / 'studio.lock.json').read_text())
        implementation = Path(os.environ['STUDIO_ENGINE_ROOT']).resolve()
        actual = execute(['git','rev-parse','HEAD'],cwd=implementation,capture=True)
        if actual != candidate.data['tooling_revision'] or actual != lock['revision']:
            raise CandidateError('Promotion must execute the candidate tooling revision')
    xcode = config['toolchain'].get('xcode')
    if xcode:
        matches = []
        for app in Path('/Applications').glob('Xcode*.app'):
            developer = app / 'Contents/Developer'
            result = subprocess.run(['/usr/bin/xcodebuild','-version'],env={**os.environ,'DEVELOPER_DIR':str(developer)},capture_output=True,text=True)
            if result.returncode == 0 and result.stdout.splitlines()[0] == f'Xcode {xcode}':
                matches.append(developer)
        if not matches:
            raise RuntimeError(f'The runner lacks declared Xcode {xcode}')
        os.environ['DEVELOPER_DIR'] = str(matches[0])
        if os.environ.get('GITHUB_ENV'):
            with open(os.environ['GITHUB_ENV'],'a') as target:
                target.write(f'DEVELOPER_DIR={matches[0]}\n')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as target:
            for name in ('java','dotnet'):
                target.write(f'{name}={config["toolchain"].get(name,"")}\n')
    if os.environ.get('NODE_AUTH_TOKEN'):
        import tempfile
        path=Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir()))/'studio-npmrc'
        path.write_text('//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}\n')
        path.chmod(0o600)
        os.environ['NPM_CONFIG_USERCONFIG']=str(path)
        if os.environ.get('GITHUB_ENV'):
            with open(os.environ['GITHUB_ENV'],'a') as target: target.write(f'NPM_CONFIG_USERCONFIG={path}\n')
    native=config['toolchain'].get('brew',[])
    for formula in native:
        if not __import__('re').fullmatch(r'[a-z][a-z0-9@+-]*',formula): raise ValueError('Invalid native dependency')
        if not shutil.which(formula): execute(['brew','install',formula])
    pnpm = config['toolchain'].get('pnpm')
    if pnpm:
        if shutil.which('corepack'):
            execute(['corepack','enable'])
            execute(['corepack','prepare',f'pnpm@{pnpm}','--activate'])
        else:
            execute(['npm','install','--global',f'pnpm@{pnpm}'])
    return config

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--candidate',type=Path)
    parser.add_argument('--source',type=Path,default=Path.cwd())
    args=parser.parse_args()
    setup(args.source.resolve(),candidate_directory=args.candidate.resolve() if args.candidate else None)
