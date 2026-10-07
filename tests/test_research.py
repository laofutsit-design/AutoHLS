from copy import deepcopy
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from autohls.benchmarks import BENCHMARKS, Configuration, configurations, render_candidate
from autohls.core import DEVICE
from autohls.experiments import eligible, run_experiment, summarize
from autohls.verification import find_compiler, verify_native
from autohls.vitis import build_tcl, parse_csynth_xml


class ResearchTests(unittest.TestCase):
    def test_candidates_target_existing_loops_and_arrays(self):
        for name, benchmark in BENCHMARKS.items():
            self.assertEqual(render_candidate(benchmark, Configuration()), benchmark.source.read_text(encoding="utf-8"))
            generated = render_candidate(benchmark, Configuration(1, 4, 4))
            self.assertIn(f"{benchmark.loop}:", generated)
            self.assertEqual(generated.count("#pragma HLS UNROLL factor=4"), 1)
            self.assertNotIn("LOOP_INNER", generated)
            for array, dim in benchmark.arrays:
                self.assertIn(f"variable={array} cyclic factor=4 dim={dim}", generated)
            self.assertNotIn("type=cyclic", generated)

    def test_bounded_configurations(self):
        self.assertEqual(len(configurations()), 30)
        self.assertEqual(len({x.key for x in configurations()}), 30)
        for args in ((1, 2, 8), (True, 1, 1), (1, 3, 1)):
            with self.assertRaises(ValueError):
                Configuration(*args)

    def test_failures_and_unknown_metrics_cannot_win(self):
        valid = {"id": "p0-u1-a1", "status": "synthesized", "verification": {"passed": True},
                 "synthesis": {"csim_passed": True, "cosim_passed": False},
                 "metrics": {"latency_us": 12, "timing_met": True,
                             "resources": {"lut": 200, "ff": 100, "dsp": 2, "bram": 0}}}
        records = [valid]
        for change in ("unknown", "timing", "resource", "wrong", "zero", "nan", "missing_resource", "no_csim"):
            item = deepcopy(valid)
            item["id"] = change
            if change == "unknown": item["metrics"]["latency_us"] = None
            if change == "timing": item["metrics"]["timing_met"] = False
            if change == "resource": item["metrics"]["resources"]["bram"] = DEVICE["capacity"]["bram"] + 1
            if change == "wrong": item["verification"]["passed"] = False
            if change == "zero": item["metrics"]["latency_us"] = 0
            if change == "nan": item["metrics"]["latency_us"] = float("nan")
            if change == "missing_resource": item["metrics"]["resources"]["dsp"] = None
            if change == "no_csim": item["synthesis"]["csim_passed"] = False
            self.assertFalse(eligible(item, DEVICE["capacity"]), change)
            records.append(item)
        summary = summarize(records, DEVICE["capacity"], "balanced")
        self.assertEqual(summary["best_id"], valid["id"])
        self.assertEqual(summary["feasible_synthesized"], 1)
        self.assertFalse(summary["best_rtl_verified"])

    def test_native_runs_have_no_hardware_ranking(self):
        result = summarize([{"id": "a", "status": "verified", "verification": {"passed": True}}], DEVICE["capacity"], "latency")
        self.assertIsNone(result["best_id"])
        self.assertIsNone(result["hls_speedup"])

    def test_model_experiment_pins_identity_and_propagates_seed(self):
        identity = {"model": {"name": "test:tag", "digest": "test-digest"}, "ollama_version": "test"}
        with tempfile.TemporaryDirectory() as directory:
            with patch("autohls.experiments.find_compiler", return_value="g++"), \
                    patch("autohls.experiments.detect_vitis", return_value={"available": True}), \
                    patch("autohls.experiments.inspect_ollama", return_value=identity), \
                    patch("autohls.experiments.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "test compiler", "")), \
                    patch("autohls.experiments.verify_native", return_value={"passed": True}), \
                    patch("autohls.experiments.run_synthesis", side_effect=RuntimeError("test synthesis failure")), \
                    patch("autohls.experiments.propose_ollama", return_value=Configuration(1, 2, 2)) as proposal:
                run = run_experiment("matmul", directory, backend="hls", planner="ollama", model="test:tag",
                                     budget=2, seed=42, feedback=False)
            manifest = json.loads((run / "manifest.json").read_text())
            summary = json.loads((run / "summary.json").read_text())
            records = [json.loads(line) for line in (run / "history.jsonl").read_text().splitlines()]
            self.assertEqual(manifest["model_identity"], identity)
            self.assertEqual(manifest["model_options"]["seed"], 42)
            self.assertEqual(manifest["candidate_order"], {"policy": "canonical", "seed": None, "seed_rule": None})
            self.assertIsNone(proposal.call_args.kwargs["candidate_order_seed"])
            self.assertEqual(proposal.call_args.kwargs["seed"], 42)
            self.assertEqual(proposal.call_args.kwargs["expected_identity"], identity)
            self.assertEqual(proposal.call_args.kwargs["target"]["transform"]["loop"], "DOT")
            self.assertFalse(proposal.call_args.args[7])
            self.assertEqual(records[0]["id"], Configuration().key)
            self.assertTrue(summary["completed_budget"])
            self.assertIsNone(summary["best_id"])
            self.assertEqual(summary["evaluation_seconds"], sum(item["evaluation_seconds"] for item in records))
            self.assertTrue(all(item["status"] == "failed" for item in records))

    def test_missing_model_stops_before_any_evaluation(self):
        with patch("autohls.experiments.find_compiler", return_value="g++"), \
                patch("autohls.experiments.detect_vitis", return_value={"available": True}), \
                patch("autohls.experiments.inspect_ollama", side_effect=ValueError("not installed")), \
                patch("autohls.experiments.verify_native") as native:
            with self.assertRaisesRegex(ValueError, "not installed"):
                run_experiment("matmul", backend="hls", planner="ollama", model="test:tag")
            native.assert_not_called()

    def test_order_seed_requires_model_planner_and_integer(self):
        for planner, value in (("random", 1), ("exhaustive", 1), ("ollama", True), ("ollama", "1")):
            with patch("autohls.experiments.find_compiler") as compiler:
                with self.assertRaisesRegex(ValueError, "Candidate order seed"):
                    run_experiment("matmul", backend="hls", planner=planner, model="fixture", candidate_order_seed=value)
                compiler.assert_not_called()

    def test_order_seed_keeps_baseline_budget_and_records_step_seeds(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("autohls.experiments.find_compiler", return_value="g++"), \
                    patch("autohls.experiments.detect_vitis", return_value={"available": True}), \
                    patch("autohls.experiments.inspect_ollama", return_value={"model": {"digest": "fixture"}}), \
                    patch("autohls.experiments.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "fixture", "")), \
                    patch("autohls.experiments.verify_native", return_value={"passed": True}), \
                    patch("autohls.experiments.run_synthesis", side_effect=RuntimeError("fixture failure")) as synthesis, \
                    patch("autohls.experiments.propose_ollama", side_effect=[Configuration(1, 2, 2), Configuration(2, 2, 2)]) as proposal:
                run = run_experiment("matmul", directory, backend="hls", planner="ollama", model="fixture",
                                     budget=3, seed=7, candidate_order_seed=100)
            manifest = json.loads((run / "manifest.json").read_text())
            records = [json.loads(line) for line in (run / "history.jsonl").read_text().splitlines()]
            self.assertEqual(manifest["candidate_order"], {"policy": "remaining-shuffle-v1", "seed": 100, "seed_rule": "base + evaluation_index"})
            self.assertEqual([c.kwargs["candidate_order_seed"] for c in proposal.call_args_list], [101, 102])
            self.assertEqual([c.kwargs["seed"] for c in proposal.call_args_list], [7, 7])
            self.assertEqual(records[0]["id"], Configuration().key)
            self.assertEqual(len(records), 3)
            self.assertEqual(synthesis.call_count, 3)
            self.assertEqual(len({r["id"] for r in records}), 3)

    def test_recommendation_never_chooses_a_dominated_tie(self):
        first = {"id": "a", "status": "synthesized", "verification": {"passed": True},
                 "synthesis": {"csim_passed": True, "cosim_passed": False},
                 "metrics": {"latency_us": 10, "timing_met": True,
                             "resources": {"lut": 50, "ff": 100, "dsp": 2, "bram": 0}}}
        better = deepcopy(first)
        better["id"] = "b"
        better["metrics"]["resources"]["lut"] = 40
        for goal in ("latency", "resource", "balanced"):
            result = summarize([first, better], DEVICE["capacity"], goal)
            self.assertEqual(result["best_id"], "b")
            self.assertIn(result["best_id"], result["pareto_ids"])

    def test_xml_preserves_unknown_and_zero(self):
        xml = """<Report><PerformanceEstimates><SummaryOfOverallLatency>
        <Average-caseLatency>undef</Average-caseLatency></SummaryOfOverallLatency>
        </PerformanceEstimates><AreaEstimates><Resources><DSP48E>0</DSP48E></Resources></AreaEstimates></Report>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.xml"
            path.write_text(xml)
            result = parse_csynth_xml(path)
        self.assertIsNone(result["cycles"])
        self.assertIsNone(result["latency_us"])
        self.assertIsNone(result["resources"]["lut"])
        self.assertEqual(result["resources"]["dsp"], 0)

    def test_target_clock_drives_latency_and_detects_missed_timing(self):
        xml = """<Report><PerformanceEstimates><SummaryOfOverallLatency>
        <Worst-caseLatency>100</Worst-caseLatency><Average-caseLatency>50</Average-caseLatency>
        </SummaryOfOverallLatency><SummaryOfTimingAnalysis><EstimatedClockPeriod>6</EstimatedClockPeriod>
        </SummaryOfTimingAnalysis></PerformanceEstimates></Report>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.xml"
            path.write_text(xml)
            result = parse_csynth_xml(path, target_clock_ns=5)
        self.assertEqual(result["latency_us"], .5)
        self.assertFalse(result["timing_met"])

    def test_tcl_orders_validation_and_escapes_paths(self):
        script = build_tcl('D:/test/[abc]/file.cpp', 'matmul', 'xck26-test', 5, testbench='D:/test/tb.cpp', cosim=True)
        self.assertIn('\\[abc\\]', script)
        self.assertLess(script.index('csim_design'), script.index('csynth_design'))
        self.assertLess(script.index('csynth_design'), script.index('cosim_design'))
        with self.assertRaises(ValueError):
            build_tcl('kernel.cpp', 'kernel; exit', 'part', 5)

    @unittest.skipUnless(find_compiler(), "C++ compiler not on PATH")
    def test_reference_rejects_broken_matmul(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            candidate = path / 'kernel.cpp'
            source = render_candidate(BENCHMARKS['matmul'], Configuration(1, 4, 4))
            candidate.write_text(source.replace('c[i][j] = sum;', 'c[i][j] = sum + 1;'))
            result = verify_native(candidate, BENCHMARKS['matmul'].testbench, path / 'native')
        self.assertEqual(result['compile_exit_code'], 0)
        self.assertFalse(result['passed'])
        self.assertEqual(result['test_exit_code'], 1)


if __name__ == '__main__':
    unittest.main()
