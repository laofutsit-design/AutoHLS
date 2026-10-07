import ast
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hardware import board_hardware_batch as driver, batch_contract

try:
    import numpy as np
except ImportError:
    np = None

ROOT = Path(__file__).resolve().parents[1]


class HardwareBatchDriverTests(unittest.TestCase):
    def test_offline_and_contract(self):
        for module in (driver, batch_contract):
            ast.parse(Path(module.__file__).read_text(), feature_version=(3, 6))
        for flag in (False, None, 1, 'yes'):
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                driver.run_fpga('missing', None, [], 4, confirm_board=flag)
        for count in (True, 0, 65, 1.0):
            with self.assertRaises(ValueError):
                driver.run_fpga('missing', None, [], count, confirm_board=True)
        with self.assertRaises(ValueError):
            driver.run_fpga('missing', None, [], 4, confirm_board=True, guard=1)
        bundle = ROOT / 'artifacts/hardware-batch-20260926/bundle'
        if not bundle.exists():
            self.skipTest('Frozen hardware package not available')
        self.assertEqual(batch_contract.inspect_bundle(bundle)['batch_count_max'], 64)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'bundle'
            shutil.copytree(bundle, destination)
            manifest = json.loads((destination / 'manifest.json').read_text())
            for key, value in (('schema_version', 1), ('batch_count_max', 65), ('clock_mhz', 50)):
                wrong = dict(manifest, **{key: value})
                (destination / 'manifest.json').write_text(json.dumps(wrong))
                with self.assertRaises(ValueError):
                    batch_contract.inspect_bundle(destination)
            (destination / 'manifest.json').write_text(json.dumps(manifest))
            (destination / 'autohls_matmul.bit').write_bytes(b'wrong')
            with self.assertRaisesRegex(ValueError, 'Checksum'):
                batch_contract.inspect_bundle(destination)

    @unittest.skipUnless(np is not None, 'NumPy dependency')
    def test_same_name_stale_firmware_is_rejected(self):
        payload = bytes(range(16))
        fake_parser = SimpleNamespace(parse_bit_header=lambda _: {'data': payload})
        with tempfile.TemporaryDirectory() as directory:
            firmware = Path(directory) / 'autohls_matmul.bin'
            overlay = SimpleNamespace(bitfile_name='new/autohls_matmul.bit', firmware_path=str(firmware))
            with patch.dict('sys.modules', {'pynq.pl_server.device': fake_parser}):
                firmware.write_bytes(np.frombuffer(payload, 'i4').byteswap().tobytes())
                self.assertEqual(len(driver.verify_firmware(overlay)), 64)
                # A different overlay of the same basename overwrites the shared file.
                firmware.write_bytes(b'old bitstream')
                with self.assertRaisesRegex(RuntimeError, 'Staged firmware mismatch'):
                    driver.verify_firmware(overlay)

    @unittest.skipUnless(np is not None, 'NumPy dependency')
    def test_single_launch_guard_and_fault_lifetime(self):
        values, _ = driver.software.bench.dataset(42, 8)
        for scenario in ('pass', 'cached', 'guard', 'wrong_last', 'tail_write', 'input_write',
                         'timeout_second', 'idle_interrupt', 'busy', 'done_busy', 'bad_cache',
                         'bad_view', 'bad_ddr', 'stale_overlay', 'bad_clock', 'wrong_firmware'):
            with self.subTest(scenario=scenario):
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
                    buffer.physical_address = 0x100000 + len(allocated) * 0x80000
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
                        assert self.registers[40] == 4
                        for owner, port in zip(allocated, (16, 24, 32)):
                            assert self.registers[port] == owner.physical_address
                            assert owner.flushes == self.starts
                        arrays = [b.view(np.ndarray) for b in allocated]
                        for i in range(4):
                            arrays[2][i] = np.dot(arrays[0][i].astype(np.int64), arrays[1][i].astype(np.int64))
                        if scenario == 'wrong_last':
                            arrays[2][3, -1, -1] += 1
                        if scenario == 'tail_write':
                            arrays[2][4, 0, 0] = 1
                        if scenario == 'input_write':
                            arrays[0][0, 0, 0] += 1
                        self.status = 6

                ip = IP()
                bit = str(Path('fake/autohls_matmul.bit').resolve())
                overlay = SimpleNamespace(matmul_axi_0=ip, bitfile_name=bit, timestamp='loaded',
                                          is_loaded=lambda: scenario != 'stale_overlay',
                                          ip_dict={'matmul_axi_0': {'phys_addr': 0x43c00000}})
                fake = SimpleNamespace(__version__='2.5', allocate=allocate, PL=SimpleNamespace(bitfile_name=bit),
                                       Clocks=SimpleNamespace(fclk0_mhz=50 if scenario == 'bad_clock' else 100))
                manifest = {'control_base': 0x43c00000, 'sha256': {'autohls_matmul.bit': 'fake'}}
                asarray = (lambda a: np.array(a, copy=True)) if scenario == 'bad_view' else np.asarray
                guard = scenario in ('guard', 'tail_write', 'input_write')
                with patch.dict('sys.modules', {'pynq': fake}), \
                        patch.object(driver, 'inspect_bundle', return_value=manifest), \
                        patch.object(driver, 'verify_firmware', side_effect=RuntimeError('Staged firmware mismatch')
                                     if scenario == 'wrong_firmware' else None, return_value='verified'), \
                        patch.object(np, 'asarray', side_effect=asarray):
                    try:
                        if scenario in ('pass', 'cached', 'guard'):
                            result = driver.run_fpga('fake', overlay, values, 4, warmup_matrices=4,
                                                     confirm_board=True, cacheable=scenario == 'cached', guard=guard)
                            self.assertEqual(ip.starts, 3)
                            self.assertEqual(result['launches'], 3)
                            self.assertEqual(result['checks'], 12 * 1024)
                            self.assertEqual(result['guard_checks'], 3 * 3 * 1024 if guard else 0)
                            self.assertEqual(allocated[2].invalidates, 3)
                        else:
                            error = {'wrong_last': AssertionError, 'tail_write': AssertionError,
                                     'input_write': AssertionError, 'timeout_second': TimeoutError,
                                     'idle_interrupt': KeyboardInterrupt, 'bad_ddr': ValueError}.get(scenario, RuntimeError)
                            with self.assertRaises(error):
                                driver.run_fpga('fake', overlay, values, 4, warmup_matrices=4,
                                                confirm_board=True, guard=guard)
                        retained = scenario in ('timeout_second', 'idle_interrupt', 'busy', 'done_busy')
                        self.assertEqual(bool(driver._retained_buffers), retained)
                        self.assertTrue(all(b.freed != retained for b in allocated))
                        if scenario == 'wrong_firmware':
                            self.assertFalse(allocated)
                            self.assertEqual(ip.starts, 0)
                        if retained:
                            with self.assertRaisesRegex(RuntimeError, 'Previous operation'):
                                driver.run_fpga('fake', overlay, values, 4, confirm_board=True)
                    finally:
                        driver._retained_buffers.clear()  # Heap test doubles only; never real CMA.
