"""Snapshot completed jobs only; keep active output out of an immutable archive."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def export(root, metadata=None):
    suite = root / "artifacts/model-suite"
    status_bytes = (suite / "status.json").read_bytes()
    status = json.loads(status_bytes)
    paths = [root / name for name in (metadata if metadata is not None else ("source-checksums.json", "suite-protocol.json", "cloud-tests.log",
             "docs/MODEL_SUITE_20260927.md", "scripts/audit_model_suite.py", "scripts/verify_model_suite.py",
             "scripts/export_model_suite.py", "artifacts/model-suite/protocol.json"))]
    for job in status["jobs"]:
        paths.append(suite / (job["id"] + ".log"))
        if job["run"]:
            run = (suite / job["run"]).resolve()
            if not run.is_relative_to(suite.resolve()):
                raise ValueError("Run outside suite")
            paths.extend(p for p in run.rglob("*") if p.is_file())
    rtl = root / "artifacts/model-suite-rtl"
    if (rtl / "results.json").is_file():
        if not json.loads((rtl / "results.json").read_text())["complete"]:
            raise ValueError("Do not archive a partially written RTL verification")
        paths.extend(p for p in rtl.rglob("*") if p.is_file())
    if status["complete"]:
        paths.extend(root / name for name in ("artifacts/model-server.json", "artifacts/model-server.log", "suite-launch.log"))
    destination = root / "artifacts/snapshots"
    destination.mkdir(exist_ok=True)
    archive = destination / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".zip")
    checksums = {}
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(set(paths)):
            if path.suffix not in {".json", ".jsonl", ".csv", ".cpp", ".log", ".xml", ".rpt", ".tcl", ".py", ".md"}:
                continue
            if any(part in {".autopilot", "xsim.dir", "__pycache__"} for part in path.parts):
                continue
            name = path.relative_to(root).as_posix()
            data = path.read_bytes()
            handle.writestr(name, data)
            checksums[name] = hashlib.sha256(data).hexdigest()
        name = "artifacts/model-suite/status.json"
        handle.writestr(name, status_bytes)
        checksums[name] = hashlib.sha256(status_bytes).hexdigest()
        handle.writestr("export-checksums.json", json.dumps(checksums, indent=2))
    print(json.dumps({"archive": str(archive), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                      "bytes": archive.stat().st_size, "files": len(checksums), "finished_jobs": len(status["jobs"]),
                      "complete": status["complete"]}))


if __name__ == "__main__":
    export(Path(sys.argv[1]).resolve())
