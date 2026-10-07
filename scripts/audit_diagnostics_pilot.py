"""Audit the isolated old/new diagnostic pilot without changing any search record."""
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import json
import re
import statistics
import sys

from autohls.experiments import file_hash
from autohls.vitis import build_tcl
from scripts.audit_model_suite import audit_run, audit_rtl, history, read, require
from scripts.diagnostics_pilot import protocol


def check_diagnostics(run, records, arm, plan, *, feedback=True):
    manifest = read(run / "manifest.json")
    clock = manifest["clock_ns"]
    require(clock == plan["clock_ns"], "Diagnostic audit clock changed")
    baseline = records[0]
    require(baseline["status"] == "synthesized", "Need an observed baseline to verify original HLS paths")
    remote = PurePosixPath(baseline["synthesis"]["run_dir"]).parents[2]
    failures = []
    for index, record in enumerate(records):
        candidate = run / record["id"]
        logs = sorted((candidate / "hls").glob("*/vitis.*.log"))
        if record["status"] == "synthesized":
            stdout = next(p for p in logs if p.name == "vitis.stdout.log").read_text(encoding="utf-8")
            expected = [line for line in stdout.splitlines() if any(t in line.lower()
                        for t in ("warning", "violation", "unable", "cannot"))][:40]
            require(record["diagnostics"] == expected, "Successful diagnostics changed")
            continue
        if record["status"] != "failed":
            continue
        source, tb = remote / record["id"] / "kernel.cpp", remote / "testbench.cpp"
        tcls = list((candidate / "hls").glob("*/run_hls.tcl"))
        require(len(tcls) == 1, "Failed synthesis missing its original Tcl")
        require(tcls[0].read_text() == build_tcl(str(source), manifest["benchmark"], plan["part"], clock,
                testbench=str(tb)), "Failed HLS commands differ from protocol")
        require(any(p.name == "vitis.stdout.log" for p in logs), "Missing failed HLS stdout")
        raw = [line for path in logs for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
        errors = [line.replace(str(source), "kernel.cpp").replace(str(tb), "testbench.cpp") for line in raw
                  if any(token in line.lower() for token in ("error:", "cannot", "unable"))]
        expected = sorted(errors, key=lambda line: "error:" not in line.lower())[:40] if arm == "fixed" else []
        require(record.get("diagnostics", []) == expected, "Failure diagnostics differ from raw logs or assigned arm")
        factors = sorted({int(match.group(1)) for line in raw
                          if (match := re.search(r"incorrect partition factor (\d+)", line))})
        next_request = run / f"proposal-{index + 1:03d}/request.json"
        observed = []
        if next_request.is_file():
            prompt = json.loads(read(next_request)["messages"][0]["content"])
            if feedback:
                matches = [item["diagnostics"] for item in prompt["observations"] if item["id"] == record["id"]]
                require(len(matches) == 1, "Next request missing or repeated failed observation")
                observed = matches[0]
            else:
                require(prompt["observations"] == [], "No-feedback request contains observations")
        require(observed == ([line[:240] for line in expected[:3]] if next_request.is_file() and feedback else []),
                "Next request does not contain exactly the available diagnostic excerpt")
        failures.append({"evaluation": index + 1, "id": record["id"], "partition_factors": factors,
                         "has_next_request": next_request.is_file(), "next_request_diagnostics": observed,
                         "raw_log_sha256": {p.relative_to(run).as_posix(): file_hash(p) for p in logs}})
    return {"failures": failures, "partition_failure_count": sum(bool(f["partition_factors"]) for f in failures),
            "partition_failures_after_first": max(0, sum(bool(f["partition_factors"]) for f in failures) - 1),
            "errors_with_next_request": sum(f["has_next_request"] for f in failures),
            "errors_delivered_to_next_request": sum(bool(f["next_request_diagnostics"]) for f in failures)}


def audit(root):
    frozen = read(root / "source-checksums.json")
    for path in (Path(__file__).resolve().parents[1] / "autohls").glob("*.py"):
        require(file_hash(path) == frozen["autohls/" + path.name], "Audit engine differs from frozen fixed engine")
        if path.name != "experiments.py":
            require(frozen["autohls/" + path.name] == frozen["legacy/autohls/" + path.name], "Unplanned engine change")
    for name, digest in frozen.items():
        if name.startswith(("legacy/examples/", "legacy/benchmarks/")):
            require(digest == frozen[name.removeprefix("legacy/")], "Control inputs differ")
    suite = root / "artifacts/model-suite"
    plan, state = read(suite / "protocol.json"), read(suite / "status.json")
    require(plan == protocol() == read(root / "diagnostics-protocol.json"), "Pilot protocol changed")
    require([j["id"] for j in state["jobs"]] == [j["id"] for j in plan["jobs"][:len(state["jobs"])]], "Job order changed")
    rows = []
    for job in plan["jobs"]:
        entry = next((e for e in state["jobs"] if e["id"] == job["id"]), None)
        if not entry or not entry.get("run"):
            rows.append({**job, "audited": False, "state": "pending" if not entry else "missing_evidence"})
            continue
        run = (suite / entry["run"]).resolve()
        require(run.is_relative_to((suite / job["id"]).resolve()), "Run outside job directory")
        engine = {name.removeprefix("legacy/"): value for name, value in frozen.items()
                  if name.startswith("legacy/")} if job["arm"] == "legacy" else frozen
        result = audit_run(run, job, plan, engine)
        require(result["summary"] == entry["summary"], "Runner summary differs from evidence")
        result.update(exit_code=entry["exit_code"], state="audited",
                      diagnostic_evidence=check_diagnostics(run, history(run), job["arm"], plan))
        rows.append(result)
    rtl = audit_rtl(root, rows, plan)
    groups = []
    for arm in ("legacy", "fixed"):
        selected = [r for r in rows if r["arm"] == arm and r["audited"] and r["summary"]["completed_budget"]]
        latencies = [r["best_metrics"]["latency_us"] for r in selected if r["best_metrics"]]
        groups.append({"arm": arm, "planned": 2, "completed": len(selected), "latencies_us": latencies,
                       "median_latency_us": statistics.median(latencies) if latencies else None,
                       "synthesis_failures": sum(r["statuses"].get("failed", 0) for r in selected),
                       "feasible": sum(r["summary"]["feasible_synthesized"] for r in selected),
                       "repeat_orders_identical": len({tuple(r["order"]) for r in selected}) == 1 if len(selected) == 2 else None})
    return {"audited_at_utc": datetime.now(timezone.utc).isoformat(), "protocol": plan, "runs": rows,
            "groups": groups, "rtl": rtl, "complete": state["complete"],
            "all_budgets_completed": state.get("all_budgets_completed", False),
            "all_evidence_audited": all(row["audited"] for row in rows),
            "snapshot_status_sha256": file_hash(suite / "status.json"), "board_verified": False}


if __name__ == "__main__":
    result = audit(Path(sys.argv[1]))
    with Path(sys.argv[2]).open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"complete": result["complete"], "all_evidence_audited": result["all_evidence_audited"],
                      "groups": result["groups"], "rtl": result["rtl"]}, indent=2))
