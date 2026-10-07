"""Explicit operations for a fixed 42-job coverage comparison; no board access."""
import json
import os
from pathlib import Path
import sys

from scripts import model_suite, prepare_model_suite
from scripts.audit_model_suite import aggregate, audit_suite, read, require
from scripts.export_model_suite import export
from scripts.verify_model_suite import verify

ROOT = Path(__file__).resolve().parents[1]
POLICY = "pipeline-warmup-v1"
DOCUMENT = "docs/MODEL_COVERAGE_SUITE_PLAN_20260927.md"


def protocol():
    plan = model_suite.protocol()
    plan.update(experiment="pipeline-coverage-comparison-v1", coverage_comparison=True, jobs=[])
    schedule = [("random", "none", 0, 0), ("random", POLICY, 0, 0),
                ("no-feedback", POLICY, 0, 0), ("feedback", POLICY, 0, 0),
                ("feedback", POLICY, 0, 1), ("no-feedback", POLICY, 0, 1)]
    for seed in range(1, 5):
        policies = ("none", POLICY) if seed % 2 == 0 else (POLICY, "none")
        schedule += [("random", policy, seed, 0) for policy in policies]
    for benchmark in ("matmul", "fir", "conv2d"):
        for group, policy, seed, repeat in schedule:
            plan["jobs"].append({"id": f"{benchmark}-{group}-{policy}-s{seed}-r{repeat}",
                "benchmark": benchmark, "group": group, "coverage_policy": policy,
                "seed": seed, "repeat": repeat})
    return plan


def aggregate_conditions(rows, plan):
    groups = []
    for policy in ("none", POLICY):
        selected = [r for r in rows if r["coverage_policy"] == policy]
        subset = {**plan, "jobs": [j for j in plan["jobs"] if j["coverage_policy"] == policy]}
        groups.extend({**g, "coverage_policy": policy} for g in aggregate(selected, subset) if g["planned_runs"])
    return groups


def audit(root, references):
    plan = protocol()
    state = read(root / "artifacts/model-suite/status.json")
    require([j["id"] for j in state["jobs"]] == [j["id"] for j in plan["jobs"][:len(state["jobs"])]], "Job schedule changed")
    require(state["complete"] == (len(state["jobs"]) == len(plan["jobs"])), "Wrong completion status")
    result = audit_suite(root, references, expected_plan=plan, failure_diagnostics=True)
    result["groups"] = aggregate_conditions(result["runs"], plan)
    return result


def main():
    action = sys.argv[1]
    if action == "prepare":
        prepare_model_suite.main(ROOT / "artifacts/model-coverage-suite-20260927/launch-v1", protocol(), DOCUMENT)
    elif action == "run":
        model_suite.main(expected_plan=protocol())
    elif action == "audit":
        result = audit(Path(sys.argv[2]), Path(sys.argv[3]))
        with Path(sys.argv[4]).open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({"complete": result["complete"], "audited_runs": sum(r["audited"] for r in result["runs"]),
                          "all_evidence_audited": result["all_evidence_audited"], "rtl": result["rtl"]}))
    elif action == "rtl":
        root = Path(sys.argv[2]).resolve()
        os.environ["PATH"] = str(root / "scripts/cloud-bin") + os.pathsep + os.environ["PATH"]
        if not verify(root, expected_plan=protocol())["all_searches_have_rtl_finalist"]:
            sys.exit(1)
    elif action == "export":
        export(Path(sys.argv[2]).resolve(), metadata=("source-checksums.json", "suite-protocol.json", "cloud-tests.log",
               DOCUMENT, "scripts/model_coverage_suite.py", "scripts/check_coverage_run.py",
               "scripts/audit_model_suite.py", "scripts/verify_model_suite.py", "scripts/export_model_suite.py",
               "artifacts/model-suite/protocol.json"))
    else:
        raise ValueError("Choose prepare, run, audit, rtl or export")


if __name__ == "__main__":
    main()
