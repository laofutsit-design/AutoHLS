"""Tampering fixtures live only in temporary directories, never in real evidence."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from autohls.experiments import file_hash
from scripts.audit_delivery_board import audit, extract


BOARD = Path(__file__).resolve().parents[1] / 'artifacts/delivery-upgrade-20260927/board-v2'
RECEIPT = '49726a34979db9e8a51a54a986f6156938fab49f488a0d8ad06ff2c2c7691711'
ARCHIVE = '2199473c65aca2e37853436c00d458cd02b2b984756aef76a78a94bb317deab8'
RUNTIME = 'd009af87b49e1d81b653ee2dc1b45818abf64ed77d611d6078f9bc6eb6f83b76'


class BoardExtractionTests(unittest.TestCase):
    def package(self, path, name):
        payload = b'synthetic extraction fixture'
        with zipfile.ZipFile(path, 'w') as package:
            package.writestr(name, payload)
            package.writestr('board-evidence-checksums.json', json.dumps({name: hashlib.sha256(payload).hexdigest()}))

    def test_safe_extract_is_exclusive_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, target = root / 'fixture.zip', root / 'out'
            self.package(package, 'folder/result.txt')
            with self.assertRaisesRegex(ValueError, 'archive checksum'):
                extract(package, target, 'wrong')
            self.assertFalse(target.exists())
            self.assertEqual(extract(package, target, file_hash(package)), 2)
            self.assertEqual((target / 'folder/result.txt').read_bytes(), b'synthetic extraction fixture')
            with self.assertRaises(FileExistsError):
                extract(package, target, file_hash(package))

    def test_unsafe_member_is_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, name in enumerate(('../escape', '/absolute', 'C:/absolute', 'folder\\file', 'a//b')):
                package, target = root / ('fixture-%d.zip' % index), root / ('out-%d' % index)
                self.package(package, name)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    extract(package, target, file_hash(package))
                self.assertFalse(target.exists())


@unittest.skipUnless((BOARD / 'results').is_dir(), 'Local real board archive not in source releases')
class BoardAuditTests(unittest.TestCase):
    def test_real_archive_recomputed_without_hardware(self):
        result = audit(BOARD / 'results', BOARD / 'delivery-board-evidence.zip', ARCHIVE, RECEIPT, RUNTIME)
        self.assertEqual(result['verified_files'], 66)
        self.assertEqual(result['output_checks'] + result['input_checks'] + result['guard_checks'], 1966080)
        self.assertIsNone(result['cpu_speedup'])
        self.assertTrue(result['final_idle'])

    def test_receipt_and_runtime_cannot_be_substituted(self):
        for receipt, runtime in (('wrong', RUNTIME), (RECEIPT, 'wrong')):
            with self.subTest(receipt=receipt), self.assertRaises(ValueError):
                audit(BOARD / 'results', BOARD / 'delivery-board-evidence.zip', ARCHIVE, receipt, runtime)

    def test_mutated_copy_is_rejected_against_original_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'results'
            shutil.copytree(BOARD / 'results', root)
            (root / 'acceptance/summary.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                audit(root, BOARD / 'delivery-board-evidence.zip', ARCHIVE, RECEIPT, RUNTIME)

    def test_rehashed_invalid_samples_load_and_final_state_are_rejected(self):
        changes = [
            ('summary.json', lambda value: value.update(cpu_speedup=1.5)),
            ('final-state.json', lambda value: value.update(idle=False)),
            ('final-state.json', lambda value: value.update(retained_buffers=1)),
            ('final-state.json', lambda value: value.update(control=129)),
            ('load-proof.json', lambda value: value.update(loaded=False)),
            ('idle-before.json', lambda value: value.update(firmware_sha256='wrong')),
            ('result-13.json', lambda value: value['result']['statistics_us']['per_matrix_us'].update(median=0)),
        ]
        for name, change in changes:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'results'
                shutil.copytree(BOARD / 'results', root)
                target = root / 'acceptance' / name
                value = json.loads(target.read_text())
                change(value)
                target.write_text(json.dumps(value))
                archive = self.repack(root)
                with self.assertRaises(ValueError):
                    audit(root, archive, file_hash(archive), RECEIPT, RUNTIME)

    def test_missing_result_and_incomplete_trace_are_rejected_even_if_rehashed(self):
        for name in ('result-13.json', 'load.jsonl'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'results'
                shutil.copytree(BOARD / 'results', root)
                target = root / 'acceptance' / name
                if name.endswith('.jsonl'):
                    target.write_text('\n'.join(target.read_text().splitlines()[:-1]))
                else:
                    target.unlink()
                archive = self.repack(root)
                with self.assertRaises(ValueError):
                    audit(root, archive, file_hash(archive), RECEIPT, RUNTIME)

    def repack(self, root):
        checksums = {p.relative_to(root).as_posix(): file_hash(p) for p in root.rglob('*')
                     if p.is_file() and p.name != 'board-evidence-checksums.json'}
        (root / 'board-evidence-checksums.json').write_text(json.dumps(checksums))
        archive = root.parent / 'tampered-fixture.zip'
        with zipfile.ZipFile(archive, 'x') as package:
            for path in root.rglob('*'):
                if path.is_file():
                    package.write(path, path.relative_to(root).as_posix())
        return archive


if __name__ == '__main__':
    unittest.main()
