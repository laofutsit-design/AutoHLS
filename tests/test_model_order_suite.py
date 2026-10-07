import copy
import random
import unittest

from autohls.benchmarks import config_record, configurations
from scripts.audit_model_suite import audit_candidate_presentation
from scripts.model_order_suite import aggregate_conditions
from scripts.prepare_order_suite import protocol
from scripts.report_model_suite import render, trajectories
from scripts.plot_model_suite import aggregate_curves


class OrderSuiteAuditTests(unittest.TestCase):
    def fixture(self, seed=None):
        order = [c.key for c in configurations()[:3]]
        remaining = [config_record(c) for c in configurations()[3:]]
        trace = {}
        if seed is not None:
            trace["candidate_order_seed"] = seed + 3
            random.Random(seed + 3).shuffle(remaining)
        request = {"format": {"type": "object", "properties": {
            "id": {"type": "string", "enum": [c["id"] for c in remaining]}, "reason": {"type": "string"}},
            "required": ["id", "reason"], "additionalProperties": False}}
        return {"allowed_candidates": remaining}, request, trace, {"candidate_order_seed": seed}, order, 3

    def test_canonical_and_fixed_step_permutation_pass(self):
        audit_candidate_presentation(*self.fixture())
        audit_candidate_presentation(*self.fixture(20260927))

    def test_missing_seed_reordering_and_schema_tampering_are_rejected(self):
        for mutation in ("seed", "candidates", "schema", "used_id"):
            args = self.fixture(20260927)
            if mutation == "seed":
                args[2].clear()
            elif mutation == "candidates":
                args[0]["allowed_candidates"].reverse()
            elif mutation == "schema":
                args[1]["format"]["properties"]["id"]["enum"].reverse()
            else:
                args[0]["allowed_candidates"][0] = config_record(configurations()[0])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                audit_candidate_presentation(*args)

    def test_unplanned_shuffle_is_rejected(self):
        args = self.fixture()
        args[2]["candidate_order_seed"] = 0
        with self.assertRaisesRegex(ValueError, "Unplanned"):
            audit_candidate_presentation(*args)

    def test_aggregation_keeps_conditions_and_missing_counts_separate(self):
        plan = protocol()
        rows = []
        for job in plan["jobs"]:
            value = {"none": 10, "canonical": 20, "shuffled": 30}[job["order_condition"]]
            rows.append({**job, "audited": True, "summary": {"completed_budget": True, "wall_seconds": value},
                         "best_metrics": {"latency_us": value}, "order": ["fixture"]})
        groups = aggregate_conditions(rows, plan)
        self.assertEqual(len(groups), 15)
        for group in groups:
            self.assertEqual(group["median_latency_us"], {"none": 10, "canonical": 20, "shuffled": 30}[group["order_condition"]])
            self.assertEqual(group["planned_runs"], 5 if group["group"] == "random" else 2)
        missing = copy.deepcopy(rows)
        for row in missing:
            if row["benchmark"] == "fir" and row["order_condition"] == "shuffled":
                row["audited"] = False
        groups = aggregate_conditions(missing, plan)
        affected = [g for g in groups if g["benchmark"] == "fir" and g["order_condition"] == "shuffled"]
        self.assertEqual(len(affected), 2)
        self.assertTrue(all(g["planned_runs"] == 2 and g["complete_with_feasible"] == 0 and g["median_latency_us"] is None for g in affected))

    def test_report_keeps_rtl_conditions_separate_and_curve_merge_is_blocked(self):
        from tests.test_suite_report import SuiteReportTests
        fixture = SuiteReportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        audit = fixture.audit
        audit["protocol"] = protocol()
        original = audit["runs"][0]
        rows = []
        for condition, value in (("canonical", 20), ("shuffled", 40)):
            row = copy.deepcopy(original)
            row.update(id="fixture-" + condition, group="feedback", order_condition=condition,
                       candidate_order_seed=20260927 if condition == "shuffled" else None)
            row["best_metrics"]["latency_us"] = value
            row["rtl"]["best_metrics"]["latency_us"] = value
            rows.append(row)
        audit["runs"] = rows
        audit["groups"] = aggregate_conditions(rows, audit["protocol"])
        report = render(audit, "fixture-only-not-real-evidence")
        self.assertIn("| matmul | 模型有反馈，两次重复 · 原顺序 | 1/2 | 20.00 / 20.00 |", report)
        self.assertIn("| matmul | 模型有反馈，两次重复 · 固定种子重排 | 1/2 | 40.00 / 40.00 |", report)
        self.assertNotIn("30.00 /", report)
        with self.assertRaisesRegex(ValueError, "do not merge"):
            aggregate_curves([], audit["protocol"])

    def test_trajectory_records_condition_and_order_seed(self):
        from tests.test_suite_report import SuiteReportTests
        fixture = SuiteReportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.audit["runs"][0].update(order_condition="shuffled", candidate_order_seed=20260927)
        rows = trajectories(fixture.root, fixture.audit)
        self.assertTrue(all(r["order_condition"] == "shuffled" and r["candidate_order_seed"] == 20260927 for r in rows))

    def test_paired_comparison_uses_same_mode_and_requires_complete_denominators(self):
        from tests.test_suite_report import SuiteReportTests
        fixture = SuiteReportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        audit = fixture.audit
        audit["protocol"] = protocol()
        original = audit["runs"][0]
        rows = []
        for condition, value in (("canonical", 20), ("shuffled", 40)):
            for repeat in (0, 1):
                row = copy.deepcopy(original)
                row.update(id=f"fixture-{condition}-{repeat}", group="feedback", repeat=repeat,
                           order_condition=condition, candidate_order_seed=20260927 if condition == "shuffled" else None)
                row["best_metrics"]["latency_us"] = value
                rows.append(row)
        audit["runs"] = rows
        audit["groups"] = aggregate_conditions(rows, audit["protocol"])
        report = render(audit, "synthetic-fixture-not-real-evidence")
        self.assertIn("| matmul | 有 | 2/2 | 2/2 | 20.00 → 40.00 | +100.00% |", report)
        self.assertIn("| matmul | 无 | 0/2 | 0/2 | — → — | — |", report)
        rows[-1]["summary"]["completed_budget"] = False
        audit["groups"] = aggregate_conditions(rows, audit["protocol"])
        report = render(audit, "synthetic-fixture-not-real-evidence")
        self.assertIn("| matmul | 有 | 2/2 | 1/2 | 20.00 → 40.00 | — |", report)
