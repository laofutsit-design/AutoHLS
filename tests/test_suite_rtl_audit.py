import json
from pathlib import Path
import tempfile
import unittest

from autohls.experiments import file_hash, summarize, write_json
from autohls.vitis import build_tcl, parse_csynth_xml
from scripts.audit_model_suite import audit_rtl, read
from scripts.model_suite import protocol


class RTLAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = protocol()
        self.plan["jobs"] = self.plan["jobs"][:1]
        job = self.plan["jobs"][0]
        run = self.root / "artifacts/model-suite" / job["id"] / "fixture-matmul"
        run.mkdir(parents=True)
        self.rtl = self.root / "artifacts/model-suite-rtl"
        records, results = [], []
        for key, cycles in (("p0-u1-a1", 3000), ("p1-u1-a1", 1000)):
            solution = self.rtl / "matmul" / key / "fixture/autohls_project/solution1"
            report = solution / "syn/report/matmul_csynth.xml"
            report.parent.mkdir(parents=True)
            report.write_text(f"""<Report><PerformanceEstimates>
              <SummaryOfTimingAnalysis><EstimatedClockPeriod>8</EstimatedClockPeriod></SummaryOfTimingAnalysis>
              <SummaryOfOverallLatency><Worst-caseLatency>{cycles}</Worst-caseLatency></SummaryOfOverallLatency>
              </PerformanceEstimates><AreaEstimates><Resources><LUT>10</LUT><FF>10</FF><DSP>1</DSP><BRAM_18K>0</BRAM_18K>
              </Resources></AreaEstimates></Report>""")
            sim = solution / "sim/report/matmul_cosim.rpt"
            sim.parent.mkdir(parents=True)
            sim.write_text("fixture | Verilog | Pass |")
            remote_run = "/release/artifacts/model-suite-rtl/matmul/" + key + "/fixture"
            remote_search = "/release/artifacts/model-suite/" + job["id"] + "/fixture-matmul"
            (solution.parents[1] / "run_hls.tcl").write_text(build_tcl(remote_search + "/" + key + "/kernel.cpp",
                "matmul", self.plan["part"], self.plan["clock_ns"], testbench=remote_search + "/testbench.cpp", cosim=True))
            (solution.parents[1] / "vitis.stdout.log").write_text(
                "Vivado HLS 2019.1\nINFO: CSim done with 0 errors\n"
                "INFO: Starting C post checking ...\nPASS cases=1 checks=1 seed=1\n"
                "INFO: [COSIM 212-1000] *** C/RTL co-simulation finished: PASS ***\n")
            (solution.parents[1] / "vitis.stderr.log").write_text("")
            metrics = parse_csynth_xml(report, 10)
            records.append({"id": key, "status": "synthesized", "source_sha256": "fixture-source-" + key,
                            "verification": {"passed": True}, "synthesis": {"csim_passed": True, "cosim_passed": False,
                            "run_dir": remote_search + "/" + key + "/hls/fixture"}, "metrics": metrics})
            results.append({"benchmark": "matmul", "id": key, "origin_job": job["id"], "status": "rtl_passed",
                            "source_sha256": records[-1]["source_sha256"], "testbench_sha256": "fixture-tb",
                            "expected_metrics": metrics, "synthesis": {"cosim_passed": True, "metrics": metrics, "run_dir": remote_run},
                            "report_sha256": file_hash(report)})
        summary = summarize(records, self.plan["limits"], "latency")
        write_json(run / "summary.json", summary)
        write_json(run / "manifest.json", {"fixture": True})
        (run / "history.jsonl").write_text("\n".join(json.dumps(r) for r in records))
        selection = {"job": job["id"], "benchmark": "matmul", "status": "rtl_passed", "run": job["id"] + "/fixture-matmul",
                     "hls_best_id": "p1-u1-a1", "best_rtl_id": "p1-u1-a1", "rejected_ids": [], "best_metrics": records[-1]["metrics"],
                     "input_hashes": {name: file_hash(run / name) for name in ("manifest.json", "summary.json", "history.jsonl")}}
        write_json(self.rtl / "results.json", {"complete": True, "board_verified": False, "results": results,
                   "selections": [selection], "all_searches_have_rtl_finalist": True})
        self.rows = [{**job, "audited": True, "testbench_sha256": "fixture-tb", "summary": summary}]

    def test_pass_reports_and_frozen_inputs_are_checked(self):
        result = audit_rtl(self.root, self.rows, self.plan)
        self.assertEqual(result["passed"], 2)
        self.assertEqual(result["rejected"], 0)
        self.assertEqual(self.rows[0]["rtl"]["best_rtl_id"], "p1-u1-a1")

    def test_fail_report_cannot_be_relabelled_pass(self):
        next((self.rtl / "matmul/p1-u1-a1").glob("*/autohls_project/solution1/sim/report/*_cosim.rpt")).write_text("fixture | Verilog | Fail |")
        with self.assertRaisesRegex(ValueError, "original RTL Pass"):
            audit_rtl(self.root, self.rows, self.plan)

    def test_rtl_command_cannot_use_a_different_source(self):
        tcl = next((self.rtl / "matmul/p1-u1-a1").glob("*/run_hls.tcl"))
        tcl.write_text(tcl.read_text().replace("/p1-u1-a1/kernel.cpp", "/p0-u1-a1/kernel.cpp"))
        with self.assertRaisesRegex(ValueError, "RTL commands"):
            audit_rtl(self.root, self.rows, self.plan)

    def test_wrong_source_and_missing_input_hashes_are_rejected(self):
        path = self.rtl / "results.json"
        value = read(path)
        value["results"][1]["source_sha256"] = "other-source"
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "differs from observed"):
            audit_rtl(self.root, self.rows, self.plan)
        value["results"][1]["source_sha256"] = "fixture-source-p1-u1-a1"
        value["selections"][0]["input_hashes"] = {}
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "Missing frozen"):
            audit_rtl(self.root, self.rows, self.plan)

    def test_hls_winner_is_not_rewritten_by_rtl_results(self):
        path = self.rtl / "results.json"
        value = read(path)
        value["selections"][0]["hls_best_id"] = "p0-u1-a1"
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "HLS best was rewritten"):
            audit_rtl(self.root, self.rows, self.plan)

    def reject_winner(self):
        path = self.rtl / "results.json"
        value = read(path)
        winner = value["results"][1]
        winner.update(status="verification_rejected", error="fixture tool exit code 1")
        winner.pop("synthesis")
        winner.pop("report_sha256")
        value["selections"][0].update(best_rtl_id="p0-u1-a1", rejected_ids=["p1-u1-a1"],
                                     best_metrics=value["results"][0]["expected_metrics"])
        write_json(path, value)

    def test_pass_table_cannot_override_failed_c_postcheck(self):
        log = next((self.rtl / "matmul/p1-u1-a1").glob("*/vitis.stdout.log"))
        log.write_text(log.read_text() + "FAIL test=1 pixel=0\nERROR: [COSIM 212-361] C TB post check failed\n")
        with self.assertRaisesRegex(ValueError, "RTL final log"):
            audit_rtl(self.root, self.rows, self.plan)

    def test_rejected_candidate_requires_raw_failure_and_matching_tcl(self):
        self.reject_winner()
        with self.assertRaisesRegex(ValueError, "rejected RTL failure"):
            audit_rtl(self.root, self.rows, self.plan)
        log = next((self.rtl / "matmul/p1-u1-a1").glob("*/vitis.stdout.log"))
        log.write_text("Vivado HLS 2019.1\nFAIL test=1 pixel=0\nERROR: [COSIM 212-361] C TB post check failed\n")
        self.assertEqual(audit_rtl(self.root, self.rows, self.plan)["rejected"], 1)
        tcl = log.with_name("run_hls.tcl")
        tcl.write_text(tcl.read_text().replace("/p1-u1-a1/kernel.cpp", "/p0-u1-a1/kernel.cpp"))
        with self.assertRaisesRegex(ValueError, "RTL commands"):
            audit_rtl(self.root, self.rows, self.plan)
