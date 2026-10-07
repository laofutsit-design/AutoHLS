"""Offline-only batch bundle collection; old single-matrix drivers must reject it."""
from pathlib import Path
import shutil
import sys
import zipfile

from autohls.experiments import file_hash, write_json
from hardware.batch_contract import validate_batch_interface


def main():
    from hardware.package_bundle import collect, ROOT
    work, destination = map(Path, sys.argv[1:])
    manifest = collect(work, destination, hardware_batch=True)
    for name, source in (("matmul_batch_axi_tb.cpp", ROOT / "hardware/hls/matmul_batch_axi_tb.cpp"),
                         ("build_batch_axi.tcl", ROOT / "hardware/hls/build_batch_axi.tcl"),
                         ("native-check.log", work / "native-check.log")):
        shutil.copyfile(source, destination / name)
        manifest["sha256"][name] = file_hash(destination / name)
    write_json(destination / "manifest.json", manifest)
    write_json(destination / "checksums.json", {p.name: file_hash(p) for p in destination.iterdir() if p.is_file()})
    archive = destination.parent / "pynq-z2-hardware-batch.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(destination.iterdir()):
            output.write(path, path.name)
    print("OFFLINE_BATCH_BUNDLE", archive.name, file_hash(archive), archive.stat().st_size)


if __name__ == "__main__":
    main()
