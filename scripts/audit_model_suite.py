"""Read-only reconstruction of fixed-suite results from original reports and traces.

Usage: python -m scripts.audit_model_suite EVIDENCE_ROOT REFERENCE_SEARCH OUTPUT
EVIDENCE_ROOT contains source-checksums.json and artifacts/model-suite.
"""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import random
import re
import statistics
import sys

from autohls.benchmarks import BENCHMARKS, Configuration, configurations, config_record, render_candidate
from autohls.experiments import eligible, file_hash, summarize
from autohls.planner import model_options
from autohls.vitis import build_tcl, parse_csynth_xml
from scripts.model_suite import protocol


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def history(run):
    return [json.loads(line) for line in (run / "history.jsonl").read_text(encoding="utf-8").splitlines()]


def audit_candidate_presentation(prompt, request, trace, job, order, iteration):
    remaining = [config_record(c) for c in configurations() if c.key not in order[:iteration]]
    if job.get("coverage_policy") == "pipeline-warmup-v1" and iteration < 3:
        remaining = [c for c in remaining if c["pipeline_ii"] == iteration]
    seed = job.get("candidate_order_seed")
    if seed is not None:
        require(trace.get("candidate_order_seed") == seed + iteration, "Wrong presentation seed")
        random.Random(seed + iteration).shuffle(remaining)
    else:
        require("candidate_order_seed" not in trace, "Unplanned presentation shuffle")
    require(prompt["allowed_candidates"] == remaining, "Wrong remaining candidate list/order")
    require(request["format"] == {"type": "object", "properties": {
        "id": {"type": "string", "enum": [r["id"] for r in remaining]}, "reason": {"type": "string"}},
        "required": ["id", "reason"], "additionalProperties": False}, "Wrong constrained schema")


def audit_run(run, job, plan, frozen, *, benchmark_registry=None):
    benchmarks = BENCHMARKS if benchmark_registry is None else benchmark_registry
    manifest, original, records = read(run / "manifest.json"), read(run / "summary.json"), history(run)
    scheduled = False
    if plan.get("coverage_comparison"):
        require(plan.get("experiment") in ("pipeline-coverage-comparison-v1", "prefixsum-transfer-v1") and
                job.get("coverage_policy") in ("none", "pipeline-warmup-v1") and
                job.get("candidate_order_seed") is None, "Wrong coverage comparison condition")
        require(manifest["candidate_order"] == {"policy": "canonical", "seed": None, "seed_rule": None},
                "Unplanned presentation shuffle")
        scheduled = job["coverage_policy"] != "none"
        if scheduled:
            from scripts.check_coverage_run import check
            check(run)
        else:
            require(not list(run.glob("selection-*.json")), "Unexpected coverage selection evidence")
    if not scheduled:
        require("coverage" not in manifest, "Coverage scheduling requires its own predeclared comparison protocol")
    expected = {"benchmark": job["benchmark"], "planner": "random" if job["group"] == "random" else "ollama",
                "backend": "hls", "seed": job["seed"], "budget": plan["budget"], "goal": plan["goal"],
                "clock_ns": plan["clock_ns"], "limits": plan["limits"], "cosim_requested": False,
                "feedback": job["group"] != "no-feedback"}
    require(all(manifest[k] == v for k, v in expected.items()), "Run differs from fixed protocol")
    if "order_condition" in job:
        order_seed = job["candidate_order_seed"]
        require(manifest["candidate_order"] == {
            "policy": "canonical" if order_seed is None else "remaining-shuffle-v1", "seed": order_seed,
            "seed_rule": None if order_seed is None else "base + evaluation_index"}, "Presentation policy differs")
    require(manifest["device"]["part"] == plan["part"], "Target part changed")
    require(all(frozen["autohls/" + name] == value for name, value in manifest["engine_sha256"].items()), "Engine changed")
    for name, key, frozen_name in (("baseline.cpp", "source_sha256", "examples/" + job["benchmark"] + ".cpp"),
                                    ("testbench.cpp", "testbench_sha256", "benchmarks/" + job["benchmark"] + "_tb.cpp")):
        require(file_hash(run / name) == manifest[key] == frozen[frozen_name], name + " changed")
    computed = summarize(records, manifest["limits"], manifest["goal"])
    require(all(original[k] == v for k, v in computed.items()), "Stored summary does not match observations")
    order = [r["id"] for r in records]
    require(order and order[0] == "p0-u1-a1" and len(order) == len(set(order)), "Baseline missing or repeated IDs")
    allowed = {config.key for config in configurations()}
    require(set(order) <= allowed and len(order) <= plan["budget"], "Invalid candidate or exceeded budget")
    require(original["completed_budget"] == (len(order) == plan["budget"]), "Invalid completion label")
    require(bool(original["planner_error"]) == (not original["completed_budget"]), "Missing/unexpected planner failure")
    for index, record in enumerate(records):
        candidate = run / record["id"]
        require(record["iteration"] == index and read(candidate / "result.json") == record, "Journal/result mismatch")
        require(file_hash(candidate / "kernel.cpp") == record["source_sha256"], "Candidate source changed")
        config = Configuration(record["pipeline_ii"], record["unroll"], record["partition"])
        require(config.key == record["id"] and (candidate / "kernel.cpp").read_text(encoding="utf-8") == render_candidate(benchmarks[job["benchmark"]], config), "Candidate differs from bounded transform")
        verification = record.get("verification", {})
        if verification.get("passed"):
            log = (candidate / "native/test.log").read_text(encoding="utf-8")
            match = re.search(r"^PASS cases=(\d+) checks=(\d+) seed=(\d+)\s*$", log, re.MULTILINE)
            require(match is not None, "Missing native PASS evidence")
            require(tuple(map(int, match.groups())) == tuple(verification[k] for k in ("cases", "checks", "seed")), "Native counts differ")
            if "native_contract" in plan:
                require(all(verification[k] == v for k, v in plan["native_contract"].items()), "Native counts differ from task contract")
        if record["status"] == "synthesized":
            reports = list((candidate / "hls").glob("*/autohls_project/solution1/syn/report/" + job["benchmark"] + "_csynth.xml"))
            require(len(reports) == 1, "Expected exactly one original synthesis report")
            require(file_hash(reports[0]) == record["report_sha256"], "HLS report changed")
            require(parse_csynth_xml(reports[0], plan["clock_ns"]) == record["metrics"] == record["synthesis"]["metrics"], "HLS metrics differ")
            remote_run = PurePosixPath(record["synthesis"]["run_dir"])
            expected_tcl = build_tcl(str(remote_run.parents[1] / "kernel.cpp"), job["benchmark"], plan["part"],
                                     manifest["clock_ns"], testbench=str(remote_run.parents[2] / "testbench.cpp"))
            require((reports[0].parents[4] / "run_hls.tcl").read_text() == expected_tcl, "HLS commands differ from protocol")
            hls_log = (reports[0].parents[4] / "vitis.stdout.log").read_text(encoding="utf-8")
            require("CSim done with 0 errors" in hls_log and "2019.1" in hls_log, "Missing C simulation/tool-version evidence")
    traces, responses = [], []
    source = (run / "baseline.cpp").read_text(encoding="utf-8")
    for directory in sorted(run.glob("proposal-*")):
        iteration = len(traces) + 1
        require(directory.name == f"proposal-{iteration:03d}", "Missing or extra proposal attempt")
        trace, request = read(directory / "trace.json"), read(directory / "request.json")
        traces.append(trace)
        prompt = json.loads(request["messages"][0]["content"])
        require(request["model"] == plan["model"] == manifest["model"], "Model tag changed")
        require(request["options"] == model_options(job["seed"]) == manifest["model_options"], "Model options changed")
        require(trace["seed"] == job["seed"], "Trace seed changed")
        if "identity" in trace:
            require(trace["identity"] == manifest["model_identity"], "Per-call identity changed")
        previous = records[:iteration]
        observations = [{"id": r["id"], "status": r["status"], "metrics": r.get("metrics"),
                         "error": (r.get("error") or "")[:600],
                         "diagnostics": [line[:240] for line in r.get("diagnostics", [])[:3]]} for r in previous]
        require(prompt["observations"] == (observations if manifest["feedback"] else []), "Feedback leakage or altered history")
        audit_candidate_presentation(prompt, request, trace, job, order, iteration)
        benchmark = benchmarks[job["benchmark"]]
        require(prompt["source"] == source and prompt["goal"] == plan["goal"] and prompt["resource_limits"] == plan["limits"], "Wrong model problem")
        require(prompt["target"] == {"part": plan["part"], "clock_ns": plan["clock_ns"],
                "resource_units": manifest["device"]["resource_units"],
                "transform": {"loop": benchmark.loop, "cyclic_partition_arrays": [list(a) for a in benchmark.arrays]}}, "Wrong transform contract")
        if (directory / "response.json").is_file():
            response = read(directory / "response.json")
            require(read(directory / "response.raw.log") == response, "Raw/decoded response differ")
            responses.append(response)
        if trace["success"]:
            response = read(directory / "response.json")
            require(response["done"] is True and response.get("done_reason") != "length", "Incomplete generation")
            content = response["message"]["content"].strip()
            if trace["response_wrapping"] == "json_fence":
                content = "\n".join(content.splitlines()[1:-1])
            choice = json.loads(content)
            require(set(choice) == {"id", "reason"} and isinstance(choice["reason"], str), "Invalid model response")
            require(choice["id"] == trace["selected_id"] == records[iteration]["id"], "Proposal not used as recorded")
        else:
            require(iteration == len(records) and original["planner_error"], "Failure not terminal or omitted")
    if job["group"] == "random":
        require(not traces and manifest["model_identity"] is None, "Model used in random control")
        shuffled = configurations()
        random.Random(job["seed"]).shuffle(shuffled)
        expected_order = ["p0-u1-a1"] + [c.key for c in shuffled if c.key != "p0-u1-a1"]
        if not scheduled:  # Scheduled random was independently reconstructed by check_coverage_run.
            require(order == expected_order[:len(order)], "Random sequence differs from seed")
    else:
        identity = manifest["model_identity"]
        require(identity["model"]["digest"] == plan["model_digest"] and identity["ollama_version"] == plan["ollama_version"], "Wrong model identity")
        require(sum(t["success"] for t in traces) == len(records) - 1, "Missing successful proposal")
        require(sum(not t["success"] for t in traces) == int(bool(original["planner_error"])), "Missing failed proposal")
    require(len(traces) == original["model_attempts"] and sum(not t["success"] for t in traces) == original["model_failures"], "Wrong proposal totals")
    require(abs(sum(t["wall_seconds"] for t in traces) - original["model_wall_seconds"]) < 1e-6, "Wrong model time")
    require(abs(sum(r["evaluation_seconds"] for r in records) - original["evaluation_seconds"]) < 1e-6, "Wrong evaluation time")
    best = next((r for r in records if r["id"] == computed["best_id"]), None)
    # Counters are model-reported diagnostics, not independent timing measurements.
    usage = {"responses_recorded": len(responses)}
    for key in ("prompt_eval_count", "prompt_eval_cached_count", "eval_count", "load_duration", "prompt_eval_duration", "eval_duration"):
        usage[key] = sum(r[key] for r in responses) if len(responses) == len(traces) and all(key in r for r in responses) else None
    return {**job, "audited": True, "run": run.name, "summary": original, "order": order,
            "best_metrics": best["metrics"] if best else None, "model_reported_usage": usage,
            "statuses": dict(Counter(r["status"] for r in records)),
            "source_sha256": manifest["source_sha256"], "testbench_sha256": manifest["testbench_sha256"]}


def add_reference(result, run, reference):
    current_manifest, ref_manifest = read(run / "manifest.json"), read(reference / "manifest.json")
    for key in ("source_sha256", "testbench_sha256", "clock_ns", "device", "limits", "goal"):
        require(current_manifest[key] == ref_manifest[key], "Incomparable reference: " + key)
    ref_records = history(reference)
    require(len(ref_records) == 30 and {r["id"] for r in ref_records} == {c.key for c in configurations()}, "Incomplete reference enumeration")
    require(file_hash(reference / "baseline.cpp") == ref_manifest["source_sha256"] and file_hash(reference / "testbench.cpp") == ref_manifest["testbench_sha256"], "Reference inputs changed")
    for record in ref_records:
        candidate = reference / record["id"]
        require(file_hash(candidate / "kernel.cpp") == record["source_sha256"], "Reference candidate changed")
        if record["status"] == "synthesized":
            reports = list((candidate / "hls").glob("*/autohls_project/solution1/syn/report/" + ref_manifest["benchmark"] + "_csynth.xml"))
            require(len(reports) == 1 and file_hash(reports[0]) == record["report_sha256"], "Reference report changed or missing")
            require(parse_csynth_xml(reports[0], ref_manifest["clock_ns"]) == record["metrics"], "Reference metrics differ from XML")
    ref_summary = summarize(ref_records, ref_manifest["limits"], ref_manifest["goal"])
    best = next(r for r in ref_records if r["id"] == ref_summary["best_id"])
    latency = best["metrics"]["latency_us"]
    result["reference"] = {"run": reference.name, "best_id": best["id"], "latency_us": latency,
                           "history_sha256": file_hash(reference / "history.jsonl"),
                           "scope": "HLS-only 30-point reference, not an RTL/board optimum"}
    result["first_reference_hit"] = next((i + 1 for i, r in enumerate(history(run))
        if eligible(r, current_manifest["limits"]) and r["metrics"]["latency_us"] <= latency), None)
    result["latency_ratio_to_reference"] = result["best_metrics"]["latency_us"] / latency if result["best_metrics"] else None


def aggregate(rows, plan):
    groups = []
    for kernel in dict.fromkeys(j["benchmark"] for j in plan["jobs"]):
        for group in ("random", "no-feedback", "feedback"):
            selected = [r for r in rows if r["benchmark"] == kernel and r["group"] == group]
            finished = [r for r in selected if r.get("audited") and r["summary"]["completed_budget"] and r["best_metrics"]]
            latencies = [r["best_metrics"]["latency_us"] for r in finished]
            groups.append({"benchmark": kernel, "group": group,
                "planned_runs": sum(j["benchmark"] == kernel and j["group"] == group for j in plan["jobs"]),
                "audited_runs": sum(bool(r.get("audited")) for r in selected),
                "complete_with_feasible": len(finished), "latencies_us": latencies,
                "median_latency_us": statistics.median(latencies) if latencies else None,
                "worst_latency_us": max(latencies) if latencies else None,
                "reference_hits": None if plan.get("reference_policy") == "not_measured" else sum(r.get("first_reference_hit") is not None for r in finished),
                "reference_hit_evaluations": None if plan.get("reference_policy") == "not_measured" else [r.get("first_reference_hit") for r in finished],
                "median_wall_seconds": statistics.median(r["summary"]["wall_seconds"] for r in finished) if finished else None,
                "repeat_orders_identical": len({tuple(r["order"]) for r in finished}) == 1 if group != "random" and len(finished) == 2 else None,
                "independent_random_samples": group == "random"})
    return groups


def audit_rtl(root, rows, plan):
    output = root / "artifacts/model-suite-rtl"
    if not (output / "results.json").is_file():
        return None
    rtl = read(output / "results.json")
    require(rtl["complete"] and rtl["board_verified"] is False, "RTL output incomplete or claims board access")
    require([s["job"] for s in rtl["selections"]] == [j["id"] for j in plan["jobs"]], "RTL selections do not cover planned jobs")
    results = {(r["benchmark"], r["id"]): r for r in rtl["results"]}
    require(len(results) == len(rtl["results"]), "Unexpected duplicate RTL execution")
    for key, result in results.items():
        origin = next(s for s in rtl["selections"] if s["job"] == result["origin_job"])
        original_records = history(root / "artifacts/model-suite" / origin["run"])
        original = next(r for r in original_records if r["id"] == result["id"])
        require(result["source_sha256"] == original["source_sha256"] and result["expected_metrics"] == original["metrics"], "RTL record differs from observed candidate")
        origin_row = next(row for row in rows if row["id"] == result["origin_job"])
        require(origin_row["audited"] and result["testbench_sha256"] == origin_row["testbench_sha256"], "RTL testbench differs from audited search")
        tcls = list((output / key[0] / key[1]).glob("*/run_hls.tcl"))
        require(len(tcls) == 1, "Missing or repeated original RTL commands")
        remote_search = PurePosixPath(original["synthesis"]["run_dir"]).parents[2]
        expected_tcl = build_tcl(str(remote_search / result["id"] / "kernel.cpp"), key[0], plan["part"], plan["clock_ns"],
                                 testbench=str(remote_search / "testbench.cpp"), cosim=True)
        require(tcls[0].read_text() == expected_tcl, "RTL commands differ from selected source or protocol")
        stdout = tcls[0].with_name("vitis.stdout.log").read_text(encoding="utf-8")
        stderr = tcls[0].with_name("vitis.stderr.log").read_text(encoding="utf-8")
        failed_log = re.search(r"^(?:ERROR:|FATAL_ERROR:|FAIL\b)", stdout + "\n" + stderr, re.MULTILINE)
        if result["status"] != "rtl_passed":
            require(result["status"] == "verification_rejected" and result.get("error"), "Unknown RTL status")
            # A rejection must be traceable, not just a label used to skip a better candidate.
            reports = list(tcls[0].parent.glob("autohls_project/solution1/syn/report/" + key[0] + "_csynth.xml"))
            changed_metrics = len(reports) == 1 and parse_csynth_xml(reports[0], plan["clock_ns"]) != result["expected_metrics"]
            require(failed_log or changed_metrics, "Missing original rejected RTL failure evidence")
            continue
        require(not failed_log and "CSim done with 0 errors" in stdout and
                "C/RTL co-simulation finished: PASS" in stdout, "RTL final log does not confirm successful C post-check")
        reports = list((output / key[0] / key[1]).glob("*/autohls_project/solution1/syn/report/" + key[0] + "_csynth.xml"))
        require(len(reports) == 1 and file_hash(reports[0]) == result["report_sha256"], "RTL HLS report missing or changed")
        require(reports[0].parents[4] == tcls[0].parent, "RTL report and commands belong to different runs")
        require(parse_csynth_xml(reports[0], plan["clock_ns"]) == result["expected_metrics"] == result["synthesis"]["metrics"], "RTL metrics differ from search")
        sim_reports = list(reports[0].parents[2].glob("sim/report/*_cosim.rpt"))
        require(len(sim_reports) == 1 and re.search(r"\|\s*Verilog\s*\|\s*Pass\s*\|", sim_reports[0].read_text()), "Missing original RTL Pass report")
        require(result["synthesis"]["cosim_passed"] is True, "RTL Pass label missing")
    for row, selection in zip(rows, rtl["selections"]):
        require(row["audited"] and row["id"] == selection["job"], "Cannot attach RTL to unaudited search")
        run = root / "artifacts/model-suite" / selection["run"]
        require(run.resolve().is_relative_to((root / "artifacts/model-suite" / row["id"]).resolve()), "RTL source run outside job")
        records = history(run)
        require(set(selection["input_hashes"]) == {"manifest.json", "summary.json", "history.jsonl"}, "Missing frozen RTL selection inputs")
        for name, expected in selection["input_hashes"].items():
            require(name in {"manifest.json", "summary.json", "history.jsonl"} and file_hash(run / name) == expected, "Search changed after RTL selection")
        require(selection["hls_best_id"] == row["summary"]["best_id"], "HLS best was rewritten after RTL")
        retained = records[:]
        for rejected in selection["rejected_ids"]:
            require(summarize(retained, plan["limits"], plan["goal"])["best_id"] == rejected, "Fallback skipped a better observed candidate")
            require(results[(row["benchmark"], rejected)]["status"] == "verification_rejected", "Rejected candidate has no failed RTL record")
            retained = [r for r in retained if r["id"] != rejected]
        if selection["status"] == "rtl_passed":
            best_id = summarize(retained, plan["limits"], plan["goal"])["best_id"]
            require(selection["best_rtl_id"] == best_id, "Incorrect RTL fallback selection")
            for key in ("p0-u1-a1", best_id):
                observed = next(r for r in records if r["id"] == key)
                verified = results[(row["benchmark"], key)]
                require(verified["status"] == "rtl_passed" and verified["source_sha256"] == observed["source_sha256"] and verified["testbench_sha256"] == row["testbench_sha256"], "RTL result belongs to different source or testbench")
            require(selection["best_metrics"] == next(r for r in records if r["id"] == best_id)["metrics"], "Incorrect RTL best metrics")
        row["rtl"] = selection
    require(rtl["all_searches_have_rtl_finalist"] == all(s["status"] == "rtl_passed" for s in rtl["selections"]), "Wrong RTL aggregate status")
    return {"complete": True, "passed": sum(r["status"] == "rtl_passed" for r in results.values()),
            "rejected": sum(r["status"] == "verification_rejected" for r in results.values()),
            "finalists_passed": sum(s["status"] == "rtl_passed" for s in rtl["selections"]),
            "results_sha256": file_hash(output / "results.json"), "board_verified": False}


def audit_suite(root, references, *, expected_plan=None, failure_diagnostics=False, benchmark_registry=None):
    benchmarks = BENCHMARKS if benchmark_registry is None else benchmark_registry
    frozen = read(root / "source-checksums.json")
    for path in (Path(__file__).resolve().parents[1] / "autohls").glob("*.py"):
        require(file_hash(path) == frozen["autohls/" + path.name], "Audit engine differs from frozen search")
    for benchmark in benchmarks.values():
        require(file_hash(benchmark.source) == frozen["examples/" + benchmark.source.name], "Audit kernel differs from frozen search")
    suite = root / "artifacts/model-suite"
    plan = read(suite / "protocol.json")
    require(plan == (protocol() if expected_plan is None else expected_plan), "Suite plan differs from predeclared protocol")
    no_reference = plan.get("reference_policy") == "not_measured"
    if no_reference:
        require(plan.get("experiment") == "prefixsum-transfer-v1" and references is None,
                "Unplanned reference omission or retrospective reference injection")
    state = read(suite / "status.json")
    entries = {entry["id"]: entry for entry in state["jobs"]}
    require(len(entries) == len(state["jobs"]) and set(entries) <= {j["id"] for j in plan["jobs"]}, "Duplicate/foreign job")
    rows = []
    for job in plan["jobs"]:
        entry = entries.get(job["id"])
        if not entry or not entry["run"]:
            rows.append({**job, "audited": False, "state": "missing_evidence" if entry else "pending",
                         "error": "Job ended without run evidence" if entry else "Not ended in this snapshot",
                         "exit_code": entry["exit_code"] if entry else None})
            continue
        try:
            run = (suite / entry["run"]).resolve()
            require(run.is_relative_to((suite / job["id"]).resolve()), "Job evidence outside its directory")
            result = audit_run(run, job, plan, frozen, benchmark_registry=benchmark_registry)
            if failure_diagnostics:
                from scripts.audit_diagnostics_pilot import check_diagnostics
                result["diagnostic_evidence"] = check_diagnostics(run, history(run), "fixed", plan,
                                                                 feedback=job["group"] != "no-feedback")
            require(entry.get("summary") == result["summary"], "Runner summary differs from original")
            if no_reference:
                result.update(reference=None, first_reference_hit=None, latency_ratio_to_reference=None)
            else:
                matching = list(references.glob("*-" + job["benchmark"]))
                require(len(matching) == 1, "Expected one reference enumeration")
                add_reference(result, run, matching[0])
            rows.append({**result, "state": "audited", "exit_code": entry["exit_code"]})
        except (OSError, ValueError, KeyError, IndexError) as exc:
            rows.append({**job, "audited": False, "state": "audit_failed", "error": str(exc), "exit_code": entry["exit_code"]})
    rtl = audit_rtl(root, rows, plan)
    return {"audited_at_utc": datetime.now(timezone.utc).isoformat(), "rtl": rtl,
            "snapshot_status_sha256": file_hash(suite / "status.json"),
            "complete": state["complete"], "all_evidence_audited": all(r["audited"] for r in rows),
            "protocol": plan, "runs": rows, "groups": aggregate(rows, plan), "board_verified": False,
            "caveat": "Completed-run statistics exclude missing/failed-budget runs; denominators and errors remain explicit. HLS estimates are not board timings."}


if __name__ == "__main__":
    evidence, references, destination = map(Path, sys.argv[1:])
    result = audit_suite(evidence, references)
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"complete": result["complete"], "all_evidence_audited": result["all_evidence_audited"], "groups": result["groups"]}, indent=2))
