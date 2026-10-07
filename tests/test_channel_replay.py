import hashlib
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from autohls import channel_replay as model
from scripts import channel_replay as evidence


class ReplayTests(unittest.TestCase):
    def test_three_scenarios_independent_numpy_and_bounds(self):
        for name in model.SCENARIOS:
            result = model.replay(name)
            raw = np.array(result['raw'], dtype=np.int64)
            weights = np.array(result['weights_q8'], dtype=np.int64)
            np.testing.assert_array_equal(raw @ weights, result['output_q8'])
            self.assertLessEqual(abs(raw).max(), 1000)
            self.assertLessEqual(abs(weights).max(), 1000)
            self.assertLess(abs(raw @ weights).max(), 2**31)
            blocks = model.board_values(name)
            digest = hashlib.sha256()
            for a, b, ref in blocks:
                digest.update(a.astype('<i4').tobytes()); digest.update(b.astype('<i4').tobytes())
                np.testing.assert_array_equal(a.astype(np.int64) @ b, ref)
            self.assertEqual(digest.hexdigest(), result['input_sha256'])
            self.assertFalse(result['board_verified'])
            self.assertIsNone(result['cpu_speedup'])

    def test_quality_is_distinct_from_correct_arithmetic(self):
        matched, bypass, drift = (model.replay(n) for n in model.SCENARIOS)
        self.assertEqual(matched['corrected'], matched['target'])
        self.assertTrue(matched['quality_target_met'])
        self.assertEqual(bypass['corrected'], bypass['raw'])
        self.assertEqual(bypass['before'], bypass['after'])
        self.assertFalse(bypass['quality_target_met'])
        self.assertFalse(drift['quality_target_met'])
        self.assertEqual(drift['after']['max_abs_counts'], 21)
        self.assertAlmostEqual(drift['after']['rmse_counts'], (585 / 8)**.5)

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(ValueError): model.replay('../matched')
        raw, weights, _ = model.matrices('matched')
        for invalid in ([], raw[:31], [[0]*31]*32, [[True]*32]*32, [[1001]*32]*32):
            with self.assertRaises(ValueError): model.multiply(invalid, weights)
        with self.assertRaises(ValueError): model.multiply(raw, weights[:-1])

    def test_freeze_recompute_no_overwrite_no_board_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'evidence'
            self.assertEqual(evidence.view(root, 'matched'), {'available': False})
            evidence.prepare(root)
            self.assertEqual(evidence.audit(root)['output_values'], 24576)
            self.assertFalse(evidence.view(root, 'matched')['board_verified'])
            with self.assertRaises(ValueError): evidence.prepare(root)
            original = json.loads((root / 'matched.json').read_text())
            original['after']['rmse_counts'] = 123
            (root / 'matched.json').write_text(json.dumps(original))
            with self.assertRaises(ValueError): evidence.audit(root)
            manifest = json.loads((root / 'manifest.json').read_text())
            manifest['files']['matched.json'] = evidence.file_hash(root / 'matched.json')
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'does not recompute'): evidence.audit(root)

    def test_runner_requires_confirmation_before_hardware(self):
        from hardware.channel_replay import run_step
        with self.assertRaisesRegex(ValueError, 'Exclusive'): run_step('missing', None, 0)

    def test_download_record_mismatch_never_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'evidence'; evidence.prepare(root)
            (root / 'board').mkdir()
            (root / 'board/record.json').write_text('{}')
            (root / 'board/download.json').write_text('{"downloaded":{"sha256":"wrong"}}')
            with self.assertRaisesRegex(ValueError, 'changed'): evidence.view(root, 'matched')

    def test_failed_hardware_attempt_cannot_be_retried(self):
        from hardware import channel_replay as runner
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'software'; evidence.prepare(root)
            output = root / 'board'
            with patch.object(runner.acceptance, 'binding'), patch.object(runner, 'state',
                return_value={'firmware_sha256': 'fixture'}), patch.object(runner.acceptance.driver,
                'run_fpga', side_effect=RuntimeError('fixture failure')) as run:
                runner.start(root, output, 'fixture-bundle', 'fixture-receipt', None)
                with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                    runner.run_step(output, None, 0, confirm_board=True)
                with self.assertRaises(FileExistsError): runner.run_step(output, None, 0, confirm_board=True)
                self.assertEqual(run.call_count, 1)
                self.assertTrue((output / 'attempt-0.json').is_file())
                self.assertTrue((output / 'failure-0.json').is_file())
                with self.assertRaises(FileNotFoundError): runner.run_step(output, None, 1, confirm_board=True)

    def test_real_record_audit_and_independent_fault_rejection(self):
        root = evidence.ROOT / 'artifacts/channel-replay-20260927/v1'
        path = root / 'board/record.json'
        if not path.is_file(): self.skipTest('Real board archive not on this host')
        record = evidence.read(path)
        summary = evidence.board_audit(record, root, record['receipt_sha256'])
        self.assertEqual(summary['output_checks'], 24576)
        # Tamper only in-memory copies; never change real records or fill gaps.
        mutations = [
            lambda r: r.update(firmware_sha256='other'),
            lambda r: r.update(engine_sha256='other'),
            lambda r: r.update(runner_sha256='other'),
            lambda r: r.update(cpu_speedup=1.263),
            lambda r: r.update(final_idle=False),
            lambda r: r['scenarios'].pop('drift'),
            lambda r: r['attempts'].pop('bypass'),
            lambda r: r['scenarios']['matched'].update(input_sha256='other'),
            lambda r: r['scenarios']['drift'].update(passed=False),
            lambda r: r['scenarios']['matched']['measurement'].update(checks=8191),
            lambda r: r['scenarios']['matched']['measurement']['statistics_us'].clear(),
            lambda r: r['scenarios']['matched']['measurement']['measurements'][0].update(per_matrix_us=0),
            lambda r: r['final'].update(control=0x85),
            lambda r: r['final'].update(retained_buffers=1),
            lambda r: r['before'].update(clock_mhz=125),
        ]
        for change in mutations:
            altered = copy.deepcopy(record); change(altered)
            with self.assertRaises(ValueError): evidence.board_audit(altered, root, record['receipt_sha256'])
