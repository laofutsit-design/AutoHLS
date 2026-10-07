"""Freeze source and the predeclared multi-kernel comparison protocol."""
import hashlib
import json
from pathlib import Path
import tarfile

from scripts.model_suite import protocol

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/model-suite-20260927/v1"


def main(output=OUTPUT, expected_plan=None, document="docs/MODEL_SUITE_20260927.md", extra_documents=()):
    output.mkdir(parents=True, exist_ok=False)
    plan = output / "suite-protocol.json"
    plan.write_text(json.dumps(protocol() if expected_plan is None else expected_plan, indent=2) + "\n", encoding="utf-8")
    files = []
    for directory in ("autohls", "benchmarks", "examples", "hardware", "scripts", "tests", "web"):
        files.extend(p for p in (ROOT / directory).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    files.extend(ROOT / name for name in ("server.py", "README.md", document, *extra_documents))
    checksums = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    checksums["suite-protocol.json"] = hashlib.sha256(plan.read_bytes()).hexdigest()
    checksum_path = output / "source-checksums.json"
    checksum_path.write_text(json.dumps(checksums, indent=2) + "\n", encoding="utf-8")
    archive = output / "autohls-model-suite-source.tar.gz"
    with tarfile.open(archive, "x:gz") as handle:
        for path in sorted(files):
            name = path.relative_to(ROOT).as_posix()
            info = handle.gettarinfo(str(path), arcname=name)
            if name in {"scripts/cloud-bin/vivado_hls", "scripts/cloud-bin/vivado", "scripts/cloud-xilinx.sh"}:
                info.mode |= 0o111
            with path.open("rb") as source:
                handle.addfile(info, source)
        handle.add(plan, arcname=plan.name)
        handle.add(checksum_path, arcname=checksum_path.name)
    print(json.dumps({"archive": str(archive), "files": len(checksums), "bytes": archive.stat().st_size,
                      "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
