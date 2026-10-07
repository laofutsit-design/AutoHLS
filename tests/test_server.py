import json
import io
import threading
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from server import AutoHLSHandler


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), AutoHLSHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method: str, path: str, body: dict | None = None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        encoded = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode())
        connection.close()
        return response.status, payload

    def test_status(self) -> None:
        status, payload = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("matmul", payload["examples"])

    def test_replay_is_explicit_read_only_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory, patch('server.REPLAY_ROOT', Path(directory)):
            self.assertEqual(self.request('GET', '/api/channel-replay'), (200, {'available': False}))
            self.assertEqual(self.request('POST', '/api/channel-replay', {})[0], 404)
            for query in ('scenario=', 'scenario=../matched', 'scenario=matched&scenario=drift'):
                self.assertEqual(self.request('GET', '/api/channel-replay?' + query)[0], 400)
            (Path(directory) / 'manifest.json').write_text('{}')
            self.assertEqual(self.request('GET', '/api/channel-replay')[0], 503)

    def test_delivery_is_read_only_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory, patch("server.DELIVERY_ROOT", Path(directory)):
            self.assertEqual(self.request("GET", "/api/delivery"), (200, {"available": False}))
            self.assertEqual(self.request("POST", "/api/delivery", {})[0], 404)
            (Path(directory) / "active.json").write_text('{"version":"../outside"}')
            self.assertEqual(self.request("GET", "/api/delivery")[0], 503)

    def test_research_records_preserve_evidence_level(self) -> None:
        status, payload = self.request("GET", "/api/research")
        self.assertEqual(status, 200)
        for run in payload["runs"]:
            if run["backend"] == "native":
                self.assertEqual(run["summary"]["feasible_synthesized"], 0)
                self.assertIsNone(run["summary"]["hls_speedup"])

    def test_explore(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/explore",
            {"source": "void add(int a[8]) { for(int i=0;i<8;i++){a[i]++;} }", "goal": "latency"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["goal"], "latency")
        self.assertEqual(len(payload["candidates"]), 8)

    def test_bad_request(self) -> None:
        status, payload = self.request("POST", "/api/explore", {"source": ""})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_suite_endpoint_never_substitutes_demo_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "audit.json"
            with patch("server.MODEL_SUITE_REPORT", report):
                status, payload = self.request("GET", "/api/model-suite")
                self.assertEqual((status, payload), (200, {"available": False}))
                original = {"complete": False, "all_evidence_audited": False, "board_verified": False,
                            "runs": [{"id": "failed", "audited": False, "error": "fixture failure"}]}
                report.write_text(json.dumps(original))
                status, payload = self.request("GET", "/api/model-suite")
                self.assertEqual(status, 200)
                self.assertEqual(payload, {"available": True, "report": original})
                report.write_text("incomplete JSON")
                status, payload = self.request("GET", "/api/model-suite")
                self.assertEqual(status, 503)
                self.assertIn("error", payload)
                status, _ = self.request("POST", "/api/model-suite", {})
                self.assertEqual(status, 404)

    def test_unsupported_post_reads_bounded_body_before_closing(self) -> None:
        handler = AutoHLSHandler.__new__(AutoHLSHandler)
        handler.path = "/api/model-suite"
        handler.headers = {"Content-Length": "2"}
        handler.rfile = io.BytesIO(b"{}")
        with patch.object(handler, "_json") as response:
            handler.do_POST()
        self.assertEqual(handler.rfile.tell(), 2)
        self.assertEqual(response.call_args.args[1], 404)

    def test_suite_selection_is_explicit_and_never_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old, new = Path(directory) / "old.json", Path(directory) / "new.json"
            old.write_text('{"protocol":{"budget":8}}')
            with patch("server.MODEL_SUITE_REPORT", old), patch("server.BUDGET16_SUITE_REPORT", new, create=True):
                self.assertEqual(self.request("GET", "/api/model-suite?suite=budget16"), (200, {"available": False}))
                new.write_text('{"protocol":{"budget":16}}')
                for name, budget in (("budget8", 8), ("budget16", 16)):
                    status, payload = self.request("GET", "/api/model-suite?suite=" + name)
                    self.assertEqual(status, 200)
                    self.assertEqual(payload["report"]["protocol"]["budget"], budget)
                for query in ("suite=../../server.py", "suite=other", "suite=", "suite=budget8&suite=budget16"):
                    self.assertEqual(self.request("GET", "/api/model-suite?" + query)[0], 400)
                before = new.read_bytes()
                self.assertEqual(self.request("POST", "/api/model-suite?suite=budget16", {"changed": True})[0], 404)
                self.assertEqual(new.read_bytes(), before)

    def test_unsupported_post_response_survives_separate_body_packets(self) -> None:
        for _ in range(20):
            status, payload = self.request("POST", "/api/model-suite", {"fixture": "x" * 4096})
            self.assertEqual(status, 404)
            self.assertEqual(payload["error"], "接口不存在")

    def test_order_suite_is_separate_read_only_and_never_substituted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            order = Path(directory) / "order.json"
            with patch("server.ORDER_SUITE_REPORT", order, create=True):
                route = "/api/model-suite?suite=order39"
                self.assertEqual(self.request("GET", route), (200, {"available": False}))
                original = {"protocol": {"budget": 8, "candidate_order_comparison": True},
                            "complete": False, "runs": [{"id": "pending", "audited": False}]}
                order.write_text(json.dumps(original))
                self.assertEqual(self.request("GET", route), (200, {"available": True, "report": original}))
                before = order.read_bytes()
                self.assertEqual(self.request("POST", route, {"changed": True})[0], 404)
                self.assertEqual(order.read_bytes(), before)
                for query in ("suite=order39&suite=budget8", "suite=order39&suite=order39",
                              "suite=../order39"):
                    self.assertEqual(self.request("GET", "/api/model-suite?" + query)[0], 400)
                order.write_text("unfinished JSON")
                self.assertEqual(self.request("GET", route)[0], 503)

    def test_prefixsum_suite_preserves_unmeasured_reference_and_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefixsum = Path(directory) / "prefixsum.json"
            with patch("server.PREFIXSUM_SUITE_REPORT", prefixsum, create=True):
                route = "/api/model-suite?suite=prefixsum14"
                self.assertEqual(self.request("GET", route), (200, {"available": False}))
                original = {"protocol": {"budget": 8, "reference_policy": "not_measured"},
                            "groups": [{"reference_hits": None}], "complete": True}
                prefixsum.write_text(json.dumps(original))
                self.assertEqual(self.request("GET", route), (200, {"available": True, "report": original}))
                before = prefixsum.read_bytes()
                self.assertEqual(self.request("POST", route, {"changed": True})[0], 404)
                self.assertEqual(prefixsum.read_bytes(), before)
                for query in ("suite=prefixsum14&suite=coverage42", "suite=prefixsum14&suite=prefixsum14",
                              "suite=../prefixsum14"):
                    self.assertEqual(self.request("GET", "/api/model-suite?" + query)[0], 400)
                prefixsum.write_text("unfinished JSON")
                self.assertEqual(self.request("GET", route)[0], 503)

    def test_oversized_unsupported_post_is_not_read(self) -> None:
        handler = AutoHLSHandler.__new__(AutoHLSHandler)
        handler.path = "/api/model-suite"
        handler.headers = {"Content-Length": "1000001"}
        handler.rfile = io.BytesIO(b"not read")
        with patch.object(handler, "_json") as response:
            handler.do_POST()
        self.assertEqual(handler.rfile.tell(), 0)
        self.assertEqual(response.call_args.args[1], 400)

    def test_coverage_suite_is_separate_read_only_and_never_substituted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coverage = Path(directory) / "coverage.json"
            with patch("server.COVERAGE_SUITE_REPORT", coverage, create=True):
                route = "/api/model-suite?suite=coverage42"
                self.assertEqual(self.request("GET", route), (200, {"available": False}))
                original = {"protocol": {"budget": 8, "coverage_comparison": True},
                            "complete": False, "runs": [{"id": "pending", "audited": False}]}
                coverage.write_text(json.dumps(original))
                self.assertEqual(self.request("GET", route), (200, {"available": True, "report": original}))
                before = coverage.read_bytes()
                self.assertEqual(self.request("POST", route, {"changed": True})[0], 404)
                self.assertEqual(coverage.read_bytes(), before)
                for query in ("suite=coverage42&suite=order39", "suite=coverage42&suite=coverage42",
                              "suite=../coverage42"):
                    self.assertEqual(self.request("GET", "/api/model-suite?" + query)[0], 400)
                coverage.write_text("unfinished JSON")
                self.assertEqual(self.request("GET", route)[0], 503)


if __name__ == "__main__":
    unittest.main()
