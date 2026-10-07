import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.import_model_suite import extract


class SuiteArchiveTests(unittest.TestCase):
    def archive(self, root, name="evidence.json", checksum=None):
        archive = root / "snapshot.zip"
        data = b'{"fixture":true}'
        with zipfile.ZipFile(archive, "x") as handle:
            handle.writestr(name, data)
            handle.writestr("export-checksums.json", json.dumps({name: checksum or hashlib.sha256(data).hexdigest()}))
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_verified_archive_extracts_once_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, checksum = self.archive(root)
            output = root / "evidence"
            self.assertEqual(extract(archive, output, checksum), 1)
            self.assertEqual(json.loads((output / "evidence.json").read_text()), {"fixture": True})
            with self.assertRaises(FileExistsError):
                extract(archive, output, checksum)

    def test_bad_archive_or_member_checksum_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, checksum = self.archive(root, checksum="bad")
            output = root / "evidence"
            with self.assertRaisesRegex(ValueError, "Archive checksum"):
                extract(archive, output, "bad")
            with self.assertRaisesRegex(ValueError, "Evidence checksum"):
                extract(archive, output, checksum)
            self.assertFalse(output.exists())

    def test_member_cannot_escape_evidence_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, checksum = self.archive(root, name="../outside.json")
            with self.assertRaisesRegex(ValueError, "outside evidence"):
                extract(archive, root / "evidence", checksum)
            self.assertFalse((root / "outside.json").exists())
