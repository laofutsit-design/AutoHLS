import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autohls.experiments import file_hash, write_json
from scripts import verify_finalists


class FinalistVerificationTests(unittest.TestCase):
    def test_rtl_failure_cannot_remain_selected_and_history_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            search = root / "search"
            search.mkdir()
            before = {}
            for name in ("matmul", "fir", "conv2d"):
                run = search / name
                run.mkdir()
                (run / "testbench.cpp").write_text("// test-only fixture")
                write_json(run / "manifest.json", {"benchmark": name, "device": {"part": "xc7z020clg400-1"},
                           "clock_ns": 10, "limits": {"lut": 100, "ff": 100, "dsp": 10, "bram": 10}, "goal": "latency"})
                write_json(run / "summary.json", {"completed_budget": True, "planner_error": None, "best_id": "fast"})
                records = []
                for key, latency in (("p0-u1-a1", 30), ("fast", 10), ("backup", 20)):
                    candidate = run / key
                    candidate.mkdir()
                    (candidate / "kernel.cpp").write_text("// " + key)
                    records.append({"id": key, "status": "synthesized", "source_sha256": file_hash(candidate / "kernel.cpp"),
                                    "verification": {"passed": True}, "synthesis": {"csim_passed": True, "cosim_passed": False},
                                    "metrics": {"latency_us": latency, "timing_met": True,
                                                "resources": {"lut": 10, "ff": 10, "dsp": 1, "bram": 0}}})
                (run / "history.jsonl").write_text("\n".join(json.dumps(record) for record in records))
            before = {str(path): file_hash(path) for path in search.rglob("*") if path.is_file()}
            report = root / "fake-report.xml"
            report.write_text("fake tool output")

            def synthesize(source, *args, **kwargs):
                if source.parent.name == "fast":
                    raise RuntimeError("fixture: RTL output mismatch")
                records = [json.loads(line) for line in (source.parents[1] / "history.jsonl").read_text().splitlines()]
                record = next(item for item in records if item["id"] == source.parent.name)
                return {"metrics": record["metrics"], "report": str(report), "csim_passed": True, "cosim_passed": True}

            output = root / "finalists"
            with patch.object(verify_finalists, "run_synthesis", side_effect=synthesize) as tool, \
                    patch("sys.argv", ["verify", str(search), str(output)]):
                verify_finalists.main()
            result = json.loads((output / "results.json").read_text())
            self.assertTrue(result["complete"])
            self.assertFalse(result["board_verified"])
            self.assertEqual(tool.call_count, 9)
            self.assertEqual([item["best_id"] for item in result["verified"]], ["backup"] * 3)
            self.assertTrue(all(item["rejected_ids"] == ["fast"] for item in result["verified"]))
            self.assertEqual(sum(item["status"] == "verification_rejected" for item in result["results"]), 3)
            self.assertEqual(before, {str(path): file_hash(path) for path in search.rglob("*") if path.is_file()})
