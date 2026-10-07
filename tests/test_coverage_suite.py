"""Synthetic suite fixtures only; never execute a model, HLS or board operation."""
from collections import Counter
import copy
import unittest

from autohls.experiments import write_json
from scripts.audit_model_suite import read
from scripts.model_coverage_suite import aggregate_conditions, audit, protocol
from scripts.model_suite import command, run_jobs
from scripts.plot_model_suite import aggregate_curves
from scripts.report_model_suite import render, trajectories
from tests import test_order_suite_integration as fixtures

POLICY = "pipeline-warmup-v1"


class CoverageSuiteTests(unittest.TestCase):
    def test_fixed_budget_commands_and_controls(self):
        plan = protocol()
        self.assertEqual(len(plan["jobs"]), 42)
        self.assertEqual(len({j["id"] for j in plan["jobs"]}), 42)
        self.assertEqual(len(plan["jobs"]) * plan["budget"], 336)
        self.assertEqual(sum(plan["budget"] - 1 for j in plan["jobs"] if j["group"] != "random"), 84)
        for kernel in ("matmul", "fir", "conv2d"):
            jobs = [j for j in plan["jobs"] if j["benchmark"] == kernel]
            self.assertEqual(Counter((j["group"], j["coverage_policy"]) for j in jobs), {
                ("random", "none"): 5, ("random", POLICY): 5,
                ("no-feedback", POLICY): 2, ("feedback", POLICY): 2})
            for policy in ("none", POLICY):
                self.assertEqual([j["seed"] for j in jobs if j["group"] == "random" and j["coverage_policy"] == policy], list(range(5)))
        for job in plan["jobs"]:
            args = command(job, "synthetic-output", plan)
            self.assertEqual(args[args.index("--coverage-policy") + 1], job["coverage_policy"])
            self.assertEqual("--no-feedback" in args, job["group"] == "no-feedback")
            self.assertNotIn("--candidate-order-seed", args)
            self.assertNotIn("--cosim", args)
        self.assertFalse(plan["board_access"])
        self.assertEqual(plan["retries"], 0)

    def test_report_does_not_mix_controls_or_invent_complete_comparisons(self):
        from tests.test_suite_report import SuiteReportTests
        fixture = SuiteReportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result = fixture.audit
        result["protocol"] = protocol()
        original = result["runs"][0]
        rows = []
        for job in protocol()["jobs"]:
            row = copy.deepcopy(original)
            row.update(job)
            row["best_metrics"]["latency_us"] = 10 if job["coverage_policy"] == "none" else 20
            row["rtl"]["best_metrics"]["latency_us"] = row["best_metrics"]["latency_us"]
            rows.append(row)
        result.update(runs=rows, groups=aggregate_conditions(rows, result["protocol"]))
        self.assertEqual(len(result["groups"]), 12)
        text = render(result, "SYNTHETIC")
        self.assertIn("随机五种子 · 无覆盖", text)
        self.assertIn("随机五种子 · 流水覆盖", text)
        self.assertIn("| matmul | 随机覆盖增量 | 5/5 | 5/5 | 10.00 → 20.00 | +100.00% |", text)
        self.assertIn("| matmul | 随机五种子 · 无覆盖 | 5/5 | 10.00 / 10.00 |", text)
        self.assertNotIn("15.00 /", text)
        rows[0]["summary"]["completed_budget"] = False
        result["groups"] = aggregate_conditions(rows, result["protocol"])
        self.assertIn("| matmul | 随机覆盖增量 | 4/5 | 5/5 | 10.00 → 20.00 | — |", render(result, "SYNTHETIC"))
        with self.assertRaisesRegex(ValueError, "do not merge"):
            aggregate_curves([], result["protocol"])
        fixture.audit = {**fixture.audit, "runs": [original]}
        original["coverage_policy"] = POLICY
        self.assertTrue(all(row["coverage_policy"] == POLICY for row in trajectories(fixture.root, fixture.audit)))


class CoverageSuiteIntegrationTests(unittest.TestCase):
    native = fixtures.OrderSuiteIntegrationTests.native
    synthesis = fixtures.OrderSuiteIntegrationTests.synthesis
    chat = fixtures.OrderSuiteIntegrationTests.chat
    execute = fixtures.OrderSuiteIntegrationTests.execute

    def setUp(self):
        fixtures.OrderSuiteIntegrationTests.setUp(self)
        self.plan = protocol()

    def test_all_42_jobs_full_audit_and_policy_tamper(self):
        state = run_jobs(self.output, self.plan, execute=self.execute)
        result = audit(self.root, self.references)
        self.assertTrue(result["complete"] and result["all_evidence_audited"], result["runs"])
        self.assertEqual((len(state["jobs"]), self.chat_calls), (42, 84))
        self.assertEqual(sum(r["summary"]["evaluated"] for r in result["runs"]), 336)
        self.assertEqual(sum(r["statuses"]["failed"] for r in result["runs"]), 294)
        self.assertEqual(len(result["groups"]), 12)
        self.assertFalse(result["board_verified"])
        for row in result["runs"]:
            self.assertEqual(row["diagnostic_evidence"]["errors_delivered_to_next_request"],
                             6 if row["group"] == "feedback" else 0)
        job = state["jobs"][1]
        manifest_path = self.output / job["run"] / "manifest.json"
        manifest = read(manifest_path)
        manifest.pop("coverage")
        write_json(manifest_path, manifest)
        changed = audit(self.root, self.references)
        self.assertFalse(changed["all_evidence_audited"])
        self.assertEqual(changed["runs"][1]["state"], "audit_failed")

    def test_stop_partial_budget_failure_and_schedule_tamper(self):
        self.fail_chat_at, self.stop_after = 3, 3
        state = run_jobs(self.output, self.plan, execute=self.execute)
        result = audit(self.root, self.references)
        self.assertTrue(state["stopped"])
        self.assertFalse(result["complete"] or result["all_evidence_audited"])
        self.assertEqual((len(state["jobs"]), self.chat_calls), (3, 3))
        self.assertEqual(sum(r["state"] == "pending" for r in result["runs"]), 39)
        failed = result["runs"][2]
        self.assertTrue(failed["audited"], failed)
        self.assertEqual(failed["summary"]["model_failures"], 1)
        self.assertFalse(failed["summary"]["completed_budget"])
        before = (self.output / "status.json").read_bytes()
        with self.assertRaises(FileExistsError):
            run_jobs(self.output, self.plan, execute=self.execute)
        self.assertEqual((self.output / "status.json").read_bytes(), before)
        state["jobs"].reverse()
        write_json(self.output / "status.json", state)
        with self.assertRaisesRegex(ValueError, "schedule changed"):
            audit(self.root, self.references)
