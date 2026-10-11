"""Prepare deployment of reviewed website bytes without invoking a build."""
import hashlib
import json
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from .candidate import PromotionJournal


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

RECOVERY_SCHEMA = 2
RECOVERY_PAGE_SIZE = 100
RECOVERY_MAX_PAGES = 100


def _recovery_cli(toolchain):
    version = toolchain.get('wrangler') if isinstance(toolchain, dict) else None
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Workers recovery requires an exact Wrangler version')
    return ['pnpm', 'dlx', f'wrangler@{version}']


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Cloudflare recovery API redirects are not allowed')


class CloudflareWorkersAPI:
    """Small bounded transport for the scoped Workers recovery endpoints."""

    def __init__(self, token=None):
        self.token = token if token is not None else os.environ.get('CLOUDFLARE_API_TOKEN')
        if not isinstance(self.token, str) or not self.token:
            raise ValueError('CLOUDFLARE_API_TOKEN is required for Workers recovery')

    def request(self, method, path, data=None, headers=None):
        if not isinstance(path, str) or not path.startswith('/accounts/') or '..' in path.split('/'):
            raise ValueError('Cloudflare recovery requests must stay within the account API')
        encoded = json.dumps(data).encode() if data is not None else None
        request_headers = {'Authorization': 'Bearer ' + self.token,
                           'Accept': 'application/json',
                           'Content-Type': 'application/json'}
        if headers is not None:
            if not isinstance(headers, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                                    for key, value in headers.items()) or any(
                    key.casefold() in {'authorization', 'host'} for key in headers):
                raise ValueError('Cloudflare recovery request headers are malformed')
            request_headers.update(headers)
        request = Request('https://api.cloudflare.com/client/v4' + path, method=method,
                          data=encoded, headers=request_headers)
        try:
            with build_opener(_NoRedirect()).open(request, timeout=30) as response:
                raw = response.read(2_000_001)
        except HTTPError as error:
            error.close()
            raise ValueError(f'Cloudflare recovery API request failed (HTTP {error.code})') from None
        except (URLError, OSError):
            raise ValueError('Cloudflare recovery API request did not complete') from None
        if len(raw) > 2_000_000:
            raise ValueError('Cloudflare recovery API response is too large')
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ValueError('Cloudflare recovery API did not return valid JSON') from None
        if not isinstance(payload, dict) or payload.get('success') is False:
            raise ValueError('Cloudflare recovery API rejected the request')
        result = payload.get('result')
        if 'result_info' in payload:
            return {'result': result, 'result_info': payload['result_info']}
        return result

    def get(self, path):
        return self.request('GET', path)


def _recovery_scope(component, environment):
    deploy = component.get('deploy', {})
    if deploy.get('provider') != 'workers':
        raise ValueError('Recovery records are supported only for Workers sites')
    env_name = deploy.get('environments', {}).get(environment)
    if not isinstance(env_name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}', env_name):
        raise ValueError('Workers recovery requires an explicit environment binding')
    return env_name, environment


def _api_get(api, path):
    getter = getattr(api, 'get', None)
    if getter:
        return getter(path)
    return api.request('GET', path)


def _api_mutate(api, method, path, data, headers=None):
    return api.request(method, path, data, headers=headers)


def _deployment_version(api, account, worker):
    payload = _api_get(api, f'/accounts/{account}/workers/scripts/{worker}/deployments')
    deployments = payload.get('deployments') if isinstance(payload, dict) else None
    if not isinstance(deployments, list) or not deployments or not isinstance(deployments[0], dict):
        raise ValueError('Cloudflare has no complete Worker deployment to recover')
    versions = deployments[0].get('versions')
    if not isinstance(versions, list):
        raise ValueError('Cloudflare deployment version state is malformed')
    active = [v for v in versions if isinstance(v, dict) and v.get('percentage', 0) > 0]
    if len(active) != 1 or active[0].get('percentage') != 100:
        raise ValueError('Workers recovery requires one complete 100% deployment')
    version = active[0].get('version_id')
    if not isinstance(version, str) or not re.fullmatch(r'[A-Fa-f0-9-]{16,64}', version):
        raise ValueError('Cloudflare deployment has an invalid version identity')
    return version


def _version_artifact_digest(api, account, worker, version):
    detail = _api_get(api, f'/accounts/{account}/workers/scripts/{worker}/versions/{version}')
    if not isinstance(detail, dict) or detail.get('id') != version:
        raise ValueError('Cloudflare version details do not match the selected Worker version')
    resources = detail.get('resources', {})
    bindings = resources.get('bindings', []) if isinstance(resources, dict) else []
    for binding in bindings if isinstance(bindings, list) else []:
        if isinstance(binding, dict) and binding.get('name') == 'STUDIO_ARTIFACT_SHA256':
            digest = binding.get('text', binding.get('value'))
            if isinstance(digest, str) and re.fullmatch(r'[a-f0-9]{64}', digest):
                return digest
    raise ValueError('Prior Worker version does not identify its immutable website artifact')


def _domain_page(api, account, worker, environment, page):
    return _api_get(api, f'/accounts/{account}/workers/domains/records?page={page}'
                    f'&per_page={RECOVERY_PAGE_SIZE}&service={worker}&environment={environment}')


def _domains(api, account, worker, environment):
    domains = []
    for page in range(RECOVERY_MAX_PAGES):
        payload = _domain_page(api, account, worker, environment, page)
        if isinstance(payload, list):
            rows, info = payload, {}
        elif isinstance(payload, dict):
            rows = payload.get('result', payload.get('domains', []))
            info = payload.get('result_info', {})
        else:
            raise ValueError('Cloudflare custom-domain response is malformed')
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Cloudflare custom-domain response is malformed')
        domains.extend(rows)
        total_pages = info.get('total_pages') if isinstance(info, dict) else None
        if total_pages is not None:
            if not isinstance(total_pages, int) or total_pages < 0 or total_pages > RECOVERY_MAX_PAGES:
                raise ValueError('Cloudflare custom-domain pagination is outside the recovery bound')
            if total_pages == 0 and not domains:
                return domains
            if total_pages == 0:
                raise ValueError('Cloudflare custom-domain pagination omitted existing records')
            if page + 1 >= total_pages:
                return domains
        elif len(rows) < RECOVERY_PAGE_SIZE:
            return domains
    raise ValueError('Cloudflare custom-domain pagination exceeded the recovery bound')


def _scoped_routes(api, account, worker, environment):
    result = _api_get(api, f'/accounts/{account}/workers/services/{worker}/environments/{environment}/routes?show_zonename=true')
    routes = result.get('routes', result.get('result')) if isinstance(result, dict) else result
    if not isinstance(routes, list) or any(not isinstance(row, dict) for row in routes):
        raise ValueError('Cloudflare Worker route response is malformed')
    if any(row.get('script') not in (None, worker) for row in routes):
        raise ValueError('Cloudflare route response contains another Worker')
    return routes


def _scoped_subdomain(api, account, worker, environment):
    result = _api_get(api, f'/accounts/{account}/workers/services/{worker}/environments/{environment}/subdomain')
    if isinstance(result, dict) and isinstance(result.get('subdomain'), dict):
        result = result['subdomain']
    if not isinstance(result, dict) or not isinstance(result.get('enabled'), bool):
        raise ValueError('Cloudflare Worker subdomain state is malformed')
    return {'enabled': result['enabled'],
            'previews_enabled': bool(result.get('previews_enabled', False))}


def _service_environment(api, account, worker):
    """Resolve the Cloudflare API environment from Wrangler's service metadata."""
    result = _api_get(api, f'/accounts/{account}/workers/services/{worker}')
    default = result.get('default_environment') if isinstance(result, dict) else None
    environment = default.get('environment') if isinstance(default, dict) else None
    if not isinstance(environment, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}', environment):
        raise ValueError('Cloudflare Worker service metadata has no valid default environment')
    return environment


def workers_site_routing_digest(routes, custom_domains, subdomain):
    if (not isinstance(routes, list) or any(not isinstance(row, dict) for row in routes)
            or not isinstance(custom_domains, list)
            or any(not isinstance(row, dict) for row in custom_domains)
            or not isinstance(subdomain, dict)):
        raise ValueError('Worker route/domain state must use explicit records')
    route_state = sorted(( {key: row[key] for key in ('pattern', 'zone_id', 'zone_name',
                    'custom_domain', 'enabled', 'previews_enabled') if key in row}
                    for row in routes), key=lambda row: json.dumps(row, sort_keys=True, separators=(',', ':')))
    domain_state = sorted(({key: row[key] for key in ('hostname', 'zone_id', 'zone_name',
                     'service', 'environment', 'enabled', 'previews_enabled') if key in row}
                     for row in custom_domains), key=lambda row: json.dumps(row, sort_keys=True, separators=(',', ':')))
    subdomain_state = {key: subdomain[key] for key in ('enabled', 'previews_enabled') if key in subdomain}
    payload = json.dumps({'routes': route_state, 'custom_domains': domain_state,
                          'subdomain': subdomain_state}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def _snapshot_file(path, source, expected_digest=None):
    path, source = Path(path), Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError('Recovery source files must be regular files')
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_digest is not None and digest != expected_digest:
        raise ValueError('Prior Worker archive differs from its recorded artifact digest')
    _write_immutable(path, data)
    return digest


def _write_immutable(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.recovery-', delete=False) as temporary:
        temporary.write(data)
        temporary.flush()
        os.fsync(temporary.fileno())
        temp_path = Path(temporary.name)
    try:
        os.link(temp_path, path)
    except FileExistsError:
        raise ValueError('Workers recovery evidence cannot be replaced') from None
    finally:
        temp_path.unlink(missing_ok=True)


def capture_workers_site_recovery(component, environment, config_path, archive_path, output_path, *, api):
    """Persist an immutable prior Worker version, archive, config, and live routing state."""
    selected_env, env = _recovery_scope(component, environment)
    config_path, archive_path, output_path = map(Path, (config_path, archive_path, output_path))
    try:
        config = json.loads(config_path.read_text())
    except (OSError, ValueError, UnicodeError):
        raise ValueError('Prior Workers recovery config must be readable strict JSON') from None
    selected_env = component['deploy'].get('environments', {}).get(env)
    account = config.get('account_id') if isinstance(config, dict) else None
    if (not isinstance(config, dict) or not isinstance(account, str)
            or not re.fullmatch(r'[a-f0-9]{32}', account)
            or not isinstance(config.get('env'), dict)
            or not isinstance(config['env'].get(selected_env), dict)):
        raise ValueError('Prior Workers config does not match the selected account and environment')
    worker = config['env'][selected_env].get('name')
    if not isinstance(worker, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', worker):
        raise ValueError('Prior Workers config does not identify the selected Worker')
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError('Recovery source files must be regular files')
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    version = _deployment_version(api, account, worker)
    archive_name = output_path.stem + '.worker.zip'
    config_name = output_path.stem + '.wrangler.json'
    if _version_artifact_digest(api, account, worker, version) != digest:
        raise ValueError('Prior Worker version and retained archive have different artifact identities')
    service_env = _service_environment(api, account, worker)
    routes = _scoped_routes(api, account, worker, service_env)
    domains = _domains(api, account, worker, service_env)
    for domain in domains:
        if domain.get('service') != worker or domain.get('environment') != service_env:
            raise ValueError('Cloudflare custom-domain response contains another Worker environment')
    subdomain = _scoped_subdomain(api, account, worker, service_env)
    routing_digest = workers_site_routing_digest(routes, domains, subdomain)
    reread_version = _deployment_version(api, account, worker)
    reread_routes = _scoped_routes(api, account, worker, service_env)
    reread_domains = _domains(api, account, worker, service_env)
    for domain in reread_domains:
        if domain.get('service') != worker or domain.get('environment') != service_env:
            raise ValueError('Cloudflare custom-domain response contains another Worker environment')
    reread_subdomain = _scoped_subdomain(api, account, worker, service_env)
    if (reread_version != version or workers_site_routing_digest(
            reread_routes, reread_domains, reread_subdomain) != routing_digest):
        raise ValueError('Live Worker deployment or routing state changed while the recovery snapshot was captured')
    config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    record = {
        'schema': RECOVERY_SCHEMA, 'provider': 'workers', 'account_id': account,
        'worker': worker, 'environment': env, 'logical_environment': env,
        'service_environment': service_env, 'prior_version_id': version,
        'artifact_sha256': digest, 'archive_file': archive_name,
        'config_sha256': config_digest, 'config_file': config_name,
        'routes': routes, 'custom_domains': domains, 'subdomain': subdomain,
        'prior_routing_sha256': routing_digest,
    }
    record['record_sha256'] = hashlib.sha256(json.dumps(
        record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    _snapshot_file(output_path.parent / archive_name, archive_path, digest)
    _snapshot_file(output_path.parent / config_name, config_path, config_digest)
    _write_immutable(output_path, (json.dumps(record, indent=2, sort_keys=True) + '\n').encode())
    return record


def _load_recovery_record(path, component, environment, current_config_path):
    path = Path(path)
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeError):
        raise ValueError('Workers recovery record must be readable strict JSON') from None
    selected_env, env = _recovery_scope(component, environment)
    account = record.get('account_id') if isinstance(record, dict) else None
    if (not isinstance(record, dict) or record.get('schema') != RECOVERY_SCHEMA
            or record.get('provider') != 'workers' or not isinstance(account, str)
            or not re.fullmatch(r'[a-f0-9]{32}', account)
            or record.get('environment') != env or record.get('logical_environment') != env):
        raise ValueError('Workers recovery record does not match the selected account and environment')
    record_body = {key: value for key, value in record.items() if key != 'record_sha256'}
    record_sha = hashlib.sha256(json.dumps(record_body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if record.get('record_sha256') != record_sha:
        raise ValueError('Workers recovery record integrity check failed')
    for file_key, digest_key in [('archive_file', 'artifact_sha256'), ('config_file', 'config_sha256')]:
        name = record.get(file_key)
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError('Workers recovery files must stay beside the recovery record')
        source = path.parent / name
        if source.is_symlink() or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != record.get(digest_key):
            raise ValueError('Workers recovery file digest does not match its record')
    try:
        config = json.loads((path.parent / record['config_file']).read_text())
    except (OSError, ValueError, UnicodeError):
        raise ValueError('Workers recovery config is invalid') from None
    if (not isinstance(config, dict) or config.get('account_id') != account
            or not isinstance(config.get('env'), dict)
            or not isinstance(config['env'].get(selected_env), dict)
            or config['env'][selected_env].get('name') != record.get('worker')):
        raise ValueError('Workers recovery config does not match its recorded identity')
    try:
        current_config_path = Path(current_config_path)
        if current_config_path.is_symlink() or not current_config_path.is_file():
            raise ValueError('not a regular config file')
        current_config_bytes = current_config_path.read_bytes()
        current_config = json.loads(current_config_bytes)
    except (OSError, ValueError, UnicodeError, TypeError):
        raise ValueError('Current reviewed Workers config must be readable strict JSON') from None
    current_worker = None
    current_binding = None
    if isinstance(current_config, dict) and isinstance(current_config.get('env'), dict):
        current_binding = current_config['env'].get(selected_env)
        if isinstance(current_binding, dict):
            current_worker = current_binding.get('name')
    if (not isinstance(current_config, dict) or current_config.get('account_id') != account
            or component.get('deploy', {}).get('account_id') != account
            or not isinstance(current_binding, dict)
            or current_binding.get('account_id', account) != account
            or current_worker != record.get('worker')
            or _service_environment_from_snapshot(record) is None):
        raise ValueError('Workers recovery record does not match the current reviewed config and component identity')
    if (not isinstance(record.get('routes'), list)
            or any(not isinstance(row, dict) or not isinstance(row.get('pattern'), str)
                   or row.get('script') not in (None, record.get('worker')) for row in record['routes'])
            or not isinstance(record.get('custom_domains'), list)
            or any(not isinstance(row, dict) or row.get('service') != record.get('worker')
                   or row.get('environment') != record.get('service_environment') for row in record['custom_domains'])):
        raise ValueError('Workers recovery routing state exceeds the recorded Worker scope')
    if (not re.fullmatch(r'[A-Fa-f0-9-]{16,64}', str(record.get('prior_version_id', '')))
            or record.get('prior_routing_sha256') != workers_site_routing_digest(
                record.get('routes'), record.get('custom_domains'), record.get('subdomain'))):
        raise ValueError('Workers recovery record has invalid version or route/domain state')
    return record, path.parent, hashlib.sha256(current_config_bytes).hexdigest()


def _service_environment_from_snapshot(record):
    value = record.get('service_environment')
    return value if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}', value) else None


def _restore_route(route):
    if not isinstance(route, dict) or not isinstance(route.get('pattern'), str):
        raise ValueError('Workers recovery route record is malformed')
    restored = {'pattern': route['pattern']}
    for key in ('zone_id', 'zone_name', 'custom_domain', 'enabled', 'previews_enabled'):
        if key in route:
            restored[key] = route[key]
    return restored


def _restore_domain(domain):
    if not isinstance(domain, dict) or not isinstance(domain.get('hostname'), str):
        raise ValueError('Workers recovery custom-domain record is malformed')
    restored = {'hostname': domain['hostname']}
    for key in ('zone_id', 'zone_name', 'enabled', 'previews_enabled'):
        if key in domain:
            restored[key] = domain[key]
    return restored


def _scope_digest(scope, value):
    if scope == 'routes':
        return workers_site_routing_digest(value, [], {'enabled': False})
    if scope == 'domains':
        return workers_site_routing_digest([], value, {'enabled': False})
    if scope == 'subdomain':
        return workers_site_routing_digest([], [], value)
    raise ValueError('Unknown Worker recovery scope')


def _read_recovery_routing(api, account, worker, service_env):
    routes = _scoped_routes(api, account, worker, service_env)
    domains = _domains(api, account, worker, service_env)
    if any(domain.get('service') != worker or domain.get('environment') != service_env for domain in domains):
        raise ValueError('Live custom-domain state contains another Worker environment')
    subdomain = _scoped_subdomain(api, account, worker, service_env)
    return {'routes': routes, 'domains': domains, 'subdomain': subdomain}


def _journal_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _mark_recovery_step(journal, name, result):
    journal.data['operations'][name] = {'state': 'completed', 'result': result}
    journal._save()


def _perform_recovery_step(journal, name, inspect_target, action):
    journal.data['state'] = 'partial'
    journal._save()
    try:
        if inspect_target():
            _mark_recovery_step(journal, name, {'reconciled': True})
            return
        if journal.data['operations'].get(name, {}).get('state') == 'completed':
            journal.data['operations'][name] = {'state': 'pending', 'result': {'reopened_after_state_recheck': True}}
            journal._save()

        def guarded_action():
            if inspect_target():
                return {'state': 'completed', 'reconciled': True}
            return action()

        journal.perform(name, guarded_action)
    except Exception:
        if journal.data['operations'].get(name, {}).get('state') != 'failed':
            journal.data['operations'][name] = {'state': 'failed'}
        journal.data['state'] = 'partial'
        journal._save()
        raise


def restore_workers_site_recovery(record_path, component, environment, *, current_config_path, expected_current_version,
                                  expected_current_routing_sha256, confirmation, toolchain, api, runner,
                                  journal_path=None):
    """Restore a saved Worker version, resuming only from journaled original or target states."""
    record_path = Path(record_path)
    record, root, config_sha256 = _load_recovery_record(record_path, component, environment, current_config_path)
    account, worker = record['account_id'], record['worker']
    logical_env, service_env = record['logical_environment'], record['service_environment']
    expected = f'restore:{account}:{worker}:{logical_env}:{service_env}:{record["prior_version_id"]}:{record["record_sha256"]}'
    if confirmation != expected:
        raise ValueError('Explicit recovery confirmation does not match the saved Worker version')
    if not isinstance(expected_current_version, str) or not re.fullmatch(r'[A-Fa-f0-9-]{16,64}', expected_current_version):
        raise ValueError('Recovery requires the expected current Worker version')
    if (not isinstance(expected_current_routing_sha256, str)
            or not re.fullmatch(r'[a-f0-9]{64}', expected_current_routing_sha256)):
        raise ValueError('Recovery requires the reviewed current route/domain digest')
    if not isinstance(record.get('routes'), list) or not isinstance(record.get('custom_domains'), list):
        raise ValueError('Workers recovery record has incomplete route or domain state')
    component_id = component.get('id')
    if not isinstance(component_id, str) or not component_id:
        raise ValueError('Recovery requires a stable component identity')
    identity = {
        'record_sha256': record['record_sha256'], 'component_id': component_id,
        'account_id': account, 'worker': worker, 'logical_environment': logical_env,
        'service_environment': service_env, 'config_sha256': config_sha256,
        'selected_config_environment': component['deploy']['environments'][logical_env],
    }
    journal_path = Path(journal_path) if journal_path is not None else Path(str(record_path) + '.restore.json')
    journal = PromotionJournal(journal_path, record['record_sha256'])
    context = journal.data.get('recovery_context')
    if context is not None:
        if not isinstance(context, dict):
            raise ValueError('Recovery journal start-state snapshot is malformed')
        context_body = {key: value for key, value in context.items() if key != 'context_sha256'}
        if (context.get('context_sha256') != _journal_digest(context_body)
                or any(context_body.get(key) != value for key, value in identity.items())
                or context_body.get('start_version') != expected_current_version
                or context_body.get('start_routing_sha256') != expected_current_routing_sha256):
            raise ValueError('Recovery journal does not match the original reviewed start state and config identity')
    elif journal.data['operations']:
        raise ValueError('Recovery journal has operations without its immutable start-state snapshot')

    live_version = _deployment_version(api, account, worker)
    if _service_environment(api, account, worker) != service_env:
        raise ValueError('Live Worker service environment changed; recovery requires a fresh review')
    live = _read_recovery_routing(api, account, worker, service_env)
    live_routing_digest = workers_site_routing_digest(live['routes'], live['domains'], live['subdomain'])
    if context is None:
        if live_version != expected_current_version:
            raise ValueError('Live current Worker version changed; recovery requires a fresh review')
        if live_routing_digest != expected_current_routing_sha256:
            raise ValueError('Live Worker routes or domains changed; recovery requires a fresh review')
        context_body = {
            **identity, 'start_version': live_version,
            'start_routing_sha256': live_routing_digest,
            'start_routing': live,
        }
        context = {**context_body, 'context_sha256': _journal_digest(context_body)}
        journal.data['recovery_context'] = context
        journal.data['state'] = 'prepared'
        journal._save()
    start = context['start_routing']
    targets = {
        'routes': record['routes'], 'domains': record['custom_domains'], 'subdomain': record['subdomain'],
    }
    target_version = record['prior_version_id']
    for scope in ('routes', 'domains', 'subdomain'):
        actual_digest = _scope_digest(scope, live[scope])
        allowed = {_scope_digest(scope, start[scope]), _scope_digest(scope, targets[scope])}
        if actual_digest not in allowed:
            raise ValueError('Recovery found unexpected live Worker state outside the journaled original or target snapshots')
    if live_version not in {context['start_version'], target_version}:
        raise ValueError('Recovery found unexpected live Worker state outside the journaled original or target snapshots')
    if live_version == target_version and _version_artifact_digest(
            api, account, worker, live_version) != record['artifact_sha256']:
        raise ValueError('Recovery target version no longer matches the retained artifact identity')

    cli = _recovery_cli(toolchain)
    saved_config_path = root / record['config_file']

    def guard_live_state():
        version = _deployment_version(api, account, worker)
        if _service_environment(api, account, worker) != service_env:
            raise ValueError('Recovery found unexpected live Worker state: service environment changed')
        routing = _read_recovery_routing(api, account, worker, service_env)
        if version not in {context['start_version'], target_version}:
            raise ValueError('Recovery found unexpected live Worker state: deployment version changed')
        if version == target_version and _version_artifact_digest(
                api, account, worker, version) != record['artifact_sha256']:
            raise ValueError('Recovery target version no longer matches the retained artifact identity')
        for scope in ('routes', 'domains', 'subdomain'):
            digest = _scope_digest(scope, routing[scope])
            if digest not in {_scope_digest(scope, start[scope]), _scope_digest(scope, targets[scope])}:
                raise ValueError(f'Recovery found unexpected live Worker state: {scope} changed')
        return {'version': version, **routing}

    def inspect_version_target():
        return guard_live_state()['version'] == target_version

    def rollback_version():
        runner([*cli, 'rollback', target_version, '--name', worker, '--yes',
                '--message', 'Studio guarded website recovery', '--config', str(saved_config_path),
                '--env', identity['selected_config_environment']])
        resulting_version = _deployment_version(api, account, worker)
        if (resulting_version != target_version
                or _version_artifact_digest(api, account, worker, resulting_version) != record['artifact_sha256']):
            raise RuntimeError('Worker recovery did not restore the retained artifact content')
        return {'version_id': resulting_version, 'artifact_sha256': record['artifact_sha256']}

    _perform_recovery_step(journal, 'version', inspect_version_target, rollback_version)

    routes_path = f'/accounts/{account}/workers/services/{worker}/environments/{service_env}/routes'
    target_route_digest = _scope_digest('routes', targets['routes'])
    _perform_recovery_step(journal, 'routes',
        lambda: _scope_digest('routes', guard_live_state()['routes']) == target_route_digest,
        lambda: _mutate_and_verify(api, 'PUT', routes_path,
            [_restore_route(row) for row in targets['routes']], lambda: _scope_digest(
                'routes', _scoped_routes(api, account, worker, service_env)), target_route_digest))

    domains_path = f'/accounts/{account}/workers/scripts/{worker}/domains/records?replace_state=true'
    target_domain_digest = _scope_digest('domains', targets['domains'])
    _perform_recovery_step(journal, 'domains',
        lambda: _scope_digest('domains', guard_live_state()['domains']) == target_domain_digest,
        lambda: _mutate_and_verify(api, 'PUT', domains_path, {
            'override_scope': True, 'override_existing_origin': False,
            'override_existing_dns_record': False,
            'origins': [_restore_domain(row) for row in targets['domains']],
        }, lambda: _scope_digest('domains', _domains(api, account, worker, service_env)), target_domain_digest))

    subdomain_path = f'/accounts/{account}/workers/scripts/{worker}/subdomain'
    target_subdomain_digest = _scope_digest('subdomain', targets['subdomain'])
    _perform_recovery_step(journal, 'subdomain',
        lambda: _scope_digest('subdomain', guard_live_state()['subdomain']) == target_subdomain_digest,
        lambda: _mutate_and_verify(api, 'POST', subdomain_path, targets['subdomain'],
            lambda: _scope_digest('subdomain', _scoped_subdomain(api, account, worker, service_env)),
            target_subdomain_digest,
            headers={'Cloudflare-Workers-Script-Api-Date': '2025-08-01'}))

    restored_version = _deployment_version(api, account, worker)
    if restored_version != target_version or _version_artifact_digest(api, account, worker, restored_version) != record['artifact_sha256']:
        raise RuntimeError('Worker recovery did not restore the retained artifact content')
    restored = _read_recovery_routing(api, account, worker, service_env)
    if workers_site_routing_digest(restored['routes'], restored['domains'], restored['subdomain']) != record['prior_routing_sha256']:
        raise RuntimeError('Worker recovery did not restore the saved route and domain state')
    journal.data['state'] = 'completed'
    journal.data['receipt'] = {'version_id': restored_version,
                               'artifact_sha256': record['artifact_sha256'],
                               'prior_routing_sha256': record['prior_routing_sha256']}
    journal._save()
    return {'state': 'completed', 'prior_version_id': target_version,
            'artifact_sha256': record['artifact_sha256'], 'routes': len(record['routes']),
            'custom_domains': len(record['custom_domains']), 'journal': str(journal_path)}


def _mutate_and_verify(api, method, path, data, verify, expected_digest, headers=None):
    _api_mutate(api, method, path, data, headers=headers)
    if verify() != expected_digest:
        raise RuntimeError('Cloudflare did not apply the requested recovery scope')
    return {'state': 'completed', 'verified': True}


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
