"""Derive release versions from conventional commits and CI sequences."""
import re
import subprocess


def _version(value):
    match = re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', str(value))
    if not match:
        raise ValueError('Version must be numeric major.minor.patch')
    return tuple(map(int, match.groups()))


def next_version(previous, commits, override=None):
    current = _version(previous)
    if override is not None:
        target = _version(override)
        if target <= current:
            raise ValueError('Explicit next version must advance the released version')
        return '.'.join(map(str, target))
    bump = 0
    for message in commits:
        subject = message.splitlines()[0] if message else ''
        match = re.match(r'([a-z]+)(?:\([^\n)]+\))?(!)?: ', subject)
        if match and match.group(2) or re.search(r'(?m)^BREAKING[ -]CHANGE:', message):
            bump = max(bump, 3)
        elif match and match.group(1) == 'feat':
            bump = max(bump, 2)
        elif match and match.group(1) == 'fix':
            bump = max(bump, 1)
    major, minor, patch = current
    if not bump:
        bump = 1
    return f'{major + 1}.0.0' if bump == 3 else f'{major}.{minor + 1}.0' if bump == 2 else f'{major}.{minor}.{patch + 1}'


def build_number(offset, run_number):
    try:
        if type(offset) not in {int, str} or type(run_number) not in {int, str}:
            raise ValueError
        offset, run_number = int(offset), int(run_number)
        if offset < 0 or run_number < 1:
            raise ValueError
    except (ValueError, TypeError) as error:
        raise ValueError('Build sequence requires a nonnegative offset and positive CI run number') from error
    return offset + run_number


def version_from_git(root, override=None, *, previous=None):
    def git(*args):
        return subprocess.run(['git', *args], cwd=root, check=True, text=True, capture_output=True).stdout.strip()
    if previous is not None:
        baseline=previous['version']
        source=previous['source_sha']
        if not re.fullmatch('[0-9a-f]{40}',source):
            raise ValueError('Production version requires its immutable source revision')
        git('merge-base','--is-ancestor',source,'HEAD')
        revision=f'{source}..HEAD'
    else:
        try:
            tag = git('describe', '--tags', '--abbrev=0', '--match', 'v[0-9]*')
            baseline = tag
            revision = f'{tag}..HEAD'
        except subprocess.CalledProcessError:
            baseline, revision = '0.0.0', 'HEAD'
    commits = git('log', '--format=%B%x00', revision).split('\x00')
    # A committed override is consumed once that version reaches production.
    if previous is not None and override is not None and _version(override) == _version(baseline):
        override = None
    return next_version(baseline, [message.strip() for message in commits if message.strip()], override)
