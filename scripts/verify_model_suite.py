"""Fresh, deduplicated RTL checks and per-run fallback; never modifies searches.

Run after the 27 search jobs: python -m scripts.verify_model_suite RELEASE_ROOT
"""
import json
import os
from pathlib import Path
import sys

from autohls.experiments import file_hash, summarize, write_json
from autohls.vitis import run_synthesis
from scripts.audit_model_suite import history, read, require
from scripts.model_suite import protocol


def verify(root, synthesize=run_synthesis, *, expected_plan=None):
    suite = root / "artifacts/model-suite"
    plan, state = read(suite / "protocol.json"), read(suite / "status.json")
    require(plan == (protocol() if expected_plan is None else expected_plan) and state["complete"],
            "Finish the frozen suite before RTL verification")
    require([entry["id"] for entry in state["jobs"]] == [job["id"] for job in plan["jobs"]], "Unexpected job schedule")
    output = root / "artifacts/model-suite-rtl"
    output.mkdir(parents=True, exist_ok=False)
    results, selections, cache = [], [], {}
    final = {"complete": False, "results": results, "selections": selections, "board_verified": False}

    for job in state["jobs"]:
        if not job["run"]:
            selections.append({"job": job["id"], "status": "missing_search", "board_verified": False})
            continue
        run = (suite / job["run"]).resolve()
        require(run.is_relative_to((suite / job["id"]).resolve()), "Search outside job directory")
        manifest, records, original = read(run / "manifest.json"), history(run), read(run / "summary.json")
        require(manifest["benchmark"] == job["benchmark"] and manifest["clock_ns"] == plan["clock_ns"] and manifest["limits"] == plan["limits"] and manifest["goal"] == plan["goal"], "Search protocol mismatch")
        computed = summarize(records, manifest["limits"], manifest["goal"])
        require(all(original[k] == v for k, v in computed.items()), "Search summary changed")
        tb = run / "testbench.cpp"
        require(file_hash(tb) == manifest["testbench_sha256"], "Testbench changed")
        selection = {"job": job["id"], "benchmark": job["benchmark"], "run": job["run"],
                     "search_completed": original["completed_budget"], "hls_best_id": computed["best_id"],
                     "best_rtl_id": None, "rejected_ids": [], "board_verified": False,
                     "input_hashes": {name: file_hash(run / name) for name in ("manifest.json", "history.jsonl", "summary.json")}}
        selections.append(selection)

        def check(record):
            source = run / record["id"] / "kernel.cpp"
            require(file_hash(source) == record["source_sha256"], "Candidate changed before RTL")
            key = (job["benchmark"], record["source_sha256"], manifest["testbench_sha256"])
            if key in cache:
                require(cache[key]["expected_metrics"] == record["metrics"], "Same source produced different HLS estimates")
                return cache[key]
            result = {"benchmark": job["benchmark"], "id": record["id"], "source_sha256": record["source_sha256"],
                      "testbench_sha256": manifest["testbench_sha256"], "origin_job": job["id"],
                      "expected_metrics": record["metrics"], "board_verified": False}
            try:
                synthesis = synthesize(source, job["benchmark"], output / job["benchmark"] / record["id"],
                    part=plan["part"], clock_ns=plan["clock_ns"], testbench=tb, cosim=True, timeout_seconds=3600)
                result["synthesis"] = synthesis
                require(synthesis["cosim_passed"] and synthesis["metrics"] == record["metrics"], "RTL missing or metrics changed")
                result.update(status="rtl_passed", report_sha256=file_hash(Path(synthesis["report"])))
            except (RuntimeError, ValueError) as exc:
                result.update(status="verification_rejected", error=str(exc))
            cache[key] = result
            results.append(result)
            print(job["benchmark"], record["id"], result["status"], flush=True)
            write_json(output / "results.json", final)
            return result

        baseline = next(r for r in records if r["id"] == "p0-u1-a1")
        if baseline.get("metrics") is None or check(baseline)["status"] != "rtl_passed":
            selection["status"] = "baseline_rejected"
            write_json(output / "results.json", final)
            continue
        while True:
            retained = [r for r in records if r["id"] not in selection["rejected_ids"]]
            best_id = summarize(retained, manifest["limits"], manifest["goal"])["best_id"]
            if best_id is None:
                selection["status"] = "no_rtl_feasible_candidate"
                break
            record = next(r for r in retained if r["id"] == best_id)
            result = check(record)
            if result["status"] == "rtl_passed":
                selection.update(status="rtl_passed", best_rtl_id=best_id, best_metrics=record["metrics"],
                                 hls_speedup=baseline["metrics"]["latency_us"] / record["metrics"]["latency_us"],
                                 rtl_evidence_key=[job["benchmark"], record["source_sha256"], manifest["testbench_sha256"]])
                break
            selection["rejected_ids"].append(best_id)
        write_json(output / "results.json", final)
    final["complete"] = True
    final["all_searches_have_rtl_finalist"] = all(s["status"] == "rtl_passed" for s in selections)
    write_json(output / "results.json", final)
    return final


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    os.environ["PATH"] = str(root / "scripts/cloud-bin") + os.pathsep + os.environ["PATH"]
    result = verify(root)
    if not result["all_searches_have_rtl_finalist"]:
        sys.exit(1)
