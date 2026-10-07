"""Freeze a new pilot and its unchanged legacy engine; no remote access."""
from pathlib import Path
import tarfile

from autohls.experiments import file_hash, write_json
from scripts.diagnostics_pilot import protocol

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/model-diagnostics-20260927/v2"
LEGACY = ROOT / "artifacts/model-suite-20260927/frozen-review-v3"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=False)
    files = {}
    for directory in ("autohls", "benchmarks", "examples", "hardware", "scripts", "tests"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files[path.relative_to(ROOT).as_posix()] = path
    for directory in ("autohls", "benchmarks", "examples"):
        for path in (LEGACY / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files["legacy/" + path.relative_to(LEGACY).as_posix()] = path
    for name in ("server.py", "docs/DIAGNOSTICS_PILOT_20260927.md"):
        files[name] = ROOT / name
    for name, path in files.items():
        if name.startswith(("legacy/benchmarks/", "legacy/examples/")):
            assert file_hash(path) == file_hash(ROOT / name.removeprefix("legacy/")), name
    write_json(OUTPUT / "diagnostics-protocol.json", protocol())
    files["diagnostics-protocol.json"] = OUTPUT / "diagnostics-protocol.json"
    write_json(OUTPUT / "source-checksums.json", {name: file_hash(path) for name, path in sorted(files.items())})
    files["source-checksums.json"] = OUTPUT / "source-checksums.json"
    archive = OUTPUT / "source.tar.gz"
    with tarfile.open(archive, "x:gz") as handle:
        for name, path in sorted(files.items()):
            info = handle.gettarinfo(str(path), arcname=name)
            if name in {"scripts/cloud-bin/vivado_hls", "scripts/cloud-bin/vivado", "scripts/cloud-xilinx.sh"}:
                info.mode |= 0o111
            with path.open("rb") as source:
                handle.addfile(info, source)
    print({"archive": str(archive), "sha256": file_hash(archive), "bytes": archive.stat().st_size})


if __name__ == "__main__":
    main()
