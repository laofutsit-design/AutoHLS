import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hardware import board_batch as batch

try:
    import numpy as np
except ImportError:
    np = None


class BatchTests(unittest.TestCase):
    def test_offline_and_python36(self):
        ast.parse(Path(batch.__file__).read_text(), feature_version=(3, 6))
        for value in (False, None, 1, 'yes'):
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                batch.run_fpga('missing', [], 4, confirm_board=value)
        with self.assertRaisesRegex(ValueError, 'cacheable'):
            batch.run_fpga('missing', [], 4, confirm_board=True, cacheable=1)

    @unittest.skipUnless(np is not None, 'NumPy dependency')
    def test_batch_layout_and_contract(self):
        values, _ = batch.bench.dataset(42, 64)
        for size in (1, 4, 16, 64):
            blocks = batch._batches(values, size, 64)
            self.assertEqual(len(blocks), 128 // size)
            self.assertTrue(np.array_equal(np.concatenate([b[0] for b in blocks]),
                                           np.stack([v[0] for v in values + values])))
        for size, warmup, count in [(True, 0, 64), (2, 0, 64), (128, 0, 64),
                                    (4, 1, 64), (4, 0, 3), (4, 68, 64), (4, 0, 0)]:
            with self.assertRaises(ValueError):
                batch._batches(values[:count], size, warmup)

    @unittest.skipUnless(np is not None, 'NumPy dependency')
    def test_cpu_all_matrices_checked_and_error_rejected(self):
        values, _ = batch.bench.dataset(42, 8)
        for scenario in ('pass', 'wrong_last', 'timer_error'):
            calls = []

            def cpu(a, b, c, count):
                calls.append(count)
                arrays = [np.ctypeslib.as_array(p, shape=(count * 1024,)).reshape(count, 32, 32)
                          for p in (a, b, c)]
                for i in range(count):
                    arrays[2][i] = np.dot(arrays[0][i].astype(np.int64), arrays[1][i].astype(np.int64))
                arrays[2][-1, -1, -1] += scenario == 'wrong_last'
                return 2 ** 64 - 1 if scenario == 'timer_error' else 1000000

            with patch.object(batch.ctypes, 'CDLL', return_value=SimpleNamespace(matmul_cpu_batch_ns=cpu)):
                if scenario == 'pass':
                    result = batch.run_cpu('fake', values, 4, warmup_matrices=4)
                    self.assertEqual(result['measured_batches'], 2)
                    self.assertEqual(result['checks'], 12 * 1024)
                    self.assertEqual(calls, [4, 4, 4])
                    for row in result['measurements']:
                        self.assertEqual(row['end_to_end_us'] / 4, row['per_matrix_us'])
                        self.assertEqual(row['compute_us'], 1000)
                else:
                    with self.assertRaises(AssertionError if scenario == 'wrong_last' else RuntimeError):
                        batch.run_cpu('fake', values, 4, warmup_matrices=4)

    @unittest.skipUnless(np is not None, 'NumPy dependency')
    def test_fpga_strides_cache_calls_and_fault_lifetimes(self):
        values, _ = batch.bench.dataset(42, 8)
        for scenario in ('pass', 'cached', 'wrong_last', 'timeout_second', 'idle_interrupt',
                         'busy', 'done_busy', 'bad_cache', 'bad_view', 'bad_ddr'):
            allocated = []

            class Buffer(np.ndarray):
                def flush(self):
                    self.flushes += 1

                def invalidate(self):
                    self.invalidates += 1

                def freebuffer(self):
                    self.freed = True

            def allocate(shape, dtype, cacheable=0):
                buffer = np.empty(shape, dtype=dtype).view(Buffer)
                buffer.physical_address = 0x100000 + len(allocated) * 0x40000
                if scenario == 'bad_ddr':
                    buffer.physical_address = 0x1ffff000
                buffer.cacheable = not cacheable if scenario == 'bad_cache' else bool(cacheable)
                buffer.flushes = buffer.invalidates = 0
                buffer.freed = False
                allocated.append(buffer)
                return buffer

            class IP:
                def __init__(self):
                    self.status, self.starts, self.registers = 4, 0, {}

                def read(self, offset):
                    if scenario == 'idle_interrupt':
                        raise KeyboardInterrupt()
                    if scenario == 'busy':
                        return 0
                    value = self.status
                    if value == 6:
                        self.status = 0 if scenario == 'done_busy' else 4
                    return value

                def write(self, offset, value):
                    self.registers[offset] = value
                    if offset != 0:
                        return
                    self.starts += 1
                    if scenario == 'timeout_second' and self.starts == 2:
                        raise TimeoutError()
                    views = []
                    for owner, port in zip(allocated, (16, 24, 32)):
                        delta = self.registers[port] - owner.physical_address
                        assert delta % 4096 == 0 and 0 <= delta <= owner.nbytes - 4096
                        assert delta // 4096 == (self.starts - 1) % 4
                        assert owner.flushes == (self.starts - 1) // 4 + 1
                        views.append(owner.view(np.ndarray)[delta // 4096])
                    views[2][:] = np.dot(views[0].astype(np.int64), views[1].astype(np.int64))
                    if scenario == 'wrong_last' and self.starts % 4 == 0:
                        views[2][-1, -1] += 1
                    self.status = 6

            ip = IP()
            overlay = SimpleNamespace(matmul_axi_0=ip, ip_dict={'matmul_axi_0': {'phys_addr': 0x43c00000}})
            fake = SimpleNamespace(__version__='2.5', Clocks=SimpleNamespace(fclk0_mhz=100),
                                   Overlay=lambda _: overlay, allocate=allocate)
            manifest = {'control_base': 0x43c00000, 'clock_mhz': 100, 'registers': {'a': 16, 'b': 24, 'c': 32},
                        'sha256': {'autohls_matmul.bit': 'fake'}}
            asarray = (lambda a: np.array(a, copy=True)) if scenario == 'bad_view' else np.asarray
            with patch.dict('sys.modules', {'pynq': fake}), patch.object(batch.bench.board_test, 'inspect_bundle', return_value=manifest), patch.object(np, 'asarray', side_effect=asarray):
                try:
                    if scenario in ('pass', 'cached'):
                        result = batch.run_fpga('fake', values, 4, warmup_matrices=4,
                                                confirm_board=True, cacheable=scenario == 'cached')
                        self.assertEqual(result['checks'], 12288)
                        self.assertEqual(result['measured_matrices'], 8)
                        self.assertEqual(ip.starts, 12)
                        self.assertEqual(allocated[2].invalidates, 3)
                        self.assertEqual([r['batch'] for r in result['measurements']], [0, 1])
                    else:
                        error = {'wrong_last': AssertionError, 'timeout_second': TimeoutError,
                                 'idle_interrupt': KeyboardInterrupt, 'bad_ddr': ValueError}.get(scenario, RuntimeError)
                        with self.assertRaises(error):
                            batch.run_fpga('fake', values, 4, warmup_matrices=4, confirm_board=True)
                    retained = scenario in ('timeout_second', 'idle_interrupt', 'busy', 'done_busy')
                    self.assertEqual(bool(batch._retained_buffers), retained)
                    self.assertTrue(all(b.freed != retained for b in allocated))
                    if retained:
                        with self.assertRaisesRegex(RuntimeError, 'Previous operation'):
                            batch.run_fpga('fake', values, 4, confirm_board=True)
                finally:
                    batch._retained_buffers.clear()  # Only test heap arrays, never real CMA.
