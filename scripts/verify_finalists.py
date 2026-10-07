"""Re-rank archived observations, then verify baseline and winner in RTL.

Original search manifests, histories and summaries are never modified.
Run from the release root: python -m scripts.verify_finalists SEARCH OUTPUT
"""
import json
from pathlib import Path
import sys

from autohls.benchmarks import Configuration
from autohls.experiments import file_hash, summarize, write_json
from autohls.vitis import run_synthesis


def select(run):
    manifest = json.loads((run / "manifest.json").read_text())
    original = json.loads((run / "summary.json").read_text())
    if not original["completed_budget"] or original["planner_error"]:
        raise ValueError("Search is incomplete: " + str(run))
    records = [json.loads(line) for line in (run / "history.jsonl").read_text().splitlines()]
    summary = summarize(records, manifest["limits"], manifest["goal"])
    if not summary["best_id"]:
        raise ValueError("No feasible candidate")
    return {
        "run_dir": str(run.resolve()), "benchmark": manifest["benchmark"],
        "part": manifest["device"]["part"], "clock_ns": manifest["clock_ns"],
        "original_best_id": original["best_id"], "summary": summary,
        "original_hashes": {name: file_hash(run / name) for name in
                            ("manifest.json", "history.jsonl", "summary.json", "testbench.cpp")},
        "reranking_reason": "Choose only Pareto-frontier candidates, including score ties.",
        "selected": {role: next(item for item in records if item["id"] == key)
                     for role, key in (("baseline", Configuration().key), ("best", summary["best_id"]))},
        "board_verified": False,
    }


def main():
    search, output = map(Path, sys.argv[1:])
    selections = [select(run) for run in sorted(search.iterdir()) if (run / "manifest.json").is_file()]
    if {item["benchmark"] for item in selections} != {"matmul", "fir", "conv2d"} or len(selections) != 3:
        raise ValueError("Expected one completed run for each benchmark")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "selection.json", {"selections": selections, "board_verified": False,
               "reranking_code_sha256": file_hash(Path("autohls/experiments.py"))})
    results, verified = [], []
    for item in selections:
        run = Path(item["run_dir"])
        tb = run / "testbench.cpp"
        observations = [json.loads(line) for line in (run / "history.jsonl").read_text().splitlines()]
        manifest = json.loads((run / "manifest.json").read_text())
        rejected = set()
        baseline = item["selected"]["baseline"]
        role, record = "baseline", baseline
        while True:
            source = run / record["id"] / "kernel.cpp"
            if file_hash(source) != record["source_sha256"] or file_hash(tb) != item["original_hashes"]["testbench.cpp"]:
                raise ValueError("Evidence changed before verification")
            result = {"benchmark": item["benchmark"], "role": role, "id": record["id"],
                      "source_sha256": file_hash(source), "board_verified": False}
            try:
                result["synthesis"] = run_synthesis(source, item["benchmark"], output / item["benchmark"] / role / record["id"],
                                                   part=item["part"], clock_ns=item["clock_ns"],
                                                   testbench=tb, cosim=True, timeout_seconds=3600)
                if result["synthesis"]["metrics"] != record["metrics"]:
                    raise ValueError("Finalist synthesis metrics differ from archived search")
                result["report_sha256"] = file_hash(Path(result["synthesis"]["report"]))
                result["status"] = "rtl_passed"
            except RuntimeError as exc:
                result.update(status="verification_rejected", error=str(exc))
                rejected.add(record["id"])
            results.append(result)
            print(item["benchmark"], role, record["id"], result["status"], flush=True)
            write_json(output / "results.json", {"results": results, "verified": verified,
                                                  "complete": False, "board_verified": False})
            if role == "baseline" and result["status"] != "rtl_passed":
                raise RuntimeError("Baseline verification failed; stop this benchmark")
            if role == "best" and result["status"] == "rtl_passed":
                verified.append({"benchmark": item["benchmark"], "best_id": record["id"],
                                 "rejected_ids": sorted(rejected), "best_rtl_verified": True,
                                 "hls_speedup": baseline["metrics"]["latency_us"] / record["metrics"]["latency_us"],
                                 "board_verified": False})
                break
            # A failed RTL candidate cannot win even when its HLS estimates are best.
            remaining = [entry for entry in observations if entry["id"] not in rejected]
            key = summarize(remaining, manifest["limits"], manifest["goal"])["best_id"]
            if key is None:
                raise RuntimeError("No remaining feasible candidate")
            role, record = "best", next(entry for entry in remaining if entry["id"] == key)
    write_json(output / "results.json", {"results": results, "verified": verified,
                                          "complete": True, "board_verified": False})


if __name__ == "__main__":
    main()
