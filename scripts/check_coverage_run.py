"""Read-only schedule/evidence checks; NOT HLS, RTL or model-quality validation.

Usage: python -m scripts.check_coverage_run RUN_DIR [RUN_DIR ...] --output NEW_JSON
Independent of the scheduling helper: reconstruct the declared rule from IDs.
"""
import argparse
import json
from pathlib import Path
import random

from autohls.benchmarks import configurations, config_record
from autohls.experiments import file_hash
from scripts.audit_model_suite import history, read, require


def check(run):
    run = run.resolve()
    checksums = read(run / "checksums.json")

    def checked(relative):
        path = (run / relative).resolve()
        require(path.is_relative_to(run), "Evidence path outside run")
        require(relative in checksums and file_hash(path) == checksums[relative], "Evidence changed: " + relative)
        return read(path)

    manifest, summary = checked("manifest.json"), checked("summary.json")
    require(manifest.get("coverage") == {"policy": "pipeline-warmup-v1", "baseline_first": True,
        "warmup_pipeline_ii": [0, 1, 2], "counting": "evaluation attempts including failures",
        "after_warmup": "all remaining candidates"}, "Wrong coverage contract")
    require(manifest["planner"] in ("random", "ollama") and 3 <= manifest["budget"] <= 30, "Wrong planner/budget")
    require(manifest["candidate_order"] == {"policy": "canonical", "seed": None, "seed_rule": None},
            "Coverage cannot be mixed with presentation shuffle")
    require(file_hash(run / "history.jsonl") == checksums["history.jsonl"], "History changed")
    records = history(run)
    require(1 <= len(records) <= manifest["budget"], "Invalid evaluation count")
    require(summary["evaluated"] == len(records) and summary["completed_budget"] == (len(records) == manifest["budget"]),
            "Wrong budget accounting")
    failed = bool(summary["planner_error"])
    require(failed == (len(records) < manifest["budget"]), "Missing or unexpected terminal proposal error")
    attempts = len(records) + int(failed)
    require({p.name for p in run.glob("selection-*.json")} == {f"selection-{i:03d}.json" for i in range(attempts)},
            "Missing or extra selection attempt")
    remaining, rng = configurations(), random.Random(manifest["seed"])
    for index in range(attempts):
        # Deliberately do not call coverage_candidates: a scheduler bug must not
        # automatically be accepted by its evidence checker.
        allowed = [c for c in remaining if (c.key == "p0-u1-a1" if index == 0 else
                   c.pipeline_ii == index if index < 3 else True)]
        ids = [c.key for c in allowed]
        require(checked(f"selection-{index:03d}.json") == {
            "policy": "pipeline-warmup-v1", "iteration": index, "allowed_ids": ids}, "Wrong scheduled pool")
        if manifest["planner"] == "ollama" and index:
            prefix = f"proposal-{index:03d}/"
            request, trace = checked(prefix + "request.json"), checked(prefix + "trace.json")
            prompt = json.loads(request["messages"][0]["content"])
            require(prompt["allowed_candidates"] == [config_record(c) for c in allowed] and
                    request["format"]["properties"]["id"]["enum"] == ids, "Wrong model candidate pool/schema")
            observations = [{"id": r["id"], "status": r["status"], "metrics": r.get("metrics"),
                "error": (r.get("error") or "")[:600], "diagnostics": [s[:240] for s in r.get("diagnostics", [])[:3]]}
                for r in records[:index]]
            require(prompt["observations"] == (observations if manifest["feedback"] else []), "Feedback leakage/history changed")
            require(trace["success"] == (index < len(records)), "Proposal success does not match evaluation")
            if index < len(records):
                response = checked(prefix + "response.json")
                require(checked(prefix + "response.raw.log") == response, "Raw/decoded response differ")
                content = response["message"]["content"].strip()
                if trace["response_wrapping"] == "json_fence":
                    content = "\n".join(content.splitlines()[1:-1])
                require(json.loads(content)["id"] == trace["selected_id"] == records[index]["id"], "Proposal not used")
        if index == len(records):
            require(checked("planner-error.json")["iteration"] == index, "Wrong terminal failure position")
            continue
        record = records[index]
        require(record["iteration"] == index and record["id"] in ids, "Repeated/out-of-pool candidate")
        require(checked(record["id"] + "/result.json") == record, "History/result differ")
        selected = next(c for c in allowed if c.key == record["id"])
        require(all(record[k] == v for k, v in config_record(selected).items()), "Candidate fields differ from ID")
        if manifest["planner"] == "random":
            require(selected == (allowed[0] if index == 0 else rng.choice(allowed)), "Random seed/selection differs")
        remaining.remove(selected)
    proposals = list(run.glob("proposal-*"))
    expected_proposals = attempts - 1 if manifest["planner"] == "ollama" else 0
    require({p.name for p in proposals} == {f"proposal-{i:03d}" for i in range(1, expected_proposals + 1)},
            "Missing or extra proposal directory")
    require(summary["model_attempts"] == expected_proposals and
            summary["model_failures"] == (int(failed) if manifest["planner"] == "ollama" else 0), "Wrong model counts")
    return {"run": str(run), "scope": "schedule and selection evidence only; not HLS/RTL/performance validation",
            "schedule_valid": True, "completed_budget": summary["completed_budget"], "evaluations": len(records),
            "selection_attempts": attempts, "warmup_attempts": [r["pipeline_ii"] for r in records[:3]],
            "manifest_sha256": file_hash(run / "manifest.json"), "history_sha256": file_hash(run / "history.jsonl")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = [check(run) for run in args.runs]
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"checked_runs": len(result), "output": str(args.output)}))


if __name__ == "__main__":
    main()
