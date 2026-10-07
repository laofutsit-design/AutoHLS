"""Offline report rejection tests; modified copies never enter real evidence."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from autohls.experiments import file_hash
from scripts.audit_delivery import reports
from scripts.prepare_delivery_board import prepare


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'artifacts/delivery-upgrade-20260927/v2/exported/artifacts/delivery/bundle'


@unittest.skipUnless(BUNDLE.is_dir(), 'Local archived delivery reports are not included in source releases')
class DeliveryReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.bundle = Path(temporary.name)
        for name in ('manifest.json', 'native-check.log', 'hls.log', 'matmul_axi_cosim.rpt',
                     'matmul_axi_csynth.xml', 'timing_summary.rpt', 'check_timing.rpt',
                     'vivado-build.log', 'drc.rpt'):
            shutil.copyfile(BUNDLE / name, self.bundle / name)

    def test_real_archived_reports_reparse(self):
        result = reports(self.bundle)
        self.assertEqual(result['native_checks'], 720896)
        self.assertEqual(result['setup_slack_ns'], 2.579)
        self.assertEqual(sum(row['count'] for row in result['drc_warnings']), 21)

    def test_failures_not_hidden_by_a_pass_line(self):
        for name, extra in (('native-check.log', '\nAddressSanitizer: fixture\n'),
                            ('hls.log', '\nERROR: synthetic unreviewed failure\n'),
                            ('drc.rpt', '\n| Error | fixture |\n')):
            path = self.bundle / name
            original = path.read_text()
            path.write_text(original + extra)
            with self.subTest(name=name), self.assertRaises(ValueError):
                reports(self.bundle)
            path.write_text(original)

    def test_summary_metric_and_warning_tampering_rejected(self):
        path = self.bundle / 'manifest.json'
        original = path.read_text()
        for key, value in (('setup_slack_ns', 3), ('hold_slack_ns', -1),
                           ('drc_warnings', []), ('hls_wrapper_metrics', {})):
            manifest = json.loads(original)
            manifest[key] = value
            path.write_text(json.dumps(manifest))
            with self.subTest(key=key), self.assertRaises(ValueError):
                reports(self.bundle)
        path.write_text(original)

    def test_missing_cosim_and_timing_evidence_rejected(self):
        for name in ('matmul_axi_cosim.rpt', 'timing_summary.rpt', 'check_timing.rpt'):
            path = self.bundle / name
            original = path.read_text()
            path.write_text('synthetic missing report')
            with self.subTest(name=name), self.assertRaises(ValueError):
                reports(self.bundle)
            path.write_text(original)


class DeliveryBoardPackageTests(unittest.TestCase):
    def test_rejection_precedes_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'package'
            with patch('scripts.prepare_delivery_board.verify_delivery', side_effect=ValueError('fixture rejected')):
                with self.assertRaisesRegex(ValueError, 'fixture rejected'):
                    prepare(Path(directory), output)
            self.assertFalse(output.exists())

    def test_exact_frozen_driver_hashes_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            delivery, output = root / 'delivery', root / 'package'
            bundle = delivery / 'artifacts/delivery/bundle'
            bundle.mkdir(parents=True)
            (bundle / 'delivery.json').write_text('{"fixture": true}')
            sources = ['hardware/' + name for name in
                       ('board_hardware_batch.py', 'board_batch.py', 'board_benchmark.py',
                        'board_test.py', 'batch_contract.py')]
            sources.append('scripts/trace_pynq_load.py')
            for name in sources:
                path = delivery / name
                path.parent.mkdir(exist_ok=True)
                path.write_text('# synthetic frozen source: ' + name)
            with patch('scripts.prepare_delivery_board.verify_delivery', return_value={'bit_sha256': 'fixture'}):
                result = prepare(delivery, output)
                with self.assertRaises(FileExistsError):
                    prepare(delivery, output)
            self.assertFalse(result['board_verified'])
            self.assertEqual(file_hash(output / result['archive']), result['sha256'])
            runtime = output / 'runtime'
            manifest = json.loads((runtime / 'runtime-manifest.json').read_text())
            with zipfile.ZipFile(output / result['archive']) as archive:
                self.assertEqual(set(archive.namelist()), set(manifest['files']) | {'runtime-manifest.json'})
                for name, checksum in manifest['files'].items():
                    self.assertEqual(file_hash(runtime / name), checksum)
                    self.assertEqual(archive.read(name), (runtime / name).read_bytes())
            for name in sources:
                self.assertEqual(file_hash(runtime / name), file_hash(delivery / name))


if __name__ == '__main__':
    unittest.main()
