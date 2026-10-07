"""Prepare an offline, fixed-state candidate-order probe; never call a model/HLS.

Usage: python -m scripts.prepare_order_probe AUDIT_JSON EVIDENCE_ROOT NEW_DIR
"""
import copy
import hashlib
import json
from pathlib import Path
import random
import sys


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def permute_request(payload, seed):
    result = copy.deepcopy(payload)
    require(len(result["messages"]) == 1 and result["messages"][0]["role"] == "user", "Expected one user prompt")
    prompt = json.loads(result["messages"][0]["content"])
    candidates = prompt["allowed_candidates"]
    ids = [item["id"] for item in candidates]
    require(len(ids) >= 2 and len(set(ids)) == len(ids), "Candidates must be unique")
    require(result["format"]["properties"]["id"]["enum"] == ids, "Schema order differs from candidates")
    random.Random(seed).shuffle(candidates)
    result["format"]["properties"]["id"]["enum"] = [item["id"] for item in candidates]
    result["messages"][0]["content"] = json.dumps(prompt, ensure_ascii=False)
    return result


def prepare(audit_path, root, destination):
    audit_bytes = audit_path.read_bytes()
    audit = json.loads(audit_bytes)
    require(audit["complete"] and audit["all_evidence_audited"] and (audit.get("rtl") or {}).get("complete"), "Use a completed audited suite")
    require(audit["protocol"]["budget"] == 16, "This probe is defined for the budget16 suite")
    checksums_bytes = (root / "export-checksums.json").read_bytes()
    checksums = json.loads(checksums_bytes)

    def checked(relative):
        path = (root / relative).resolve()
        require(path.is_relative_to(root.resolve()), "Evidence path escapes root")
        data = path.read_bytes()
        require(digest(data) == checksums[relative], "Evidence checksum differs: " + relative)
        return data

    status = checked("artifacts/model-suite/status.json")
    require(digest(status) == audit["snapshot_status_sha256"], "Audit belongs to a different snapshot")
    files, states, jobs = {}, [], []
    for index, benchmark in enumerate(("matmul", "fir", "conv2d")):
        job_id = benchmark + "-feedback-s0-r0"
        rows = [r for r in audit["runs"] if r["id"] == job_id]
        require(len(rows) == 1 and rows[0]["audited"], "Missing audited source run")
        row = rows[0]
        prefix = "artifacts/model-suite/" + job_id + "/" + row["run"]
        raw = checked(prefix + "/proposal-003/request.json")
        payload = json.loads(raw)
        prompt = json.loads(payload["messages"][0]["content"])
        records = [json.loads(line) for line in checked(prefix + "/history.jsonl").decode().splitlines()]
        require([r["id"] for r in records] == row["order"], "Audited candidate order differs")
        expected = [{"id": r["id"], "status": r["status"], "metrics": r.get("metrics"),
                     "error": (r.get("error") or "")[:600],
                     "diagnostics": [line[:240] for line in r.get("diagnostics", [])[:3]]} for r in records[:3]]
        require(len(expected) == 3 and prompt["observations"] == expected, "Feedback differs from fixed state")
        require(prompt["source"] == checked(prefix + "/p0-u1-a1/kernel.cpp").decode(), "Source differs from baseline")
        manifest = json.loads(checked(prefix + "/manifest.json"))
        require(payload["model"] == audit["protocol"]["model"] and payload["options"] == manifest["model_options"], "Model/options differ")
        require(manifest["model_identity"]["model"]["digest"] == audit["protocol"]["model_digest"], "Model digest differs")
        seed = 20260927 + index
        changed = permute_request(payload, seed)
        changed_bytes = json.dumps(changed, ensure_ascii=False, indent=2).encode()
        names = {"A": benchmark + "/original.json", "B": benchmark + "/permuted.json"}
        files[names["A"]], files[names["B"]] = raw, changed_bytes
        states.append({"benchmark": benchmark, "origin_job": job_id, "origin_request": prefix + "/proposal-003/request.json",
                       "origin_sha256": digest(raw), "observation_count": 3, "permutation_seed": seed,
                       "original_order": payload["format"]["properties"]["id"]["enum"],
                       "permuted_order": changed["format"]["properties"]["id"]["enum"]})
        for label in ("A1", "B1", "B2", "A2"):
            name = names[label[0]]
            jobs.append({"id": benchmark + "-" + label, "benchmark": benchmark, "condition": label[0],
                         "request": name, "request_sha256": digest(files[name])})
    plan = {"experiment": "fixed-state-candidate-order-probe-v1", "execution_status": "not_run",
            "audit_sha256": digest(audit_bytes), "export_manifest_sha256": digest(checksums_bytes),
            "preparer_sha256": digest(Path(__file__).read_bytes()), "model": audit["protocol"]["model"],
            "model_digest": audit["protocol"]["model_digest"], "ollama_version": audit["protocol"]["ollama_version"],
            "scope": "12 planned model calls, no HLS or board; same measured state per kernel; no adaptive feedback",
            "interpretation": "Sensitivity diagnostic only, not a new search or evidence of performance improvement",
            "states": states, "jobs": jobs}
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        path = destination / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    (destination / "probe-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


if __name__ == "__main__":
    result = prepare(*map(Path, sys.argv[1:]))
    print(json.dumps({"execution_status": result["execution_status"], "planned_calls": len(result["jobs"]), "states": len(result["states"])}))
