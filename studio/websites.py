"""Prepare deployment of reviewed website bytes without invoking a build."""
import json
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


# This bounded profile deliberately excludes filesystem/build options that need
# their own artifact contract. New Wrangler fields must be reviewed before use.
SITE_FIELDS = set('''$schema account_id name main compatibility_date compatibility_flags
no_bundle find_additional_modules preserve_file_names base_dir rules assets env route routes
workers_dev preview_urls vars kv_namespaces r2_buckets d1_databases services durable_objects
vectorize hyperdrive queues analytics_engine_datasets workflows pipelines artifacts
dispatch_namespaces secrets_store_secrets ratelimits migrations exports observability logpush
placement limits tail_consumers version_metadata keep_vars triggers ai browser
wasm_modules text_blobs data_blobs upload_source_maps'''.split())
RESOURCE_KEYS = {
    'kv_namespaces': ('kv', ('id',)), 'r2_buckets': ('r2', ('bucket_name',)),
    'd1_databases': ('d1', ('database_id',)), 'services': ('worker', ('service',)),
    'tail_consumers': ('worker', ('service',)), 'vectorize': ('vectorize', ('index_name',)),
    'hyperdrive': ('hyperdrive', ('id',)),
    'analytics_engine_datasets': ('analytics', ('dataset',)),
    'workflows': ('workflow', ('name',)), 'artifacts': ('artifacts', ('namespace',)),
    'dispatch_namespaces': ('dispatch', ('namespace',)),
    'secrets_store_secrets': ('secret', ('store_id', 'secret_name')),
    'ratelimits': ('ratelimit', ('namespace_id',)),
}


def _resource_identities(binding):
    identities = set()

    def rows(value):
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValueError('Website resource bindings require explicit identity arrays')
        return value

    def identity(kind, row, fields):
        values = tuple(row.get(key) for key in fields)
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError('Website resource bindings require explicit provider identities')
        identities.add((kind, *(value.casefold() for value in values)))

    for key, (kind, fields) in RESOURCE_KEYS.items():
        for row in rows(binding.get(key, [])):
            identity(kind, row, fields)
    for row in rows(binding.get('pipelines', [])):
        identity('pipeline', {'id': row.get('stream', row.get('pipeline'))}, ('id',))
    queues = binding.get('queues', {})
    durable = binding.get('durable_objects', {})
    if not isinstance(queues, dict) or not isinstance(durable, dict):
        raise ValueError('Website resource bindings require explicit mappings')
    for row in rows(queues.get('producers', [])) + rows(queues.get('consumers', [])):
        identity('queue', row, ('queue',))
        if row.get('dead_letter_queue'):
            identity('queue', row, ('dead_letter_queue',))
    for row in rows(durable.get('bindings', [])):
        # No script_name means the current, already isolated Worker namespace.
        identity('durable', {**row, 'script_name': row.get('script_name', binding['name'])},
                 ('script_name', 'class_name'))
        if row.get('script_name'):
            identity('worker', row, ('script_name',))
    return identities


def _freeze_files(binding, root, entry, assets):
    unsupported = set(binding) - SITE_FIELDS
    if unsupported or binding.get('upload_source_maps'):
        raise ValueError('Workers site configuration contains unsupported build or filesystem options')
    if 'base_dir' in binding:
        binding['base_dir'] = str(_inside(root, binding['base_dir'], 'Module root', directory=True))
    # Explicit CLI main/assets win over configuration, but remove ambiguous
    # relative values before Wrangler resolves the copied configuration.
    if 'main' in binding:
        binding['main'] = str(entry)
    if isinstance(binding.get('assets'), dict) and 'directory' in binding['assets']:
        binding['assets']['directory'] = str(assets)
    for key in ('wasm_modules', 'text_blobs', 'data_blobs'):
        if key not in binding:
            continue
        if not isinstance(binding[key], dict):
            raise ValueError('File bindings must identify files inside the archive')
        binding[key] = {name: str(_inside(root, relative, 'File binding'))
                        for name, relative in binding[key].items()}
    for rule in binding.get('rules', []):
        if not isinstance(rule, dict) or not isinstance(rule.get('globs'), list):
            raise ValueError('Module rules must identify paths inside the archive')
        if any(not isinstance(glob, str) or Path(glob).is_absolute() or '..' in Path(glob).parts
               or '\\' in glob for glob in rule['globs']):
            raise ValueError('Module rules must stay inside the archive')


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
    scopes = {}
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
        scopes[scope] = binding
    if names[0] == names[1] or environment not in ('staging', 'production'):
        raise ValueError('Website environments must be isolated')
    for host in routes['staging']:
        if '*' in host or any(fnmatchcase(host, production) for production in routes['production']):
            raise ValueError('Staging route hosts must be explicitly isolated from production')
    for key in (*RESOURCE_KEYS, 'queues', 'durable_objects', 'pipelines'):
        if (key in config or any(key in binding for binding in scopes.values())) and any(
                key not in binding for binding in scopes.values()):
            raise ValueError('Website resources require explicit environment bindings')
    production = _resource_identities(scopes['production']) | {('worker', names[1])}
    if _resource_identities(scopes['staging']) & production:
        raise ValueError('Staging write resources must be isolated from production')
    _freeze_files(config, root, entry, assets)
    for binding in scopes.values():
        _freeze_files(binding, root, entry, assets)
    target = root / '.studio-site' / origin.name
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(config, sort_keys=True) + '\n')
    command = ['deploy', str(entry), '--no-bundle', '--autoconfig=false', '--config', str(target),
               '--assets', str(assets), '--env', declared[environment], '--env-file', os.devnull]
    for variable, value in [('STUDIO_SOURCE_SHA', metadata['sourceSha']),
                            ('STUDIO_ARTIFACT_SHA256', metadata['artifactSha256']),
                            ('STUDIO_CANDIDATE_ID', metadata['candidateId'])]:
        command.extend(['--var', f'{variable}:{value}'])
    return command, metadata
