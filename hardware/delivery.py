"""Audited search -> frozen build -> offline firmware receipt. No board access.

The first reviewed adapter accepts coverage42 matmul finalists only. New search
protocols or hardware contracts need explicit adapters, not guessed compatibility.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import zipfile

from autohls.benchmarks import BENCHMARKS, Configuration, render_candidate
from autohls.experiments import file_hash, write_json
from hardware.batch_contract import inspect_bundle
from scripts.import_model_suite import extract

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "matmul32-batch64-v1"


def source_files():
    files = [ROOT / "server.py", ROOT / "README.md"]
    for directory in ("autohls", "hardware", "scripts", "examples", "benchmarks", "tests", "docs", "web"):
        files.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts)
    return sorted(files)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def member(root, name):
    require(isinstance(name, str) and "\\" not in name and ":" not in name,
            "Invalid evidence path")
    relative = PurePosixPath(name)
    require(not relative.is_absolute() and ".." not in relative.parts and name not in {"", "."},
            "Invalid evidence path")
    target = (root / name).resolve()
    require(target.is_relative_to(root.resolve()), "Evidence path escapes root")
    return target


def verify_files(root, checksums):
    require(isinstance(checksums, dict) and bool(checksums), "Missing file checksums")
    for name, expected in checksums.items():
        require(file_hash(member(root, name)) == expected, "Checksum mismatch: " + name)
    return len(checksums)


def select_input(evidence, audit, job):
    require(audit.get("complete") is True and audit.get("all_evidence_audited") is True,
            "Incomplete search audit")
    require(audit["protocol"].get("experiment") == "pipeline-coverage-comparison-v1" and
            audit["protocol"]["part"] == "xc7z020clg400-1" and audit["protocol"]["clock_ns"] == 10,
            "Unreviewed search protocol")
    rows = [row for row in audit["runs"] if row["id"] == job]
    require(len(rows) == 1, "Unknown or ambiguous job")
    row = rows[0]
    require(row["benchmark"] == "matmul" and row.get("audited") is True and
            row["summary"]["completed_budget"] is True and row["rtl"]["status"] == "rtl_passed",
            "A completed, audited matmul RTL finalist is required")
    suite = evidence / "artifacts/model-suite"
    run = member(suite, row["rtl"]["run"])
    require(run.is_relative_to(member(suite, job)), "Search outside selected job")
    verify_files(run, row["rtl"]["input_hashes"])
    records = [json.loads(line) for line in (run / "history.jsonl").read_text().splitlines()]
    selected = [record for record in records if record["id"] == row["rtl"]["best_rtl_id"]]
    require(len(selected) == 1, "Missing or duplicate finalist")
    record = selected[0]
    config = Configuration(**{key: record[key] for key in ("pipeline_ii", "unroll", "partition")})
    require(config.key == record["id"], "Candidate ID/configuration mismatch")
    source = member(run, config.key + "/kernel.cpp")
    require(file_hash(source) == record["source_sha256"], "Selected source changed")
    require(source.read_text(encoding="utf-8") == render_candidate(BENCHMARKS["matmul"], config),
            "Source outside reviewed matmul contract")
    require(file_hash(run / "testbench.cpp") == row["testbench_sha256"], "Testbench changed")
    rtl = read(evidence / "artifacts/model-suite-rtl/results.json")
    matches = [r for r in rtl["results"] if r["benchmark"] == "matmul" and r["id"] == config.key]
    require(rtl["complete"] is True and len(matches) == 1 and matches[0]["status"] == "rtl_passed" and
            matches[0]["source_sha256"] == record["source_sha256"] and
            matches[0]["testbench_sha256"] == row["testbench_sha256"], "RTL source binding mismatch")
    return row, record, source, run


def prepare(archive, archive_sha256, frozen, references, job, output):
    # Audit under the original engine, not a silently updated working tree.
    frozen = frozen.resolve()
    verify_files(frozen, read(frozen / "source-checksums.json"))
    output.mkdir(parents=True, exist_ok=False)
    evidence = output / "search-evidence"
    count = extract(archive, evidence, archive_sha256)
    require(file_hash(evidence / "source-checksums.json") == file_hash(frozen / "source-checksums.json"),
            "Frozen code and archive differ")
    with (output / "search-audit.log").open("x", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-m", "scripts.model_coverage_suite", "audit", str(evidence.resolve()),
                        str(references.resolve()), str((output / "search-audit.json").resolve())],
                       cwd=frozen, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    audit = read(output / "search-audit.json")
    row, record, source, run = select_input(evidence, audit, job)
    release = output / "release"
    release.mkdir()
    # Copy only project sources; no local keys, machine settings or old binaries.
    for path in source_files():
        target = release / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    inputs = release / "inputs"
    inputs.mkdir()
    for name, path in (("kernel.cpp", source), ("testbench.cpp", run / "testbench.cpp"),
                       ("audit.json", output / "search-audit.json"),
                       ("rtl-results.json", evidence / "artifacts/model-suite-rtl/results.json")):
        shutil.copyfile(path, inputs / name)
    shutil.copytree(run, inputs / "search")
    shutil.copytree(evidence / "artifacts/model-suite-rtl/matmul" / record["id"], inputs / "rtl")
    provenance = {"schema_version": 1, "contract": CONTRACT, "job": job, "candidate": record["id"],
                  "source_sha256": file_hash(source), "testbench_sha256": file_hash(run / "testbench.cpp"),
                  "search_archive_sha256": archive_sha256, "search_archive_files": count,
                  "frozen_search_code_sha256": file_hash(frozen / "source-checksums.json"),
                  "audit_sha256": file_hash(inputs / "audit.json"), "selection": row["rtl"],
                  "search_metrics": record["metrics"], "board_verified": False,
                  "created_utc": datetime.now(timezone.utc).isoformat(),
                  "note": "Historical search finalist; new wrapper/build. HLS estimates are not board timings."}
    write_json(inputs / "provenance.json", provenance)
    checksums = {p.relative_to(release).as_posix(): file_hash(p) for p in sorted(release.rglob("*")) if p.is_file()}
    write_json(release / "source-checksums.json", checksums)
    verify_input(release)
    package = output / "delivery-source.tar.gz"
    with tarfile.open(package, "x:gz") as handle:
        for name in sorted(checksums):
            handle.add(release / name, arcname=name, recursive=False)
        handle.add(release / "source-checksums.json", arcname="source-checksums.json")
    write_json(output / "launch.json", {"archive": package.name, "sha256": file_hash(package),
               "source_files": len(checksums), "job": job, "candidate": record["id"], "board_verified": False})
    return read(output / "launch.json")


def verify_input(root):
    checksums = read(root / "source-checksums.json")
    verify_files(root, checksums)
    required = {"inputs/provenance.json", "inputs/audit.json", "inputs/kernel.cpp", "inputs/testbench.cpp",
                "hardware/hls/matmul_batch_axi.cpp", "hardware/hls/matmul_batch_axi_tb.cpp",
                "hardware/hls/build_batch_axi.tcl", "hardware/build_overlay.tcl", "scripts/cloud-build-delivery.sh"}
    require(required <= set(checksums), "Incomplete frozen build inputs")
    provenance = read(root / "inputs/provenance.json")
    require(provenance["contract"] == CONTRACT and provenance["board_verified"] is False,
            "Unreviewed delivery contract")
    for key, name in (("source_sha256", "kernel.cpp"), ("testbench_sha256", "testbench.cpp"), ("audit_sha256", "audit.json")):
        require(file_hash(root / "inputs" / name) == provenance[key], "Provenance mismatch: " + key)
    audit = read(root / "inputs/audit.json")
    require(audit["complete"] is True and audit["all_evidence_audited"] is True, "Missing audited search")
    rows = [r for r in audit["runs"] if r["id"] == provenance["job"]]
    require(len(rows) == 1 and rows[0]["rtl"] == provenance["selection"] and
            provenance["selection"]["status"] == "rtl_passed" and
            provenance["selection"]["best_rtl_id"] == provenance["candidate"], "Selection binding mismatch")
    records = [json.loads(line) for line in (root / "inputs/search/history.jsonl").read_text().splitlines()]
    selected = [r for r in records if r["id"] == provenance["candidate"]]
    require(len(selected) == 1 and selected[0]["source_sha256"] == provenance["source_sha256"] and
            selected[0]["metrics"] == provenance["search_metrics"], "Selected candidate binding mismatch")
    return provenance


def seal(root):
    from hardware.package_bundle import collect
    provenance = verify_input(root)
    work = root / "artifacts/delivery/build"
    require(file_hash(work / "kernel.cpp") == provenance["source_sha256"], "Build used another candidate")
    native = (work / "native-check.log").read_text()
    require("PASS cases=11 checks=720896 seed=20260926" in native and "FAIL" not in native,
            "Missing wrapper native verification")
    destination = root / "artifacts/delivery/bundle"
    manifest = collect(work, destination, hardware_batch=True)
    for name, source in (("native-check.log", work / "native-check.log"),
                         ("hls.log", work / "hls.log"), ("vivado-build.log", work / "vivado-build.log"),
                         ("provenance.json", root / "inputs/provenance.json"),
                         ("source-checksums.json", root / "source-checksums.json")):
        shutil.copyfile(source, destination / name)
    inspect_bundle(destination)
    receipt = {"schema_version": 1, "contract": CONTRACT, "job": provenance["job"],
               "candidate": provenance["candidate"], "source_sha256": provenance["source_sha256"],
               "provenance_sha256": file_hash(root / "inputs/provenance.json"),
               "build_code_sha256": file_hash(root / "source-checksums.json"),
               "manifest_sha256": file_hash(destination / "manifest.json"),
               "bit_sha256": manifest["sha256"]["autohls_matmul.bit"],
               "hwh_sha256": manifest["sha256"]["autohls_matmul.hwh"],
               "state": "awaiting_board_confirmation", "board_verified": False,
               "created_utc": datetime.now(timezone.utc).isoformat(),
               "scope": "Native + wrapper RTL + routed timing; no board performance claim"}
    write_json(destination / "delivery.json", receipt)
    write_json(destination / "checksums.json", {p.name: file_hash(p) for p in sorted(destination.iterdir()) if p.is_file()})
    return receipt


def verify_delivery(root):
    provenance = verify_input(root)
    bundle = root / "artifacts/delivery/bundle"
    verify_files(bundle, read(bundle / "checksums.json"))
    manifest = inspect_bundle(bundle)
    receipt = read(bundle / "delivery.json")
    require(receipt["contract"] == CONTRACT and receipt["board_verified"] is False and
            receipt["state"] == "awaiting_board_confirmation", "Unexpected offline delivery state")
    for key, expected in (("job", provenance["job"]), ("candidate", provenance["candidate"]),
                          ("source_sha256", provenance["source_sha256"]),
                          ("provenance_sha256", file_hash(root / "inputs/provenance.json")),
                          ("build_code_sha256", file_hash(root / "source-checksums.json")),
                          ("manifest_sha256", file_hash(bundle / "manifest.json")),
                          ("bit_sha256", manifest["sha256"]["autohls_matmul.bit"]),
                          ("hwh_sha256", manifest["sha256"]["autohls_matmul.hwh"])):
        require(receipt[key] == expected, "Delivery binding mismatch: " + key)
    require(file_hash(bundle / "kernel.cpp") == provenance["source_sha256"], "Firmware built from another source")
    require(file_hash(bundle / "matmul_axi.cpp") == file_hash(root / "hardware/hls/matmul_batch_axi.cpp"),
            "Firmware wrapper differs from frozen input")
    status = (root / "artifacts/delivery/build/build.status").read_text().splitlines()
    require(status and status[0] == "exit_code=0", "Build did not complete successfully")
    return receipt


def export(root):
    verify_delivery(root)
    bundle = root / "artifacts/delivery/bundle"
    archive = root / "artifacts/delivery/delivery-evidence.zip"
    # Include the actual frozen sources, search selection and build receipt.
    names = set(read(root / "source-checksums.json")) | {"source-checksums.json"}
    names.update(p.relative_to(root).as_posix() for p in bundle.iterdir() if p.is_file())
    names.add("artifacts/delivery/build/build.status")
    work = root / "artifacts/delivery/build"
    for pattern in ("*.log", "*.tcl", "axi_project/solution1/impl/ip/*ippack*.tcl",
                    "axi_project/solution1/impl/ip/repackage.log", "axi_project/solution1/sim/report/*"):
        names.update(p.relative_to(root).as_posix() for p in work.glob(pattern) if p.is_file())
    checksums = {}
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as handle:
        for name in sorted(names):
            path = member(root, name)
            handle.write(path, name)
            checksums[name] = file_hash(path)
        handle.writestr("export-checksums.json", json.dumps(checksums, indent=2))
    return {"archive": str(archive), "sha256": file_hash(archive), "files": len(checksums), "board_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare_parser = commands.add_parser("prepare")
    for name in ("archive", "frozen", "references", "output"):
        prepare_parser.add_argument("--" + name, type=Path, required=True)
    prepare_parser.add_argument("--archive-sha256", required=True)
    prepare_parser.add_argument("--job", required=True)
    for name in ("verify-input", "verify-delivery", "seal", "export"):
        commands.add_parser(name).add_argument("root", type=Path)
    args = vars(parser.parse_args())
    action = args.pop("action")
    result = prepare(**args) if action == "prepare" else {"verify-input": verify_input, "verify-delivery": verify_delivery,
                                                       "seal": seal, "export": export}[action](args["root"].resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
