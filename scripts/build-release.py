#!/usr/bin/env python3
"""Package the CLI and pure Python YAML reader into reproducible zip bytes."""
import argparse
import hashlib
import importlib.metadata
import pathlib
import zipfile


def build(output: pathlib.Path) -> str:
    import yaml
    root = pathlib.Path(__file__).resolve().parents[1]
    sources = [(root / "studio", "studio"), (pathlib.Path(yaml.__file__).parent, "yaml")]
    files = {"__main__.py": b"from studio.__main__ import main\nraise SystemExit(main())\n"}
    package=importlib.metadata.distribution('PyYAML')
    license_path=next(path for path in package.files if str(path).endswith('licenses/LICENSE'))
    files['licenses/PyYAML.txt']=package.locate_file(license_path).read_bytes()
    files['licenses/release-engineering.txt']=(root/'LICENSE').read_bytes()
    for directory, prefix in sources:
        for path in sorted(directory.rglob("*.py")):
            files[f"{prefix}/{path.relative_to(directory).as_posix()}"] = path.read_bytes()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name("SHA256SUMS").write_text(f"{digest}  {output.name}\n")
    return digest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path("dist/studio.pyz"))
    arguments = parser.parse_args()
    print(build(arguments.output))
