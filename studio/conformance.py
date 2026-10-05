"""Reject release implementations in product repositories."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

SHARED = 'TheHypertextStudio/release-engineering'
POLICY = re.compile(r'\bcodesign\b|\bnotarytool\b|\bstapler\b|\bgenerate_appcast\b|\bsign_update\b|\bappStoreVersionReleaseRequests\b|\bhdiutil\s+create\b|\bproductbuild\b|\baltool\b.*--upload|\bgcloud\s+run\s+(?:deploy|services\s+update-traffic)\b|\bwrangler\s+(?:deploy|publish|pages\s+deploy)\b|\bvercel\s+deploy\b')


def check(config):
    root = config.root
    lock = json.loads((root / 'studio.lock.json').read_text())
    if lock.get('repository') != SHARED or not re.fullmatch('[0-9a-f]{40}',lock.get('revision','')) or not re.fullmatch('[0-9a-f]{64}',lock.get('sha256','')) or lock.get('revision') == '0'*40 or lock.get('sha256') == '0'*64 or not re.fullmatch(r'v\d+\.\d+\.\d+',lock.get('version','')):
        raise ValueError('Shared lifecycle requires an immutable tooling lock')
    launcher = (root / 'run').read_text()
    if f"version='{lock['version']}'" not in launcher or f"expected='{lock['sha256']}'" not in launcher:
        raise ValueError('Launcher does not match the tooling lock')
    exceptions = config['release'].get('legacy_files',{})
    if not isinstance(exceptions,dict):
        raise ValueError('Legacy exceptions must bind exact file digests')
    for name,digest in exceptions.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError(f'Legacy implementation changed: {name}')
        if name.startswith('.github/workflows/') and path.suffix in {'.yml','.yaml'}:
            raise ValueError(f'Legacy workflow must be archived: {name}')
    try:
        paths = subprocess.run(['git','ls-files','--cached','--others','--exclude-standard'],cwd=root,capture_output=True,text=True,check=True).stdout.splitlines()
    except subprocess.CalledProcessError:
        paths = [str(path.relative_to(root)) for path in root.rglob('*') if path.is_file()]
    for name in paths:
        path = root / name
        if path.name == 'project.pbxproj' and path.is_file():
            content=path.read_text()
            for match in re.finditer(r'"?repositoryURL"?\s*=\s*"https://github\.com/'+re.escape(SHARED)+r'\.git";\s*"?requirement"?\s*=\s*\{([^}]+)\}',content,re.S):
                requirement=match.group(1)
                if not re.search(r'"?kind"?\s*=\s*"?revision"?;',requirement) or not re.search(r'"?revision"?\s*=\s*"'+lock['revision']+r'";',requirement):
                    raise ValueError(f'Swift distribution package does not match the immutable lock: {name}')
        if path.suffix not in {'.sh','.py','.mjs','.js','.ts','.yml','.yaml'} or not path.is_file():
            continue
        if name in exceptions:
            if hashlib.sha256(path.read_bytes()).hexdigest() != exceptions[name]:
                raise ValueError(f'Legacy implementation changed: {name}')
            if name.startswith('.github/workflows/') and re.search(r'^\s+(push|pull_request|schedule|workflow_run|repository_dispatch):|^on:\s*\[',path.read_text(),re.M):
                raise ValueError(f'Legacy release workflow is still automatic: {name}')
            continue
        if any(part in {'tests','__tests__'} for part in path.parts) or re.search(r'(?:^test_|[.]test[.]|[.]spec[.])',path.name):
            continue
        content = path.read_text(errors='replace')
        inspected='\n'.join(line for line in content.splitlines() if '--dry-run' not in line)
        if POLICY.search(inspected):
            raise ValueError(f'Product must use shared release policy: {name}')
        if name.startswith('.github/workflows/'):
            for match in re.finditer(r'\btooling_revision:\s*[\x27"]?([0-9a-f]{40})',content):
                if match.group(1) != lock['revision']:
                    raise ValueError(f'Workflow input does not match the immutable lock: {name}')
            for match in re.finditer(re.escape(SHARED) + r'/\.github/workflows/[\w.-]+@([^\s#]+)',content):
                if match.group(1) != lock['revision']:
                    raise ValueError(f'Workflow does not match the immutable lock: {name}')
    return {'status':'passed','tooling_revision':lock['revision']}
