"""Tool dispatch tests use stubs, not evidence of a real HLS installation."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from autohls.core import DEVICE
from autohls.experiments import run_experiment
from autohls.vitis import detect_vitis, run_synthesis


class HLSToolchainTests(unittest.TestCase):
    def test_default_device_is_pynq_z2(self):
        self.assertEqual(DEVICE["part"], "xc7z020clg400-1")
        self.assertEqual(DEVICE["clock_ns"], 10.0)
        self.assertEqual(DEVICE["capacity"], {"lut": 53200, "ff": 106400, "dsp": 220, "bram": 280})

    def test_detect_vivado_and_vitis(self):
        for name, engine in (("vivado_hls", "vivado-hls"), ("vitis_hls", "vitis-hls")):
            with self.subTest(name=name), patch("autohls.vitis.shutil.which", side_effect=lambda n: n if n == name else None):
                detected = detect_vitis()
                self.assertTrue(detected["available"])
                self.assertEqual(detected["engine"], engine)

    def test_prefer_vivado_for_pynq_25(self):
        with patch("autohls.vitis.shutil.which", side_effect=lambda name: name):
            self.assertEqual(detect_vitis()["executable"], "vivado_hls")

    def test_missing_tool_fails_without_synthetic_result(self):
        with patch("autohls.vitis.shutil.which", return_value=None):
            self.assertFalse(detect_vitis()["available"])
            with self.assertRaisesRegex(RuntimeError, "vivado_hls"):
                run_synthesis("missing.cpp", "kernel", "unused")

    def test_synthesis_uses_pynq_part_and_selected_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "kernel.cpp"
            source.write_text("void kernel() {}", encoding="utf-8")

            def fake_hls(command, **kwargs):
                self.assertEqual(command[0], "vivado_hls")
                script = Path(command[2]).read_text(encoding="utf-8")
                self.assertIn("set_part {xc7z020clg400-1}", script)
                self.assertIn("create_clock -period 10.0", script)
                report = kwargs["cwd"] / "autohls_project/solution1/syn/report/kernel_csynth.xml"
                report.parent.mkdir(parents=True)
                report.write_text("<Report/>", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "TEST STUB ONLY", "")

            with patch("autohls.vitis.shutil.which", side_effect=lambda n: n if n == "vivado_hls" else None), \
                    patch("autohls.vitis.subprocess.run", side_effect=fake_hls):
                result = run_synthesis(source, "kernel", directory)
            self.assertEqual(result["engine"], "vivado-hls")
            self.assertEqual(result["part"], DEVICE["part"])
            self.assertFalse(result["cosim_passed"])

    def test_research_rejects_resources_larger_than_pynq(self):
        limits = dict(DEVICE["capacity"], dsp=221)
        with self.assertRaisesRegex(ValueError, "PYNQ-Z2"):
            run_experiment("matmul", limits=limits)


if __name__ == "__main__":
    unittest.main()
