import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_order_probe import permute_request, prepare


class OrderProbeTests(unittest.TestCase):
    def payload(self):
        candidates = [{"id": "candidate-" + str(i), "factor": i} for i in range(8)]
        prompt = {"source": "source stays unchanged", "allowed_candidates": candidates,
                  "observations": [{"id": "baseline", "diagnostics": ["ERROR: sample"], "metrics": None}]}
        return {"model": "fixture:tag", "stream": False, "options": {"seed": 0},
                "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
                "format": {"properties": {"id": {"type": "string", "enum": [r["id"] for r in candidates]},
                                           "reason": {"type": "string"}}, "additionalProperties": False}}

    def test_changes_only_candidate_order_and_matching_enum(self):
        original = self.payload()
        saved = copy.deepcopy(original)
        changed = permute_request(original, 20260927)
        prompt = json.loads(changed["messages"][0]["content"])
        before = json.loads(original["messages"][0]["content"])
        self.assertEqual(original, saved)
        self.assertNotEqual(prompt["allowed_candidates"], before["allowed_candidates"])
        self.assertCountEqual(prompt["allowed_candidates"], before["allowed_candidates"])
        self.assertEqual(changed["format"]["properties"]["id"]["enum"], [r["id"] for r in prompt["allowed_candidates"]])
        prompt["allowed_candidates"] = before["allowed_candidates"]
        changed["messages"][0]["content"] = json.dumps(prompt, ensure_ascii=False)
        changed["format"]["properties"]["id"]["enum"] = original["format"]["properties"]["id"]["enum"]
        self.assertEqual(changed, original)

    def test_fixed_seed_is_reproducible(self):
        self.assertEqual(permute_request(self.payload(), 9), permute_request(self.payload(), 9))

    def test_duplicate_ids_and_mismatched_schema_are_rejected(self):
        payload = self.payload()
        payload["format"]["properties"]["id"]["enum"].reverse()
        with self.assertRaisesRegex(ValueError, "Schema"):
            permute_request(payload, 1)
        payload = self.payload()
        prompt = json.loads(payload["messages"][0]["content"])
        prompt["allowed_candidates"][1]["id"] = prompt["allowed_candidates"][0]["id"]
        payload["messages"][0]["content"] = json.dumps(prompt)
        with self.assertRaisesRegex(ValueError, "unique"):
            permute_request(payload, 1)

    def test_extra_message_is_not_silently_dropped(self):
        payload = self.payload()
        payload["messages"].append({"role": "system", "content": "fixture"})
        with self.assertRaisesRegex(ValueError, "one user prompt"):
            permute_request(payload, 1)

    def test_incomplete_audit_creates_no_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit = root / "audit.json"
            audit.write_text(json.dumps({"complete": False}), encoding="utf-8")
            destination = root / "output"
            with self.assertRaisesRegex(ValueError, "completed audited"):
                prepare(audit, root, destination)
            self.assertFalse(destination.exists())

    def test_missing_rtl_creates_no_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit = root / "audit.json"
            audit.write_text(json.dumps({"complete": True, "all_evidence_audited": True, "rtl": None}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "completed audited"):
                prepare(audit, root, root / "output")
            self.assertFalse((root / "output").exists())
