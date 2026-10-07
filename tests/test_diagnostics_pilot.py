from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.diagnostics_pilot import ROOT, protocol, run_jobs
from scripts.model_suite import command


class DiagnosticsPilotTests(unittest.TestCase):
    def test_fixed_reversed_schedule_and_unchanged_budget(self):
        plan = protocol()
        self.assertEqual([(j["arm"], j["repeat"]) for j in plan["jobs"]],
                         [("legacy", 0), ("fixed", 0), ("fixed", 1), ("legacy", 1)])
        self.assertFalse(plan["model_repeats_are_independent_samples"])
        self.assertFalse(plan["board_access"])
        for job in plan["jobs"]:
            self.assertEqual((job["benchmark"], job["group"], job["seed"]), ("conv2d", "feedback", 0))
            args = command(job, Path("output"), plan)
            self.assertEqual(args[args.index("--budget") + 1], "8")
            self.assertNotIn("--no-feedback", args)

    def test_isolated_engine_selection_failures_retained_and_no_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "suite"
            calls = []

            def execute(args, **kwargs):
                calls.append(kwargs["cwd"])
                self.assertEqual(args[args.index("--limits") + 1], str(ROOT / "scripts/preboard-limits.json"))
                return subprocess.CompletedProcess(args, 1)

            state = run_jobs(output, protocol(), execute)
            self.assertEqual(calls, [ROOT / "legacy", ROOT, ROOT, ROOT / "legacy"])
            self.assertTrue(state["complete"])
            self.assertFalse(state["all_budgets_completed"])
            self.assertEqual([j["exit_code"] for j in state["jobs"]], [1] * 4)
            self.assertTrue(all(j["run"] is None for j in state["jobs"]))
            before = (output / "status.json").read_bytes()
            with self.assertRaises(FileExistsError):
                run_jobs(output, protocol(), execute)
            self.assertEqual((output / "status.json").read_bytes(), before)
            self.assertEqual(len(calls), 4)

    def test_stop_between_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "suite"

            def execute(args, **kwargs):
                (output / "STOP").touch()
                return subprocess.CompletedProcess(args, 0)

            state = run_jobs(output, protocol(), execute)
            self.assertTrue(state["stopped"])
            self.assertFalse(state["complete"])
            self.assertEqual(len(state["jobs"]), 1)
