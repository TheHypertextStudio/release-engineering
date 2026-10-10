"""Prepare deployment of reviewed website bytes without invoking a build."""
import json
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit


def _hosts(config):
    routes = config.get('routes', [])
    if not isinstance(routes, list):
        raise ValueError('Website routes must be an array')
    if config.get('route'):
        routes = [*routes, config['route']]
    hosts = []
    for route in routes:
        pattern = route.get('pattern') if isinstance(route, dict) else route
        if not isinstance(pattern, str) or not pattern:
            raise ValueError('Website routes require explicit patterns')
        host = urlsplit(pattern if '://' in pattern else '//' + pattern).hostname
        if not host:
            raise ValueError('Website routes require explicit hosts')
        hosts.append(host)
    return hosts


def _inside(root, relative, label, *, directory=False):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError(f'{label} must stay inside the candidate archive')
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f'{label} must exist inside the candidate archive')
    return path


def workers_site_inputs(component, artifact, environment, build_root, bindings_root):
    """Return a bounded Wrangler argument list and the expected live identity.

    Site configurations use strict JSON (also valid in a .jsonc file). Existing
    standalone Worker TOML/JSONC handling stays in the standalone adapter.
    """
    deploy = component['deploy']
    metadata = {
        'sourceSha': artifact.get('source_sha', ''),
        'artifactSha256': artifact.get('sha256', ''),
        'candidateId': artifact.get('candidate_id', ''),
    }
    for key, pattern in [('sourceSha', r'[a-f0-9]{40}'), ('artifactSha256', r'[a-f0-9]{64}'), ('candidateId', r'[0-9]+-[0-9]+')]:
        if not isinstance(metadata[key], str) or not re.fullmatch(pattern, metadata[key]):
            raise ValueError('Website release identity requires a source SHA, artifact digest, and candidate ID')
    root = Path(build_root).resolve()
    bindings = Path(bindings_root).resolve()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('Website archives cannot contain symlinks')
    entry = _inside(root, deploy.get('entrypoint'), 'Worker entrypoint')
    assets = _inside(root, deploy.get('assets'), 'Static assets', directory=True)
    component_path = component.get('path')
    if not isinstance(component_path, str) or Path(component_path).is_absolute() or '..' in Path(component_path).parts:
        raise ValueError('Website component path must stay inside the immutable candidate')
    origin = _inside(bindings, str(Path(component_path) / deploy['config']), 'Worker configuration')
    try:
        config = json.loads(origin.read_text())
    except (ValueError, UnicodeError) as error:
        raise ValueError('Workers site configuration must be strict JSON') from error
    if not isinstance(config, dict) or 'build' in config:
        raise ValueError('Website promotion forbids custom build configuration')
    if not isinstance(config.get('account_id'), str) or not re.fullmatch(r'[a-f0-9]{32}', config['account_id']):
        raise ValueError('Website configuration requires an explicit Cloudflare account binding')
    declared = deploy.get('environments', {})
    environments = config.get('env', {})
    if not isinstance(declared, dict) or not isinstance(environments, dict):
        raise ValueError('Website environments must be mappings')
    names = []
    routes = {}
    for scope in ('staging', 'production'):
        selected = declared.get(scope)
        binding = environments.get(selected, {}) if isinstance(selected, str) else {}
        if not isinstance(binding, dict) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', str(binding.get('name', ''))):
            raise ValueError('Website environments require explicit isolated Worker names')
        if 'build' in binding:
            raise ValueError('Website promotion forbids environment build configuration')
        if binding.get('account_id', config['account_id']) != config['account_id']:
            raise ValueError('Website environments must use the reviewed account binding')
        if scope == 'staging' and any(key in config and key not in binding for key in ('routes', 'route')):
            raise ValueError('Staging routes must be explicitly isolated from production')
        names.append(binding['name'])
        effective = {key: binding.get(key, config.get(key, [] if key == 'routes' else None))
                     for key in ('routes', 'route')}
        routes[scope] = _hosts(effective)
    if names[0] == names[1] or environment not in ('staging', 'production'):
        raise ValueError('Website environments must be isolated')
    for host in routes['staging']:
        if '*' in host or any(fnmatchcase(host, production) for production in routes['production']):
            raise ValueError('Staging route hosts must be explicitly isolated from production')
    target = root / '.studio-site' / origin.name
    target.parent.mkdir(exist_ok=True)
    shutil.copyfile(origin, target)
    command = ['deploy', str(entry), '--no-bundle', '--autoconfig=false', '--config', str(target),
               '--assets', str(assets), '--env', declared[environment], '--env-file', os.devnull]
    for variable, value in [('STUDIO_SOURCE_SHA', metadata['sourceSha']),
                            ('STUDIO_ARTIFACT_SHA256', metadata['artifactSha256']),
                            ('STUDIO_CANDIDATE_ID', metadata['candidateId'])]:
        command.extend(['--var', f'{variable}:{value}'])
    return command, metadata
