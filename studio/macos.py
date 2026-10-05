"""Developer ID archives, notarization, and signed Sparkle update artifacts."""
import base64
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse, urljoin
import xml.etree.ElementTree as ET

from .candidate import sha256
from .lifecycle import _xcode_target

SPARKLE_NS = 'http://www.andymatuschak.org/xml-namespaces/sparkle'
MACHO = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'}


class MacOSError(ValueError):
    pass


def execute(command, cwd, *, env=None, input=None):
    """Capture output because native tools may echo secret input on failure."""
    try:
        result = subprocess.run(command, cwd=cwd, env=env, input=input, capture_output=True, text=True, check=False)
    except OSError as error:
        raise MacOSError(f'Cannot run native tool: {Path(command[0]).name}') from error
    if result.returncode:
        raise MacOSError(f'Native tool failed: {Path(command[0]).name} (exit {result.returncode})')
    return result.stdout or result.stderr


def _settings(config, component):
    policy = config['release'].get('macos', {})
    facts = dict(component.get('macos', {}))
    for name in ('app_name', 'bundle_id', 'min_system_version', 'architectures', 'store_configuration', 'store_provisioning_profiles'):
        if name in component:
            facts[name] = component[name]
    declared_entitlements = component.get('entitlements')
    if isinstance(declared_entitlements, dict):
        facts['entitlements'] = declared_entitlements.get('direct')
        facts['store_entitlements'] = declared_entitlements.get('store')
    elif isinstance(declared_entitlements, str):
        facts['entitlements'] = declared_entitlements
    if 'nested_binaries' in component:
        facts['nested_entitlements'] = component['nested_binaries']
    if not isinstance(policy, dict) or not isinstance(facts, dict):
        raise MacOSError('macOS policy and product facts must be mappings')
    for name in ('app_name', 'bundle_id', 'entitlements'):
        if not isinstance(facts.get(name), str) or not facts[name]:
            raise MacOSError(f'macOS component requires {name}')
    if '/' in facts['app_name'] or facts['app_name'].startswith('.'):
        raise MacOSError('Invalid app_name')
    if not isinstance(policy.get('team_id'), str) or not policy['team_id']:
        raise MacOSError('macOS release requires team_id')
    return policy, facts


def _path(config, value):
    if not isinstance(value, str) or not value:
        raise MacOSError('Missing repository path')
    result = (config.root / value).resolve()
    if not result.is_relative_to(config.root.resolve()) or not result.is_file():
        raise MacOSError('Entitlements must name a file inside the repository')
    return result


def entitlements(config, path, *, store=False):
    try:
        values = plistlib.loads(_path(config, path).read_bytes())
    except (OSError, plistlib.InvalidFileException) as error:
        raise MacOSError('Cannot read entitlement plist') from error
    validate_entitlements(values, store=store)
    return values


def validate_entitlements(values, *, store=False):
    if not isinstance(values, dict):
        raise MacOSError('Entitlements must be a dictionary')
    if values.get('com.apple.security.get-task-allow'):
        raise MacOSError('Release entitlements cannot enable get-task-allow')
    if store and values.get('com.apple.security.app-sandbox') is not True:
        raise MacOSError('App Store entitlements must enable app-sandbox')
    if store and any(values.get(key) for key in ('com.apple.security.cs.disable-library-validation', 'com.apple.security.cs.allow-dyld-environment-variables', 'com.apple.security.cs.allow-unsigned-executable-memory')):
        raise MacOSError('App Store entitlements enable unsupported signing exceptions')


def _https(value, label):
    parsed = urlparse(value if isinstance(value, str) else '')
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise MacOSError(f'{label} requires an HTTPS URL')
    return value


def sparkle_public_key(private):
    try:
        decoded=base64.b64decode(private,validate=True)
    except (ValueError,TypeError):
        raise MacOSError('Sparkle private key must be valid base64') from None
    if len(decoded) == 32:
        with tempfile.TemporaryDirectory(prefix='studio-ed25519-') as directory:
            key = Path(directory) / 'key.der'
            key.write_bytes(bytes.fromhex('302e020100300506032b657004220420') + decoded)
            key.chmod(0o600)
            result = subprocess.run(['openssl','pkey','-inform','DER','-in',str(key),'-pubout','-outform','DER'],capture_output=True,check=False)
            if result.returncode or len(result.stdout) != 44:
                raise MacOSError('Native OpenSSL cannot derive the Sparkle public key')
            derived = result.stdout[-32:]
    elif len(decoded) == 96:
        derived = decoded[-32:]
    else:
        raise MacOSError('Sparkle private keys must contain a 32-byte seed or the legacy 96-byte key')
    return base64.b64encode(derived).decode()


def _sparkle(policy, root=None):
    settings = policy.get('sparkle', {})
    if not isinstance(settings, dict):
        raise MacOSError('Sparkle configuration is missing')
    _https(settings.get('feed_url'), 'Sparkle feed')
    _https(settings.get('download_url'), 'Sparkle downloads')
    private = os.environ.get('SPARKLE_PRIVATE_KEY')
    if not private:
        raise MacOSError('SPARKLE_PRIVATE_KEY is missing')
    try:
        public = base64.b64decode(settings.get('public_key', ''), validate=True)
        decoded = base64.b64decode(private, validate=True)
    except (ValueError, TypeError) as error:
        raise MacOSError('Sparkle keys must be valid base64') from error
    derived=base64.b64decode(sparkle_public_key(private))
    if len(public) != 32 or derived != public:
        raise MacOSError('Sparkle public key does not match the private key')
    from .tools import verified_sparkle
    declared = settings.get('tools_path')
    if declared and not Path(declared).is_absolute() and root is not None:
        declared = Path(root) / declared
    try:
        tools = verified_sparkle(declared)
    except ValueError as error:
        raise MacOSError(str(error)) from None
    return settings, private, tools


def preflight(config, component, *, channel='direct', runner=None):
    runner = runner or execute
    policy, facts = _settings(config, component)
    if channel != 'direct' or channel not in config['release'].get('channels', []):
        raise MacOSError('Direct distribution channel is not authorized')
    _sparkle(policy, config.root)
    entitlements(config, facts['entitlements'])
    nested = facts.get('nested_entitlements', {})
    if not isinstance(nested, dict):
        raise MacOSError('nested_entitlements must be a mapping')
    for value in nested.values():
        entitlements(config, value)
    identity = policy.get('developer_id')
    if not isinstance(identity, str) or not identity.startswith('Developer ID Application:'):
        raise MacOSError('Developer ID Application identity is missing')
    identities = runner(['security', 'find-identity', '-v', '-p', 'codesigning'], config.root)
    if identity not in identities:
        raise MacOSError('Developer ID identity and private key are not installed')
    if not policy.get('notary_profile'):
        for name in ('APPLE_API_PRIVATE_KEY', 'APPLE_API_KEY_ID', 'APPLE_API_ISSUER'):
            if not os.environ.get(name):
                raise MacOSError(f'{name} is missing for notarization')
    return {'signing': 'passed', 'sparkle': 'passed', 'entitlements': 'passed'}


def archive(config, component, directory, version, build_number, *, runner, store=False):
    policy, facts = _settings(config, component)
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or type(build_number) is not int or build_number < 1:
        raise MacOSError('Archive requires exact semantic version and positive build number')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    archive_path = directory / 'Application.xcarchive'
    cwd = (config.root / component['path']).resolve()
    configuration = facts.get('store_configuration', 'AppStore') if store else component.get('build', {}).get('configuration', 'Release')
    runner(['xcodebuild', *_xcode_target({**component,'scheme':component.get('store_scheme',component['scheme'])} if store else component), '-configuration', configuration, '-destination', 'generic/platform=macOS', '-jobs', '2', '-archivePath', str(archive_path), f'MARKETING_VERSION={version}', f'CURRENT_PROJECT_VERSION={build_number}', f'DEVELOPMENT_TEAM={policy["team_id"]}', 'CODE_SIGN_STYLE=Manual',f'CODE_SIGN_IDENTITY={policy.get("store",{}).get("application_identity","Apple Distribution") if store else policy["developer_id"]}', *([f'ARCHS={" ".join(facts["architectures"])}','ONLY_ACTIVE_ARCH=NO'] if facts.get('architectures') else []), *([] if store else [f'INFOPLIST_KEY_SUFeedURL={policy["sparkle"]["feed_url"]}', f'INFOPLIST_KEY_SUPublicEDKey={policy["sparkle"]["public_key"]}']), f'CODE_SIGN_ENTITLEMENTS={_path(config, facts["store_entitlements"] if store else facts["entitlements"])}', 'archive'], cwd)
    options = {'method': 'app-store-connect' if store else 'developer-id', 'teamID': policy['team_id'], 'signingStyle': 'manual', 'stripSwiftSymbols': True, 'destination': 'export'}
    if store:
        options['signingCertificate'] = policy.get('store', {}).get('application_identity', 'Apple Distribution')
        profiles = facts.get('store_provisioning_profiles', {})
        if not profiles:
            raise MacOSError('Store export requires explicit provisioning profiles')
        options['provisioningProfiles'] = profiles
    else:
        options['signingCertificate'] = policy['developer_id']
    options_path = directory / 'ExportOptions.plist'
    options_path.write_bytes(plistlib.dumps(options))
    export = directory / 'export'
    runner(['xcodebuild', '-exportArchive', '-archivePath', str(archive_path), '-exportOptionsPlist', str(options_path), '-exportPath', str(export)], cwd)
    app = export / (facts['app_name'] + '.app')
    if not app.is_dir():
        raise MacOSError('Xcode export did not produce the declared app')
    try:
        info = plistlib.loads((app / 'Contents' / 'Info.plist').read_bytes())
    except (OSError, plistlib.InvalidFileException) as error:
        raise MacOSError('Exported app has no valid Info.plist') from error
    expected = {'CFBundleIdentifier': facts['bundle_id'], 'CFBundleShortVersionString': version, 'CFBundleVersion': str(build_number)}
    if any(info.get(key) != value for key, value in expected.items()):
        raise MacOSError('Exported app version, build or bundle identifier differs from candidate')
    minimum = facts.get('min_system_version')
    if minimum is not None and info.get('LSMinimumSystemVersion') != str(minimum):
        raise MacOSError('Exported app minimum system version differs from declaration')
    architectures = facts.get('architectures')
    if architectures is not None:
        if not isinstance(architectures, list) or not architectures or set(architectures) - {'arm64', 'x86_64'}:
            raise MacOSError('Declared macOS architectures are invalid')
        executable = app / 'Contents' / 'MacOS' / info.get('CFBundleExecutable', facts['app_name'])
        actual_architectures = runner(['lipo', '-archs', str(executable)], config.root).strip().split()
        if set(actual_architectures) != set(architectures):
            raise MacOSError('Exported app architectures differ from declaration')
    if not store:
        settings = policy['sparkle']
        if info.get('SUFeedURL') != settings['feed_url'] or info.get('SUPublicEDKey') != settings['public_key']:
            raise MacOSError('Exported app Sparkle feed or public key differs from declaration')
    return app


def _code_targets(app):
    targets = []
    for path in app.rglob('*'):
        if path.is_symlink():
            continue
        if path.is_dir() and path.suffix in {'.app', '.xpc', '.framework', '.bundle', '.appex'}:
            targets.append(path)
        elif path.is_file():
            with path.open('rb') as source:
                if source.read(4) in MACHO:
                    targets.append(path)
    return sorted(targets, key=lambda path: (-len(path.parts), str(path)))


def sign(config, component, app, *, runner):
    policy, facts = _settings(config, component)
    for target in [*_code_targets(app), app]:
        relative = str(target.relative_to(app)) if target != app else '.'
        entitlement = facts['entitlements'] if target == app else facts.get('nested_entitlements', {}).get(relative)
        sparkle_helper = any(parent.name == 'Sparkle.framework' for parent in target.parents)
        if target != app and target.suffix in {'.app', '.xpc', '.appex'} and not entitlement and not sparkle_helper:
            raise MacOSError(f'Nested helper requires explicit entitlements: {relative}')
        command = ['codesign', '--force', '--sign', policy['developer_id'], '--options', 'runtime', '--timestamp']
        if sparkle_helper:
            command += ['--preserve-metadata=entitlements']
        if entitlement:
            command += ['--entitlements', str(_path(config, entitlement))]
        runner([*command, str(target)], config.root)
    runner(['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)], config.root)
    actual = runner(['codesign', '-d', '--entitlements', ':-', str(app)], config.root)
    actual_values = plistlib.loads(actual.encode())
    validate_entitlements(actual_values)
    expected_values = entitlements(config, facts['entitlements'])
    if any(actual_values.get(key) != value for key, value in expected_values.items()):
        raise MacOSError('Signed app does not contain declared entitlements')


def notarize(config, path, *, runner):
    if Path(path).suffix.lower() not in {'.zip', '.dmg', '.pkg'} or not Path(path).is_file():
        raise MacOSError('Apple notarization requires a ZIP, DMG or PKG file')
    policy = config['release']['macos']
    with tempfile.TemporaryDirectory(prefix='studio-notary-') as temporary:
        command = ['xcrun', 'notarytool', 'submit', str(path), '--wait', '--output-format', 'json']
        if policy.get('notary_profile'):
            command += ['--keychain-profile', policy['notary_profile']]
        else:
            key = Path(temporary) / 'AuthKey.p8'
            key.write_text(os.environ['APPLE_API_PRIVATE_KEY'])
            key.chmod(0o600)
            command += ['--key', str(key), '--key-id', os.environ['APPLE_API_KEY_ID'], '--issuer', os.environ['APPLE_API_ISSUER']]
        try:
            result = json.loads(runner(command, config.root))
        except json.JSONDecodeError as error:
            raise MacOSError('Apple notarization returned invalid status') from error
        if result.get('status') != 'Accepted':
            raise MacOSError(f'Apple notarization did not accept artifact (request {result.get("id", "unknown")})')
    return result


def _appcast(config, component, zip_path, version, build_number, *, runner):
    settings, private, tools = _sparkle(config['release']['macos'], config.root)
    original = sha256(zip_path)
    with tempfile.TemporaryDirectory(prefix='studio-sparkle-') as temporary:
        temporary = Path(temporary)
        key = temporary / 'ed25519.key'
        key.write_text(private)
        key.chmod(0o600)
        signature = runner([str(tools / 'sign_update'), '--ed-key-file', str(key), '-p', str(zip_path)], config.root).strip()
        try:
            if len(base64.b64decode(signature, validate=True)) != 64:
                raise ValueError()
        except ValueError as error:
            raise MacOSError('Sparkle returned invalid archive signature') from error
        runner([str(tools / 'sign_update'), '--ed-key-file', str(key), '--verify', str(zip_path), signature], config.root)
        staging = temporary / 'updates'
        staging.mkdir()
        shutil.copy2(zip_path, staging / zip_path.name)
        feed = zip_path.parent / 'appcast.xml'
        runner([str(tools / 'generate_appcast'), '--ed-key-file', str(key), '--download-url-prefix', settings['download_url'], '--maximum-deltas', '0', '-o', str(feed), str(staging)], config.root)
        try:
            tree = ET.parse(feed)
            items = tree.findall('./channel/item')
            matches = [item for item in items if item.findtext(f'{{{SPARKLE_NS}}}version') == str(build_number)]
            if len(matches) != 1:
                raise ValueError()
            item = matches[0]
            enclosure = item.find('enclosure')
            if enclosure is None or item.findtext(f'{{{SPARKLE_NS}}}shortVersionString') != version or enclosure.get('length') != str(zip_path.stat().st_size) or enclosure.get(f'{{{SPARKLE_NS}}}edSignature') != signature or enclosure.get('url') != urljoin(settings['download_url'].rstrip('/') + '/', zip_path.name):
                raise ValueError()
        except (OSError, ET.ParseError, ValueError) as error:
            raise MacOSError('Generated appcast does not describe the final signed ZIP') from error
    if sha256(zip_path) != original:
        raise MacOSError('Sparkle tools changed immutable ZIP bytes')
    return feed


def prepare(config, component, output, version, build_number, channel='direct', *, runner=None):
    runner = runner or execute
    preflight(config, component, channel=channel, runner=runner)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    policy, facts = _settings(config, component)
    app = archive(config, component, output / 'work', version, build_number, runner=runner)
    sign(config, component, app, runner=runner)
    submission = output / 'work' / 'notarization.zip'
    runner(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(submission)], config.root)
    notarize(config, submission, runner=runner)
    runner(['xcrun', 'stapler', 'staple', str(app)], config.root)
    runner(['xcrun', 'stapler', 'validate', str(app)], config.root)
    runner(['spctl', '--assess', '--type', 'execute', '--verbose=2', str(app)], config.root)
    dmg = output / f'{facts["app_name"]}-{version}-{build_number}.dmg'
    runner(['hdiutil', 'create', '-volname', facts['app_name'], '-srcfolder', str(app), '-format', 'ULFO', '-fs', 'APFS', str(dmg)], config.root)
    runner(['codesign', '--sign', policy['developer_id'], '--timestamp', str(dmg)], config.root)
    notarize(config, dmg, runner=runner)
    runner(['xcrun', 'stapler', 'staple', str(dmg)], config.root)
    runner(['xcrun', 'stapler', 'validate', str(dmg)], config.root)
    zip_path = output / f'{facts["app_name"]}-{version}-{build_number}.zip'
    runner(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(zip_path)], config.root)
    feed = _appcast(config, component, zip_path, version, build_number, runner=runner)
    return [zip_path, dmg, feed]
