"""Verify an exported snapshot and extract only into a new evidence directory."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def extract(archive, destination, expected_sha256):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("Archive checksum mismatch")
    with zipfile.ZipFile(archive) as handle:
        checksums = json.loads(handle.read("export-checksums.json"))
        if len(handle.namelist()) != len(checksums) + 1 or set(handle.namelist()) != set(checksums) | {"export-checksums.json"}:
            raise ValueError("Unexpected or duplicate archive members")
        for name, expected in checksums.items():
            target = (destination / name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Archive member outside evidence directory")
            if hashlib.sha256(handle.read(name)).hexdigest() != expected:
                raise ValueError("Evidence checksum mismatch: " + name)
        destination.mkdir(parents=True, exist_ok=False)
        handle.extractall(destination)
    return len(checksums)


if __name__ == "__main__":
    archive, destination, expected = sys.argv[1:]
    print(json.dumps({"verified_files": extract(Path(archive), Path(destination), expected), "evidence": destination}))
