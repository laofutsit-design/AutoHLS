"""Offline unit fixtures for the one-attempt board runner; no FPGA access."""
import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hardware import delivery_acceptance as acceptance


class DeliveryAcceptanceTests(unittest.TestCase):
    def test_import_is_py36_and_never_loads_overlay(self):
        source = Path(acceptance.__file__).read_text()
        ast.parse(source, feature_version=(3, 6))
        self.assertNotIn('Overlay(', source)
        self.assertNotIn('.download(', source)
        for flag in (False, 1, 'yes', None):
            with self.assertRaisesRegex(RuntimeError, 'explicitly confirmed'):
                acceptance.run_step('missing', None, 0, confirm_board=flag)

    def test_wrong_receipt_rejected_before_pynq(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'delivery.json').write_text('{}')
            with patch.object(acceptance.batch_contract, 'inspect_bundle') as inspect:
                with self.assertRaisesRegex(ValueError, 'receipt changed'):
                    acceptance.binding(root, 'wrong')
            inspect.assert_not_called()

    def test_no_overwrite_and_no_retry_after_success_or_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(acceptance, 'binding', return_value={'bit_sha256': 'fixture'}):
            output = Path(directory) / 'acceptance'
            plan = acceptance.start('fixture', 'fixture-receipt', output)
            self.assertEqual(len(plan['steps']), 14)
            with self.assertRaises(FileExistsError):
                acceptance.start('fixture', 'fixture-receipt', output)
            with patch.object(acceptance.driver, 'run_fpga', return_value={'bit_sha256': 'fixture'}) as run:
                acceptance.run_step(output, None, 0, True)
                with self.assertRaises(FileExistsError):
                    acceptance.run_step(output, None, 0, True)
                self.assertEqual(run.call_count, 1)
            with patch.object(acceptance.driver, 'run_fpga', side_effect=TimeoutError('fixture timeout')) as run:
                with self.assertRaises(TimeoutError):
                    acceptance.run_step(output, None, 1, True)
                with self.assertRaises(FileExistsError):
                    acceptance.run_step(output, None, 1, True)
                self.assertEqual(run.call_count, 1)
                self.assertFalse(acceptance.read(output / 'failure-01.json')['retry_allowed'])
                with self.assertRaises(FileNotFoundError):
                    acceptance.run_step(output, None, 2, True)

    def test_changed_protocol_blocks_before_driver(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(acceptance, 'binding', return_value={'bit_sha256': 'fixture'}):
            output = Path(directory) / 'acceptance'
            plan = acceptance.start('fixture', 'fixture-receipt', output)
            plan['steps'][0]['batch_size'] = 65
            (output / 'protocol.json').write_text(json.dumps(plan))
            with patch.object(acceptance.driver, 'run_fpga') as run:
                with self.assertRaisesRegex(ValueError, 'steps changed'):
                    acceptance.run_step(output, None, 0, True)
            run.assert_not_called()

    def test_offline_audit_requires_raw_samples_and_exact_firmware(self):
        # The fixture driver returns artificial timings ONLY inside this temp dir.
        def fixture_run(bundle, overlay, values, batch_size, **options):
            rows = [{'batch': i, 'launch_wait_us': 1.0, 'end_to_end_us': 8.0,
                     'per_matrix_us': 8.0 / batch_size} for i in range(2)]
            return acceptance.driver.software._result(rows, batch_size, 0,
                participant='hardware_cached' if options['cacheable'] else 'hardware_uncached',
                cacheable=options['cacheable'], guard=True, clock_mhz=100, launches=2,
                guard_checks=6144, input_checks=2 * batch_size * 2048,
                bit_sha256='fixture-bit', firmware_sha256='fixture-firmware')
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(acceptance, 'binding', return_value={'bit_sha256': 'fixture-bit'}), \
             patch.object(acceptance.driver, 'run_fpga', side_effect=fixture_run):
            output = Path(directory) / 'acceptance'
            acceptance.start('fixture', 'fixture-receipt', output)
            for index in range(14):
                acceptance.run_step(output, None, index, True)
            result = acceptance.audit(output, 'fixture', 'fixture-receipt')
            self.assertEqual(result['output_checks'], 626688)
            self.assertEqual(result['guard_checks'], 86016)
            self.assertEqual(result['input_checks'], 1253376)
            self.assertIsNone(result['cpu_speedup'])
            path = output / 'result-13.json'
            value = acceptance.read(path)
            value['result']['bit_sha256'] = 'other'
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'bit_sha256'):
                acceptance.audit(output, 'fixture', 'fixture-receipt')
            value['result']['bit_sha256'] = 'fixture-bit'
            value['result']['statistics_us']['end_to_end_us']['median'] = 1
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'Statistics'):
                acceptance.audit(output, 'fixture', 'fixture-receipt')
