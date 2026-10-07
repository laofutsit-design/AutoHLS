"""Four predeclared old/new failure-feedback comparisons on the development kernel."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from autohls.experiments import file_hash, write_json
from autohls.planner import inspect_ollama
from autohls.vitis import detect_vitis
from scripts.audit_model_suite import read, require
from scripts.model_suite import protocol as suite_protocol, command

ROOT = Path(__file__).resolve().parents[1]


def protocol():
    plan = suite_protocol()
    plan["jobs"] = [{"id": f"conv2d-{arm}-r{repeat}", "benchmark": "conv2d", "group": "feedback",
                     "arm": arm, "seed": 0, "repeat": repeat}
                    for arm, repeat in (("legacy", 0), ("fixed", 0), ("fixed", 1), ("legacy", 1))]
    plan["experiment"] = "failure-diagnostics-development-pilot-v1"
    plan["rtl_policy"] = "fresh baseline and ranked finalists; deduplicate identical source/testbench; preserve rejections"
    return plan


def run_jobs(output, plan, execute=subprocess.run):
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "protocol.json", plan)
    state = {"complete": False, "stopped": False, "jobs": [], "board_verified": False}
    started = time.monotonic()
    for job in plan["jobs"]:
        if (output / "STOP").exists():
            state["stopped"] = True
            break
        state["active_job"] = job["id"]
        write_json(output / "status.json", state)
        print("START", job["id"], flush=True)
        job_root = output / job["id"]
        cwd = ROOT / "legacy" if job["arm"] == "legacy" else ROOT
        args = command(job, job_root, plan)
        args[args.index("--limits") + 1] = str(ROOT / "scripts/preboard-limits.json")
        with (output / (job["id"] + ".log")).open("x") as log:
            result = execute(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
        manifests = list(job_root.glob("*/manifest.json"))
        entry = {**job, "exit_code": result.returncode,
                 "run": manifests[0].parent.relative_to(output).as_posix() if len(manifests) == 1 else None}
        if entry["run"] and (output / entry["run"] / "summary.json").is_file():
            entry["summary"] = read(output / entry["run"] / "summary.json")
        state["jobs"].append(entry)
        state.update(active_job=None, wall_seconds=round(time.monotonic() - started, 3))
        write_json(output / "status.json", state)
        print("END", job["id"], "exit", result.returncode, flush=True)
    state["complete"] = len(state["jobs"]) == len(plan["jobs"])
    state["all_budgets_completed"] = state["complete"] and all(
        job["exit_code"] == 0 and job.get("summary", {}).get("completed_budget") for job in state["jobs"])
    write_json(output / "status.json", state)
    return state


def main():
    os.chdir(ROOT)
    os.environ["PATH"] = str(ROOT / "scripts/cloud-bin") + os.pathsep + os.environ["PATH"]
    frozen = read(ROOT / "source-checksums.json")
    require(all(file_hash(ROOT / name) == digest for name, digest in frozen.items()), "Frozen source changed")
    require(read(ROOT / "diagnostics-protocol.json") == protocol(), "Frozen protocol changed")
    require(read(ROOT / "scripts/preboard-limits.json") == protocol()["limits"], "Resource limits changed")
    require(detect_vitis()["available"], "HLS wrapper not executable or not on PATH; stop before any job")
    for name in (ROOT / "autohls").glob("*.py"):
        if name.name != "experiments.py":
            require(file_hash(name) == file_hash(ROOT / "legacy/autohls" / name.name), "Unplanned engine difference")
    output = ROOT / "artifacts/model-suite"
    require(not output.exists(), "Pilot already exists; do not overwrite or silently resume")
    with socket.socket() as probe:
        require(probe.connect_ex(("127.0.0.1", 11434)) != 0, "Model port already occupied")
    output.parent.mkdir(exist_ok=True)
    with (output.parent / "model-server.log").open("x") as log:
        server = subprocess.Popen(["bash", "scripts/cloud-model-serve.sh"], stdout=log, stderr=subprocess.STDOUT)
        metadata = {"pid": server.pid, "listen": "127.0.0.1:11434", "stopped": False}
        write_json(output.parent / "model-server.json", metadata)
        try:
            for _ in range(30):
                require(server.poll() is None, "Model server exited")
                try:
                    identity = inspect_ollama(protocol()["model"])
                    break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError("Model server startup timeout")
            require(identity["model"]["digest"] == protocol()["model_digest"]
                    and identity["ollama_version"] == protocol()["ollama_version"], "Model identity changed")
            state = run_jobs(output, protocol())
            require(state["all_budgets_completed"], "Pilot did not complete every budget; preserve failed evidence")
        finally:
            server.terminate()
            server.wait(timeout=30)
            metadata.update(stopped=True, returncode=server.returncode)
            write_json(output.parent / "model-server.json", metadata)


if __name__ == "__main__":
    main()
