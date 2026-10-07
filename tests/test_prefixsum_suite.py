"""Temporary SYNTHETIC integration fixtures, never real experimental results."""
from collections import Counter
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from autohls.benchmarks import BENCHMARKS, VALIDATION_BENCHMARKS
from autohls.cli import main as cli
from autohls.experiments import file_hash, write_json
from autohls.vitis import build_tcl, parse_csynth_xml
from scripts.audit_model_suite import audit_suite, read
from scripts.export_model_suite import export
from scripts.import_model_suite import extract
from scripts.model_prefixsum_suite import CONTRACT, INPUTS, audit, check_inputs, protocol, rtl
from scripts.model_suite import command, run_jobs
from scripts.report_model_suite import publish, trajectories
from scripts.verify_model_suite import verify
from tests import test_order_suite_integration as fixtures

ROOT = Path(__file__).resolve().parents[1]


class PrefixsumProtocolTests(unittest.TestCase):
    def test_fixed_protocol_contract_and_legacy_defaults(self):
        check_inputs(ROOT)
        plan = protocol()
        self.assertEqual(tuple(BENCHMARKS), ("matmul", "fir", "conv2d"))
        self.assertEqual(len(plan["jobs"]), 14)
        self.assertEqual(len({j["id"] for j in plan["jobs"]}), 14)
        self.assertEqual(Counter((j["group"], j["coverage_policy"]) for j in plan["jobs"]),
                         {("random", "none"): 5, ("random", "pipeline-warmup-v1"): 5,
                          ("no-feedback", "pipeline-warmup-v1"): 2, ("feedback", "pipeline-warmup-v1"): 2})
        self.assertEqual(plan["budget"] * len(plan["jobs"]), 112)
        self.assertEqual(sum((plan["budget"] - 1) for j in plan["jobs"] if j["group"] != "random"), 28)
        self.assertEqual(plan["reference_policy"], "not_measured")
        for job in plan["jobs"]:
            args = command(job, Path("temporary"), plan)
            self.assertEqual(args[4], "prefixsum")
            self.assertNotIn("--candidate-order-seed", args)
            self.assertEqual("--no-feedback" in args, job["group"] == "no-feedback")
        with patch("sys.argv", ["autohls", "research", "all"]), patch("autohls.cli.run_experiment") as run:
            run.return_value.__truediv__.return_value.read_text.return_value = json.dumps(
                {"completed_budget": True, "correctness_passed": 1, "evaluated": 1})
            cli()
        self.assertEqual([c.args[0] for c in run.call_args_list], list(BENCHMARKS))


class PrefixsumIntegrationTests(unittest.TestCase):
    chat = fixtures.OrderSuiteIntegrationTests.chat
    execute = fixtures.OrderSuiteIntegrationTests.execute

    def setUp(self):
        # Reuse the no-network harness but explicitly prohibit even synthetic references.
        with patch.object(fixtures, "run_experiment"):
            fixtures.OrderSuiteIntegrationTests.setUp(self)
        self.plan = protocol()
        self.assertFalse(self.references.exists())
        frozen = read(self.root / "source-checksums.json")
        frozen.update(INPUTS)
        write_json(self.root / "source-checksums.json", frozen)
        (self.root / CONTRACT).parent.mkdir()
        shutil.copyfile(ROOT / CONTRACT, self.root / CONTRACT)
        self.fail_native = False
        self.rtl_calls = []

    def native(self, candidate, testbench, output):
        output.mkdir()
        passed = not self.fail_native or candidate.parent.name != "p2-u1-a1"
        log = "PASS cases=32 checks=16512 seed=20260927\n" if passed else "FAIL test=1 output=0\n"
        (output / "test.log").write_text("SYNTHETIC, no native tool executed\n" + log)
        return {"passed": passed, "cases": 32, "checks": 16512, "seed": 20260927}

    def synthesis(self, source, top, output, *, part, clock_ns, testbench, cosim):
        self.assertFalse(cosim)
        run = output / "synthetic-hls"
        run.mkdir(parents=True)
        (run / "run_hls.tcl").write_text(build_tcl(source.as_posix(), top, part, clock_ns,
                                                  testbench=testbench.as_posix()))
        if source.parent.name not in ("p0-u1-a1", "p1-u1-a1"):
            (run / "vitis.stdout.log").write_text("ERROR: injected synthetic HLS failure, no tool executed\n")
            raise RuntimeError("injected synthetic HLS failure")
        (run / "vitis.stdout.log").write_text("SYNTHETIC 2019.1, no tool executed\nCSim done with 0 errors\n")
        report = run / "autohls_project/solution1/syn/report" / (top + "_csynth.xml")
        report.parent.mkdir(parents=True)
        cycles = 1000 if source.parent.name == "p0-u1-a1" else 500
        report.write_text(f"<Report><PerformanceEstimates><SummaryOfOverallLatency>"
            f"<Worst-caseLatency>{cycles}</Worst-caseLatency></SummaryOfOverallLatency>"
            "<SummaryOfTimingAnalysis><EstimatedClockPeriod>5</EstimatedClockPeriod>"
            "</SummaryOfTimingAnalysis></PerformanceEstimates><AreaEstimates><Resources>"
            "<LUT>10</LUT><FF>10</FF><DSP48E>1</DSP48E><BRAM_18K>0</BRAM_18K>"
            "</Resources></AreaEstimates></Report>")
        return {"run_dir": run.as_posix(), "report": str(report), "metrics": parse_csynth_xml(report, clock_ns),
                "csim_passed": True, "cosim_passed": False}

    def cosim(self, source, top, output, *, part, clock_ns, testbench, cosim, timeout_seconds):
        self.assertTrue(cosim)
        self.rtl_calls.append(source.parent.name)
        run = output / "synthetic-rtl"
        run.mkdir(parents=True)
        (run / "run_hls.tcl").write_text(build_tcl(source.as_posix(), top, part, clock_ns,
                                                  testbench=testbench.as_posix(), cosim=True))
        (run / "vitis.stderr.log").write_text("")
        report = run / "autohls_project/solution1/syn/report" / (top + "_csynth.xml")
        report.parent.mkdir(parents=True)
        shutil.copyfile(next((source.parent / "hls").glob("*/autohls_project/solution1/syn/report/*xml")), report)
        sim = run / "autohls_project/solution1/sim/report" / (top + "_cosim.rpt")
        sim.parent.mkdir(parents=True)
        sim.write_text("SYNTHETIC | Verilog | Pass |")
        text = "SYNTHETIC Vivado HLS 2019.1\nCSim done with 0 errors\n*** C/RTL co-simulation finished: PASS ***\n"
        if source.parent.name == "p1-u1-a1":
            # A displayed Pass row must not override final postcheck failure.
            text += "FAIL test=1 output=0\nERROR: injected final postcheck failure\n"
        (run / "vitis.stdout.log").write_text(text)
        if source.parent.name == "p1-u1-a1":
            raise RuntimeError("SYNTHETIC final postcheck failure")
        return {"run_dir": run.as_posix(), "report": str(report), "metrics": parse_csynth_xml(report, clock_ns),
                "csim_passed": True, "cosim_passed": True}

    def test_full_14_searches_export_audit_rtl_fallback_and_report_replay(self):
        self.fail_native = True
        state = run_jobs(self.output, self.plan, execute=self.execute)
        with patch("scripts.audit_model_suite.add_reference", side_effect=AssertionError("No reference allowed")):
            result = audit(self.root)
        self.assertTrue(result["all_evidence_audited"], result["runs"])
        self.assertEqual((len(state["jobs"]), self.chat_calls), (14, 28))
        self.assertEqual(sum(r["summary"]["evaluated"] for r in result["runs"]), 112)
        self.assertGreater(sum(r["statuses"].get("failed", 0) for r in result["runs"]), 0)
        self.assertGreater(sum(r["statuses"].get("verification_failed", 0) for r in result["runs"]), 0)
        self.assertEqual(len(result["groups"]), 4)
        self.assertTrue(all(g["reference_hits"] is None and g["reference_hit_evaluations"] is None for g in result["groups"]))
        self.assertTrue(all(r["reference"] is None for r in result["runs"]))
        before = {p: file_hash(p) for p in self.output.rglob("*") if p.is_file()}
        verified = verify(self.root, synthesize=self.cosim, expected_plan=self.plan)
        self.assertTrue(verified["all_searches_have_rtl_finalist"])
        self.assertEqual(self.rtl_calls, ["p0-u1-a1", "p1-u1-a1"])
        self.assertTrue(all(file_hash(p) == h for p, h in before.items()))
        with self.assertRaises(FileExistsError):
            verify(self.root, synthesize=self.cosim, expected_plan=self.plan)
        write_json(self.root / "artifacts/model-server.json", {"stopped": True})
        for name in ("artifacts/model-server.log", "suite-launch.log"):
            (self.root / name).write_text("SYNTHETIC fixture only")
        export(self.root, metadata=("source-checksums.json", CONTRACT, "artifacts/model-suite/protocol.json"))
        archive = next((self.root / "artifacts/snapshots").glob("*.zip"))
        imported = self.root / "reimport"
        self.assertGreater(extract(archive, imported, file_hash(archive)), 100)
        audited = audit(imported)
        self.assertTrue(audited["complete"] and audited["all_evidence_audited"])
        self.assertEqual((audited["rtl"]["passed"], audited["rtl"]["rejected"], audited["rtl"]["finalists_passed"]), (1, 1, 14))
        for row in audited["runs"]:
            self.assertEqual(row["rtl"]["best_rtl_id"], "p0-u1-a1")
            if row["summary"]["best_id"] == "p1-u1-a1":
                self.assertEqual(row["rtl"]["rejected_ids"], ["p1-u1-a1"])
        audit_path = self.root / "SYNTHETIC-audit.json"
        write_json(audit_path, audited)
        for destination in (self.root / "report", self.root / "report-replay"):
            self.assertEqual(publish(audit_path, imported, destination), 112)
        for name in ("REPORT.md", "trajectory.csv", "provenance.json"):
            self.assertEqual((self.root / "report" / name).read_bytes(), (self.root / "report-replay" / name).read_bytes())
        text = (self.root / "report/REPORT.md").read_text(encoding="utf-8")
        self.assertIn("未测量", text)
        self.assertNotIn("未达到", text)
        self.assertNotIn("均为开发集", text)
        self.assertEqual(text.count("| prefixsum | 随机覆盖增量 |"), 1)

    def test_stop_failure_no_retry_and_schedule_tamper(self):
        self.fail_chat_at, self.stop_after = 3, 3
        state = run_jobs(self.output, self.plan, execute=self.execute)
        result = audit(self.root)
        self.assertTrue(state["stopped"])
        self.assertEqual((len(state["jobs"]), self.chat_calls), (3, 3))
        self.assertFalse(result["complete"] or result["all_evidence_audited"])
        self.assertEqual(sum(r["state"] == "pending" for r in result["runs"]), 11)
        self.assertTrue(result["runs"][2]["audited"], result["runs"][2])
        self.assertFalse(result["runs"][2]["summary"]["completed_budget"])
        self.assertEqual(result["runs"][2]["summary"]["model_failures"], 1)
        with self.assertRaises(FileExistsError):
            run_jobs(self.output, self.plan, execute=self.execute)
        with self.assertRaisesRegex(ValueError, "Audit all"):
            rtl(self.root)
        with self.assertRaises(ValueError):
            trajectories(self.root, result)
        state["jobs"].reverse()
        write_json(self.output / "status.json", state)
        with self.assertRaisesRegex(ValueError, "schedule changed"):
            audit(self.root)

    def test_no_reference_injection_or_no_feedback_leakage(self):
        self.stop_after = 3
        state = run_jobs(self.output, self.plan, execute=self.execute)
        with self.assertRaisesRegex(ValueError, "reference injection"):
            audit_suite(self.root, self.references, expected_plan=self.plan, benchmark_registry=VALIDATION_BENCHMARKS)
        run = self.output / state["jobs"][2]["run"]
        request = next(run.glob("proposal-*/request.json"))
        data = read(request)
        prompt = json.loads(data["messages"][0]["content"])
        self.assertEqual(prompt["observations"], [])
        prompt["observations"] = [{"id": "injected-leak"}]
        data["messages"][0]["content"] = json.dumps(prompt)
        write_json(request, data)
        result = audit(self.root)
        self.assertFalse(result["runs"][2]["audited"])

    def test_rtl_requires_stopped_server_and_unused_output(self):
        with patch("scripts.model_prefixsum_suite.audit", return_value={"complete": True, "all_evidence_audited": True}), \
             patch("scripts.model_prefixsum_suite.verify") as verify_call, \
             patch("scripts.model_prefixsum_suite.socket.socket") as sock:
            (self.root / "artifacts").mkdir(exist_ok=True)
            write_json(self.root / "artifacts/model-server.json", {"stopped": False})
            with self.assertRaisesRegex(ValueError, "not stopped"):
                rtl(self.root)
            write_json(self.root / "artifacts/model-server.json", {"stopped": True})
            sock.return_value.__enter__.return_value.connect_ex.return_value = 0
            with self.assertRaisesRegex(ValueError, "still occupied"):
                rtl(self.root)
            verify_call.assert_not_called()

    def test_native_counts_must_match_frozen_contract(self):
        self.stop_after = 1
        state = run_jobs(self.output, self.plan, execute=self.execute)
        run = self.output / state["jobs"][0]["run"]
        history_path = run / "history.jsonl"
        records = [json.loads(line) for line in history_path.read_text().splitlines()]
        record = records[0]
        record["verification"].update(cases=1, checks=1, seed=0)
        write_json(run / record["id"] / "result.json", record)
        (run / record["id"] / "native/test.log").write_text("PASS cases=1 checks=1 seed=0\n")
        history_path.write_text("\n".join(json.dumps(r) for r in records))
        result = audit(self.root)
        self.assertFalse(result["runs"][0]["audited"])
        self.assertIn("task contract", result["runs"][0]["error"])


if __name__ == "__main__":
    unittest.main()
