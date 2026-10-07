import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hardware.delivery_view import status


class DeliveryViewTests(unittest.TestCase):
    def test_missing_and_bad_version_do_not_select_other_release(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self.assertEqual(status(base), {"available": False})
            (base / "active.json").write_text('{"version":"../other"}')
            with self.assertRaisesRegex(ValueError, "Invalid delivery"):
                status(base)
            (base / "active.json").write_text('{"version":"v2"}')
            self.assertEqual(status(base), {"available": False})

    def test_snapshot_is_not_full_verified_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "active.json").write_text('{"version":"v2"}')
            run = base / "v2"
            (run / "release/inputs").mkdir(parents=True)
            (run / "release/inputs/kernel.cpp").write_text("fixture")
            (run / "launch.json").write_text('{}')
            (run / "remote-observation.json").write_text('{"phase":"complete","observed_utc":"fixture"}')
            provenance = {"job": "fixture", "candidate": "fixture", "source_sha256": "fixture",
                          "search_metrics": {}, "contract": "fixture", "created_utc": "fixture"}
            with patch('hardware.delivery_view.verify_input', return_value=provenance), \
                 patch('hardware.delivery_view.verify_delivery', return_value={"board_verified": False}) as verify:
                result = status(base)
                self.assertEqual(result["state"], "remote_snapshot_available")
                self.assertFalse(result["board_verified"])
                self.assertIsNone(result["receipt"])
                verify.assert_not_called()
                (run / "exported").mkdir()
                self.assertEqual(status(base)["state"], "awaiting_board_confirmation")
                verify.assert_called_once_with(run / "exported")

    def test_corrupt_source_propagates_instead_of_claiming_success(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "active.json").write_text('{"version":"v2"}')
            (base / "v2").mkdir()
            (base / "v2/launch.json").write_text('{}')
            with patch('hardware.delivery_view.verify_input', side_effect=ValueError("Checksum mismatch")):
                with self.assertRaisesRegex(ValueError, "Checksum"):
                    status(base)

    def test_board_requires_independent_audit_bound_to_offline_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'active.json').write_text('{"version":"v2"}')
            (base / 'v2/release/inputs').mkdir(parents=True)
            (base / 'v2/release/inputs/kernel.cpp').write_text('fixture')
            (base / 'v2/launch.json').write_text('{}')
            (base / 'v2/exported').mkdir()
            (base / 'board-v2/results').mkdir(parents=True)
            provenance = dict(job='fixture', candidate='fixture', source_sha256='fixture',
                              search_metrics={}, contract='fixture', created_utc='fixture')
            with patch('hardware.delivery_view.verify_input', return_value=provenance), \
                 patch('hardware.delivery_view.verify_delivery', return_value={'board_verified': False}), \
                 patch('hardware.delivery_view.file_hash', return_value='receipt-fixture'), \
                 patch('hardware.delivery_view.audit_directory', return_value={'complete': True}) as audit:
                result = status(base)
                audit.assert_called_once_with(base / 'board-v2', 'receipt-fixture')
                self.assertTrue(result['board_verified'])
                self.assertEqual(result['state'], 'board_functional_verified')
                self.assertFalse(result['receipt']['board_verified'])
                audit.side_effect = ValueError('fixture corrupted')
                with self.assertRaisesRegex(ValueError, 'corrupted'):
                    status(base)
