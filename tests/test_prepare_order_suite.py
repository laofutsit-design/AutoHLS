from collections import Counter
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autohls.cli import main
from scripts.model_suite import protocol as old_protocol
from scripts.prepare_order_suite import command, protocol


class OrderSuitePreparationTests(unittest.TestCase):
    def test_fixed_schedule_and_accounted_calls(self):
        plan = protocol()
        jobs = plan["jobs"]
        self.assertEqual(len(jobs), 39)
        self.assertEqual(len({j["id"] for j in jobs}), 39)
        self.assertEqual(len(jobs) * plan["budget"], 312)
        self.assertEqual(sum(plan["budget"] - 1 for j in jobs if j["group"] != "random"), 168)
        for benchmark in ("matmul", "fir", "conv2d"):
            selected = [j for j in jobs if j["benchmark"] == benchmark]
            self.assertEqual(Counter((j["group"], j["order_condition"]) for j in selected), {
                ("random", "none"): 5, ("no-feedback", "canonical"): 2, ("no-feedback", "shuffled"): 2,
                ("feedback", "canonical"): 2, ("feedback", "shuffled"): 2})
            self.assertEqual([j["seed"] for j in selected if j["group"] == "random"], list(range(5)))
            self.assertEqual([j["order_condition"] for j in selected if j["group"] == "no-feedback"], ["canonical", "shuffled", "shuffled", "canonical"])
            self.assertEqual([j["order_condition"] for j in selected if j["group"] == "feedback"], ["shuffled", "canonical", "canonical", "shuffled"])
        self.assertFalse(plan["board_access"])
        self.assertEqual(plan["retries"], 0)
        self.assertFalse(plan["model_repeats_are_independent_samples"])
        self.assertEqual(len(old_protocol()["jobs"]), 27)

    def test_commands_keep_feedback_budget_and_separate_order_seed(self):
        plan = protocol()
        for job in plan["jobs"]:
            args = command(job, Path("fixture") / job["id"], plan)
            self.assertEqual(args[args.index("--budget") + 1], "8")
            self.assertEqual(args[args.index("--seed") + 1], str(job["seed"]))
            self.assertEqual("--no-feedback" in args, job["group"] == "no-feedback")
            self.assertEqual("--model" in args, job["group"] != "random")
            self.assertEqual("--candidate-order-seed" in args, job["order_condition"] == "shuffled")
            if job["order_condition"] == "shuffled":
                self.assertEqual(args[args.index("--candidate-order-seed") + 1], "20260927")
                self.assertEqual(job["seed"], 0)
            self.assertNotIn("--cosim", args)

    def test_all_commands_reach_experiment_with_exact_options(self):
        plan = protocol()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "summary.json").write_text(json.dumps({"completed_budget": True, "correctness_passed": 8,
                "evaluated": 8, "feasible_synthesized": 1}))
            limits = root / "limits.json"
            limits.write_text(json.dumps(plan["limits"]))
            for job in plan["jobs"]:
                args = command(job, root / job["id"], plan)[3:]
                args[args.index("--limits") + 1] = str(limits)
                with patch("sys.argv", ["autohls", *args]), patch("sys.stdout", io.StringIO()), \
                        patch("autohls.cli.run_experiment", return_value=root) as run:
                    main()
                run.assert_called_once()
                options = run.call_args.kwargs
                self.assertEqual(options["candidate_order_seed"], job["candidate_order_seed"])
                self.assertEqual(options["feedback"], job["group"] != "no-feedback")
                self.assertEqual(options["seed"], job["seed"])
                self.assertEqual(options["budget"], 8)
                self.assertEqual(options["limits"], plan["limits"])
