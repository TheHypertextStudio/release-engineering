"""Resolve a product's lifecycle root without following checkout symlinks."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
def _candidate_error(message):
    from .candidate import CandidateError
    return CandidateError(message)


def validate_product_root(value):
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise _candidate_error('Product root must be a safe relative path')
    if '\\' in value or PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise _candidate_error('Product root must be a safe relative path')
    if value != '.' and (value.startswith('/') or any(part in {'', '.', '..'} for part in value.split('/'))):
        raise _candidate_error('Product root must be a canonical checkout-relative path')
    return value


def resolve_product_root(checkout, value='.'):
    value = validate_product_root(value)
    checkout = Path(checkout).absolute()
    if checkout.is_symlink():
        raise _candidate_error('Product checkout cannot be a symlink')
    root = checkout
    if value != '.':
        for part in value.split('/'):
            root = root / part
            if root.is_symlink():
                raise _candidate_error('Product root cannot traverse a symlink')
    if not root.is_dir():
        raise _candidate_error('Product root is not a directory')
    for name in ('studio.yaml', 'studio.lock.json'):
        manifest = root / name
        if manifest.is_symlink() or not manifest.is_file():
            raise _candidate_error(f'Product root requires a regular {name}')
    return root


def repository_relative_root(root):
    root = Path(root).resolve()
    try:
        result = __import__('subprocess').run(
            ['git', 'rev-parse', '--show-toplevel'], cwd=root, check=True,
            capture_output=True, text=True)
        if not isinstance(result.stdout, str):
            return '.'
        top = Path(result.stdout.strip()).resolve()
        relative = root.relative_to(top).as_posix()
    except __import__('subprocess').CalledProcessError:
        # Older unit/integration callers construct temporary configs without a Git checkout.
        return '.'
    except (OSError, TypeError, ValueError) as error:
        raise _candidate_error('Product root must be inside its source repository') from error
    return validate_product_root(relative if relative != '.' else '.')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('product_root', nargs='?', default='.')
    parser.add_argument('--checkout', type=Path, default=Path.cwd())
    parser.add_argument('--tooling-revision')
    args = parser.parse_args(argv)
    value = validate_product_root(args.product_root)
    root = resolve_product_root(args.checkout, value)
    if args.tooling_revision:
        try:
            lock = json.loads((root / 'studio.lock.json').read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise _candidate_error('Product lock is unreadable') from error
        if lock.get('revision') != args.tooling_revision:
            raise _candidate_error('Product lock does not match the requested tooling revision')
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a') as target:
            target.write(f'product_root={value}\nproduct_root_path={root}\n')
    print(root)


if __name__ == '__main__':
    main()
