import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from autohls.experiments import run_experiment


class FailureFeedbackTests(unittest.TestCase):
    def experiment(self, directory, failure, feedback=True):
        identity = {"model": {"digest": "test"}, "ollama_version": "test"}
        responses = [io.BytesIO(json.dumps({"done": True, "message": {"content": json.dumps(
            {"id": choice, "reason": "test response"})}}).encode())
            for choice in ("p1-u2-a2", "p1-u4-a4")]
        with patch("autohls.experiments.find_compiler", return_value="g++"), \
                patch("autohls.experiments.detect_vitis", return_value={"available": True}), \
                patch("autohls.experiments.inspect_ollama", return_value=identity), \
                patch("autohls.planner.inspect_ollama", return_value=identity), \
                patch("autohls.experiments.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "test", "")), \
                patch("autohls.experiments.verify_native", return_value={"passed": True}), \
                patch("autohls.experiments.run_synthesis", side_effect=failure), \
                patch("autohls.planner.urlopen", side_effect=responses):
            return run_experiment("conv2d", directory, backend="hls", planner="ollama",
                                  model="test:tag", budget=3, feedback=feedback)

    def test_actual_failure_reaches_only_later_feedback_and_remains_bounded(self):
        def failure(source, top, output, **kwargs):
            log = Path(output) / "run-test" / "vitis.stdout.log"
            log.parent.mkdir(parents=True)
            text = "WARNING: earlier warning\n" * 45
            text += f"ERROR: [XFORM 203-103] Cannot partition array 'weights' ({source.as_posix()}:8): incorrect partition factor 4.\n"
            text += f"ERROR: candidate {source.parent.name}\n" + "ERROR: " + "x" * 500 + "\n"
            text += "ERROR: fourth error\n"
            log.write_text(text, encoding="utf-8")
            raise RuntimeError("synthesis failed; see raw log")

        for feedback in (True, False):
            with self.subTest(feedback=feedback), tempfile.TemporaryDirectory() as directory:
                # Nested short components work on Windows while making the tool path >240 characters.
                output = Path(directory).joinpath(*(["long-evidence-directory"] * 7))
                run = self.experiment(output, failure, feedback)
                records = [json.loads(line) for line in (run / "history.jsonl").read_text().splitlines()]
                first = records[0]["diagnostics"][0]
                self.assertIn("(kernel.cpp:8): incorrect partition factor 4.", first)
                self.assertLess(len(first), 240)
                self.assertIn("synthesis failed; see raw log", records[0]["error"])
                raw = next((run / records[0]["id"] / "hls").glob("*/vitis.stdout.log")).read_text()
                self.assertIn(str((run / records[0]["id"] / "kernel.cpp").as_posix()), raw)
                for index in (1, 2):
                    request = json.loads((run / f"proposal-{index:03d}" / "request.json").read_text())
                    observations = json.loads(request["messages"][0]["content"])["observations"]
                    self.assertEqual([item["id"] for item in observations],
                                     [item["id"] for item in records[:index]] if feedback else [])
                    if feedback:
                        self.assertEqual(len(observations[0]["diagnostics"]), 3)
                        self.assertIn("incorrect partition factor 4", observations[0]["diagnostics"][0])
                        self.assertTrue(all(len(line) <= 240 for item in observations for line in item["diagnostics"]))
                summary = json.loads((run / "summary.json").read_text())
                self.assertEqual(summary["evaluated"], 3)
                self.assertTrue(summary["completed_budget"])
                self.assertIsNone(summary["best_id"])

    def test_failure_before_log_creation_keeps_original_error_and_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.experiment(directory, RuntimeError("failed before log creation"))
            records = [json.loads(line) for line in (run / "history.jsonl").read_text().splitlines()]
            self.assertEqual(len(records), 3)
            self.assertTrue(all(item["error"] == "RuntimeError: failed before log creation" for item in records))
            self.assertTrue(all(not item.get("diagnostics") for item in records))


if __name__ == "__main__":
    unittest.main()
