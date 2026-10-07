import csv
import json
from pathlib import Path
import tempfile
import unittest

from autohls.experiments import file_hash, write_json
from scripts.audit_model_suite import aggregate
from scripts.model_suite import protocol
from scripts.report_model_suite import publish, trajectories


class SuiteReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = protocol()
        job = self.plan["jobs"][0]
        self.run = self.root / "artifacts/model-suite" / job["id"] / "fixture-matmul"
        self.run.mkdir(parents=True)
        records = []
        for key, latency, state in (("p0-u1-a1", 30, "synthesized"), ("failed", 1, "failed"), ("winner", 10, "synthesized")):
            records.append({"id": key, "status": state, "verification": {"passed": True},
                            "synthesis": {"csim_passed": True, "cosim_passed": False},
                            "metrics": {"latency_us": latency, "timing_met": True,
                                        "resources": {"lut": 10, "ff": 10, "dsp": 1, "bram": 0}}})
        self.history = self.run / "history.jsonl"
        self.history.write_text("\n".join(json.dumps(r) for r in records))
        status = self.root / "artifacts/model-suite/status.json"
        write_json(status, {"complete": True})
        write_json(self.root / "export-checksums.json", {self.history.relative_to(self.root).as_posix(): file_hash(self.history)})
        row = {**job, "run": self.run.name, "audited": True, "exit_code": 0, "order": [r["id"] for r in records],
               "summary": {"completed_budget": True, "wall_seconds": 12, "best_id": "winner", "evaluated": 3,
                           "correctness_passed": 3, "feasible_synthesized": 2, "model_attempts": 0, "model_failures": 0,
                           "model_wall_seconds": 0, "evaluation_seconds": 11},
               "statuses": {"synthesized": 2, "failed": 1}, "first_reference_hit": 3,
               "best_metrics": records[-1]["metrics"], "rtl": {"status": "rtl_passed", "best_rtl_id": "winner",
                   "rejected_ids": [], "best_metrics": records[-1]["metrics"]}}
        self.audit = {"complete": True, "all_evidence_audited": True, "protocol": self.plan,
                      "snapshot_status_sha256": file_hash(status), "runs": [row], "groups": aggregate([row], self.plan),
                      "rtl": {"complete": True, "passed": 2, "rejected": 0, "finalists_passed": 1}}
        self.audit_path = self.root / "audit.json"
        write_json(self.audit_path, self.audit)

    def test_failure_counts_without_future_winner_leakage(self):
        rows = trajectories(self.root, self.audit)
        self.assertEqual([r["best_hls_latency_us"] for r in rows], [30, 30, 10])
        self.assertFalse(rows[1]["feasible"])
        self.assertEqual(rows[1]["evaluation"], 2)
        destination = self.root / "published"
        self.assertEqual(publish(self.audit_path, self.root, destination), 3)
        with (destination / "trajectory.csv").open(encoding="utf-8") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 3)
        report = (destination / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("不是 RTL 实测时延或板卡计时", report)
        self.assertIn("随机五种子", report)
        self.assertIn("3 / 3 / 2 / 2 | 1 / 0 | 0 / 0 | 0.000 | 11.000", report)
        self.assertIn("| 3 | 10.00 | 10 / 10 / 1 / 0 |", report)
        self.assertTrue((destination / "provenance.json").is_file())
        with self.assertRaises(FileExistsError):
            publish(self.audit_path, self.root, destination)

    def test_incomplete_or_unverified_suite_not_published(self):
        for field, value in (("complete", False), ("all_evidence_audited", False), ("rtl", None)):
            changed = {**self.audit, field: value}
            with self.assertRaisesRegex(ValueError, "complete, audited"):
                trajectories(self.root, changed)

    def test_modified_history_rejected(self):
        self.history.write_text(self.history.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "History differs"):
            trajectories(self.root, self.audit)
