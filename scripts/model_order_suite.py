"""Explicit operations for the matched order comparison; no implicit execution."""
import json
import os
from pathlib import Path
import sys

from scripts import model_suite, prepare_model_suite
from scripts.audit_model_suite import aggregate, audit_suite, read, require
from scripts.export_model_suite import export
from scripts.prepare_order_suite import protocol
from scripts.verify_model_suite import verify

ROOT = Path(__file__).resolve().parents[1]


def aggregate_conditions(rows, plan):
    groups = []
    for condition in ("none", "canonical", "shuffled"):
        selected = [r for r in rows if r["order_condition"] == condition]
        subset = {**plan, "jobs": [j for j in plan["jobs"] if j["order_condition"] == condition]}
        groups.extend({**g, "order_condition": condition} for g in aggregate(selected, subset) if g["planned_runs"])
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
        prepare_model_suite.main(ROOT / "artifacts/model-order-suite-20260927/launch-v1", protocol(),
                                 "docs/MODEL_ORDER_SUITE_PLAN_20260927.md")
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
               "docs/MODEL_ORDER_SUITE_PLAN_20260927.md", "scripts/model_order_suite.py", "scripts/prepare_order_suite.py",
               "scripts/audit_model_suite.py", "scripts/verify_model_suite.py", "scripts/export_model_suite.py",
               "artifacts/model-suite/protocol.json"))
    else:
        raise ValueError("Choose prepare, run, audit, rtl or export")


if __name__ == "__main__":
    main()
