"""Fixed 27-job comparison; each job performs fresh native/C/HLS evaluations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from autohls.experiments import file_hash, write_json
from autohls.planner import inspect_ollama
from autohls.vitis import detect_vitis

ROOT = Path(__file__).resolve().parents[1]
MODEL = "autohls-qwen-coder:7b-q4km-ms9bc02b77"
DIGEST = "d589b66e0bb670ee449736b6a327f14aaac1ea29860a89aa39f3967774d1c987"


def protocol():
    jobs = []
    for benchmark in ("matmul", "fir", "conv2d"):
        # Reverse model order on the repeat; repeats are not independent seeds.
        groups = [("random", 0, 0), ("no-feedback", 0, 0), ("feedback", 0, 0),
                  ("feedback", 0, 1), ("no-feedback", 0, 1)]
        groups += [("random", seed, 0) for seed in range(1, 5)]
        for group, seed, repeat in groups:
            jobs.append({"id": f"{benchmark}-{group}-s{seed}-r{repeat}",
                         "benchmark": benchmark, "group": group, "seed": seed, "repeat": repeat})
    return {"schema_version": 1, "budget": 8, "goal": "latency", "clock_ns": 10,
            "limits": {"lut": 40000, "ff": 80000, "dsp": 180, "bram": 200},
            "part": "xc7z020clg400-1", "model": MODEL, "model_digest": DIGEST,
            "ollama_version": "0.34.1", "jobs": jobs, "board_access": False,
            "retries": 0, "cosim_during_search": False,
            "model_repeats_are_independent_samples": False}


def command(job, output, plan):
    args = [sys.executable, "-m", "autohls", "research", job["benchmark"], "--backend", "hls",
            "--planner", "random" if job["group"] == "random" else "ollama",
            "--budget", str(plan["budget"]), "--goal", plan["goal"], "--seed", str(job["seed"]),
            "--clock", str(plan["clock_ns"]), "--limits", "scripts/preboard-limits.json",
            "--output-dir", str(output)]
    if job["group"] != "random":
        args += ["--model", plan["model"]]
    if job["group"] == "no-feedback":
        args += ["--no-feedback"]
    if job.get("candidate_order_seed") is not None:
        args += ["--candidate-order-seed", str(job["candidate_order_seed"])]
    if "coverage_policy" in job:
        args += ["--coverage-policy", job["coverage_policy"]]
    return args


def run_jobs(output, plan, execute=subprocess.run, check_model=None):
    # One invocation only. Interrupted suites are preserved, not silently resumed.
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "protocol.json", plan)
    (output / "runner.pid").write_text(str(os.getpid()) + "\n")
    state = {"complete": False, "stopped": False, "jobs": [], "board_verified": False}
    started = time.monotonic()
    write_json(output / "status.json", state)
    for job in plan["jobs"]:
        if (output / "STOP").exists():
            state["stopped"] = True
            break
        if check_model:
            check_model()
        state["active_job"] = job["id"]
        write_json(output / "status.json", state)
        print("START", job["id"], flush=True)
        job_root = output / job["id"]
        with (output / (job["id"] + ".log")).open("x", encoding="utf-8") as log:
            result = execute(command(job, job_root, plan), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        runs = sorted(job_root.glob("*/manifest.json"))
        entry = {**job, "exit_code": result.returncode,
                 "run": runs[0].parent.relative_to(output).as_posix() if len(runs) == 1 else None}
        if entry["run"] and (output / entry["run"] / "summary.json").is_file():
            entry["summary"] = json.loads((output / entry["run"] / "summary.json").read_text())
        state["jobs"].append(entry)
        state["active_job"] = None
        state["wall_seconds"] = round(time.monotonic() - started, 3)
        write_json(output / "status.json", state)
        print("END", job["id"], "exit", result.returncode, flush=True)
    state["complete"] = len(state["jobs"]) == len(plan["jobs"])
    write_json(output / "status.json", state)
    return state


def main(expected_plan=None):
    os.chdir(ROOT)
    os.environ["PATH"] = str(ROOT / "scripts/cloud-bin") + os.pathsep + os.environ["PATH"]
    for name, expected in json.loads((ROOT / "source-checksums.json").read_text()).items():
        if file_hash(ROOT / name) != expected:
            raise ValueError("Frozen source changed: " + name)
    plan = json.loads((ROOT / "suite-protocol.json").read_text())
    if plan != (protocol() if expected_plan is None else expected_plan) or plan["limits"] != json.loads((ROOT / "scripts/preboard-limits.json").read_text()):
        raise ValueError("Frozen protocol mismatch")
    if not detect_vitis()["available"]:
        raise RuntimeError("HLS wrapper unavailable; stop before starting a model or evaluating a candidate")
    output = ROOT / "artifacts/model-suite"
    if output.exists():
        raise FileExistsError("Suite already exists; preserve it and do not repeat")

    def check_model():
        identity = inspect_ollama(plan["model"])
        if identity["model"]["digest"] != plan["model_digest"] or identity["ollama_version"] != plan["ollama_version"]:
            raise ValueError("Model identity mismatch; stop the suite")

    # Owned child only, no boot service or public port. Refuse a pre-existing server.
    import socket
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 11434)) == 0:
            raise RuntimeError("Port 11434 already occupied; inspect existing service first")
    (ROOT / "artifacts").mkdir(exist_ok=True)
    with (ROOT / "artifacts/model-server.log").open("x") as log:
        server = subprocess.Popen(["bash", "scripts/cloud-model-serve.sh"], stdout=log, stderr=subprocess.STDOUT)
        write_json(ROOT / "artifacts/model-server.json", {"pid": server.pid, "listen": "127.0.0.1:11434",
                   "model": plan["model"], "stopped": False})
        try:
            for _ in range(30):
                if server.poll() is not None:
                    raise RuntimeError("Model server exited")
                try:
                    check_model()
                    break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError("Model server startup timeout")
            state = run_jobs(output, plan, check_model=check_model)
            if not state["complete"] or any(j["exit_code"] != 0 or not j.get("summary", {}).get("completed_budget") for j in state["jobs"]):
                sys.exit(1)
        finally:
            server.terminate()
            server.wait(timeout=30)
            write_json(ROOT / "artifacts/model-server.json", {"pid": server.pid, "listen": "127.0.0.1:11434",
                       "model": plan["model"], "stopped": True, "returncode": server.returncode})


if __name__ == "__main__":
    main()
