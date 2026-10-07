from pathlib import Path
import tempfile
import unittest

from autohls.benchmarks import BENCHMARKS, Configuration, render_candidate
from autohls.verification import find_compiler, verify_native


ROOT = Path(__file__).resolve().parents[1]


class BoardBundleTests(unittest.TestCase):
    def test_revision_workaround_changes_only_the_overflowing_field(self):
        from hardware.package_ip import project_revision
        original = 'set Vendor "autohls.local"\nset Revision    "2609231351"\nset_property core_revision $Revision $core\n'
        fixed, old = project_revision(original)
        self.assertEqual(old, 2609231351)
        self.assertEqual(fixed, original.replace('set Revision    "2609231351"', 'set Revision "1"'))
        for invalid in ('set Revision "1"\n', original + original, 'no revision'):
            with self.assertRaises(ValueError):
                project_revision(invalid)

    @unittest.skipUnless(find_compiler(), "C++ compiler not on PATH")
    def test_axi_wrapper_matches_reference_and_rejects_wrong_output(self):
        wrapper = (ROOT / "hardware/hls/matmul_axi.cpp").read_text()
        kernel = render_candidate(BENCHMARKS["matmul"], Configuration(1, 4, 4))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for wrong in (False, True):
                source = root / ("wrong.cpp" if wrong else "correct.cpp")
                current = wrapper.replace("c[i] = local_c[i / 32][i % 32];",
                                          "c[i] = local_c[i / 32][i % 32] + 1;") if wrong else wrapper
                source.write_text(kernel + "\n" + current)
                result = verify_native(source, ROOT / "hardware/hls/matmul_axi_tb.cpp", root / source.stem)
                self.assertEqual(result["passed"], not wrong)
                if not wrong:
                    self.assertEqual(result["checks"], 20480)

    def test_board_device_and_hls_interfaces(self):
        from xml.etree import ElementTree
        board = ElementTree.parse(ROOT / "hardware/vendor/pynq-z2/A.0/board.xml").getroot()
        self.assertEqual(board.find("./components/component").attrib["part_name"], "xc7z020clg400-1")
        source = (ROOT / "hardware/hls/matmul_axi.cpp").read_text()
        for port in ("a", "b", "c"):
            self.assertIn(f"m_axi port={port} offset=slave bundle=gmem depth=1024", source)
            self.assertIn(f"s_axilite port={port} bundle=control", source)
        tcl = (ROOT / "hardware/hls/build_axi.tcl").read_text()
        self.assertLess(tcl.index("csim_design"), tcl.index("csynth_design"))
        self.assertLess(tcl.index("cosim_design"), tcl.index("export_design"))
        overlay = (ROOT / "hardware/build_overlay.tcl").read_text()
        self.assertIn("create_bd_design design_1\n", overlay)
        self.assertNotIn("create_bd_design design\n", overlay)
        self.assertIn("design_1_wrapper.bit", overlay)
        self.assertIn("CONFIG.C_AUX_RESET_HIGH {1}", overlay)
