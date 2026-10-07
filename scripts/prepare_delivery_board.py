"""Create a receipt-pinned board upload package; never contacts the board."""
import json
from pathlib import Path
import shutil
import sys
import zipfile

from autohls.experiments import file_hash, write_json
from hardware.delivery import verify_delivery

ROOT = Path(__file__).resolve().parents[1]


def prepare(delivery, output):
    receipt = verify_delivery(delivery)
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    runtime.mkdir()
    shutil.copytree(delivery / "artifacts/delivery/bundle", runtime / "bundle")
    board_sources = ("board_hardware_batch.py", "board_batch.py", "board_benchmark.py", "board_test.py", "batch_contract.py")
    for name in board_sources:
        target = runtime / "hardware" / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(delivery / "hardware" / name, target)
    shutil.copyfile(ROOT / "hardware/delivery_acceptance.py", runtime / "hardware/delivery_acceptance.py")
    (runtime / "scripts").mkdir()
    shutil.copyfile(delivery / "scripts/trace_pynq_load.py", runtime / "scripts/trace_pynq_load.py")
    files = {p.relative_to(runtime).as_posix(): file_hash(p) for p in sorted(runtime.rglob("*")) if p.is_file()}
    manifest = {"schema_version": 1, "receipt_sha256": file_hash(runtime / "bundle/delivery.json"),
                "bit_sha256": receipt["bit_sha256"], "runner_sha256": files["hardware/delivery_acceptance.py"],
                "files": files, "board_verified": False,
                "scope": "Separate confirmed loading step, followed by 14 one-attempt guarded functional checks"}
    write_json(runtime / "runtime-manifest.json", manifest)
    archive = output / "pynq-z2-delivery.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as handle:
        for name in sorted(set(files) | {"runtime-manifest.json"}):
            handle.write(runtime / name, name)
    result = {"archive": archive.name, "sha256": file_hash(archive), "files": len(files) + 1,
              "receipt_sha256": manifest["receipt_sha256"], "runner_sha256": manifest["runner_sha256"], "board_verified": False}
    write_json(output / "package.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(prepare(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()), indent=2))
