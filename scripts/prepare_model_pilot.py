"""Freeze the bounded model pilot source; does not access cloud or board."""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/model-schema-20260927/v4"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    files = []
    for directory in ("autohls", "benchmarks", "examples", "hardware", "scripts", "tests", "web"):
        files.extend(p for p in (ROOT / directory).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    files.extend(ROOT / name for name in ("server.py", "README.md", "docs/MODEL_PILOT_20260926.md", "docs/MODEL_SCHEMA_20260927.md"))
    checksums = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    with (OUTPUT / "source-checksums.json").open("x", encoding="utf-8") as handle:
        json.dump(checksums, handle, indent=2)
    archive = OUTPUT / "autohls-model-pilot-source.tar.gz"
    with tarfile.open(archive, "x:gz") as handle:
        for path in sorted(files):
            handle.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
        handle.add(OUTPUT / "source-checksums.json", arcname="source-checksums.json")
    print(json.dumps({"archive": str(archive), "files": len(files), "bytes": archive.stat().st_size,
                      "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
