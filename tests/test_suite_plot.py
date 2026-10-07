import copy
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from scripts.model_suite import protocol
from scripts.plot_model_suite import aggregate_curves, publish, render

ARCHIVE = Path(__file__).resolve().parents[1] / "artifacts/model-suite-20260927/v1"


class SuitePlotTests(unittest.TestCase):
    def setUp(self):
        self.plan = protocol()
        self.plan["budget"] = 3
        self.rows = [{"run": j["id"], "benchmark": j["benchmark"], "group": j["group"], "evaluation": step,
                      "best_hls_latency_us": value + j["seed"]}
                     for j in self.plan["jobs"] for step, value in enumerate((30, 30, 10), 1)]

    def test_ranges_are_not_confidence_intervals_and_failed_step_is_retained(self):
        curves = aggregate_curves(self.rows, self.plan)
        self.assertEqual(len(curves), 27)
        self.assertEqual([r["median_us"] for r in curves[:3]], [32, 32, 12])
        self.assertEqual((curves[0]["min_us"], curves[0]["max_us"], curves[0]["runs"]), (30, 34, 5))
        self.assertEqual(curves[3]["runs"], 2)
        svg = render(curves, self.plan)
        self.assertEqual(svg, render(curves, self.plan))
        ET.fromstring(svg)
        self.assertIn("非置信区间", svg)
        self.assertIn("不是板级计时", svg)
        self.assertIn("不回写搜索轨迹", svg)

    def test_missing_duplicate_nonfinite_or_future_leaking_points_are_rejected(self):
        for mutation in ("missing", "duplicate", "nan", "future", "wrong_group"):
            with self.subTest(mutation=mutation):
                rows = copy.deepcopy(self.rows)
                if mutation == "missing": rows.pop()
                if mutation == "duplicate": rows.append(rows[-1])
                if mutation == "nan": rows[0]["best_hls_latency_us"] = float("nan")
                if mutation == "future": rows[0]["best_hls_latency_us"] = 1
                if mutation == "wrong_group": rows[0]["group"] = "feedback"
                with self.assertRaises(ValueError):
                    aggregate_curves(rows, self.plan)

    @unittest.skipUnless((ARCHIVE / "complete-with-rtl-audit.json").is_file(), "Archived suite is local only")
    def test_real_archive_plot_is_reproducible_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp) / "first", Path(temp) / "second"
            for output in (first, second):
                self.assertEqual(publish(ARCHIVE / "complete-with-rtl-audit.json", ARCHIVE / "complete-with-rtl", output), 72)
            for name in ("convergence.svg", "curves.csv", "provenance.json"):
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
            with self.assertRaises(FileExistsError):
                publish(ARCHIVE / "complete-with-rtl-audit.json", ARCHIVE / "complete-with-rtl", first)
