import hashlib
import ast
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
from types import SimpleNamespace

from hardware.board_test import inspect_bundle, run_tests, _launch_wait, validate_reset

try:
    import numpy as np
except ImportError:
    np = None


class BoardDriverTests(unittest.TestCase):
    @unittest.skipUnless((Path(__file__).resolve().parents[1] / "hardware/02_matmul_acceptance.ipynb").is_file(),
                         "Obsolete first-firmware notebook excluded from submission source")
    def test_notebook_is_unexecuted_and_defaults_to_offline(self):
        root = Path(__file__).resolve().parents[1]
        notebook = json.loads((root / "hardware/02_matmul_acceptance.ipynb").read_text(encoding="utf-8"))
        code = []
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])
                source = "".join(cell["source"])
                ast.parse(source, feature_version=(3, 6))
                code.append(source)
        self.assertIn("CONFIRM_BOARD = False", "\n".join(code))
        self.assertIn("confirm_board=CONFIRM_BOARD", code[-1])
        ast.parse((root / "hardware/board_test.py").read_text(), feature_version=(3, 6))

    def test_submission_excludes_obsolete_notebook_and_preserves_driver_syntax(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "hardware/02_matmul_acceptance.ipynb").exists())
        ast.parse((root / "hardware/board_test.py").read_text(), feature_version=(3, 6))

    def test_no_implicit_board_access(self):
        for value in (False, None, 1, "yes"):
            with self.assertRaisesRegex(RuntimeError, "not confirmed"):
                run_tests("does-not-exist", confirm_board=value)

    def test_inspection_hashes_all_hardware_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ("autohls_matmul.bit", "autohls_matmul.hwh", "xmatmul_axi_hw.h")
            data = {name: name.encode() for name in names}
            data["autohls_matmul.hwh"] = b'''<EDKSYSTEM><MODULES>
              <MODULE INSTANCE="rst"><PARAMETERS>
                <PARAMETER NAME="C_EXT_RESET_HIGH" VALUE="0"/>
                <PARAMETER NAME="C_AUX_RESET_HIGH" VALUE="1"/>
              </PARAMETERS><PORTS><PORT NAME="aux_reset_in" SIGNAME="zero"/></PORTS></MODULE>
              <MODULE MODTYPE="xlconstant"><PARAMETERS><PARAMETER NAME="CONST_VAL" VALUE="0"/>
              </PARAMETERS><PORTS><PORT NAME="dout" SIGNAME="zero"/></PORTS></MODULE>
            </MODULES></EDKSYSTEM>'''
            for name in names:
                (root / name).write_bytes(data[name])
            manifest = {"board": "PYNQ-Z2", "part": "xc7z020clg400-1",
                        "registers": {"control": 0, "a": 16, "b": 24, "c": 32},
                        "sha256": {name: hashlib.sha256(data[name]).hexdigest() for name in names}}
            (root / "manifest.json").write_text(json.dumps(manifest))
            self.assertEqual(inspect_bundle(root), manifest)
            for name in names:
                (root / name).write_bytes(b"corrupted")
                with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                    inspect_bundle(root)
                (root / name).write_bytes(data[name])

    def test_reproduced_reset_fault_is_rejected_before_loading(self):
        root = Path(__file__).resolve().parents[1]
        for variant in ("baseline32", "optimized32"):
            bundle = root / "artifacts/preboard-20260923/bundle" / variant
            if not bundle.exists():
                self.skipTest("Historical board evidence not available")
            with self.assertRaisesRegex(ValueError, "Unsafe auxiliary reset"):
                inspect_bundle(bundle)
            hwh = ET.parse(bundle / "autohls_matmul.hwh").getroot()
            polarity = hwh.find(".//MODULE[@INSTANCE='rst']/PARAMETERS/PARAMETER[@NAME='C_AUX_RESET_HIGH']")
            polarity.set("VALUE", "1")
            validate_reset(hwh)
            hwh.find(".//MODULE[@INSTANCE='rst']/PORTS/PORT[@NAME='aux_reset_in']").set("SIGNAME", "disconnected")
            with self.assertRaisesRegex(ValueError, "inactive level"):
                validate_reset(hwh)

    def test_polling_done_idle_and_timeout(self):
        class IP:
            def __init__(self, states):
                self.states = iter(states)
                self.writes = []

            def read(self, offset):
                return next(self.states)

            def write(self, offset, value):
                self.writes.append((offset, value))

        ip = IP([4, 0, 2])
        ticks = iter([0, .01, .02])
        self.assertEqual(_launch_wait(ip, 1, lambda: next(ticks)), 20000)
        self.assertEqual(ip.writes, [(0, 1)])
        busy = IP([0])
        with self.assertRaisesRegex(RuntimeError, "not idle"):
            _launch_wait(busy, 1)
        self.assertEqual(busy.writes, [])
        ticks = iter([0, 2])
        with self.assertRaises(TimeoutError):
            _launch_wait(IP([4, 0]), 1, lambda: next(ticks))

    @unittest.skipUnless(np is not None, "NumPy dispatch test dependency")
    def test_buffer_lifetime_and_output_checks_with_fake_board(self):
        from hardware import board_test
        for scenario in ("correct", "wrong", "timeout"):
            allocated = []
            memory = {}

            class Buffer(np.ndarray):
                def flush(self):
                    self.flushed = True

                def invalidate(self):
                    self.invalidated = True

                def freebuffer(self):
                    self.freed = True

            def allocate(shape, dtype):
                buffer = np.zeros(shape, dtype=dtype).view(Buffer)
                buffer.physical_address = 0x100000 + len(allocated) * 0x1000
                buffer.freed = False
                buffer.flushed = buffer.invalidated = False
                allocated.append(buffer)
                memory[buffer.physical_address] = buffer
                return buffer

            class IP:
                status = 4

                def __init__(self):
                    self.registers = {}

                def read(self, address):
                    status, self.status = self.status, 4
                    return status

                def write(self, address, value):
                    self.registers[address] = value
                    if address == 0:
                        a, b, c = [memory[self.registers[port]] for port in (16, 24, 32)]
                        assert all(buffer.flushed for buffer in (a, b, c))
                        c[:] = np.dot(a.astype(np.int64), b.astype(np.int64)) + (scenario == "wrong")
                        self.status = 6

            overlay = SimpleNamespace(matmul_axi_0=IP(), ip_dict={"matmul_axi_0": {"phys_addr": 0x43C00000}})
            fake = SimpleNamespace(__version__="2.5", Clocks=SimpleNamespace(fclk0_mhz=100),
                                   Overlay=lambda _: overlay, allocate=allocate)
            manifest = {"control_base": 0x43C00000, "clock_mhz": 100, "variant": "fake",
                        "registers": {"a": 16, "b": 24, "c": 32},
                        "sha256": {"autohls_matmul.bit": "test-only"}}
            with patch.dict("sys.modules", {"pynq": fake}), patch.object(board_test, "inspect_bundle", return_value=manifest):
                try:
                    if scenario == "correct":
                        result = run_tests("fake", confirm_board=True)
                        self.assertEqual(result["checks"], 20480)
                        self.assertEqual(len(result["measurements"]), 20)
                        self.assertTrue(all(buffer.invalidated for buffer in allocated[-1:]))
                    elif scenario == "wrong":
                        with self.assertRaisesRegex(AssertionError, "output mismatch"):
                            run_tests("fake", confirm_board=True)
                    else:
                        with patch.object(board_test, "_launch_wait", side_effect=TimeoutError):
                            with self.assertRaises(TimeoutError):
                                run_tests("fake", confirm_board=True)
                        self.assertTrue(board_test._retained_buffers)
                        with self.assertRaisesRegex(RuntimeError, "Previous operation"):
                            run_tests("fake", confirm_board=True)
                    self.assertTrue(all(buffer.freed == (scenario != "timeout") for buffer in allocated))
                finally:
                    # These are ordinary fake arrays, never physical CMA memory.
                    board_test._retained_buffers.clear()
