"""Coverage contract tests. Model/HLS responses here are synthetic test doubles."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import random
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from autohls import planner
from autohls.benchmarks import Configuration, configurations
from autohls.cli import main
from autohls.experiments import run_experiment
from scripts.audit_model_suite import audit_run
from scripts.model_suite import protocol
from scripts.check_coverage_run import check
from autohls.experiments import file_hash

POLICY = "pipeline-warmup-v1"


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="autohls-coverage-fixture-")))
        self.identity = {"model": {"digest": "SYNTHETIC"}, "ollama_version": "fixture"}
        for target, value in (("find_compiler", "fixture-compiler"),
                              ("detect_vitis", {"available": True}), ("inspect_ollama", self.identity)):
            self.stack.enter_context(patch("autohls.experiments." + target, return_value=value))
        self.stack.enter_context(patch("autohls.planner.inspect_ollama", return_value=self.identity))
        self.stack.enter_context(patch("autohls.experiments.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "SYNTHETIC compiler", "")))
        self.native = self.stack.enter_context(patch("autohls.experiments.verify_native", return_value={"passed": True}))
        self.hls = self.stack.enter_context(patch("autohls.experiments.run_synthesis",
            side_effect=RuntimeError("SYNTHETIC HLS failure; no tool executed")))
        self.requests = []
        self.bad_choice = None
        self.http = self.stack.enter_context(patch("autohls.planner.urlopen", side_effect=self.chat))
        self.stack.enter_context(redirect_stdout(io.StringIO()))

    def chat(self, request, **kwargs):
        payload = json.loads(request.data)
        self.requests.append(payload)
        prompt = json.loads(payload["messages"][0]["content"])
        chosen = self.bad_choice or prompt["allowed_candidates"][-1]["id"]
        return io.BytesIO(json.dumps({"done": True, "message": {"content": json.dumps(
            {"id": chosen, "reason": "SYNTHETIC fixture, not a model result"})}}).encode())

    def run_fixture(self, **kwargs):
        return run_experiment("matmul", self.root, **{
            "backend": "hls", "planner": "random", "budget": 8, "goal": "latency",
            "coverage_policy": POLICY, **kwargs})

    def records(self, run):
        return [json.loads(line) for line in (run / "history.jsonl").read_text().splitlines()]

    def test_pool_covers_first_three_then_releases_without_mutation(self):
        for choose_last in (False, True):
            remaining = configurations()
            original = list(remaining)
            chosen = []
            for iteration in range(30):
                snapshot = list(remaining)
                allowed = planner.coverage_candidates(remaining, iteration)
                self.assertEqual(remaining, snapshot)
                expected = ([Configuration()] if iteration == 0 else
                            [c for c in remaining if c.pipeline_ii == iteration] if iteration < 3 else remaining)
                self.assertEqual(allowed, expected)
                selected = allowed[-1 if choose_last else 0]
                chosen.append(selected)
                remaining.remove(selected)
            self.assertEqual([c.pipeline_ii for c in chosen[:3]], [0, 1, 2])
            self.assertCountEqual(chosen, original)

    def test_invalid_policy_combinations_fail_before_tools(self):
        for changes in ({"coverage_policy": "unknown"}, {"coverage_policy": None},
                        {"planner": "exhaustive"}, {"budget": 1}, {"budget": 2},
                        {"planner": "ollama", "model": "fixture", "candidate_order_seed": 7}):
            with self.subTest(changes=changes), patch("autohls.experiments.find_compiler") as compiler:
                with self.assertRaisesRegex(ValueError, "Coverage"):
                    self.run_fixture(**changes)
                compiler.assert_not_called()
        self.native.assert_not_called()
        self.http.assert_not_called()

    def test_default_and_none_preserve_legacy_random_and_exhaustive(self):
        for mode in ("random", "exhaustive"):
            for seed in (0, 7, 42):
                expected = configurations()
                if mode == "random":
                    random.Random(seed).shuffle(expected)
                expected = [Configuration()] + [c for c in expected if c != Configuration()]
                for option in ({}, {"coverage_policy": "none"}):
                    run = run_experiment("matmul", self.root, backend="native", planner=mode,
                                         budget=8, seed=seed, **option)
                    self.assertEqual([r["id"] for r in self.records(run)], [c.key for c in expected[:8]])
                    self.assertNotIn("coverage", json.loads((run / "manifest.json").read_text()))
                    self.assertEqual(list(run.glob("selection-*.json")), [])
        self.http.assert_not_called()

    def test_random_is_seeded_choice_in_the_same_scheduled_pool(self):
        for budget in (3, 8, 16, 30):
            for seed in (0, 7, 42):
                expected, remaining, rng = [], configurations(), random.Random(seed)
                for index in range(budget):
                    allowed = planner.coverage_candidates(remaining, index)
                    selected = allowed[0] if index == 0 else rng.choice(allowed)
                    expected.append(selected.key)
                    remaining.remove(selected)
                for repeat in range(2):
                    run = self.run_fixture(backend="native", budget=budget, seed=seed)
                    self.assertEqual([r["id"] for r in self.records(run)], expected)
                    summary = json.loads((run / "summary.json").read_text())
                    self.assertIsNone(summary["best_id"])
                    self.assertEqual(summary["model_attempts"], 0)
        self.http.assert_not_called()

    def test_shared_attempt_budget_and_exact_model_pool_without_feedback_leak(self):
        contracts = []
        for mode, feedback in (("random", True), ("ollama", False), ("ollama", True)):
            self.requests.clear()
            self.hls.reset_mock()
            run = self.run_fixture(planner=mode, model="fixture" if mode == "ollama" else "", feedback=feedback)
            records = self.records(run)
            manifest = json.loads((run / "manifest.json").read_text())
            contracts.append(manifest["coverage"])
            self.assertEqual(manifest["candidate_order"]["policy"], "canonical")
            self.assertEqual([r["pipeline_ii"] for r in records[:3]], [0, 1, 2])
            self.assertEqual(len(records), 8)
            self.assertEqual(self.hls.call_count, 8)  # All failures consume budget, including baseline.
            self.assertTrue(all(r["status"] == "failed" for r in records))
            remaining = configurations()
            for index, record in enumerate(records):
                allowed = planner.coverage_candidates(remaining, index)
                ids = [c.key for c in allowed]
                trace = json.loads((run / f"selection-{index:03d}.json").read_text())
                self.assertEqual(trace, {"policy": POLICY, "iteration": index, "allowed_ids": ids})
                self.assertIn(record["id"], ids)
                remaining.remove(next(c for c in remaining if c.key == record["id"]))
                if mode == "ollama" and index:
                    request = self.requests[index - 1]
                    prompt = json.loads(request["messages"][0]["content"])
                    self.assertEqual([c["id"] for c in prompt["allowed_candidates"]], ids)
                    self.assertEqual(request["format"]["properties"]["id"]["enum"], ids)
                    self.assertEqual([r["id"] for r in prompt["observations"]],
                                     [r["id"] for r in records[:index]] if feedback else [])
            summary = json.loads((run / "summary.json").read_text())
            self.assertTrue(summary["completed_budget"])
            self.assertEqual(summary["model_attempts"], 7 if mode == "ollama" else 0)
            self.assertIsNone(summary["best_id"])
        self.assertEqual(contracts[0], contracts[1])
        self.assertEqual(contracts[1], contracts[2])

    def test_out_of_pool_proposal_is_terminal_and_not_retried(self):
        self.bad_choice = "p0-u2-a1"  # Untested and valid globally, but not II=1 at step two.
        run = self.run_fixture(planner="ollama", model="fixture")
        self.assertEqual(len(self.records(run)), 1)
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(self.hls.call_count, 1)
        summary = json.loads((run / "summary.json").read_text())
        self.assertFalse(summary["completed_budget"])
        self.assertEqual(summary["model_failures"], 1)
        self.assertTrue((run / "selection-001.json").exists())
        self.assertTrue((run / "proposal-001/response.raw.log").exists())
        self.assertFalse((run / "selection-002.json").exists())

    def test_cli_propagates_explicit_coverage_policy(self):
        summary = {"completed_budget": True, "correctness_passed": 8, "evaluated": 8, "feasible_synthesized": 0}
        (self.root / "summary.json").write_text(json.dumps(summary))
        with patch("sys.argv", ["autohls", "research", "matmul", "--planner", "random", "--coverage-policy", POLICY]), \
                patch("autohls.cli.run_experiment", return_value=self.root) as run:
            main()
        self.assertEqual(run.call_args.kwargs["coverage_policy"], POLICY)

    def test_legacy_suite_auditor_rejects_unplanned_coverage(self):
        run = self.run_fixture()
        plan = protocol()
        with self.assertRaisesRegex(ValueError, "Coverage"):
            audit_run(run, plan["jobs"][0], plan, {})

    def test_independent_checker_accepts_complete_and_failed_model_runs(self):
        for mode, feedback in (("random", True), ("ollama", False), ("ollama", True)):
            run = self.run_fixture(planner=mode, model="fixture" if mode == "ollama" else "", feedback=feedback)
            result = check(run)
            self.assertTrue(result["schedule_valid"] and result["completed_budget"])
            self.assertEqual(result["warmup_attempts"], [0, 1, 2])
        self.bad_choice = "p0-u2-a1"
        result = check(self.run_fixture(planner="ollama", model="fixture"))
        self.assertTrue(result["schedule_valid"])
        self.assertFalse(result["completed_budget"])
        self.assertEqual((result["evaluations"], result["selection_attempts"]), (1, 2))

    def test_checker_rejects_resealed_pool_budget_and_feedback_tampering(self):
        for mutation in ("pool", "schema", "feedback", "budget", "seed"):
            mode = "random" if mutation == "seed" else "ollama"
            run = self.run_fixture(planner=mode, model="fixture" if mode == "ollama" else "", feedback=False)
            name = {"pool": "selection-001.json", "schema": "proposal-001/request.json",
                    "feedback": "proposal-001/request.json", "budget": "summary.json", "seed": "manifest.json"}[mutation]
            target = run / name
            data = json.loads(target.read_text())
            if mutation == "pool": data["allowed_ids"].append("p0-u2-a1")
            if mutation == "schema": data["format"]["properties"]["id"]["enum"].append("p0-u2-a1")
            if mutation == "feedback":
                prompt = json.loads(data["messages"][0]["content"])
                prompt["observations"] = [{"id": "future-winner"}]
                data["messages"][0]["content"] = json.dumps(prompt)
            if mutation == "budget": data["evaluated"] -= 1
            if mutation == "seed": data["seed"] = 999
            target.write_text(json.dumps(data))
            checksums = json.loads((run / "checksums.json").read_text())
            checksums[name] = file_hash(target)  # Semantic checks must work even with a resealed file.
            (run / "checksums.json").write_text(json.dumps(checksums))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                check(run)
