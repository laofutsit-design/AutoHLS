"""Offline request compatibility check against six frozen probe inputs.

The HTTP function always raises before network access. No model replies, HLS
metrics or board results are generated. Usage: python -m
scripts.check_order_compatibility PROBE_INPUT_DIR NEW_OUTPUT_DIR
"""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

from autohls.benchmarks import Configuration
from autohls.planner import propose_ollama
from scripts.prepare_order_probe import require


def check(inputs, output):
    plan = json.loads((inputs / "probe-plan.json").read_bytes())
    for job in plan["jobs"]:
        require(hashlib.sha256((inputs / job["request"]).read_bytes()).hexdigest() == job["request_sha256"], "Frozen input changed")
    output.mkdir(parents=True, exist_ok=False)
    checks = []
    for state in plan["states"]:
        benchmark = state["benchmark"]
        original = json.loads((inputs / benchmark / "original.json").read_bytes())
        prompt = json.loads(original["messages"][0]["content"])
        configs = [Configuration(c["pipeline_ii"], c["unroll"], c["partition"]) for c in prompt["allowed_candidates"]]
        for condition, order_seed, name in (("A", None, "original.json"), ("B", state["permutation_seed"], "permuted.json")):
            directory = output / (benchmark + "-" + condition)
            with patch("autohls.planner.urlopen", side_effect=RuntimeError("OFFLINE: network deliberately disabled")) as request:
                try:
                    propose_ollama(configs, prompt["source"], prompt["observations"], original["model"], directory,
                                   prompt["goal"], prompt["resource_limits"], seed=original["options"]["seed"],
                                   target=prompt["target"], candidate_order_seed=order_seed)
                except RuntimeError as exc:
                    require(str(exc) == "OFFLINE: network deliberately disabled", "Unexpected failure")
                request.assert_called_once()
            generated = (directory / "request.json").read_bytes()
            expected = (inputs / benchmark / name).read_bytes()
            # write_text uses native Windows newlines; the HTTP serialization
            # does not read that file. Check both contracts explicitly.
            require(generated.replace(b"\r\n", b"\n") == expected, "LF-normalized request differs from frozen " + benchmark + "-" + condition)
            wire = request.call_args.args[0].data
            expected_wire = json.dumps(json.loads(expected)).encode()
            require(wire == expected_wire, "HTTP request bytes differ")
            require(not (directory / "response.raw.log").exists(), "Unexpected model reply")
            checks.append({"id": benchmark + "-" + condition, "native_file_byte_identical": generated == expected,
                           "lf_normalized_byte_identical": True, "http_bytes_identical": True,
                           "request_sha256": hashlib.sha256(generated).hexdigest(),
                           "http_request_sha256": hashlib.sha256(wire).hexdigest()})
    result = {"scope": "offline request generation only; all HTTP calls intercepted before network",
              "model_calls": 0, "hls_evaluations": 0, "board_verified": False, "checks": checks,
              "planner_sha256": hashlib.sha256(Path(sys.modules[propose_ollama.__module__].__file__).read_bytes()).hexdigest()}
    (output / "checks.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(check(*map(Path, sys.argv[1:]))))
