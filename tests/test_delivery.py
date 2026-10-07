"""Synthetic rejection fixtures only; never real experiment evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autohls.benchmarks import BENCHMARKS, Configuration, render_candidate
from autohls.experiments import file_hash, write_json
from hardware.delivery import member, select_input, verify_files, verify_input, seal, source_files


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.job = "matmul-feedback-pipeline-warmup-v1-s0-r0"
        self.config = Configuration(1, 8, 8)
        self.run = self.root / "artifacts/model-suite" / self.job / "fixture-matmul"
        source = self.run / self.config.key / "kernel.cpp"
        source.parent.mkdir(parents=True)
        source.write_text(render_candidate(BENCHMARKS["matmul"], self.config), encoding="utf-8", newline="\n")
        (self.run / "testbench.cpp").write_text("// synthetic test fixture only\n")
        self.record = {"id": self.config.key, "pipeline_ii": 1, "unroll": 8, "partition": 8,
                       "source_sha256": file_hash(source), "metrics": {"latency_us": 1}}
        write_json(self.run / "manifest.json", {"fixture": True})
        write_json(self.run / "summary.json", {"completed_budget": True})
        (self.run / "history.jsonl").write_text(json.dumps(self.record) + "\n")
        self.selection = {"run": self.job + "/fixture-matmul", "status": "rtl_passed",
                          "best_rtl_id": self.config.key, "rejected_ids": [],
                          "input_hashes": {name: file_hash(self.run / name) for name in
                                           ("manifest.json", "history.jsonl", "summary.json")}}
        self.row = {"id": self.job, "benchmark": "matmul", "audited": True,
                    "summary": {"completed_budget": True}, "rtl": self.selection,
                    "testbench_sha256": file_hash(self.run / "testbench.cpp")}
        self.audit = {"complete": True, "all_evidence_audited": True, "runs": [self.row],
                      "protocol": {"experiment": "pipeline-coverage-comparison-v1",
                                   "part": "xc7z020clg400-1", "clock_ns": 10}}
        self.rtl = self.root / "artifacts/model-suite-rtl/results.json"
        self.rtl.parent.mkdir()
        write_json(self.rtl, {"complete": True, "results": [{"benchmark": "matmul", "id": self.config.key,
            "status": "rtl_passed", "source_sha256": self.record["source_sha256"],
            "testbench_sha256": self.row["testbench_sha256"]}]})

    def test_selects_exact_rtl_finalist(self):
        row, record, source, run = select_input(self.root, self.audit, self.job)
        self.assertEqual(record["id"], self.config.key)
        self.assertEqual(file_hash(source), self.record["source_sha256"])
        self.assertEqual(run, self.run)
        self.assertEqual(row["rtl"]["status"], "rtl_passed")

    def test_no_fallback_to_another_job(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            select_input(self.root, self.audit, "not-a-job")
        self.audit["runs"].append(copy.deepcopy(self.row))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            select_input(self.root, self.audit, self.job)

    def test_rejects_unfinished_unaudited_other_device_and_hls_only(self):
        mutations = [(lambda a: a.update(complete=False)),
                     (lambda a: a.update(all_evidence_audited=False)),
                     (lambda a: a["protocol"].update(part="other")),
                     (lambda a: a["protocol"].update(clock_ns=5)),
                     (lambda a: a["protocol"].update(experiment="other")),
                     (lambda a: a["runs"][0].update(benchmark="fir")),
                     (lambda a: a["runs"][0].update(audited=False)),
                     (lambda a: a["runs"][0]["summary"].update(completed_budget=False)),
                     (lambda a: a["runs"][0]["rtl"].update(status="verification_rejected"))]
        for mutate in mutations:
            value = copy.deepcopy(self.audit)
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                select_input(self.root, value, self.job)

    def test_rejects_path_escape_and_sibling_job(self):
        for name in ("../escape", "/etc/passwd", "C:/Windows/file", "a\\b", "", "."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                member(self.root, name)
        self.row["rtl"]["run"] = "different-job/fixture"
        with self.assertRaisesRegex(ValueError, "outside selected job"):
            select_input(self.root, self.audit, self.job)

    def test_checksum_change_stops_selection(self):
        (self.run / "summary.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Checksum"):
            select_input(self.root, self.audit, self.job)

    def test_rejects_cpp_rewrite_even_with_updated_source_hash(self):
        source = self.run / self.config.key / "kernel.cpp"
        source.write_text(source.read_text().replace("sum +=", "sum -= "))
        self.record["source_sha256"] = file_hash(source)
        (self.run / "history.jsonl").write_text(json.dumps(self.record) + "\n")
        self.selection["input_hashes"]["history.jsonl"] = file_hash(self.run / "history.jsonl")
        with self.assertRaisesRegex(ValueError, "reviewed matmul"):
            select_input(self.root, self.audit, self.job)

    def test_rtl_pass_from_another_source_is_rejected(self):
        value = json.loads(self.rtl.read_text())
        value["results"][0]["source_sha256"] = "other"
        write_json(self.rtl, value)
        with self.assertRaisesRegex(ValueError, "RTL source binding"):
            select_input(self.root, self.audit, self.job)

    def test_missing_checksums_are_not_success(self):
        with self.assertRaisesRegex(ValueError, "Missing file"):
            verify_files(self.root, {})
        write_json(self.root / "source-checksums.json", {"artifacts/model-suite-rtl/results.json": file_hash(self.rtl)})
        with self.assertRaisesRegex(ValueError, "Incomplete frozen"):
            verify_input(self.root)

    def test_building_other_source_stops_before_packaging(self):
        build = self.root / "artifacts/delivery/build"
        build.mkdir(parents=True)
        (build / "kernel.cpp").write_text("// other")
        with patch("hardware.delivery.verify_input", return_value={"source_sha256": "expected"}), \
             patch("hardware.package_bundle.collect") as collect, self.assertRaisesRegex(ValueError, "another candidate"):
            seal(self.root)
        collect.assert_not_called()

    def test_native_failure_is_not_sealed(self):
        build = self.root / "artifacts/delivery/build"
        build.mkdir(parents=True)
        (build / "kernel.cpp").write_text("// fixture")
        (build / "native-check.log").write_text("FAIL cases=11\n")
        with patch("hardware.delivery.verify_input", return_value={"source_sha256": file_hash(build / "kernel.cpp")}), \
             patch("hardware.package_bundle.collect") as collect, self.assertRaisesRegex(ValueError, "native verification"):
            seal(self.root)
        collect.assert_not_called()

    def test_build_is_single_attempt_and_does_not_touch_board(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts/cloud-build-delivery.sh").read_text()
        self.assertLess(script.index("verify-input"), script.index('mkdir "$work"'))
        self.assertIn('> "$work/build.status"', script)
        self.assertNotIn('mkdir -p "$work"', script)
        self.assertNotIn("board_jupyter", script)
        self.assertNotIn("ollama", script)
        self.assertIn("-fsanitize=address,undefined", script)
        self.assertLess(script.index("./native-check"), script.index("vivado_hls -f"))
        self.assertLess(script.index("vivado_hls -f"), script.index("vivado -mode"))
        self.assertIn("C/RTL co-simulation finished: PASS", script)


    def test_release_includes_test_contracts_and_server_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        names = {p.relative_to(root).as_posix() for p in source_files()}
        self.assertTrue({"server.py", "web/evidence.html", "docs/PREFIXSUM_CONTRACT_20260927.md",
                         "tests/test_delivery.py", "hardware/delivery.py"} <= names)
        self.assertFalse(any(name.startswith(("artifacts/", ".tools/", ".codex/")) or "__pycache__" in name for name in names))


if __name__ == "__main__":
    unittest.main()
