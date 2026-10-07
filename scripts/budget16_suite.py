"""Explicit entry points for the new, frozen 16-evaluation development suite."""
import json
import os
from pathlib import Path
import sys

from scripts import model_suite, prepare_model_suite
from scripts.audit_model_suite import audit_suite
from scripts.export_model_suite import export
from scripts.verify_model_suite import verify

ROOT = Path(__file__).resolve().parents[1]


def protocol():
    plan = model_suite.protocol()
    plan.update(budget=16, experiment="three-kernel-budget16-diagnostics-fixed-v1")
    return plan


def main():
    action = sys.argv[1]
    if action == "prepare":
        prepare_model_suite.main(ROOT / "artifacts/model-budget16-20260927/v1", protocol(),
                                 "docs/MODEL_BUDGET16_20260927.md")
    elif action == "run":
        model_suite.main(expected_plan=protocol())
    elif action == "audit":
        result = audit_suite(Path(sys.argv[2]), Path(sys.argv[3]), expected_plan=protocol(), failure_diagnostics=True)
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
               "docs/MODEL_BUDGET16_20260927.md", "scripts/budget16_suite.py", "scripts/audit_model_suite.py",
               "scripts/verify_model_suite.py", "scripts/export_model_suite.py", "artifacts/model-suite/protocol.json"))
    else:
        raise ValueError("Choose prepare, run, audit, rtl or export; no implicit action")


if __name__ == "__main__":
    main()
