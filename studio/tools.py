"""Install checksum-pinned native distribution tools in the shared tool cache."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

SPARKLE_VERSION='2.10.0'
SPARKLE_ARCHIVE_SHA256='17e28312b8e18ab7cdbbe09a6fb28cc55a5479ec6c371dbc07cdecd2a14fd959'
SPARKLE_FILES={'sign_update':'43c249771bafc3aa581228abae00731a012d324691b8292860896635050be76b',
               'generate_appcast':'59e48e8432433cf4358519027e67e2c66a7348e0fe415f75f67b3b2372c13844'}


def verified_sparkle(declared=None):
    cache=Path(os.environ.get('XDG_CACHE_HOME',str(Path.home()/'.cache'))) / 'hypertext-studio/distribution/sparkle' / SPARKLE_VERSION
    path=Path(os.environ.get('SPARKLE_TOOLS_PATH') or declared or cache)
    for name,digest in SPARKLE_FILES.items():
        tool=path/name
        if not tool.is_file() or not os.access(tool,os.X_OK) or hashlib.sha256(tool.read_bytes()).hexdigest()!=digest:
            raise ValueError('Official checksum-pinned Sparkle tools are missing; run ./run setup')
    return path


def prepare_sparkle():
    try:
        return verified_sparkle()
    except ValueError:
        pass
    cache=Path(os.environ.get('XDG_CACHE_HOME',str(Path.home()/'.cache'))) / 'hypertext-studio/distribution/sparkle' / SPARKLE_VERSION
    cache.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='studio-sparkle-install-') as directory:
        archive=Path(directory)/'Sparkle.zip'
        url=f'https://github.com/sparkle-project/Sparkle/releases/download/{SPARKLE_VERSION}/Sparkle-for-Swift-Package-Manager.zip'
        subprocess.run(['curl','--fail','--location','--retry','2','--max-time','60','--silent','--show-error',url,'--output',str(archive)],check=True)
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=SPARKLE_ARCHIVE_SHA256:
            raise ValueError('Sparkle tool archive checksum mismatch')
        with zipfile.ZipFile(archive) as bundle:
            for name,digest in SPARKLE_FILES.items():
                matches=[item for item in bundle.namelist() if item.endswith('/bin/'+name) or item=='bin/'+name]
                if len(matches)!=1:
                    raise ValueError('Sparkle archive does not contain the expected official tools')
                data=bundle.read(matches[0])
                if hashlib.sha256(data).hexdigest()!=digest:
                    raise ValueError('Sparkle executable checksum mismatch')
                target=cache/name
                temporary=cache/(name+'.new')
                temporary.write_bytes(data); temporary.chmod(0o755); temporary.replace(target)
    return verified_sparkle(cache)
