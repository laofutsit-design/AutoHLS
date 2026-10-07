import json
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

from autohls.experiments import file_hash, summarize, write_json
from scripts import verify_model_suite
from scripts.model_suite import protocol


class SuiteRTLTests(unittest.TestCase):
    def test_rejected_winner_falls_back_with_deduplication_and_searches_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            suite = root / "artifacts/model-suite"
            suite.mkdir(parents=True)
            plan = protocol()
            plan["jobs"] = plan["jobs"][:2]
            write_json(suite / "protocol.json", plan)
            entries = []
            for job in plan["jobs"]:
                run = suite / job["id"] / "fixture-matmul"
                run.mkdir(parents=True)
                (run / "testbench.cpp").write_text("// fixture")
                records = []
                for key, latency in (("p0-u1-a1", 30), ("fast", 10), ("backup", 20)):
                    directory = run / key
                    directory.mkdir()
                    (directory / "kernel.cpp").write_text("// " + key)
                    records.append({"id": key, "status": "synthesized", "source_sha256": file_hash(directory / "kernel.cpp"),
                        "verification": {"passed": True}, "synthesis": {"csim_passed": True, "cosim_passed": False},
                        "metrics": {"latency_us": latency, "timing_met": True, "resources": {"lut": 10, "ff": 10, "dsp": 1, "bram": 0}}})
                write_json(run / "manifest.json", {"benchmark": "matmul", "clock_ns": 10, "limits": plan["limits"],
                    "testbench_sha256": file_hash(run / "testbench.cpp"), "goal": "latency"})
                write_json(run / "summary.json", {**summarize(records, plan["limits"], "latency"), "completed_budget": True})
                (run / "history.jsonl").write_text("\n".join(json.dumps(r) for r in records))
                entries.append({**job, "run": run.relative_to(suite).as_posix()})
            write_json(suite / "status.json", {"complete": True, "jobs": entries})
            before = {str(p): file_hash(p) for p in suite.rglob("*") if p.is_file()}
            report = root / "fixture.xml"
            report.write_text("fixture report")
            calls = []

            def synthesize(source, *args, **kwargs):
                calls.append(source.parent.name)
                if source.parent.name == "fast":
                    raise RuntimeError("fixture: RTL mismatch")
                record = next(r for r in records if r["id"] == source.parent.name)
                return {"metrics": record["metrics"], "cosim_passed": True, "report": str(report)}

            with self.assertRaisesRegex(ValueError, "frozen suite"):
                verify_model_suite.verify(root, synthesize)
            self.assertFalse(calls)
            result = verify_model_suite.verify(root, synthesize, expected_plan=plan)
            with self.assertRaises(FileExistsError):
                verify_model_suite.verify(root, synthesize, expected_plan=plan)
            self.assertEqual(calls, ["p0-u1-a1", "fast", "backup"])
            self.assertTrue(result["complete"])
            self.assertTrue(result["all_searches_have_rtl_finalist"])
            self.assertEqual([s["hls_best_id"] for s in result["selections"]], ["fast", "fast"])
            self.assertEqual([s["best_rtl_id"] for s in result["selections"]], ["backup", "backup"])
            self.assertTrue(all(s["rejected_ids"] == ["fast"] for s in result["selections"]))
            self.assertFalse(result["board_verified"])
            self.assertEqual(before, {str(p): file_hash(p) for p in suite.rglob("*") if p.is_file()})

            # Repeat in a separate temporary root; a PASS label cannot override changed metrics.
            second = root / "metrics-mismatch"
            import shutil
            shutil.copytree(suite, second / "artifacts/model-suite")
            bad_report = deepcopy(next(r for r in records if r["id"] == "p0-u1-a1")["metrics"])
            bad_report["latency_us"] += 1

            def wrong_metrics(*args, **kwargs):
                return {"metrics": bad_report, "cosim_passed": True, "report": str(report)}

            with patch.object(verify_model_suite, "protocol", return_value=plan):
                rejected = verify_model_suite.verify(second, wrong_metrics)
            self.assertFalse(rejected["all_searches_have_rtl_finalist"])
            self.assertTrue(all(s["status"] == "baseline_rejected" for s in rejected["selections"]))
            self.assertEqual(len(rejected["results"]), 1)
