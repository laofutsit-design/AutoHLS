"""Prospective 14-job prefixsum transfer; no reference enumeration or board access."""
import json
import os
from pathlib import Path
import socket
import sys

from autohls.benchmarks import VALIDATION_BENCHMARKS
from autohls.experiments import file_hash
from scripts import model_suite, prepare_model_suite
from scripts.audit_model_suite import audit_suite, read, require
from scripts.export_model_suite import export
from scripts.model_coverage_suite import aggregate_conditions, protocol as coverage_protocol
from scripts.verify_model_suite import verify

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = "docs/MODEL_PREFIXSUM_SUITE_PLAN_20260927.md"
CONTRACT = "docs/PREFIXSUM_CONTRACT_20260927.md"
INPUTS = {
    "examples/prefixsum.cpp": "99352fef2ca07f4039e8cdbb7db6b942ab9ce643f16f9252c6f85df69310a150",
    "benchmarks/prefixsum_tb.cpp": "500ed8fad798597ab9eb8187e3e6919c98b5d06536ba95737c9994ebbce66da4",
    CONTRACT: "5fe0f52486c4850b3e8cde29bf27f81ed9076d8b61b6ce85182d2d3989a1d81e",
}
METADATA = ("source-checksums.json", "suite-protocol.json", "cloud-tests.log", DOCUMENT, CONTRACT,
            "scripts/model_prefixsum_suite.py", "scripts/audit_model_suite.py", "scripts/check_coverage_run.py",
            "scripts/audit_diagnostics_pilot.py", "scripts/verify_model_suite.py", "scripts/report_model_suite.py",
            "artifacts/model-suite/protocol.json")


def protocol():
    plan = coverage_protocol()
    jobs = [{**j, "benchmark": "prefixsum", "id": j["id"].replace("matmul-", "prefixsum-", 1)}
            for j in plan["jobs"] if j["benchmark"] == "matmul"]
    plan.update(experiment="prefixsum-transfer-v1", jobs=jobs, reference_policy="not_measured",
                task_scope="prospective single task, not a pretraining-unseen or broad generalization claim",
                task_input_sha256=dict(INPUTS), native_contract={"cases": 32, "checks": 16512, "seed": 20260927})
    return plan


def check_inputs(root):
    for name, expected in INPUTS.items():
        require(file_hash(root / name) == expected, "Frozen prefixsum contract changed: " + name)


def audit(root):
    plan = protocol()
    frozen = read(root / "source-checksums.json")
    require(all(frozen.get(k) == v for k, v in INPUTS.items()), "Task inputs differ from prospective contract")
    require(file_hash(root / CONTRACT) == INPUTS[CONTRACT], "Archived contract changed")
    state = read(root / "artifacts/model-suite/status.json")
    require([j["id"] for j in state["jobs"]] == [j["id"] for j in plan["jobs"][:len(state["jobs"])]], "Job schedule changed")
    require(state["complete"] == (len(state["jobs"]) == len(plan["jobs"])), "Wrong completion status")
    result = audit_suite(root, None, expected_plan=plan, failure_diagnostics=True,
                         benchmark_registry=VALIDATION_BENCHMARKS)
    result["groups"] = aggregate_conditions(result["runs"], plan)
    return result


def rtl(root):
    result = audit(root)
    require(result["complete"] and result["all_evidence_audited"], "Audit all search evidence before RTL")
    require(read(root / "artifacts/model-server.json")["stopped"] is True, "Model server not stopped")
    with socket.socket() as probe:
        require(probe.connect_ex(("127.0.0.1", 11434)) != 0, "Model port still occupied")
    os.environ["PATH"] = str(root / "scripts/cloud-bin") + os.pathsep + os.environ["PATH"]
    return verify(root, expected_plan=protocol())


def main():
    action = sys.argv[1]
    if action == "prepare":
        check_inputs(ROOT)
        prepare_model_suite.main(ROOT / "artifacts/model-prefixsum-suite-20260927/launch-v1",
                                 protocol(), DOCUMENT, extra_documents=(CONTRACT,))
    elif action == "run":
        check_inputs(ROOT)
        model_suite.main(expected_plan=protocol())
    elif action == "audit":
        result = audit(Path(sys.argv[2]))
        with Path(sys.argv[3]).open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({"complete": result["complete"], "all_evidence_audited": result["all_evidence_audited"],
                          "audited_runs": sum(r["audited"] for r in result["runs"]), "rtl": result["rtl"]}))
    elif action == "rtl":
        if not rtl(Path(sys.argv[2]).resolve())["all_searches_have_rtl_finalist"]:
            sys.exit(1)
    elif action == "export":
        export(Path(sys.argv[2]).resolve(), metadata=METADATA)
    else:
        raise ValueError("Choose prepare, run, audit, rtl or export")


if __name__ == "__main__":
    main()
