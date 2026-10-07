"""Temporary synthetic fixtures only: no compiler, HLS, model, network or board."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from autohls.benchmarks import BENCHMARKS
from autohls.cli import main as cli
from autohls.experiments import file_hash, run_experiment, write_json
from autohls.vitis import build_tcl, parse_csynth_xml
from scripts.audit_model_suite import read
from scripts.model_order_suite import audit
from scripts.model_suite import run_jobs
from scripts.prepare_order_suite import protocol

ROOT = Path(__file__).resolve().parents[1]


class OrderSuiteIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="autohls-synthetic-order-")))
        self.plan = protocol()
        self.output = self.root / "artifacts/model-suite"
        self.references = self.root / "synthetic-references"
        self.chat_calls = 0
        self.fail_chat_at = None
        self.stop_after = None
        self.executed = []
        identity = {"model": {"name": self.plan["model"], "digest": self.plan["model_digest"]},
                    "ollama_version": self.plan["ollama_version"]}
        for target, value in (("find_compiler", "synthetic-compiler"), ("detect_vitis", {"available": True}),
                              ("inspect_ollama", identity)):
            self.stack.enter_context(patch("autohls.experiments." + target, return_value=value))
        self.stack.enter_context(patch("autohls.planner.inspect_ollama", return_value=identity))
        self.stack.enter_context(patch("autohls.experiments.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "SYNTHETIC compiler", "")))
        self.stack.enter_context(patch("autohls.experiments.verify_native", side_effect=self.native))
        self.stack.enter_context(patch("autohls.experiments.run_synthesis", side_effect=self.synthesis))
        self.stack.enter_context(patch("autohls.planner.urlopen", side_effect=self.chat))
        # Reproduce Linux evidence text on Windows without modifying repository inputs.
        original_write = Path.write_text
        self.stack.enter_context(patch.object(Path, "write_text",
            lambda path, data, *args, **kwargs: original_write(path, data, *args, **{**kwargs, "newline": "\n"})))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(redirect_stderr(io.StringIO()))
        for benchmark in BENCHMARKS:
            run_experiment(benchmark, self.references, backend="hls", planner="exhaustive", budget=30,
                           goal=self.plan["goal"], limits=self.plan["limits"])
        paths = list((ROOT / "autohls").glob("*.py"))
        paths += [p for b in BENCHMARKS.values() for p in (b.source, b.testbench)]
        write_json(self.root / "source-checksums.json", {p.relative_to(ROOT).as_posix(): file_hash(p) for p in paths})

    def native(self, candidate, testbench, output):
        output.mkdir()
        (output / "test.log").write_text("SYNTHETIC fixture, not native execution\nPASS cases=1 checks=1 seed=0\n")
        return {"passed": True, "cases": 1, "checks": 1, "seed": 0}

    def synthesis(self, source, top, output, *, part, clock_ns, testbench, cosim):
        self.assertFalse(cosim)
        run = output / "synthetic-hls"
        run.mkdir(parents=True)
        (run / "run_hls.tcl").write_text(build_tcl(source.as_posix(), top, part, clock_ns,
                                                  testbench=testbench.as_posix()))
        log = run / "vitis.stdout.log"
        if source.parent.name != "p0-u1-a1":
            log.write_text("ERROR: injected synthetic HLS failure, no tool was executed\n")
            raise RuntimeError("injected synthetic HLS failure")
        log.write_text("SYNTHETIC 2019.1 fixture, no tool was executed\nCSim done with 0 errors\n")
        report = run / "autohls_project/solution1/syn/report" / (top + "_csynth.xml")
        report.parent.mkdir(parents=True)
        report.write_text("<Report><PerformanceEstimates><SummaryOfOverallLatency>"
            "<Worst-caseLatency>1000</Worst-caseLatency></SummaryOfOverallLatency>"
            "<SummaryOfTimingAnalysis><EstimatedClockPeriod>5</EstimatedClockPeriod>"
            "</SummaryOfTimingAnalysis></PerformanceEstimates><AreaEstimates><Resources>"
            "<LUT>10</LUT><FF>10</FF><DSP48E>1</DSP48E><BRAM_18K>0</BRAM_18K>"
            "</Resources></AreaEstimates></Report>")
        return {"run_dir": run.as_posix(), "report": str(report), "metrics": parse_csynth_xml(report, clock_ns),
                "csim_passed": True, "cosim_passed": False}

    def chat(self, request, **kwargs):
        self.chat_calls += 1
        if self.chat_calls == self.fail_chat_at:
            raise OSError("injected synthetic model failure")
        prompt = json.loads(json.loads(request.data)["messages"][0]["content"])
        choice = {"id": prompt["allowed_candidates"][0]["id"], "reason": "SYNTHETIC fixture only"}
        return io.BytesIO(json.dumps({"done": True, "message": {"content": json.dumps(choice)}}).encode())

    def execute(self, args, **kwargs):
        self.executed.append(args)
        with patch("sys.argv", ["autohls", *args[3:]]):
            try:
                cli()
                code = 0
            except SystemExit as exc:
                code = exc.code
        if len(self.executed) == self.stop_after:
            (self.output / "STOP").touch()
        return subprocess.CompletedProcess(args, code)

    def test_all_39_commands_and_evidence_audit_with_counted_tool_failures(self):
        state = run_jobs(self.output, self.plan, execute=self.execute)
        result = audit(self.root, self.references)
        self.assertEqual([j["id"] for j in state["jobs"]], [j["id"] for j in self.plan["jobs"]])
        self.assertTrue(result["complete"] and result["all_evidence_audited"])
        self.assertEqual(len(self.executed), 39)
        self.assertEqual(self.chat_calls, 168)
        self.assertEqual(sum(r["summary"]["evaluated"] for r in result["runs"]), 312)
        self.assertEqual(sum(r["statuses"]["failed"] for r in result["runs"]), 273)
        self.assertEqual(len(result["groups"]), 15)
        self.assertTrue(all(g["complete_with_feasible"] == g["planned_runs"] for g in result["groups"]))
        self.assertIsNone(result["rtl"])
        self.assertFalse(result["board_verified"])
        for row in result["runs"]:
            delivered = row["diagnostic_evidence"]["errors_delivered_to_next_request"]
            self.assertEqual(delivered, 6 if row["group"] == "feedback" else 0)

    def test_stop_and_model_failure_remain_in_partial_39_job_audit(self):
        self.fail_chat_at, self.stop_after = 3, 2
        state = run_jobs(self.output, self.plan, execute=self.execute)
        result = audit(self.root, self.references)
        self.assertTrue(state["stopped"])
        self.assertFalse(result["complete"] or result["all_evidence_audited"])
        self.assertEqual(len(self.executed), 2)
        self.assertEqual(self.chat_calls, 3)  # No retry after the injected failure.
        self.assertEqual(len(result["runs"]), 39)
        self.assertEqual(sum(r["state"] == "pending" for r in result["runs"]), 37)
        failed = result["runs"][1]
        self.assertTrue(failed["audited"])
        self.assertEqual(failed["exit_code"], 1)
        self.assertFalse(failed["summary"]["completed_budget"])
        self.assertEqual(failed["summary"]["model_failures"], 1)
        affected = next(g for g in result["groups"] if g["benchmark"] == "matmul"
                        and g["group"] == "no-feedback" and g["order_condition"] == "canonical")
        self.assertEqual((affected["complete_with_feasible"], affected["planned_runs"]), (0, 2))
        before = (self.output / "status.json").read_bytes()
        with self.assertRaises(FileExistsError):
            run_jobs(self.output, self.plan, execute=self.execute)
        self.assertEqual((self.output / "status.json").read_bytes(), before)
        tampered = read(self.output / "status.json")
        tampered["jobs"].reverse()
        write_json(self.output / "status.json", tampered)
        with self.assertRaisesRegex(ValueError, "schedule changed"):
            audit(self.root, self.references)
