"""Native contract tests for a prospective kernel; no HLS/model measurements."""
from pathlib import Path
from contextlib import redirect_stdout
import hashlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from autohls.benchmarks import BENCHMARKS, Benchmark, Configuration, configurations, render_candidate
from autohls.verification import find_compiler, verify_native
from scripts.check_prefixsum import check, fault_inputs


PREFIXSUM = Benchmark("prefixsum", "SCAN", (("input", 1),))


class PrefixsumTests(unittest.TestCase):
    def test_preflight_freezes_inputs_and_refuses_existing_output(self):
        def native_fixture(source, tb, directory):
            fault = "faults" in directory.parts
            return {"compile_exit_code": 0, "test_exit_code": 1 if fault else 0,
                    "passed": not fault, "cases": 0 if fault else 32,
                    "checks": 0 if fault else 16512, "seed": 20260927}

        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), \
                patch("scripts.check_prefixsum.find_compiler", return_value="SYNTHETIC fixture compiler"), \
                patch("scripts.check_prefixsum.verify_native", side_effect=native_fixture) as native:
            output = Path(directory) / "new"
            self.assertTrue(check(output))
            self.assertEqual(native.call_count, 42)
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(summary["candidate_checks"], 495360)
            self.assertEqual(summary["model_calls"], 0)
            self.assertEqual(summary["hls_runs"], 0)
            self.assertFalse(summary["rtl_verified"] or summary["board_verified"])
            for name, digest in json.loads((output / "checksums.json").read_text()).items():
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
            self.assertTrue((output / "frozen-code/scripts/check_prefixsum.py").is_file())
            with self.assertRaises(FileExistsError):
                check(output)
            self.assertEqual(native.call_count, 42)

    def test_preflight_does_not_accept_compilation_failure_as_fault_detection(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), \
                patch("scripts.check_prefixsum.find_compiler", return_value="SYNTHETIC fixture compiler"), \
                patch("scripts.check_prefixsum.verify_native", return_value={
                    "compile_exit_code": 1, "passed": False, "cases": 0, "checks": 0}):
            output = Path(directory) / "new"
            self.assertFalse(check(output))
            self.assertFalse(json.loads((output / "summary.json").read_text())["passed"])

    def test_legacy_default_suite_is_unchanged(self):
        self.assertEqual(tuple(BENCHMARKS), ("matmul", "fir", "conv2d"))
        self.assertNotIn(PREFIXSUM.name, BENCHMARKS)

    def test_bounded_transforms_only_touch_declared_loop_and_array(self):
        baseline = PREFIXSUM.source.read_text(encoding="utf-8")
        self.assertEqual(render_candidate(PREFIXSUM, Configuration()), baseline)
        self.assertEqual(len(configurations()), 30)
        for config in configurations():
            source = render_candidate(PREFIXSUM, config)
            self.assertEqual(source.count("#pragma HLS PIPELINE"), int(config.pipeline_ii > 0))
            self.assertEqual(source.count("#pragma HLS UNROLL"), int(config.unroll > 1))
            self.assertEqual(source.count("#pragma HLS ARRAY_PARTITION"), int(config.partition > 1))
            self.assertNotIn("variable=output", source)
            if config.partition > 1:
                self.assertIn(f"variable=input cyclic factor={config.partition} dim=1", source)

    @unittest.skipUnless(find_compiler(), "C++ compiler not on PATH")
    def test_all_candidates_pass_native_without_hardware_claims(self):
        with tempfile.TemporaryDirectory(prefix="prefixsum-native-test-") as directory:
            for config in configurations():
                with self.subTest(config=config.key):
                    root = Path(directory) / config.key
                    root.mkdir()
                    source = root / "kernel.cpp"
                    source.write_text(render_candidate(PREFIXSUM, config), encoding="utf-8")
                    result = verify_native(source, PREFIXSUM.testbench, root / "native")
                    self.assertTrue(result["passed"], result)
                    self.assertEqual((result["cases"], result["checks"], result["seed"]), (32, 16512, 20260927))
                    self.assertFalse(result["rtl_verified"])

    @unittest.skipUnless(find_compiler(), "C++ compiler not on PATH")
    def test_faults_compile_but_are_rejected_by_testbench(self):
        faults = list(fault_inputs())
        self.assertEqual(len(faults), 12)
        with tempfile.TemporaryDirectory(prefix="prefixsum-fault-test-") as directory:
            for name, source, testbench in faults:
                with self.subTest(fault=name):
                    root = Path(directory) / name
                    root.mkdir()
                    candidate, tb = root / "kernel.cpp", root / "testbench.cpp"
                    candidate.write_text(source, encoding="utf-8")
                    tb.write_text(testbench, encoding="utf-8")
                    result = verify_native(candidate, tb, root / "native")
                    self.assertEqual(result["compile_exit_code"], 0)
                    self.assertEqual(result["test_exit_code"], 1)
                    self.assertFalse(result["passed"])
                    self.assertIn("FAIL test=", (root / "native/test.log").read_text())


if __name__ == "__main__":
    unittest.main()
