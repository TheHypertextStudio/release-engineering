"""Read product declarations without executing release policy from products."""
from dataclasses import dataclass
from pathlib import Path
import re
import yaml

KINDS = {'macos', 'swiftpm', 'pnpm', 'gradle', 'cloud-run', 'cloudflare-worker', 'static-site'}
SHELLS = {'sh', 'bash', 'zsh', 'fish', 'cmd', 'powershell', 'pwsh'}

class ConfigError(ValueError):
    pass

class _DeclarationLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in mapping:
            raise ConfigError('Declaration mapping keys must be unique strings')
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_DeclarationLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


@dataclass(frozen=True)
class Config:
    root: Path
    data: dict

    @property
    def product(self):
        return self.data['product']

    @property
    def components(self):
        return tuple(self.data['components'])

    def __getitem__(self, key):
        return self.data[key]


def argv(value, context):
    if not isinstance(value, list) or not value or any(not isinstance(arg, str) or not arg or '\x00' in arg for arg in value):
        raise ConfigError(f'{context} must be a nonempty argument array')
    if Path(value[0]).name in SHELLS or Path(value[0]).name in {'env', 'eval'}:
        raise ConfigError(f'{context} cannot execute a shell')
    return value


def _reject_executable_policy(value, prefix=''):
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key).lower()
            if name == 'credential_bindings':
                if prefix != 'release.' or not isinstance(child, dict) or any(not re.fullmatch(r'[A-Z][A-Z0-9_]*', variable) or not isinstance(reference, str) or not re.fullmatch(r'projects/[^/]+/secrets/[^/]+/versions/(?:latest|[0-9]+)', reference) for variable, reference in child.items()):
                    raise ConfigError('Credential bindings must map environment names to Secret Manager resources')
                continue
            if name in {'hook', 'hooks', 'release_hooks', 'pre_release', 'post_release', 'secret', 'secrets', 'password', 'api_token', 'private_key', 'token', 'credentials', 'env'} or name.endswith('_password') or name.endswith('_token') or name.endswith('_private_key'):
                raise ConfigError(f'{prefix}{key} cannot contain hooks or credentials')
            if prefix.startswith('release.') and name in {'argv', 'command', 'commands', 'script', 'scripts'}:
                raise ConfigError(f'{prefix}{key} cannot override release commands')
            _reject_executable_policy(child, f'{prefix}{key}.')
    elif isinstance(value, list):
        for child in value:
            _reject_executable_policy(child, prefix)


def _relative_path(root, value, label):
    if not isinstance(value, str) or Path(value).is_absolute() or not (root / value).resolve().is_relative_to(root):
        raise ConfigError(f'{label} must remain inside the repository')


def load_config(path='studio.yaml'):
    path = Path(path).resolve()
    try:
        data = yaml.load(path.read_text(), Loader=_DeclarationLoader)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f'Cannot read declaration: {error}') from error
    if not isinstance(data, dict) or data.get('schema') != 1:
        raise ConfigError('Declaration requires schema 1')
    _reject_executable_policy(data)
    for key in ('product', 'repository', 'owner_repository', 'toolchain', 'components', 'release'):
        if key not in data:
            raise ConfigError(f'Missing {key}')
    if not isinstance(data['product'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*', data['product']):
        raise ConfigError('Invalid product identifier')
    for key in ('repository', 'owner_repository'):
        if not isinstance(data[key], str) or not re.fullmatch(r'[\w.-]+/[\w.-]+', data[key]):
            raise ConfigError(f'{key} must be owner/repository')
    for key in ('toolchain', 'release'):
        if not isinstance(data[key], dict):
            raise ConfigError(f'{key} must be a mapping')
    if not isinstance(data['components'], list) or not data['components']:
        raise ConfigError('At least one component is required')
    ids = set()
    for component in data['components']:
        if not isinstance(component, dict) or component.get('kind') not in KINDS:
            raise ConfigError('Invalid component kind')
        identifier = component.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[\w.-]+', identifier) or identifier in ids:
            raise ConfigError('Component identifiers must be unique')
        ids.add(identifier)
        _relative_path(path.parent, component.get('path'), 'Component path')
        checks = component.get('checks')
        if not isinstance(checks, list):
            raise ConfigError('Component checks must be argument arrays')
        for index, check in enumerate(checks):
            if isinstance(check, dict):
                if not isinstance(check.get('name'), str) or not check['name']:
                    raise ConfigError('Named checks require a name')
                check = check.get('argv')
            argv(check, f'{identifier}.checks[{index}]')
        if component.get('dev') is not None:
            argv(component['dev'], f'{identifier}.dev')
        build = component.get('build', {})
        if not isinstance(build, dict):
            raise ConfigError('Build must use native profile inputs')
        if 'argv' in build:
            raise ConfigError('Build commands are owned by native lifecycle profiles')
        if 'command' in build or 'script' in build and component['kind'] not in {'pnpm', 'static-site', 'cloudflare-worker', 'cloud-run'}:
            raise ConfigError('Build cannot use arbitrary command strings')
    release = data['release']
    for key in ('channels', 'required_checks', 'required_evidence'):
        value = release.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise ConfigError(f'release.{key} must contain names')
    return Config(path.parent, data)
