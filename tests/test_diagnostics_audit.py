import json
from pathlib import Path
import tempfile
import unittest

from autohls.vitis import build_tcl
from scripts.audit_diagnostics_pilot import check_diagnostics
from scripts.diagnostics_pilot import protocol


class DiagnosticsAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.plan = protocol()
        (self.run / "manifest.json").write_text('{"clock_ns":10.0,"benchmark":"conv2d"}')
        self.remote = "/release/artifacts/model-suite/job/run"
        baseline = self.run / "p0-u1-a1/hls/baseline"
        baseline.mkdir(parents=True)
        (baseline / "vitis.stdout.log").write_text("INFO: test baseline\n")
        self.failure = self.run / "p0-u8-a4/hls/failed"
        self.failure.mkdir(parents=True)
        self.error = "ERROR: [XFORM 203-103] Cannot partition array 'weights' (kernel.cpp:8): incorrect partition factor 4."
        (self.failure / "vitis.stdout.log").write_text(self.error.replace("kernel.cpp", self.remote + "/p0-u8-a4/kernel.cpp"))
        (self.failure / "vitis.stderr.log").write_text("")
        self.tcl = self.failure / "run_hls.tcl"
        self.tcl.write_text(build_tcl(self.remote + "/p0-u8-a4/kernel.cpp", "conv2d", self.plan["part"], 10.0,
                                     testbench=self.remote + "/testbench.cpp"))
        self.records = [{"id": "p0-u1-a1", "status": "synthesized", "diagnostics": [],
                         "synthesis": {"run_dir": self.remote + "/p0-u1-a1/hls/baseline"}},
                        {"id": "p0-u8-a4", "status": "failed", "diagnostics": [self.error]}]
        self.request = self.run / "proposal-002/request.json"
        self.request.parent.mkdir()
        self.save_request([self.error])

    def save_request(self, diagnostics):
        self.request.write_text(json.dumps({"messages": [{"content": json.dumps({"observations": [
            {"id": "p0-u8-a4", "diagnostics": diagnostics}]})}]}))

    def test_fixed_and_legacy_diagnostic_exposure_is_distinguished(self):
        result = check_diagnostics(self.run, self.records, "fixed", self.plan)
        self.assertEqual(result["errors_delivered_to_next_request"], 1)
        self.assertEqual(result["failures"][0]["partition_factors"], [4])
        self.records[1].pop("diagnostics")
        self.save_request([])
        result = check_diagnostics(self.run, self.records, "legacy", self.plan)
        self.assertEqual(result["partition_failure_count"], 1)
        self.assertEqual(result["errors_delivered_to_next_request"], 0)

    def test_forged_diagnostic_or_missing_request_excerpt_is_rejected(self):
        self.records[1]["diagnostics"] = ["ERROR: invented explanation"]
        with self.assertRaisesRegex(ValueError, "raw logs"):
            check_diagnostics(self.run, self.records, "fixed", self.plan)
        self.records[1]["diagnostics"] = [self.error]
        self.save_request([])
        with self.assertRaisesRegex(ValueError, "Next request"):
            check_diagnostics(self.run, self.records, "fixed", self.plan)

    def test_failed_candidate_tcl_is_checked_too(self):
        self.tcl.write_text(self.tcl.read_text().replace("xc7z020clg400-1", "xc7z010clg400-1"))
        with self.assertRaisesRegex(ValueError, "Failed HLS commands"):
            check_diagnostics(self.run, self.records, "fixed", self.plan)

    def test_other_kernels_use_their_own_top_and_no_feedback_stays_empty(self):
        for kernel in ("matmul", "fir"):
            with self.subTest(kernel=kernel):
                (self.run / "manifest.json").write_text(json.dumps({"clock_ns": 10.0, "benchmark": kernel}))
                self.tcl.write_text(build_tcl(self.remote + "/p0-u8-a4/kernel.cpp", kernel, self.plan["part"], 10.0,
                                             testbench=self.remote + "/testbench.cpp"))
                self.request.write_text(json.dumps({"messages": [{"content": '{"observations":[]}'}]}))
                result = check_diagnostics(self.run, self.records, "fixed", self.plan, feedback=False)
                self.assertEqual(result["errors_with_next_request"], 1)
                self.assertEqual(result["errors_delivered_to_next_request"], 0)
                self.save_request([self.error])
                with self.assertRaisesRegex(ValueError, "No-feedback"):
                    check_diagnostics(self.run, self.records, "fixed", self.plan, feedback=False)

    def test_final_failure_without_next_request_is_not_claimed_delivered(self):
        self.request.unlink()
        result = check_diagnostics(self.run, self.records, "fixed", self.plan)
        self.assertEqual(result["errors_with_next_request"], 0)
        self.assertEqual(result["errors_delivered_to_next_request"], 0)
