"""Create portable cloud evidence archives; exclude binaries except board bundle."""
from pathlib import Path
import sys
import zipfile

from autohls.experiments import file_hash, write_json


def archive(destination, root, paths):
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(paths):
            handle.write(path, path.relative_to(root).as_posix())


def main():
    root = Path.cwd()
    preboard = root / "artifacts/preboard"
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Include journals, inputs and externally useful logs/reports, not compiler DBs.
    evidence = []
    for path in preboard.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(preboard)
        if rel.parts[0] in ("distribution", "distribution-final"):
            continue
        if any(part in (".autopilot", "ipcache", "xsim.dir", "ipshared", "webtalk", "__pycache__") for part in rel.parts):
            continue
        if path.suffix in (".json", ".jsonl", ".csv", ".rpt", ".log", ".status"):
            evidence.append(path)
        elif path.suffix in (".cpp", ".tcl", ".xml", ".h") and (
                path.name in ("kernel.cpp", "baseline.cpp", "testbench.cpp", "run_hls.tcl", "run_ippack.tcl",
                              "run_ippack_autohls.tcl", "overlay_bd.tcl", "xmatmul_axi_hw.h", "component.xml")
                or path.name.endswith("_csynth.xml")):
            evidence.append(path)
    archive(output / "preboard-evidence.zip", root, evidence)
    distribution = preboard / "distribution-final"
    archive(output / "pynq-z2-preboard.zip", distribution, [p for p in distribution.rglob("*") if p.is_file()])
    sources = []
    for name in ("autohls", "benchmarks", "examples", "hardware", "scripts", "tests", "web", "docs"):
        sources.extend(p for p in (root / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    sources.extend(root / name for name in ("README.md", "server.py"))
    archive(output / "preboard-source.zip", root, sources)
    write_json(output / "archives.json", {path.name: {"sha256": file_hash(path), "bytes": path.stat().st_size}
                                          for path in output.glob("*.zip")})
    print("Archives:", output)


if __name__ == "__main__":
    main()
