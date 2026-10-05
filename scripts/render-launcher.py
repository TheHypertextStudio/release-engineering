#!/usr/bin/env python3
"""Generate a consumer launcher pinned to immutable verified release bytes."""
import argparse
import pathlib
import re


def render(version: str, digest: str) -> str:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise ValueError("version must be a vMAJOR.MINOR.PATCH release tag")
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("SHA-256 must contain 64 lowercase hex digits")
    template = pathlib.Path(__file__).resolve().parents[1] / "assets/run.sh.in"
    return template.read_text().replace("@VERSION@", version).replace("@SHA256@", digest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    arguments = parser.parse_args()
    try:
        result = render(arguments.version, arguments.sha256)
    except ValueError as error:
        parser.error(str(error))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(result)
    arguments.output.chmod(0o755)
