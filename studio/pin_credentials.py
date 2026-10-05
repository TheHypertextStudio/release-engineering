"""Read the repository-scoped pin writer through workload identity."""
import os
from pathlib import Path
import re

from .providers import execute


def prepare():
    resource = os.environ.get('STUDIO_PIN_UPDATE_SECRET', '')
    match = re.fullmatch(r'projects/([a-z][a-z0-9-]+)/secrets/([a-zA-Z0-9][a-zA-Z0-9_-]*)/versions/(latest|[1-9][0-9]*)', resource)
    if not match:
        raise ValueError('Pin updates require a declared Secret Manager binding')
    target = os.environ.get('GITHUB_OUTPUT')
    if not target:
        raise ValueError('Pin credentials require the GitHub step output file')
    project, secret, version = match.groups()
    value = execute(['gcloud', 'secrets', 'versions', 'access', version, '--secret', secret, '--project', project], capture=True)
    masked = value.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print(f'::add-mask::{masked}')
    delimiter = 'STUDIO_' + os.urandom(16).hex()
    with Path(target).open('a') as output:
        output.write(f'ssh_private_key<<{delimiter}\n{value}\n{delimiter}\n')


if __name__ == '__main__':
    prepare()
