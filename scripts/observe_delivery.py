"""Read-only cloud progress snapshot. Does not start, retry or cancel work."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


def observe(root):
    files = {"launcher": root / "launch.status", "regression": root / "cloud-tests.log",
             "native": root / "artifacts/delivery/build/native-check.log",
             "hls": root / "artifacts/delivery/build/hls.log",
             "route": root / "artifacts/delivery/build/vivado-build.log",
             "build_status": root / "artifacts/delivery/build/build.status"}
    result = {"observed_utc": datetime.now(timezone.utc).isoformat(), "release": root.name,
              "kind": "read_only_log_snapshot", "board_verified": False, "logs": {}}
    for name, path in files.items():
        if path.is_file():
            result["logs"][name] = "\n".join(path.read_text(errors="replace").splitlines()[-8:])
    if (root / "export-result.json").is_file():
        result["export"] = json.loads((root / "export-result.json").read_text())
    return result


if __name__ == "__main__":
    print(json.dumps(observe(Path(sys.argv[1]).resolve()), indent=2))
