import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.model_suite import command, protocol, run_jobs


class ModelSuiteTests(unittest.TestCase):
    def test_fixed_schedule_and_fair_budget(self):
        plan = protocol()
        self.assertEqual(len(plan["jobs"]), 27)
        self.assertEqual(len({job["id"] for job in plan["jobs"]}), 27)
        for kernel in ("matmul", "fir", "conv2d"):
            jobs = [job for job in plan["jobs"] if job["benchmark"] == kernel]
            self.assertEqual([j["seed"] for j in jobs if j["group"] == "random"], list(range(5)))
            for group in ("feedback", "no-feedback"):
                repeats = [j for j in jobs if j["group"] == group]
                self.assertEqual([j["repeat"] for j in repeats], [0, 1])
                self.assertTrue(all(j["seed"] == 0 for j in repeats))
            for job in jobs:
                args = command(job, Path("output") / job["id"], plan)
                self.assertEqual("--no-feedback" in args, job["group"] == "no-feedback")
                self.assertEqual(args[args.index("--budget") + 1], "8")
                self.assertNotIn("--cosim", args)
        self.assertFalse(plan["model_repeats_are_independent_samples"])

    def test_failed_jobs_retained_without_retry_and_existing_suite_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "suite"
            plan = protocol()
            plan["jobs"] = plan["jobs"][:2]
            calls = []

            def execute(args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, 1)

            result = run_jobs(output, plan, execute=execute)
            self.assertTrue(result["complete"])
            self.assertEqual(len(calls), 2)
            self.assertEqual([j["exit_code"] for j in result["jobs"]], [1, 1])
            self.assertTrue(all(j["run"] is None for j in result["jobs"]))
            before = (output / "status.json").read_bytes()
            with self.assertRaises(FileExistsError):
                run_jobs(output, plan, execute=execute)
            self.assertEqual((output / "status.json").read_bytes(), before)
            self.assertEqual(len(calls), 2)

    def test_stop_marker_prevents_next_job(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "suite"

            def execute(args, **kwargs):
                (output / "STOP").touch()
                return subprocess.CompletedProcess(args, 0)

            result = run_jobs(output, protocol(), execute=execute)
            self.assertTrue(result["stopped"])
            self.assertFalse(result["complete"])
            self.assertEqual(len(result["jobs"]), 1)
            self.assertEqual(json.loads((output / "status.json").read_text()), result)
