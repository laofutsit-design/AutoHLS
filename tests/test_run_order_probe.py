import json
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_order_probe import digest
from scripts.run_order_probe import decode_choice, run_probe


class OrderProbeRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = {"format": {"properties": {"id": {"enum": ["a", "b"]}}}}
        raw = json.dumps(self.payload).encode()
        (self.root / "request.json").write_bytes(raw)
        self.plan = {"model_digest": "fixture", "ollama_version": "fixture", "jobs": [
            {"id": str(i), "request": "request.json", "request_sha256": digest(raw)} for i in range(2)]}
        self.identity = lambda: {"model": {"digest": "fixture"}, "ollama_version": "fixture"}

    def response(self, choice=None, **fields):
        return json.dumps({"done": True, "message": {"content": json.dumps(choice or {"id": "b", "reason": "fixture"})}, **fields}).encode()

    def test_valid_response_and_exact_json_fence(self):
        self.assertEqual(decode_choice(self.response(), self.payload)[1]["id"], "b")
        raw = json.dumps({"done": True, "message": {"content": '```json\n{"id":"a","reason":"fixture"}\n```'}}).encode()
        self.assertEqual(decode_choice(raw, self.payload)[1]["id"], "a")

    def test_invalid_id_extra_field_incomplete_and_oversize_are_rejected(self):
        values = [self.response({"id": "unknown", "reason": "fixture"}),
                  self.response({"id": "b", "reason": "fixture", "command": "not allowed"}),
                  self.response(done=False), self.response(done_reason="length"), b"x" * 2_000_001]
        for value in values:
            with self.assertRaises(ValueError):
                decode_choice(value, self.payload)

    def test_failure_is_recorded_once_and_schedule_continues_without_retry(self):
        calls = []
        def call(raw):
            calls.append(raw)
            if len(calls) == 1:
                raise TimeoutError("fixture timeout")
            return self.response()
        output = self.root / "output"
        result = run_probe(self.root, self.plan, output, call, self.identity)
        self.assertTrue(result["complete"])
        self.assertEqual(len(calls), 2)
        self.assertEqual([j["status"] for j in result["jobs"]], ["failed", "valid"])
        self.assertEqual(result["jobs"][1]["candidate_position"], 2)
        self.assertFalse(result["hls_executed"])
        self.assertEqual((output / "1/request.json").read_bytes(), calls[1])
        before = (output / "status.json").read_bytes()
        with self.assertRaises(FileExistsError):
            run_probe(self.root, self.plan, output, call, self.identity)
        self.assertEqual((output / "status.json").read_bytes(), before)
        self.assertEqual(len(calls), 2)

    def test_stop_marker_prevents_the_next_call(self):
        output = self.root / "output"
        def call(raw):
            (output / "STOP").touch()
            return self.response()
        result = run_probe(self.root, self.plan, output, call, self.identity)
        self.assertTrue(result["stopped"])
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["jobs"]), 1)

    def test_invalid_reply_is_preserved_without_retry(self):
        raw = b'{"done": false, "message": {"content": "truncated"}}'
        output = self.root / "invalid"
        result = run_probe(self.root, self.plan, output, lambda request: raw, self.identity)
        self.assertEqual([j["status"] for j in result["jobs"]], ["failed", "failed"])
        for index in range(2):
            self.assertEqual((output / str(index) / "response.raw.log").read_bytes(), raw)
            self.assertFalse((output / str(index) / "response.json").exists())

    def test_interrupt_keeps_partial_trace_and_stops_schedule(self):
        output = self.root / "interrupted"
        def call(raw):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            run_probe(self.root, self.plan, output, call, self.identity)
        state = json.loads((output / "status.json").read_text())
        self.assertFalse(state["complete"])
        self.assertEqual([j["status"] for j in state["jobs"]], ["interrupted"])
        self.assertIsNone(state["active_job"])
        self.assertFalse((output / "1").exists())

    def test_changed_identity_or_payload_blocks_call(self):
        for changed in ("identity", "payload"):
            output = self.root / changed
            check = self.identity
            if changed == "identity":
                check = lambda: {"model": {"digest": "wrong"}, "ollama_version": "fixture"}
            else:
                (self.root / "request.json").write_text("changed")
            with self.assertRaises(ValueError):
                run_probe(self.root, self.plan, output, lambda raw: self.fail("Unexpected call"), check)
