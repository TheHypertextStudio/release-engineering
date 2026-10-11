"""Observe external documentation publication; never build or publish a site."""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Mintlify API redirects are not allowed')


class MintlifyClient:
    def deployment(self, identity):
        _identifier(identity, 'deployment identifier')
        key = os.environ.get('MINTLIFY_API_KEY')
        if not key:
            raise ValueError('MINTLIFY_API_KEY is missing; the Mintlify REST API requires Pro or Enterprise and a read-scoped admin key')
        if not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', key):
            raise ValueError('MINTLIFY_API_KEY has an invalid format')
        request = Request('https://api.mintlify.com/v1/project/update-status/' + identity,
                          headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}, method='GET')
        try:
            with build_opener(NoRedirect()).open(request, timeout=30) as response:
                content = response.read(2_000_001)
                if len(content) > 2_000_000:
                    raise ValueError('Mintlify API response is too large')
                return json.loads(content)
        except HTTPError as error:
            error.close()
            raise ValueError(f'Mintlify API request failed (HTTP {error.code}); verify subscription and read-scoped key') from None
        except (URLError, OSError, json.JSONDecodeError):
            raise ValueError('Mintlify API request did not return valid JSON') from None


class GitHubClient:
    def request(self, path):
        result = subprocess.run(['gh', 'api', path, '--paginate', '--slurp'], capture_output=True, text=True)
        if result.returncode:
            raise ValueError('GitHub evidence lookup failed; verify read access to the source repository and checks')
        try:
            pages = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise ValueError('GitHub evidence lookup did not return valid JSON') from None
        if '/check-runs?' in path:
            return pages
        if not isinstance(pages, list) or len(pages) != 1:
            raise ValueError('GitHub returned ambiguous branch ancestry evidence')
        return pages[0]


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value):
        raise ValueError(f'Invalid {label}')
    return value


def _https(value, label):
    if not isinstance(value, str):
        raise ValueError(f'{label} must be an HTTPS URL')
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError(f'{label} must be an HTTPS URL without credentials, query, or fragment')
    return url


def settings(config, project):
    _identifier(project, 'documentation project key')
    projects = config.data.get('documentation', {})
    binding = projects.get(project) if isinstance(projects, dict) else None
    if not isinstance(binding, dict) or binding.get('provider') != 'mintlify':
        raise ValueError('Documentation project requires an explicit Mintlify binding')
    for key in ('organization', 'project', 'project_id'):
        _identifier(binding.get(key), key)
    path = binding.get('path')
    if not isinstance(path, str) or not path or Path(path).is_absolute() or not (config.root / path).resolve().is_relative_to(config.root) or not (config.root / path).is_dir():
        raise ValueError('Documentation source path must be a directory inside the repository')
    branch = binding.get('branch')
    if not isinstance(branch, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', branch) or any(part in ('', '.', '..') for part in branch.split('/')) or '..' in branch or branch.endswith(('/', '.lock', '.')):
        raise ValueError('Documentation requires a valid deployment branch')
    upstream = _https(binding.get('upstream'), 'Mintlify upstream')
    allowed = {binding['project'] + suffix for suffix in ('.mintlify.site', '.mintlify.dev')}
    if upstream.hostname not in allowed or upstream.path not in ('', '/') or upstream.port:
        raise ValueError('Mintlify upstream must be the explicit project proxy origin')
    _https(binding.get('public_url'), 'Public docs URL')
    checks = binding.get('required_checks')
    if not isinstance(checks, list) or not checks or any(not isinstance(name, str) or not name for name in checks) or len(set(checks)) != len(checks):
        raise ValueError('Documentation requires unique source check names')
    return binding


def inspect(config, project, source_sha, deployment_id, *, github=None, mintlify=None):
    binding = settings(config, project)
    if not isinstance(source_sha, str) or not re.fullmatch(r'[a-f0-9]{40}', source_sha):
        raise ValueError('Documentation source must be a full reviewed commit SHA')
    _identifier(deployment_id, 'deployment identifier')
    github, mintlify = github or GitHubClient(), mintlify or MintlifyClient()
    repository = config['repository']
    comparison = github.request(f'repos/{repository}/compare/{source_sha}...{quote(binding["branch"], safe="")}')
    if not isinstance(comparison, dict) or comparison.get('status') not in ('ahead', 'identical'):
        raise ValueError('Reviewed documentation source is not on the deployment branch')
    pages = github.request(f'repos/{repository}/commits/{source_sha}/check-runs?filter=latest&per_page=100')
    if not isinstance(pages, list) or any(not isinstance(page, dict) or not isinstance(page.get('check_runs'), list) for page in pages):
        raise ValueError('Malformed GitHub source check evidence')
    checks = [check for page in pages for check in page['check_runs']]
    evidence = []
    for name in binding['required_checks']:
        matching = [check for check in checks if isinstance(check, dict) and check.get('name') == name]
        if len(matching) != 1:
            raise ValueError(f'Required documentation check is missing or ambiguous: {name}')
        check = matching[0]
        if check.get('head_sha') != source_sha or check.get('status') != 'completed' or check.get('conclusion') != 'success' or check.get('app', {}).get('slug') != 'github-actions':
            raise ValueError(f'Required documentation source check did not pass: {name}')
        url = _https(check.get('details_url'), 'Source check evidence URL')
        if url.hostname != 'github.com' or not url.path.startswith('/' + repository + '/actions/') or not isinstance(check.get('id'), int):
            raise ValueError('Source check evidence must identify this repository and check run')
        evidence.append({'name': name, 'id': check['id'], 'status': 'passed', 'url': check['details_url']})
    update = mintlify.deployment(deployment_id)
    if not isinstance(update, dict) or update.get('_id') != deployment_id or update.get('projectId') != binding['project_id'] or update.get('subdomain') != binding['project']:
        raise ValueError('Mintlify returned a different deployment or project')
    commit = update.get('commit')
    if not isinstance(commit, dict) or commit.get('sha') != source_sha or commit.get('ref') != 'refs/heads/' + binding['branch']:
        raise ValueError('Mintlify deployment does not match the reviewed source SHA and branch')
    states = {'queued': 'provider-pending', 'in_progress': 'provider-pending', 'failure': 'failed', 'success': 'completed'}
    if update.get('status') not in states:
        raise ValueError('Mintlify returned an unknown deployment status')
    return {
        'schema': 1, 'provider': 'mintlify', 'repository': repository,
        'project_key': project, 'organization': binding['organization'], 'project': binding['project'],
        'project_id': binding['project_id'], 'source_path': binding['path'], 'source_sha': source_sha,
        'branch': binding['branch'], 'deployment_id': deployment_id, 'checks': evidence,
        'upstream': binding['upstream'], 'public_url': binding['public_url'],
        'provider_status': update['status'], 'state': states[update['status']],
        'created_at': update.get('createdAt'), 'ended_at': update.get('endedAt'),
        'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'provider_url': f'https://app.mintlify.com/{binding["organization"]}/{binding["project"]}/activity',
    }


def record(config, result):
    """Keep each observed publisher state; no artifact identity is implied."""
    binding = settings(config, result.get('project_key'))
    for key, expected in [('repository', config['repository']), ('project_id', binding['project_id']), ('provider', 'mintlify')]:
        if result.get(key) != expected:
            raise ValueError('Documentation observation differs from the configured identity')
    _identifier(result.get('deployment_id'), 'deployment identifier')
    if not isinstance(result.get('source_sha'), str) or not re.fullmatch(r'[a-f0-9]{40}', result['source_sha']):
        raise ValueError('Documentation record requires a full source SHA')
    target = config.root / '.studio' / 'documentation' / result['project_key'] / result['source_sha'] / (result['deployment_id'] + '.json')
    if not target.resolve().is_relative_to(config.root) or any(path.is_symlink() for path in (target, *target.parents) if path != config.root):
        raise ValueError('Documentation record cannot escape the repository through a symlink')
    observations = []
    if target.exists():
        previous = json.loads(target.read_text())
        for key in ('provider', 'repository', 'project_key', 'project_id', 'source_sha', 'deployment_id'):
            if previous.get(key) != result.get(key):
                raise ValueError('Existing documentation observation has a different identity')
        observations = previous.get('observations', [])
        if not isinstance(observations, list):
            raise ValueError('Existing documentation history is malformed')
    observations = [*observations, {key: result[key] for key in ('observed_at', 'state', 'provider_status', 'created_at', 'ended_at')}]
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False) as temporary:
        temporary.write(json.dumps({**result, 'observations': observations}, indent=2) + '\n')
        temporary_path = Path(temporary.name)
    temporary_path.replace(target)
    return target
