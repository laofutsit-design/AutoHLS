"""Execute the frozen 12-call order diagnostic on the existing cloud host only."""
import json
from datetime import datetime, timezone
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

from autohls.experiments import file_hash, write_json
from autohls.planner import inspect_ollama
from scripts.prepare_order_probe import digest, require


PLAN_SHA256 = "8464c8561d15bccb4797ccf82686a98d872256565139dba4f93cdbad74f4ea66"


def decode_choice(raw, payload):
    require(len(raw) <= 2_000_000, "Response exceeds evidence size limit")
    response = json.loads(raw)
    require(response.get("done") is True and response.get("done_reason") != "length", "Incomplete response")
    content = response["message"]["content"].strip()
    lines = content.splitlines()
    if len(lines) >= 3 and lines[0] == "```json" and lines[-1] == "```":
        content = "\n".join(lines[1:-1])
    choice = json.loads(content)
    require(isinstance(choice, dict) and set(choice) == {"id", "reason"} and isinstance(choice["reason"], str), "Invalid response object")
    require(choice["id"] in payload["format"]["properties"]["id"]["enum"], "ID outside remaining candidates")
    return response, choice


def send_request(raw):
    request = Request("http://127.0.0.1:11434/api/chat", raw, {"Content-Type": "application/json"})
    with urlopen(request, timeout=600) as response:
        return response.read(2_000_001)


def run_probe(root, plan, output, call, check_model):
    # No retries or resumes. Preserve partial failures in a new output directory.
    output.mkdir(parents=True, exist_ok=False)
    state = {"complete": False, "stopped": False, "jobs": [], "hls_executed": False, "board_verified": False,
             "started_at": datetime.now(timezone.utc).isoformat()}
    write_json(output / "status.json", state)
    for job in plan["jobs"]:
        if (output / "STOP").exists():
            state["stopped"] = True
            break
        identity = check_model()
        require(identity["model"]["digest"] == plan["model_digest"] and identity["ollama_version"] == plan["ollama_version"], "Model identity changed")
        source = (root / job["request"]).resolve()
        require(source.is_relative_to(root.resolve()), "Request outside release")
        raw = source.read_bytes()
        require(digest(raw) == job["request_sha256"], "Request changed before call")
        payload = json.loads(raw)
        directory = output / job["id"]
        directory.mkdir()
        (directory / "request.json").write_bytes(raw)
        item = {**job, "status": "interrupted", "identity": identity,
                "started_at": datetime.now(timezone.utc).isoformat()}
        state["active_job"] = job["id"]
        write_json(output / "status.json", state)
        started = time.monotonic()
        print("START", job["id"], flush=True)
        try:
            response_raw = call(raw)
            (directory / "response.raw.log").write_bytes(response_raw)
            response, choice = decode_choice(response_raw, payload)
            write_json(directory / "response.json", response)
            item.update(status="valid", selected_id=choice["id"],
                        candidate_position=payload["format"]["properties"]["id"]["enum"].index(choice["id"]) + 1)
        except Exception as exc:
            item.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            item["wall_seconds"] = round(time.monotonic() - started, 6)
            write_json(directory / "trace.json", item)
            state["jobs"].append(item)
            state["active_job"] = None
            write_json(output / "status.json", state)
        print("END", job["id"], item["status"], item.get("selected_id"), flush=True)
    state["complete"] = len(state["jobs"]) == len(plan["jobs"])
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(output / "status.json", state)
    return state


def main():
    root = Path(__file__).resolve().parents[1]
    require(sys.platform == "linux", "Run only on the existing Linux cloud host")
    require(file_hash(root / "probe-plan.json") == PLAN_SHA256, "Unexpected frozen probe plan")
    for name, expected in json.loads((root / "source-checksums.json").read_text()).items():
        require(file_hash(root / name) == expected, "Frozen source changed: " + name)
    plan = json.loads((root / "probe-plan.json").read_text())
    require(plan == json.loads((root / "suite-protocol.json").read_text()), "Deployment protocol differs")
    for job in plan["jobs"]:
        require(file_hash(root / job["request"]) == job["request_sha256"], "Request checksum differs")
    output = root / "artifacts/order-probe"
    if output.exists():
        raise FileExistsError("Probe already exists; do not retry or overwrite")
    with socket.socket() as probe:
        require(probe.connect_ex(("127.0.0.1", 11434)) != 0, "Existing model service must be inspected first")
    output.parent.mkdir(exist_ok=True)
    with (output.parent / "order-probe-server.log").open("x") as log:
        server = subprocess.Popen(["bash", str(root / "scripts/cloud-model-serve.sh")], cwd=root, stdout=log, stderr=subprocess.STDOUT)
        server_record = {"pid": server.pid, "listen": "127.0.0.1:11434", "stopped": False}
        write_json(output.parent / "order-probe-server.json", server_record)
        try:
            for _ in range(30):
                require(server.poll() is None, "Owned model service exited at startup")
                try:
                    identity = inspect_ollama(plan["model"])
                    require(identity["model"]["digest"] == plan["model_digest"] and identity["ollama_version"] == plan["ollama_version"], "Unexpected installed model")
                    break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError("Model service startup timeout")
            state = run_probe(root, plan, output, send_request, lambda: inspect_ollama(plan["model"]))
            if not state["complete"] or any(j["status"] != "valid" for j in state["jobs"]):
                sys.exit(1)
        finally:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server_record["forced_stop"] = True
                server.kill()
                server.wait(timeout=10)
            write_json(output.parent / "order-probe-server.json", {**server_record, "stopped": True, "returncode": server.returncode})


if __name__ == "__main__":
    main()
