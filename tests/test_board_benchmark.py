import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hardware import board_benchmark as bench

try:
    import numpy as np
except ImportError:
    np = None


class BenchmarkTests(unittest.TestCase):
    def test_offline_and_python36(self):
        ast.parse(Path(bench.__file__).read_text(), feature_version=(3, 6))
        for confirmation in (False, None, 1, "yes"):
            with self.assertRaisesRegex(RuntimeError, "not confirmed"):
                bench.run_fpga("missing", [], confirm_board=confirmation)
        for options in ({"cacheable": 1}, {"profile": "yes"}, {"plain_views": 1}):
            with self.assertRaisesRegex(ValueError, "must be bool"):
                bench.run_fpga("missing", [], confirm_board=True, **options)

    def test_statistics_keep_outliers(self):
        stats = bench.distribution(list(range(1, 100)) + [10000])
        self.assertEqual(stats, {"count": 100, "min": 1, "median": 50.5, "p95": 95, "max": 10000})
        for values in ([], [float("nan")], [-1], [float("inf")]):
            with self.assertRaises(ValueError):
                bench.distribution(values)

    @unittest.skipUnless(np is not None, "NumPy test dependency")
    def test_dataset_contract_and_warmup(self):
        values, digest = bench.dataset(42, 5)
        self.assertEqual(digest, bench.dataset(42, 5)[1])
        self.assertNotEqual(digest, bench.dataset(43, 5)[1])
        self.assertTrue(np.all(values[1][2] == 32000000))
        self.assertTrue(np.all(values[2][2] == -32000000))
        self.assertTrue(np.array_equal(values[3][0], values[3][2]))
        self.assertEqual(len(bench._inputs(values, 2)), 7)
        values[0][0][0, 0] = 1001
        with self.assertRaisesRegex(ValueError, "domain"):
            bench._inputs(values, 2)

    @unittest.skipUnless(np is not None, "NumPy test dependency")
    def test_cpu_wrong_output_rejected_and_warmup_excluded(self):
        values, _ = bench.dataset(42, 4)
        for wrong in (False, True):
            def cpu(a, b, c):
                arrays = [np.ctypeslib.as_array(pointer, shape=(1024,)).reshape(32, 32)
                          for pointer in (a, b, c)]
                arrays[2][:] = np.dot(arrays[0].astype(np.int64), arrays[1].astype(np.int64)) + wrong
                return 123000
            with patch.object(bench.ctypes, "CDLL", return_value=SimpleNamespace(matmul_cpu_ns=cpu)):
                if wrong:
                    with self.assertRaisesRegex(AssertionError, "CPU output mismatch"):
                        bench.run_cpu("fake", values, warmup=2)
                else:
                    result = bench.run_cpu("fake", values, warmup=2)
                    self.assertEqual(result["measured_cases"], 4)
                    self.assertEqual(result["checks"], 6144)
                    self.assertEqual(result["statistics_us"]["cpu_compute_us"]["median"], 123)

    @unittest.skipUnless(np is not None, "NumPy test dependency")
    def test_fpga_output_lifecycle_and_retry_guard(self):
        values, _ = bench.dataset(42, 4)
        for scenario in ("pass", "cached_profile", "view_profile", "cache_mismatch", "wrong", "timeout", "done_busy", "idle_interrupt",
                         "view_wrong", "view_timeout", "view_done_busy", "view_idle_interrupt", "view_copy"):
            allocated, memory = [], {}
            mode = scenario[5:] if scenario.startswith("view_") else scenario

            class Buffer(np.ndarray):
                def flush(self):
                    self.flushed = True

                def invalidate(self):
                    self.invalidated = True

                def freebuffer(self):
                    self.freed = True

            def allocate(shape, dtype, cacheable=0):
                buffer = np.empty(shape, dtype=dtype).view(Buffer)
                buffer.physical_address = 0x100000 + 4096 * len(allocated)
                buffer.flushed = buffer.invalidated = buffer.freed = False
                buffer.cacheable = bool(cacheable) if scenario != "cache_mismatch" else not cacheable
                allocated.append(buffer)
                memory[buffer.physical_address] = buffer
                return buffer

            class IP:
                status = 4

                def __init__(self):
                    self.registers = {}

                def read(self, offset):
                    if mode == "idle_interrupt":
                        raise KeyboardInterrupt()
                    value = self.status
                    self.status = 0 if mode == "done_busy" and value == 6 else 4
                    return value

                def write(self, offset, value):
                    self.registers[offset] = value
                    if offset == 0:
                        if mode == "timeout":
                            raise TimeoutError()
                        a, b, c = [memory[self.registers[port]] for port in (16, 24, 32)]
                        assert all(buffer.flushed for buffer in (a, b, c))
                        c[:] = np.dot(a.astype(np.int64), b.astype(np.int64)) + (mode == "wrong")
                        self.status = 6

            overlay = SimpleNamespace(matmul_axi_0=IP(), ip_dict={"matmul_axi_0": {"phys_addr": 0x43C00000}})
            fake = SimpleNamespace(__version__="2.5", Clocks=SimpleNamespace(fclk0_mhz=100),
                                   Overlay=lambda _: overlay, allocate=allocate)
            manifest = {"control_base": 0x43C00000, "clock_mhz": 100, "variant": "fake",
                        "registers": {"a": 16, "b": 24, "c": 32},
                        "sha256": {"autohls_matmul.bit": "not-hardware"}}
            asarray = (lambda array: np.array(array, copy=True)) if mode == "copy" else np.asarray
            with patch.dict("sys.modules", {"pynq": fake}), patch.object(bench.board_test, "inspect_bundle", return_value=manifest), patch.object(np, "asarray", side_effect=asarray):
                try:
                    if scenario in ("pass", "cached_profile", "view_profile"):
                        profiled = scenario != "pass"
                        cached = scenario == "cached_profile"
                        views = scenario == "view_profile"
                        result = bench.run_fpga("fake", values, warmup=2, confirm_board=True,
                                                cacheable=cached, profile=profiled, plain_views=views)
                        self.assertEqual(result["measured_cases"], 4)
                        self.assertEqual(result["checks"], 6144)
                        self.assertTrue(allocated[2].invalidated)
                        self.assertEqual(result["cacheable"], cached)
                        self.assertEqual(result["profiled"], profiled)
                        self.assertEqual(result["plain_views"], views)
                        if profiled:
                            for row in result["measurements"]:
                                stages = [value for key, value in row.items() if key.endswith("_us")
                                          and key not in ("launch_wait_us", "end_to_end_us")]
                                self.assertEqual(len(stages), 12)
                                self.assertAlmostEqual(sum(stages), row["end_to_end_us"])
                    else:
                        error = {"wrong": AssertionError, "timeout": TimeoutError,
                                 "done_busy": RuntimeError, "idle_interrupt": KeyboardInterrupt,
                                 "cache_mismatch": RuntimeError, "copy": RuntimeError}[mode]
                        with self.assertRaises(error):
                            bench.run_fpga("fake", values, warmup=2, confirm_board=True,
                                           plain_views=scenario.startswith("view_"))
                    retained = mode in ("timeout", "done_busy", "idle_interrupt")
                    self.assertEqual(bool(bench._retained_buffers), retained)
                    self.assertTrue(all(buffer.freed != retained for buffer in allocated))
                    if retained:
                        with self.assertRaisesRegex(RuntimeError, "Previous operation"):
                            bench.run_fpga("fake", values, confirm_board=True)
                finally:
                    # Test-only heap arrays, never physical buffers.
                    bench._retained_buffers.clear()
