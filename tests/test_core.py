from pathlib import Path
import tempfile
import unittest

from autohls.core import analyze_source, explore, pareto_frontier
from autohls.vitis import build_tcl, parse_csynth_xml


ROOT = Path(__file__).resolve().parents[1]


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matmul = (ROOT / "examples" / "matmul.cpp").read_text(encoding="utf-8")

    def test_analyze_matmul(self) -> None:
        profile = analyze_source(self.matmul)
        self.assertEqual(profile.top_function, "matmul")
        self.assertEqual(profile.loops, 3)
        self.assertGreaterEqual(profile.max_loop_depth, 2)

    def test_explore_is_deterministic_and_ranked(self) -> None:
        first = explore(self.matmul, goal="balanced")
        second = explore(self.matmul, goal="balanced")
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["explored"], 8)
        self.assertTrue(first["candidates"][0]["recommended"])
        self.assertEqual([item["rank"] for item in first["candidates"]], list(range(1, 9)))

    def test_goals_change_recommendation(self) -> None:
        speed = explore(self.matmul, goal="latency")
        area = explore(self.matmul, goal="resource")
        self.assertNotEqual(speed["summary"]["best_id"], area["summary"]["best_id"])

    def test_pareto_filters_dominated_candidate(self) -> None:
        candidates = [
            {"id": "A", "metrics": {"latency_us": 1, "resources": {"lut": 1, "ff": 1, "dsp": 1, "bram": 1}}},
            {"id": "B", "metrics": {"latency_us": 2, "resources": {"lut": 2, "ff": 2, "dsp": 2, "bram": 2}}},
        ]
        self.assertEqual(pareto_frontier(candidates), {"A"})

    def test_rejects_empty_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能为空"):
            explore("  ")


class VitisTests(unittest.TestCase):
    def test_tcl_contains_required_steps(self) -> None:
        script = build_tcl("kernel.cpp", "kernel", "xck26-test", 5.0)
        self.assertIn("set_top kernel", script)
        self.assertIn("csynth_design", script)

    def test_parse_csynth_report(self) -> None:
        xml = """<Report><PerformanceEstimates>
          <SummaryOfTimingAnalysis><EstimatedClockPeriod>4.8</EstimatedClockPeriod></SummaryOfTimingAnalysis>
          <SummaryOfOverallLatency><Average-caseLatency>1000</Average-caseLatency><Interval-max>2</Interval-max></SummaryOfOverallLatency>
        </PerformanceEstimates><AreaEstimates><Resources>
          <LUT>123</LUT><FF>456</FF><DSP>7</DSP><BRAM_18K>8</BRAM_18K>
        </Resources></AreaEstimates></Report>"""
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.xml"
            report.write_text(xml, encoding="utf-8")
            parsed = parse_csynth_xml(report)
        self.assertEqual(parsed["cycles"], 1000)
        self.assertEqual(parsed["latency_us"], 4.8)
        self.assertEqual(parsed["resources"]["dsp"], 7)


if __name__ == "__main__":
    unittest.main()

