from pathlib import Path
import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET

from autohls.benchmarks import BENCHMARKS, Configuration, render_candidate
from autohls.verification import find_compiler, verify_native
from hardware.board_test import inspect_bundle
from hardware.package_batch import validate_batch_interface

ROOT = Path(__file__).resolve().parents[1]


class HardwareBatchTests(unittest.TestCase):
    @unittest.skipUnless(find_compiler(), "C++ compiler not on PATH")
    def test_batch_reference_and_fault_detection(self):
        wrapper = (ROOT / "hardware/hls/matmul_batch_axi.cpp").read_text()
        kernel = render_candidate(BENCHMARKS["matmul"], Configuration(2, 8, 8))
        mutations = {
            "correct": wrapper,
            "wrong_stride": wrapper.replace("matrix * 1024", "matrix * 1023"),
            "missing_last": wrapper.replace("matrix < batch_count", "matrix + 1 < batch_count"),
            "wrong_output": wrapper.replace("= local_c[i / 32][i % 32];", "= local_c[i / 32][i % 32] + 1;"),
            "bad_count": wrapper.replace("if (batch_count == 0 || batch_count > 64) return;",
                                         "if (batch_count == 0 || batch_count > 64) batch_count = 1;"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, source in mutations.items():
                with self.subTest(name=name):
                    path = root / (name + ".cpp")
                    path.write_text(kernel + "\n" + source)
                    result = verify_native(path, ROOT / "hardware/hls/matmul_batch_axi_tb.cpp", root / name)
                    self.assertEqual(result["passed"], name == "correct")
                    self.assertEqual(result["compile_exit_code"], 0)
                    if name == "correct":
                        self.assertEqual(result["checks"], 720896)
                    else:
                        self.assertEqual(result["test_exit_code"], 1)

    def test_batch_interface_and_legacy_rejection(self):
        header = ""
        root = ET.Element("EDKSYSTEM")
        module = ET.SubElement(root, "MODULE", INSTANCE="matmul_axi_0")
        parameters = ET.SubElement(module, "PARAMETERS")
        for name in ("C_M_AXI_GMEM_ADDR_WIDTH", "C_M_AXI_GMEM_DATA_WIDTH"):
            ET.SubElement(parameters, "PARAMETER", NAME=name, VALUE="32")
        for name, offset in (("a", 16), ("b", 24), ("c", 32), ("batch_count", 40)):
            header += "#define XMATMUL_AXI_CONTROL_ADDR_{}_DATA {}\n".format(name.upper(), hex(offset))
            header += "#define XMATMUL_AXI_CONTROL_BITS_{}_DATA 32\n".format(name.upper())
            register = ET.SubElement(module, "REGISTER", NAME=name)
            ET.SubElement(register, "PROPERTY", NAME="ADDRESS_OFFSET", VALUE=str(offset))
            ET.SubElement(register, "PROPERTY", NAME="SIZE", VALUE="32")
        validate_batch_interface(header, root)
        for wrong in (header.replace("COUNT_DATA 0x28", "COUNT_DATA 0x30"),
                      header.replace("BITS_A_DATA 32", "BITS_A_DATA 64"), ""):
            with self.assertRaises(ValueError):
                validate_batch_interface(wrong, root)
        for property_name in ("SIZE", "ADDRESS_OFFSET"):
            wrong = copy.deepcopy(root)
            wrong.find(".//REGISTER[@NAME='batch_count']/PROPERTY[@NAME='" + property_name + "']").set("VALUE", "64")
            with self.assertRaises(ValueError):
                validate_batch_interface(header, wrong)
        with tempfile.TemporaryDirectory() as directory:
            manifest = {"board": "PYNQ-Z2", "part": "xc7z020clg400-1",
                        "registers": {"control": 0, "a": 16, "b": 24, "c": 32, "batch_count": 40}}
            (Path(directory) / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "register layout"):
                inspect_bundle(directory)

    def test_build_isolated_and_preserves_safety_sequence(self):
        source = (ROOT / "hardware/hls/matmul_batch_axi.cpp").read_text()
        self.assertLess(source.index("if (batch_count == 0"), source.index("a[offset + i]"))
        for port in ("a", "b", "c"):
            self.assertIn("m_axi port=" + port + " offset=slave bundle=gmem depth=65536", source)
        script = (ROOT / "hardware/hls/build_batch_axi.tcl").read_text()
        self.assertLess(script.index("csim_design"), script.index("csynth_design"))
        self.assertLess(script.index("cosim_design"), script.index("export_design"))
        self.assertIn("create_clock -period 10.0", script)
        self.assertIn("hardware/build_overlay.tcl", (ROOT / "scripts/cloud-build-batch.sh").read_text())
        self.assertIn('> "$work/build.status"', (ROOT / "scripts/cloud-build-batch.sh").read_text())
