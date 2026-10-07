import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from scripts.trace_pynq_load import trace_load


class LoadTraceTests(unittest.TestCase):
    def test_requires_exact_confirmation_and_does_not_load(self):
        overlay = MagicMock()
        for confirmation in (False, 1, "yes"):
            with self.assertRaisesRegex(RuntimeError, "confirmation"):
                trace_load(overlay, "unused", confirmation)
        overlay.download.assert_not_called()

    def test_existing_evidence_blocks_load(self):
        overlay = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text("original", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                trace_load(overlay, path, True)
            self.assertEqual(path.read_text(), "original")
        overlay.download.assert_not_called()

    def test_one_load_and_progress_capture(self):
        namespace = {"__name__": "pynq.fake", "calls": []}
        exec("def download():\n    calls.append('loaded')\n", namespace)
        with tempfile.TemporaryDirectory() as directory, \
                patch("scripts.trace_pynq_load.time.sleep"), \
                patch("sys.stdout", new_callable=io.StringIO):
            path = Path(directory) / "trace.jsonl"
            trace_load(SimpleNamespace(download=namespace["download"]), path, True)
            records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(namespace["calls"], ["loaded"])
        self.assertEqual(records[0]["event"], "load_begin")
        self.assertEqual(records[-1]["event"], "load_complete")
        self.assertTrue(any(r.get("function") == "pynq.fake.download" for r in records))
        self.assertIsNone(sys.gettrace())

    def test_failure_is_not_retried_and_trace_is_removed(self):
        overlay = MagicMock()
        overlay.download.side_effect = RuntimeError("simulated failure")
        with tempfile.TemporaryDirectory() as directory, \
                patch("scripts.trace_pynq_load.time.sleep"), \
                patch("sys.stdout", new_callable=io.StringIO):
            path = Path(directory) / "trace.jsonl"
            with self.assertRaisesRegex(RuntimeError, "simulated failure"):
                trace_load(overlay, path, True)
            records = [json.loads(line) for line in path.read_text().splitlines()]
        overlay.download.assert_called_once_with()
        self.assertEqual(records[-1]["event"], "load_failed")
        self.assertIsNone(sys.gettrace())


if __name__ == "__main__":
    unittest.main()
