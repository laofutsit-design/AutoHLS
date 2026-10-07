import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.order_probe_evidence import audit, compare_payloads, export, inside, reconstruct, sha


class OrderProbeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "evidence"
        self.root.mkdir()
        self.a = {"messages": [{"role": "user", "content": json.dumps({"source": "fixture", "observations": [], "allowed_candidates": [{"id": "a"}, {"id": "b"}]})}],
                  "format": {"properties": {"id": {"enum": ["a", "b"]}}}, "options": {"seed": 0}}
        self.b = copy.deepcopy(self.a)
        prompt = json.loads(self.b["messages"][0]["content"])
        prompt["allowed_candidates"].reverse()
        self.b["messages"][0]["content"] = json.dumps(prompt)
        self.b["format"]["properties"]["id"]["enum"].reverse()
        self.plan = {"model": "fixture:tag", "model_digest": "fixture", "ollama_version": "fixture", "jobs": [], "states": []}
        self.status = {"complete": True, "jobs": [], "hls_executed": False, "board_verified": False}
        for benchmark in ("matmul", "fir", "conv2d"):
            self.plan["states"].append({"benchmark": benchmark})
            for label in ("A1", "B1", "B2", "A2"):
                payload = self.a if label[0] == "A" else self.b
                name = benchmark + ("/original.json" if label[0] == "A" else "/permuted.json")
                self.write(name, payload)
                raw = (self.root / name).read_bytes()
                job = {"id": benchmark + "-" + label, "benchmark": benchmark, "condition": label[0], "request": name, "request_sha256": sha(raw)}
                self.plan["jobs"].append(job)
                trace = {**job, "status": "valid", "selected_id": "a", "candidate_position": 1 if label[0] == "A" else 2,
                         "wall_seconds": 1, "identity": {"model": {"digest": "fixture"}, "ollama_version": "fixture"}}
                self.status["jobs"].append(trace)
                prefix = "artifacts/order-probe/" + job["id"]
                self.write(prefix + "/request.json", payload)
                self.write(prefix + "/trace.json", trace)
                response = {"done": True, "model": "fixture:tag", "message": {"content": '{"id":"a","reason":"fixture"}'}}
                self.write(prefix + "/response.raw.log", response)
                self.write(prefix + "/response.json", response)
        self.write("probe-plan.json", self.plan)
        self.write("suite-protocol.json", self.plan)
        self.write("source-checksums.json", {"suite-protocol.json": sha((self.root / "suite-protocol.json").read_bytes())})
        self.write("artifacts/order-probe/status.json", self.status)
        self.write("artifacts/order-probe-server.json", {"stopped": True, "listen": "127.0.0.1:11434"})
        self.write("artifacts/order-probe-server.log", {})
        self.write("artifacts/order-probe-launch.log", {})

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_only_order_change_is_accepted(self):
        compare_payloads(self.a, self.b)
        changed = copy.deepcopy(self.b)
        changed["options"]["seed"] = 1
        with self.assertRaisesRegex(ValueError, "Payload"):
            compare_payloads(self.a, changed)
        changed = copy.deepcopy(self.b)
        prompt = json.loads(changed["messages"][0]["content"])
        prompt["source"] = "tampered"
        changed["messages"][0]["content"] = json.dumps(prompt)
        with self.assertRaisesRegex(ValueError, "Prompt"):
            compare_payloads(self.a, changed)

    def test_reconstruct_includes_unattempted_calls(self):
        status = {**self.status, "complete": False, "jobs": self.status["jobs"][:1]}
        rows = reconstruct(self.root, self.plan, status)
        self.assertEqual(len(rows), 12)
        self.assertEqual([r["status"] for r in rows], ["valid"] + ["not_run"] * 11)

    def test_valid_label_cannot_override_raw_reply(self):
        self.write("artifacts/order-probe/matmul-A1/response.raw.log", {"done": False})
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            reconstruct(self.root, self.plan, self.status)

    def test_recorded_choice_must_match_raw(self):
        self.status["jobs"][0]["selected_id"] = "b"
        self.write("artifacts/order-probe/matmul-A1/trace.json", self.status["jobs"][0])
        with self.assertRaisesRegex(ValueError, "Recorded choice"):
            reconstruct(self.root, self.plan, self.status)

    def test_path_escape_and_active_export_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "escapes"):
            inside(self.root, "../outside")
        self.write("artifacts/order-probe/status.json", {**self.status, "complete": False})
        destination = self.root.parent / "active.zip"
        with self.assertRaisesRegex(ValueError, "active"):
            export(self.root, destination)
        self.assertFalse(destination.exists())

    def test_export_audit_and_tamper_detection(self):
        destination = self.root.parent / "complete.zip"
        result = export(self.root, destination)
        extracted = self.root.parent / "extracted"
        with zipfile.ZipFile(destination) as archive:
            archive.extractall(extracted)
        with patch("scripts.order_probe_evidence.PLAN_SHA256", sha((self.root / "probe-plan.json").read_bytes())):
            audited = audit(extracted, self.root.parent / "report")
            self.assertEqual(audited["valid_calls"], 12)
            self.assertEqual(audited["verified_files"], result["files"])
            self.assertTrue(all(g["stable"]["A"] and g["stable"]["B"] for g in audited["groups"]))
            self.assertFalse(any(g["stable_between_condition_difference"] for g in audited["groups"]))
            (extracted / "artifacts/order-probe/matmul-A1/response.raw.log").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "Export changed"):
                audit(extracted, self.root.parent / "tampered-report")
