import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.audit_model_suite import aggregate, audit_run, read
from scripts.model_suite import protocol

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "artifacts/model-schema-20260927/v4/evidence"


class AggregationTests(unittest.TestCase):
    def test_missing_and_incomplete_are_not_successful_samples(self):
        jobs = [j for j in protocol()["jobs"] if j["benchmark"] == "matmul" and j["group"] == "random"]
        rows = [{**job, "audited": index != 4, "summary": {"completed_budget": index < 3, "wall_seconds": 10},
                 "best_metrics": {"latency_us": value}, "first_reference_hit": 4 if value == 10 else None}
                for index, (job, value) in enumerate(zip(jobs, (10, 20, 30, 1, 1)))]
        group = aggregate(rows, protocol())[0]
        self.assertEqual(group["planned_runs"], 5)
        self.assertEqual(group["audited_runs"], 4)
        self.assertEqual(group["complete_with_feasible"], 3)
        self.assertEqual(group["median_latency_us"], 20)
        self.assertEqual(group["worst_latency_us"], 30)
        self.assertEqual(group["reference_hits"], 1)
        self.assertIsNone(group["repeat_orders_identical"])
        self.assertEqual(aggregate([], protocol())[0]["planned_runs"], 5)

    def test_model_repeats_not_marked_independent(self):
        jobs = [j for j in protocol()["jobs"] if j["benchmark"] == "fir" and j["group"] == "feedback"]
        rows = [{**job, "audited": True, "order": ["baseline", "choice"],
                 "summary": {"completed_budget": True, "wall_seconds": 10}, "best_metrics": {"latency_us": 5}}
                for job in jobs]
        group = next(g for g in aggregate(rows, protocol()) if g["benchmark"] == "fir" and g["group"] == "feedback")
        self.assertTrue(group["repeat_orders_identical"])
        self.assertFalse(group["independent_random_samples"])
        rows[1]["order"] = ["baseline", "other"]
        group = next(g for g in aggregate(rows, protocol()) if g["benchmark"] == "fir" and g["group"] == "feedback")
        self.assertFalse(group["repeat_orders_identical"])


@unittest.skipUnless(PILOT.exists(), "Archived v4 evidence is local only")
class EvidenceAuditTests(unittest.TestCase):
    def audit(self, group, run=None):
        job = next(j for j in protocol()["jobs"] if j["benchmark"] == "matmul" and j["group"] == group)
        if run is None:
            run = next((PILOT / "artifacts/model-pilot" / group).glob("*-matmul"))
        return audit_run(run, job, protocol(), read(PILOT / "source-checksums.json"))

    def test_original_evidence_passes(self):
        for group in ("random", "no-feedback", "feedback"):
            self.assertTrue(self.audit(group)["audited"])

    def test_forged_metric_is_rejected(self):
        original = next((PILOT / "artifacts/model-pilot/random").glob("*-matmul"))
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / original.name
            shutil.copytree(original, run)
            summary = read(run / "summary.json")
            summary["hls_speedup"] = 999
            (run / "summary.json").write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, "Stored summary"):
                self.audit("random", run)

    def test_feedback_leak_is_rejected(self):
        original = next((PILOT / "artifacts/model-pilot/no-feedback").glob("*-matmul"))
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / original.name
            shutil.copytree(original, run)
            request_path = run / "proposal-001/request.json"
            request = read(request_path)
            prompt = json.loads(request["messages"][0]["content"])
            prompt["observations"] = [{"id": "future-winner", "latency_us": 1}]
            request["messages"][0]["content"] = json.dumps(prompt)
            request_path.write_text(json.dumps(request))
            with self.assertRaisesRegex(ValueError, "Feedback leakage"):
                self.audit("no-feedback", run)

    def test_missing_original_report_is_rejected(self):
        original = next((PILOT / "artifacts/model-pilot/random").glob("*-matmul"))
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / original.name
            shutil.copytree(original, run)
            # Only the private temporary fixture is modified.
            next((run / "p0-u1-a1/hls").glob("*/autohls_project/solution1/syn/report/matmul_csynth.xml")).unlink()
            with self.assertRaisesRegex(ValueError, "exactly one original"):
                self.audit("random", run)

    def test_changed_synthesis_target_is_rejected(self):
        original = next((PILOT / "artifacts/model-pilot/random").glob("*-matmul"))
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / original.name
            shutil.copytree(original, run)
            tcl = next((run / "p0-u1-a1/hls").glob("*/run_hls.tcl"))
            tcl.write_text(tcl.read_text().replace("xc7z020clg400-1", "xc7z010clg400-1"))
            with self.assertRaisesRegex(ValueError, "HLS commands"):
                self.audit("random", run)
