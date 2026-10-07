import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autohls.benchmarks import Configuration, configurations
from autohls.planner import inspect_ollama, propose_ollama


class PlannerContractTests(unittest.TestCase):
    def request_choice(self, choice, feedback=True):
        response = json.dumps({"done": True, "done_reason": "stop", "message": {"content": json.dumps(choice)}}).encode()
        history = [{"id": "p0-u1-a1", "status": "synthesized", "metrics": {"latency_us": 9}}]
        with tempfile.TemporaryDirectory() as directory:
            with patch("autohls.planner.urlopen", return_value=io.BytesIO(response)) as request:
                result = propose_ollama([Configuration(1, 2, 2)], "void kernel() {}", history,
                                        "test-double-not-a-real-model", Path(directory), "latency", {}, feedback)
                payload = json.loads(request.call_args.args[0].data)
                prompt = json.loads(payload["messages"][0]["content"])
                return result, prompt

    def test_feedback_and_no_feedback_use_same_candidate_contract(self):
        choice = {"id": "p1-u2-a2", "reason": "Test response"}
        config, with_feedback = self.request_choice(choice)
        _, without_feedback = self.request_choice(choice, feedback=False)
        self.assertEqual(config, Configuration(1, 2, 2))
        self.assertEqual(len(with_feedback["observations"]), 1)
        self.assertEqual(without_feedback["observations"], [])
        self.assertEqual(with_feedback["allowed_candidates"], without_feedback["allowed_candidates"])

    def test_invalid_and_duplicate_ids_are_rejected(self):
        for value in ("p0-u1-a1", "unknown", "exec rm -rf"):
            with self.assertRaises(ValueError):
                self.request_choice({"id": value, "reason": "Invalid test response"})

    def test_extra_executable_fields_are_not_accepted(self):
        with self.assertRaises(ValueError):
            self.request_choice({"id": "p1-u2-a2", "reason": "Test", "command": "exit"})

    def test_seed_and_fixed_generation_options_are_sent_and_traced(self):
        response = {"done": True, "message": {"content": '{"id":"p1-u2-a2","reason":"test"}'}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch("autohls.planner.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as request:
                propose_ollama([Configuration(1, 2, 2)], "source", [], "test:tag", path, "latency", {}, seed=42)
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload["options"], {"temperature": 0, "seed": 42, "num_ctx": 16384, "num_predict": 256})
            trace = json.loads((path / "trace.json").read_text())
            self.assertTrue(trace["success"])
            self.assertEqual(trace["selected_id"], "p1-u2-a2")
            self.assertGreaterEqual(trace["wall_seconds"], 0)
            self.assertEqual(json.loads((path / "response.raw.log").read_bytes()), response)

    def test_schema_enum_tracks_remaining_candidates_without_feedback(self):
        candidates = [Configuration(0, 8, 4), Configuration(1, 2, 2)]
        requests = []
        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                selected = candidates[0]
                response = json.dumps({"done": True, "message": {"content": json.dumps(
                    {"id": selected.key, "reason": "test"})}}).encode()
                with patch("autohls.planner.urlopen", return_value=io.BytesIO(response)) as request:
                    result = propose_ollama(candidates, "source", [], "test:tag", Path(directory) / str(index),
                                            "latency", {}, feedback=False)
                payload = json.loads(request.call_args.args[0].data)
                requests.append(payload)
                schema = payload["format"]
                self.assertEqual(schema, {"type": "object", "properties": {
                    "id": {"type": "string", "enum": [item.key for item in candidates]},
                    "reason": {"type": "string"}}, "required": ["id", "reason"],
                    "additionalProperties": False})
                self.assertEqual(json.loads(payload["messages"][0]["content"])["observations"], [])
                self.assertEqual(result, selected)
                candidates.remove(result)
        self.assertNotIn("p0-u8-a4", requests[1]["format"]["properties"]["id"]["enum"])

    def test_empty_candidates_fail_before_network_request(self):
        with tempfile.TemporaryDirectory() as directory, patch("autohls.planner.urlopen") as request:
            with self.assertRaisesRegex(ValueError, "remaining"):
                propose_ollama([], "source", [], "test:tag", Path(directory), "latency", {})
            request.assert_not_called()

    def test_order_seed_reorders_both_lists_without_mutation_or_feedback_leak(self):
        candidates = configurations()[1:]
        saved = list(candidates)
        payloads = []
        with tempfile.TemporaryDirectory() as directory:
            for index, order_seed in enumerate((None, 42, 42, 43)):
                path = Path(directory) / str(index)
                response = {"done": True, "message": {"content": json.dumps({"id": candidates[-1].key, "reason": "fixture"})}}
                with patch("autohls.planner.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as request:
                    result = propose_ollama(candidates, "source", [{"id": "private-measurement"}], "fixture:tag", path,
                                            "latency", {}, feedback=False, seed=7, candidate_order_seed=order_seed)
                self.assertEqual(result, candidates[-1])
                self.assertEqual(candidates, saved)
                payload = json.loads(request.call_args.args[0].data)
                payloads.append(payload)
                prompt = json.loads(payload["messages"][0]["content"])
                ids = [c["id"] for c in prompt["allowed_candidates"]]
                self.assertCountEqual(ids, [c.key for c in candidates])
                self.assertEqual(ids, payload["format"]["properties"]["id"]["enum"])
                self.assertEqual(prompt["observations"], [])
                self.assertEqual(payload["options"]["seed"], 7)
                trace = json.loads((path / "trace.json").read_text())
                self.assertEqual(trace.get("candidate_order_seed"), order_seed)
            self.assertEqual(payloads[0]["format"]["properties"]["id"]["enum"], [c.key for c in candidates])
            self.assertEqual(payloads[1], payloads[2])
            self.assertNotEqual(payloads[0], payloads[1])
            self.assertNotEqual(payloads[1], payloads[3])

    def test_invalid_order_seed_fails_before_network(self):
        for value in (True, "1", 1.5):
            with tempfile.TemporaryDirectory() as directory, patch("autohls.planner.urlopen") as request:
                with self.assertRaisesRegex(ValueError, "order seed"):
                    propose_ollama([Configuration()], "source", [], "fixture", Path(directory), "latency", {}, candidate_order_seed=value)
                request.assert_not_called()

    def test_timeout_is_retained_and_never_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch("autohls.planner.urlopen", side_effect=TimeoutError("test timeout")) as request:
                with self.assertRaises(TimeoutError):
                    propose_ollama([Configuration(1, 2, 2)], "source", [], "test:tag", path, "latency", {})
            request.assert_called_once()
            trace = json.loads((path / "trace.json").read_text())
            self.assertFalse(trace["success"])
            self.assertIn("TimeoutError", trace["error"])
            self.assertTrue((path / "request.json").is_file())

    def test_complete_json_fence_is_accepted_without_changing_raw_evidence(self):
        content = '```json\n{"id":"p1-u2-a2","reason":"test"}\n```'
        response = json.dumps({"done": True, "message": {"content": content}}).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch("autohls.planner.urlopen", return_value=io.BytesIO(response)):
                config = propose_ollama([Configuration(1, 2, 2)], "source", [], "test:tag", path, "latency", {})
            self.assertEqual(config.key, "p1-u2-a2")
            self.assertEqual((path / "response.raw.log").read_bytes(), response)
            self.assertEqual(json.loads((path / "trace.json").read_text())["response_wrapping"], "json_fence")

    def test_fence_does_not_allow_prose_extra_fields_or_invalid_ids(self):
        valid = '{"id":"p1-u2-a2","reason":"test"}'
        contents = ['Here is JSON:\n```json\n' + valid + '\n```',
                    '```json\n' + valid + '\n```\nExecute something next',
                    '```json\n' + valid + '\n```\n```json\n' + valid + '\n```',
                    '```json\n{"id":"invalid","reason":"test"}\n```',
                    '```json\n{"id":"p1-u2-a2","reason":"test","command":"exit"}\n```']
        for content in contents:
            response = json.dumps({"done": True, "message": {"content": content}}).encode()
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                with patch("autohls.planner.urlopen", return_value=io.BytesIO(response)):
                    with self.assertRaises(ValueError):
                        propose_ollama([Configuration(1, 2, 2)], "source", [], "test:tag", Path(directory), "latency", {})

    def test_malformed_and_truncated_responses_cannot_select(self):
        for response in (b'not json', json.dumps({"done": False}).encode(),
                         json.dumps({"done": True, "done_reason": "length"}).encode()):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                with patch("autohls.planner.urlopen", return_value=io.BytesIO(response)):
                    with self.assertRaises(ValueError):
                        propose_ollama([Configuration(1, 2, 2)], "source", [], "test:tag", path, "latency", {})
                self.assertEqual((path / "response.raw.log").read_bytes(), response)
                self.assertFalse(json.loads((path / "trace.json").read_text())["success"])

    def test_installed_model_identity_requires_exact_tag_and_digest(self):
        model = {"name": "test:tag", "digest": "test-digest", "size": 123}
        with patch("autohls.planner.urlopen", side_effect=[
                io.BytesIO(json.dumps({"models": [model]}).encode()), io.BytesIO(b'{"version":"test-version"}')]):
            self.assertEqual(inspect_ollama("test:tag"), {"model": model, "ollama_version": "test-version"})
        with patch("autohls.planner.urlopen", return_value=io.BytesIO(json.dumps({"models": [model]}).encode())):
            with self.assertRaises(ValueError):
                inspect_ollama("test")

    def test_identity_drift_stops_before_generation(self):
        expected = {"model": {"digest": "old"}, "ollama_version": "test"}
        for current in ({"model": {"digest": "new"}, "ollama_version": "test"},
                        {"model": {"digest": "old"}, "ollama_version": "new"}):
            with tempfile.TemporaryDirectory() as directory:
                with patch("autohls.planner.inspect_ollama", return_value=current), patch("autohls.planner.urlopen") as request:
                    with self.assertRaisesRegex(ValueError, "changed"):
                        propose_ollama([Configuration(1, 2, 2)], "source", [], "test:tag", Path(directory),
                                        "latency", {}, expected_identity=expected)
                    request.assert_not_called()

    def test_remote_endpoints_are_rejected_before_network_access(self):
        for endpoint in ("https://localhost:11434", "http://example.com", "http://user:password@localhost"):
            with patch("autohls.planner.urlopen") as request:
                with self.assertRaises(ValueError):
                    inspect_ollama("test:tag", endpoint)
                request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
