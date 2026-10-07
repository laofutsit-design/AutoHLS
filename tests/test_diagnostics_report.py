import csv
import unittest

import test_suite_report as suite_fixture
from autohls.experiments import write_json
from scripts.report_diagnostics_pilot import publish


class DiagnosticsReportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = suite_fixture.SuiteReportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        audit = self.fixture.audit
        audit["all_budgets_completed"] = True
        row = audit["runs"][0]
        row.update(arm="fixed", statuses={"failed": 1, "synthesized": 2}, diagnostic_evidence={
            "partition_failure_count": 1, "partition_failures_after_first": 0,
            "errors_with_next_request": 1, "errors_delivered_to_next_request": 1, "failures": []})
        row["summary"].update(evaluated=3, feasible_synthesized=2, model_wall_seconds=2)
        write_json(self.fixture.audit_path, audit)

    def test_report_is_separate_and_trajectory_keeps_arm_and_failure(self):
        f = self.fixture
        output = f.root / "pilot-report"
        self.assertEqual(publish(f.audit_path, f.root, output), 3)
        with (output / "trajectory.csv").open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([r["arm"] for r in rows], ["fixed"] * 3)
        self.assertEqual([r["best_hls_latency_us"] for r in rows], ["30", "30", "10"])
        report = (output / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("不是 RTL 实测周期或板上时间", report)
        self.assertIn("两次重复不是独立统计样本", report)
        self.assertIn("具体诊断送达次数", report)
        with self.assertRaises(FileExistsError):
            publish(f.audit_path, f.root, output)

    def test_attempted_but_incomplete_budget_cannot_publish(self):
        f = self.fixture
        f.audit["all_budgets_completed"] = False
        write_json(f.audit_path, f.audit)
        with self.assertRaisesRegex(ValueError, "Incomplete pilot budget"):
            publish(f.audit_path, f.root, f.root / "report")
        self.assertFalse((f.root / "report").exists())
