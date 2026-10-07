import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autohls.experiments import write_json


class EvidenceWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "summary.json"
        self.path.write_text('{"previous": true}', encoding="utf-8")
        self.before = self.path.read_bytes()

    def test_success_keeps_existing_json_format_and_removes_pending(self):
        value = {"label": "真实测试", "count": 3}
        write_json(self.path, value)
        self.assertEqual(self.path.read_text(encoding="utf-8"), json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        self.assertEqual(list(self.root.glob("*.pending-*")), [])

    def test_invalid_json_leaves_previous_snapshot_without_staging(self):
        with self.assertRaises(ValueError):
            write_json(self.path, {"metric": float("nan")})
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(list(self.root.glob("*.pending-*")), [])

    def test_transient_permission_errors_only_repeat_replace(self):
        replace = os.replace
        calls = []
        def locked_then_ready(source, destination):
            calls.append(source)
            if len(calls) <= 2:
                self.assertEqual(self.path.read_bytes(), self.before)
                raise PermissionError("fixture sharing violation")
            return replace(source, destination)
        with patch("autohls.experiments.os.replace", side_effect=locked_then_ready), patch("autohls.experiments.time.sleep") as sleep:
            write_json(self.path, {"complete": True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(set(calls)), 1)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(json.loads(self.path.read_text()), {"complete": True})

    def test_persistent_lock_preserves_old_and_complete_pending(self):
        with patch("autohls.experiments.os.replace", side_effect=PermissionError("fixture")) as replace, patch("autohls.experiments.time.sleep") as sleep:
            with self.assertRaisesRegex(PermissionError, "complete JSON retained"):
                write_json(self.path, {"complete": True})
        self.assertEqual(replace.call_count, 5)
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual(self.path.read_bytes(), self.before)
        pending = list(self.root.glob("*.pending-*"))
        self.assertEqual(len(pending), 1)
        self.assertEqual(json.loads(pending[0].read_text()), {"complete": True})

    def test_other_io_error_is_not_retried(self):
        with patch("autohls.experiments.os.replace", side_effect=OSError("fixture disk error")) as replace, patch("autohls.experiments.time.sleep") as sleep:
            with self.assertRaisesRegex(OSError, "disk error"):
                write_json(self.path, {"complete": True})
        replace.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.before)

    @unittest.skipUnless(os.name == "nt", "Windows file sharing semantics")
    def test_actual_windows_lock_releases_before_retry(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(self.path), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        def release(delay):
            nonlocal handle
            self.assertEqual(self.path.read_bytes(), self.before)
            self.assertTrue(kernel.CloseHandle(handle))
            handle = None
        try:
            with patch("autohls.experiments.time.sleep", side_effect=release) as sleep:
                write_json(self.path, {"complete": True})
            sleep.assert_called_once_with(0.1)
        finally:
            if handle is not None:
                kernel.CloseHandle(handle)
        self.assertEqual(json.loads(self.path.read_text()), {"complete": True})
