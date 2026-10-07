"""Project-local workaround for AR76960; never changes tools or system time.

The original generated Tcl is retained. Only its overflowing package revision
is replaced by explicit project revision 1; RTL, interfaces and licenses are
unchanged. This is not the AMD installer-wide patch.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def project_revision(script):
    pattern = r'(?m)^set Revision[ \t]+"([0-9]+)"[ \t]*$'
    matches = list(re.finditer(pattern, script))
    if len(matches) != 1 or int(matches[0][1]) <= 2147483647:
        raise ValueError("Expected exactly one overflowing HLS package revision")
    return re.sub(pattern, 'set Revision "1"', script), int(matches[0][1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ip_dir", type=Path)
    args = parser.parse_args()
    ip_dir = args.ip_dir.resolve()
    original = (ip_dir / "run_ippack.tcl").read_text()
    modified, old_revision = project_revision(original)
    patched = ip_dir / "run_ippack_autohls.tcl"
    with patched.open("x") as handle:
        handle.write(modified)
    record = {"workaround": "project-local AR76960 revision", "old_revision": old_revision,
              "new_revision": 1, "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
              "patched_sha256": hashlib.sha256(modified.encode()).hexdigest()}
    with (ip_dir / "revision-workaround.json").open("x") as handle:
        json.dump(record, handle, indent=2)
    launcher = Path(__file__).resolve().parents[1] / "scripts/cloud-xilinx.sh"
    with (ip_dir / "repackage.log").open("x") as log:
        subprocess.run(["bash", str(launcher), "vivado", "-mode", "batch", "-notrace", "-source", patched.name],
                       cwd=ip_dir, stdout=log, stderr=subprocess.STDOUT, check=True)
    if not (ip_dir / "component.xml").is_file():
        raise RuntimeError("IP packager did not generate component.xml")
    print("Packaged IP with explicit project revision 1:", ip_dir, flush=True)


if __name__ == "__main__":
    main()
