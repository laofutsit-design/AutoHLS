import io
import base64
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from scripts import board_jupyter


class BoardJupyterTests(unittest.TestCase):
    def test_transient_report_lock_does_not_truncate_or_replay(self):
        real_replace = board_jupyter.os.replace
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text('{"old": true}')
            attempts = []

            def replace(source, target):
                attempts.append(source)
                self.assertEqual(json.loads(path.read_text()), {"old": True})
                if len(attempts) == 1:
                    raise PermissionError("simulated sharing conflict")
                return real_replace(source, target)

            with patch.object(board_jupyter.os, "replace", side_effect=replace), \
                    patch.object(board_jupyter.time, "sleep") as sleep:
                board_jupyter.save_report(path, {"new": True})
                sleep.assert_called_once_with(0.1)
            self.assertEqual(json.loads(path.read_text()), {"new": True})
            with patch.object(board_jupyter.os, "replace", side_effect=PermissionError()), \
                    patch.object(board_jupyter.time, "sleep"):
                with self.assertRaises(PermissionError):
                    board_jupyter.save_report(path, {"retained": True})
            self.assertEqual(json.loads(path.read_text()), {"new": True})
            pending = list(Path(directory).glob("*.pending-*"))
            self.assertEqual(len(pending), 1)
            self.assertEqual(json.loads(pending[0].read_text()), {"retained": True})

    def invoke(self, action, session, directory, extra=(), websocket=None):
        output = Path(directory) / "report.json"
        modules = {"requests": SimpleNamespace(Session=lambda: session)}
        if websocket is not None:
            modules["websocket"] = websocket
        args = ["board_jupyter", action, "--base-url", "http://localhost:9090", "--output", str(output)] + list(extra)
        with patch.dict("sys.modules", modules), patch.object(board_jupyter.sys, "argv", args), \
                patch.object(board_jupyter.sys, "stdin", io.StringIO("test-password\n")), \
                patch("sys.stdout", new_callable=io.StringIO):
            board_jupyter.main()
        return json.loads(output.read_text(encoding="utf-8"))

    def session(self, responses):
        session = MagicMock()
        session.cookies.get.return_value = "test-xsrf"
        session.cookies.items.return_value = [("_xsrf", "test-xsrf")]
        queue = []
        for data in responses:
            response = MagicMock()
            response.headers = {"Content-Type": "application/json"}
            response.json.return_value = data
            queue.append(response)
        session.request.side_effect = queue
        return session

    def test_status_is_read_only_after_login_and_does_not_save_password(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.session([[], []])
            result = self.invoke("status", session, directory)
            self.assertEqual(result["kernels"], [])
            self.assertNotIn("test-password", json.dumps(result))
            self.assertEqual([call.args[0] for call in session.request.call_args_list], ["GET", "GET"])
            self.assertFalse(session.trust_env)

    def test_explicit_origin_rejects_credentials_paths_and_non_http_before_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            for origin in ("https://localhost:9090", "http://user:fixture@localhost:9090",
                           "http://localhost:9090/tree", "http://localhost:9090?token=fixture"):
                session = self.session([])
                with self.assertRaises(SystemExit):
                    self.invoke("status", session, directory, ["--base-url", origin])
                session.get.assert_not_called()
                session.post.assert_not_called()

    def test_upload_accepts_old_api_without_size(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "package.zip"
            source.write_bytes(b"test")
            session = self.session([[], {"path": "new.zip", "type": "file"}])
            session.get.return_value.status_code = 404
            result = self.invoke("upload", session, directory, ["--file", str(source), "--remote", "new.zip"])
            self.assertIsNone(result["uploaded"]["size"])

    def test_upload_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.session([[]])
            session.get.return_value.status_code = 200
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                self.invoke("upload", session, directory, ["--file", "unused", "--remote", "existing.zip"])
            self.assertEqual(session.request.call_count, 1)

    def test_download_checks_hash_and_never_overwrites(self):
        payload = b'unit fixture only'
        response = {'type': 'file', 'format': 'base64', 'content': base64.b64encode(payload).decode()}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'evidence.zip'
            args = ['--file', str(target), '--remote', 'task/evidence.zip',
                    '--sha256', hashlib.sha256(payload).hexdigest()]
            session = self.session([[], response])
            result = self.invoke('download', session, directory, args)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(result['downloaded']['bytes'], len(payload))
            self.assertEqual([call.args[0] for call in session.request.call_args_list], ['GET', 'GET'])
            with self.assertRaises(FileExistsError):
                self.invoke('download', self.session([[]]), directory, args)

    def test_bad_download_is_not_written(self):
        for response in ({'type': 'directory', 'format': 'json'},
                         {'type': 'file', 'format': 'base64', 'content': 'd3Jvbmc='}):
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'evidence.zip'
                with self.assertRaises((ValueError, RuntimeError)):
                    self.invoke('download', self.session([[], response]), directory,
                                ['--file', str(target), '--remote', 'task/evidence.zip', '--sha256', 'expected'])
                self.assertFalse(target.exists())

    def test_timeout_retains_kernel_and_never_sends_interrupt_or_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "check.py"
            source.write_text("print('test')", encoding="utf-8")
            session = self.session([[{"id": "task-kernel"}]])
            socket = MagicMock()
            websocket = SimpleNamespace(create_connection=MagicMock(return_value=socket),
                                        WebSocketTimeoutException=TimeoutError)
            with patch.object(board_jupyter.time, "monotonic", side_effect=[0, 46]):
                with self.assertRaisesRegex(TimeoutError, "kernel retained"):
                    self.invoke("run", session, directory,
                                ["--kernel", "task-kernel", "--code", str(source)], websocket)
            report = json.loads((Path(directory) / "report.json").read_text())
            self.assertEqual(report["kernel_id"], "task-kernel")
            self.assertFalse(report["execution_confirmed"])
            socket.close.assert_called_once()
            self.assertEqual(websocket.create_connection.call_args.kwargs["http_no_proxy"], ["localhost"])
            self.assertEqual([call.args[0] for call in session.request.call_args_list], ["GET"])


if __name__ == "__main__":
    unittest.main()
