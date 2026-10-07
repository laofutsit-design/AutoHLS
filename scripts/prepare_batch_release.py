"""Freeze this batch-build source in a new archive; no remote access."""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/hardware-batch-20260926"


def main():
    OUTPUT.mkdir(exist_ok=False)
    files = []
    for directory in ("autohls", "benchmarks", "examples", "hardware", "scripts", "tests", "web"):
        files.extend(p for p in (ROOT / directory).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    files.extend(ROOT / p for p in ("server.py", "README.md", "artifacts/resetfix-20260926/bundle/optimized32/kernel.cpp"))
    kernel = files[-1]
    assert hashlib.sha256(kernel.read_bytes()).hexdigest() == "e9d4a2eb4f93e105643b93a6004160753b9c6b7b5208635603872c90db260e2b"
    checksums = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    with (OUTPUT / "source-checksums.json").open("x") as handle:
        json.dump(checksums, handle, indent=2)
    archive = OUTPUT / "autohls-hardware-batch-source.tar.gz"
    with tarfile.open(archive, "x:gz") as handle:
        for path in sorted(files):
            handle.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
        handle.add(OUTPUT / "source-checksums.json", arcname="source-checksums.json")
    print(json.dumps({"archive": str(archive), "files": len(files), "bytes": archive.stat().st_size,
                      "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
