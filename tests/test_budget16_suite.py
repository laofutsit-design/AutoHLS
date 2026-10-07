import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import test_suite_report as suite_fixture
from scripts import prepare_model_suite
from scripts import audit_model_suite
from autohls.experiments import file_hash
from scripts.budget16_suite import protocol
from scripts.model_suite import command, protocol as original_protocol
from scripts.report_model_suite import render


class Budget16Tests(unittest.TestCase):
    def test_schedule_keeps_controls_and_changes_only_declared_budget(self):
        plan = protocol()
        old = original_protocol()
        self.assertEqual(plan["jobs"], old["jobs"])
        self.assertEqual(plan["budget"] * len(plan["jobs"]), 432)
        self.assertEqual(sum(15 for j in plan["jobs"] if j["group"] != "random"), 180)
        self.assertEqual(old["budget"], 8)
        for job in plan["jobs"]:
            args = command(job, Path("output"), plan)
            self.assertEqual(args[args.index("--budget") + 1], "16")
            self.assertEqual("--no-feedback" in args, job["group"] == "no-feedback")
        self.assertFalse(plan["board_access"])

    def test_report_labels_use_actual_budget(self):
        fixture = suite_fixture.SuiteReportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.audit["protocol"]["budget"] = 16
        report = render(fixture.audit, "fixture-only")
        self.assertIn("每组 16 次候选评估", report)
        self.assertIn("不计入搜索的 16 次预算", report)
        self.assertNotIn("每组 8 次", report)

    def test_archive_preserves_executables_and_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("server.py", "README.md", "docs/plan.md", "scripts/cloud-bin/vivado_hls",
                         "scripts/cloud-bin/vivado", "scripts/cloud-xilinx.sh"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture content")
            output = root / "output"
            with patch.object(prepare_model_suite, "ROOT", root):
                prepare_model_suite.main(output, protocol(), "docs/plan.md")
                with self.assertRaises(FileExistsError):
                    prepare_model_suite.main(output, protocol(), "docs/plan.md")
            with tarfile.open(output / "autohls-model-suite-source.tar.gz") as archive:
                for name in ("scripts/cloud-bin/vivado_hls", "scripts/cloud-bin/vivado", "scripts/cloud-xilinx.sh"):
                    self.assertEqual(archive.getmember(name).mode & 0o111, 0o111)
                self.assertEqual(json.load(archive.extractfile("suite-protocol.json")), protocol())

    def test_failed_diagnostic_audit_excludes_result_from_statistics(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = protocol()
            plan["jobs"] = [next(j for j in plan["jobs"] if j["group"] == "no-feedback")]
            job = plan["jobs"][0]
            suite = root / "artifacts/model-suite"
            run = suite / job["id"] / "fixture-matmul"
            run.mkdir(parents=True)
            engine = Path(audit_model_suite.__file__).resolve().parents[1] / "autohls"
            (root / "source-checksums.json").write_text(json.dumps({"autohls/" + p.name: file_hash(p) for p in engine.glob("*.py")}))
            (suite / "protocol.json").write_text(json.dumps(plan))
            (suite / "status.json").write_text(json.dumps({"complete": True, "jobs": [
                {**job, "run": run.relative_to(suite).as_posix(), "summary": {}, "exit_code": 0}]}))
            (run / "history.jsonl").write_text('')
            with patch.object(audit_model_suite, "BENCHMARKS", {}), \
                 patch.object(audit_model_suite, "audit_run", return_value={**job, "summary": {}, "audited": True}), \
                 patch("scripts.audit_diagnostics_pilot.check_diagnostics", side_effect=ValueError("forged diagnostic")) as check:
                result = audit_model_suite.audit_suite(root, root, expected_plan=plan, failure_diagnostics=True)
            self.assertEqual(check.call_args.kwargs, {"feedback": False})
            self.assertFalse(result["all_evidence_audited"])
            self.assertEqual(result["runs"][0]["state"], "audit_failed")
            self.assertEqual(result["runs"][0]["error"], "forged diagnostic")
            group = next(g for g in result["groups"] if g["benchmark"] == "matmul" and g["group"] == "no-feedback")
            self.assertEqual(group["complete_with_feasible"], 0)
