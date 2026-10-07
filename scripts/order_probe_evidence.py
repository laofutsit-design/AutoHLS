"""Export and independently reconstruct the fixed-state order diagnostic.

Usage: python -m scripts.order_probe_evidence export ROOT NEW_ZIP
       python -m scripts.order_probe_evidence audit ROOT NEW_OUTPUT_DIR
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import zipfile


PLAN_SHA256 = "8464c8561d15bccb4797ccf82686a98d872256565139dba4f93cdbad74f4ea66"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_bytes())


def inside(root, name):
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()), "Evidence path escapes root")
    return path


def export(root, destination):
    require(read(root / "artifacts/order-probe/status.json")["complete"], "Do not export active output")
    require(read(root / "artifacts/order-probe-server.json")["stopped"], "Model service not stopped")
    names = set(read(root / "source-checksums.json"))
    names.update(("source-checksums.json", "probe-plan.json", "artifacts/order-probe-server.json",
                  "artifacts/order-probe-server.log", "artifacts/order-probe-launch.log"))
    names.update(j["request"] for j in read(root / "probe-plan.json")["jobs"])
    names.update(p.relative_to(root).as_posix() for p in (root / "artifacts/order-probe").rglob("*") if p.is_file())
    checksums = {}
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            data = inside(root, name).read_bytes()
            archive.writestr(name, data)
            checksums[name] = sha(data)
        archive.writestr("export-checksums.json", json.dumps(checksums, indent=2))
    return {"archive": str(destination), "files": len(checksums), "sha256": sha(destination.read_bytes())}


def compare_payloads(original, permuted):
    restored = copy.deepcopy(permuted)
    a, b = (json.loads(p["messages"][0]["content"]) for p in (original, restored))
    ids = [c["id"] for c in a["allowed_candidates"]]
    changed_ids = [c["id"] for c in b["allowed_candidates"]]
    require(len(ids) == len(set(ids)) and sorted(ids) == sorted(changed_ids) and ids != changed_ids, "Not a candidate permutation")
    require(original["format"]["properties"]["id"]["enum"] == ids and
            restored["format"]["properties"]["id"]["enum"] == changed_ids, "Schema/candidate order differs")
    b["allowed_candidates"].sort(key=lambda c: ids.index(c["id"]))
    require(a == b, "Prompt differs beyond ordering")
    restored["messages"][0]["content"] = original["messages"][0]["content"]
    restored["format"]["properties"]["id"]["enum"] = ids
    require(original == restored, "Payload differs beyond ordering")


def reconstruct(root, plan, status):
    require(len(plan["jobs"]) == 12 and len(status["jobs"]) <= 12, "Unexpected request count")
    rows = []
    for index, job in enumerate(plan["jobs"]):
        row = {**job, "status": "not_run", "selected_id": None, "candidate_position": None, "wall_seconds": None}
        if index < len(status["jobs"]):
            recorded = status["jobs"][index]
            require(all(recorded[k] == v for k, v in job.items()), "Executed order differs from plan")
            directory = root / "artifacts/order-probe" / job["id"]
            require(read(directory / "trace.json") == recorded, "Trace/status mismatch")
            raw_request = (directory / "request.json").read_bytes()
            require(sha(raw_request) == job["request_sha256"], "Executed request changed")
            identity = recorded["identity"]
            require(identity["model"]["digest"] == plan["model_digest"] and identity["ollama_version"] == plan["ollama_version"], "Wrong model identity")
            row.update(status=recorded["status"], wall_seconds=recorded["wall_seconds"])
            require(recorded["wall_seconds"] >= 0, "Negative duration")
            if recorded["status"] == "valid":
                raw = (directory / "response.raw.log").read_bytes()
                response = json.loads(raw)
                require(len(raw) <= 2_000_000 and response["done"] is True and response.get("done_reason") != "length", "Incomplete raw reply")
                require(response["model"] == plan["model"], "Reply model differs from plan")
                require(response == read(directory / "response.json"), "Parsed response differs from raw")
                text = response["message"]["content"].strip()
                lines = text.splitlines()
                if len(lines) >= 3 and lines[0] == "```json" and lines[-1] == "```":
                    text = "\n".join(lines[1:-1])
                choice = json.loads(text)
                require(set(choice) == {"id", "reason"} and isinstance(choice["reason"], str), "Invalid raw choice")
                ids = json.loads(raw_request)["format"]["properties"]["id"]["enum"]
                require(choice["id"] in ids, "Choice outside candidates")
                position = ids.index(choice["id"]) + 1
                require((recorded["selected_id"], recorded["candidate_position"]) == (choice["id"], position), "Recorded choice differs")
                row.update(selected_id=choice["id"], candidate_position=position)
            else:
                require(recorded["status"] in {"failed", "interrupted"}, "Unknown request status")
        rows.append(row)
    require(status["complete"] == (len(status["jobs"]) == 12), "Incorrect completion flag")
    return rows


def audit(root, destination):
    manifest = (root / "export-checksums.json").read_bytes()
    checksums = json.loads(manifest)
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    require(actual == set(checksums) | {"export-checksums.json"}, "Unlisted or missing evidence files")
    for name, expected in checksums.items():
        require(sha(inside(root, name).read_bytes()) == expected, "Export changed: " + name)
    for name, expected in read(root / "source-checksums.json").items():
        require(checksums.get(name) == expected, "Frozen source changed: " + name)
    require(sha((root / "probe-plan.json").read_bytes()) == PLAN_SHA256, "Wrong fixed plan")
    plan = read(root / "probe-plan.json")
    require(plan == read(root / "suite-protocol.json"), "Deployment protocol differs")
    for job in plan["jobs"]:
        require(checksums.get(job["request"]) == job["request_sha256"], "Planned request changed")
    for state in plan["states"]:
        benchmark = state["benchmark"]
        compare_payloads(read(root / benchmark / "original.json"), read(root / benchmark / "permuted.json"))
    status = read(root / "artifacts/order-probe/status.json")
    require(status["hls_executed"] is False and status["board_verified"] is False, "Unexpected scope label")
    server = read(root / "artifacts/order-probe-server.json")
    require(server["stopped"] and server["listen"] == "127.0.0.1:11434", "Service not stopped/loopback")
    rows = reconstruct(root, plan, status)
    groups = []
    for benchmark in ("matmul", "fir", "conv2d"):
        choices = {condition: [r["selected_id"] for r in rows if r["benchmark"] == benchmark and r["condition"] == condition] for condition in ("A", "B")}
        stable = {c: len(values) == 2 and None not in values and len(set(values)) == 1 for c, values in choices.items()}
        groups.append({"benchmark": benchmark, "choices": choices, "stable": stable,
                       "stable_between_condition_difference": all(stable.values()) and choices["A"][0] != choices["B"][0]})
    result = {"complete": status["complete"], "export_manifest_sha256": sha(manifest), "plan_sha256": PLAN_SHA256,
              "verified_files": len(checksums), "valid_calls": sum(r["status"] == "valid" for r in rows),
              "request_wall_seconds": sum(r["wall_seconds"] or 0 for r in rows), "jobs": rows, "groups": groups,
              "hls_executed": False, "board_verified": False, "model_service_stopped": server["stopped"]}
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    lines = ["# Fixed-state candidate-order diagnostic", "", f"Plan SHA-256: `{PLAN_SHA256}`", "",
             "All 12 planned calls are listed. These are fixed-state replies, not a new search or HLS measurements.", "",
             "| Call | Status | Selected ID | Presented position | Wall seconds |", "|---|---|---|---:|---:|"]
    lines += [f"| {r['id']} | {r['status']} | {r['selected_id']} | {r['candidate_position']} | {r['wall_seconds']} |" for r in rows]
    lines += ["", "| Kernel | A repeats | B repeats | A stable | B stable | Stable cross-condition difference |", "|---|---|---|---|---|---|"]
    lines += [f"| {g['benchmark']} | {', '.join(str(v) for v in g['choices']['A'])} | {', '.join(str(v) for v in g['choices']['B'])} | {g['stable']['A']} | {g['stable']['B']} | {g['stable_between_condition_difference']} |" for g in groups]
    lines += ["", "Two repetitions per condition are not independent random samples. List and schema order changed together; this cannot isolate either one. No performance, resource, RTL, board, or generalization claim follows. Wall times are operational records, not an isolated cache/cold-start comparison.", ""]
    (destination / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return result


if __name__ == "__main__":
    action, root, destination = sys.argv[1:]
    require(action in {"export", "audit"}, "Choose export or audit")
    print(json.dumps((export if action == "export" else audit)(Path(root), Path(destination))))
