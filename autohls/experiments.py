"""Evidence-producing experiments, independent of the synthetic UI demo."""

from datetime import datetime, timezone
from pathlib import Path
import csv
import hashlib
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
import uuid

from .benchmarks import BENCHMARKS, VALIDATION_BENCHMARKS, Configuration, configurations, config_record, render_candidate
from .core import DEVICE, GOAL_LABELS, pareto_frontier
from .planner import COVERAGE_POLICY, coverage_candidates, inspect_ollama, model_options, propose_ollama
from .verification import find_compiler, verify_native
from .vitis import detect_vitis, run_synthesis


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    # Keep the previous complete snapshot until the replacement is ready.
    rendered = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    pending = path.with_name(path.name + ".pending-" + uuid.uuid4().hex)
    with pending.open("x", encoding="utf-8") as handle:
        handle.write(rendered)
    for attempt in range(5):
        try:
            os.replace(pending, path)
            return
        except PermissionError as exc:
            if attempt == 4:
                raise PermissionError(f"Cannot replace {path}; complete JSON retained at {pending}") from exc
            time.sleep(0.1)  # Retry only the file replacement, never model/HLS work.


def eligible(record: dict, limits: dict) -> bool:
    """Unknown/failed metrics, unmet timing and over-budget resources cannot rank."""
    if record.get("status") != "synthesized" or not record.get("verification", {}).get("passed"):
        return False
    if not record.get("synthesis", {}).get("csim_passed"):
        return False
    metrics = record.get("metrics", {})
    latency = metrics.get("latency_us")
    if not isinstance(latency, (float, int)) or not math.isfinite(latency) or latency <= 0 or metrics.get("timing_met") is not True:
        return False
    for key, limit in limits.items():
        amount = metrics.get("resources", {}).get(key)
        if not isinstance(amount, (int, float)) or not math.isfinite(amount) or not 0 <= amount <= limit:
            return False
    return True


def summarize(records: list[dict], limits: dict, goal: str) -> dict:
    feasible = [item for item in records if eligible(item, limits)]
    frontier = pareto_frontier(feasible)

    def score(item: dict) -> tuple:
        metrics = item["metrics"]
        latency = metrics["latency_us"]
        area = max(metrics["resources"][key] / limit for key, limit in limits.items())
        if goal == "latency":
            return latency, area, item["id"]
        if goal == "resource":
            return area, latency, item["id"]
        return latency * area, latency, item["id"]

    best = min((item for item in feasible if item["id"] in frontier), key=score) if feasible else None
    baseline = next((item for item in feasible if item["id"] == Configuration().key), None)
    return {
        "evaluated": len(records),
        "correctness_passed": sum(bool(item.get("verification", {}).get("passed")) for item in records),
        "feasible_synthesized": len(feasible), "pareto_ids": sorted(frontier),
        "best_id": best["id"] if best else None,
        "hls_speedup": baseline["metrics"]["latency_us"] / best["metrics"]["latency_us"] if best and baseline else None,
        "best_rtl_verified": bool(best and best["synthesis"]["cosim_passed"]),
        "board_verified": False,
    }


def run_experiment(benchmark_name: str, output_root: str | Path = "artifacts/research", *,
                   backend: str = "native", planner: str = "exhaustive", budget: int = 8,
                   goal: str = "balanced", seed: int = 0, model: str = "", feedback: bool = True,
                   clock_ns: float = DEVICE["clock_ns"], cosim: bool = False, limits: dict | None = None,
                   candidate_order_seed: int | None = None, coverage_policy: str = "none") -> Path:
    reviewed = {**BENCHMARKS, **VALIDATION_BENCHMARKS}
    if benchmark_name not in reviewed or backend not in {"native", "hls"} or planner not in {"exhaustive", "random", "ollama"}:
        raise ValueError("Unknown benchmark, backend or planner")
    if goal not in GOAL_LABELS or type(budget) is not int or not 1 <= budget <= len(configurations()):
        raise ValueError("Invalid goal or evaluation budget (1..30)")
    if not math.isfinite(clock_ns) or clock_ns <= 0:
        raise ValueError("Clock must be positive and finite")
    if cosim and backend != "hls":
        raise ValueError("RTL co-simulation requires --backend hls")
    if planner == "ollama" and (backend != "hls" or not model):
        raise ValueError("Model-guided hardware optimization requires --backend hls and --model")
    if candidate_order_seed is not None and (type(candidate_order_seed) is not int or planner != "ollama"):
        raise ValueError("Candidate order seed requires an integer and the ollama planner")
    if coverage_policy not in ("none", COVERAGE_POLICY):
        raise ValueError("Coverage policy must be none or pipeline-warmup-v1")
    coverage = coverage_policy != "none"
    if coverage and (planner not in {"random", "ollama"} or budget < 3 or candidate_order_seed is not None):
        raise ValueError("Coverage requires random/ollama, budget >= 3 and no presentation shuffle")
    limits = dict(DEVICE["capacity"] if limits is None else limits)
    if set(limits) != set(DEVICE["capacity"]) or any(type(v) is not int or v <= 0 or v > DEVICE["capacity"][k] for k, v in limits.items()):
        raise ValueError("Resource limits must be positive integers within PYNQ-Z2 capacity; BRAM uses 18K units")
    if not find_compiler():
        raise RuntimeError("C++ compiler unavailable. Start from an x64 Native Tools prompt or add GCC/Clang to PATH.")
    if backend == "hls" and not detect_vitis()["available"]:
        raise RuntimeError("Vivado/Vitis HLS unavailable. Hardware experiments cannot use synthetic fallback metrics.")
    # Fail before spending a synthesis budget if the installed model is unavailable.
    identity = inspect_ollama(model) if planner == "ollama" else None
    benchmark = reviewed[benchmark_name]
    run_dir = Path(output_root).resolve() / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + benchmark_name)
    run_dir.mkdir(parents=True, exist_ok=False)
    source = benchmark.source.read_text(encoding="utf-8")
    (run_dir / "baseline.cpp").write_text(source, encoding="utf-8")
    tb = run_dir / "testbench.cpp"
    tb.write_text(benchmark.testbench.read_text(encoding="utf-8"), encoding="utf-8")
    compiler = find_compiler()
    version_args = [compiler] if Path(compiler).name.lower() == "cl.exe" else [compiler, "--version"]
    version = subprocess.run(version_args, capture_output=True, text=True, errors="replace", timeout=10)
    manifest = {
        "schema_version": 1, "engine": "research", "backend": backend, "benchmark": benchmark_name,
        "planner": planner, "model": model or None, "feedback": feedback, "seed": seed, "budget": budget,
        "model_identity": identity, "model_options": model_options(seed) if identity else None,
        "candidate_order": {"policy": "canonical" if candidate_order_seed is None else "remaining-shuffle-v1",
                            "seed": candidate_order_seed,
                            "seed_rule": None if candidate_order_seed is None else "base + evaluation_index"},
        "feedback_policy": "metrics and up to 3 diagnostic lines of 240 characters; error up to 600 characters",
        "failure_diagnostics_policy": "candidate-local HLS errors first; known source paths shortened; raw logs preserved",
        "goal": goal, "device": DEVICE, "clock_ns": clock_ns, "limits": limits, "cosim_requested": cosim,
        "hls_tool": detect_vitis() if backend == "hls" else None,
        "python": sys.version, "platform": platform.platform(), "compiler": compiler,
        "compiler_version": version.stdout + version.stderr,
        "source_sha256": file_hash(benchmark.source), "testbench_sha256": file_hash(tb),
        "engine_sha256": {path.name: file_hash(path) for path in Path(__file__).parent.glob("*.py")},
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    if coverage:
        manifest["coverage"] = {"policy": coverage_policy, "baseline_first": True,
                                "warmup_pipeline_ii": [0, 1, 2],
                                "counting": "evaluation attempts including failures",
                                "after_warmup": "all remaining candidates"}
    write_json(run_dir / "manifest.json", manifest)
    remaining = configurations()
    rng = random.Random(seed)
    if planner == "random" and not coverage:
        rng.shuffle(remaining)
    records = []
    failure = None
    started = time.monotonic()
    for index in range(budget):
        try:
            allowed = coverage_candidates(remaining, index) if coverage else remaining
            if coverage:
                write_json(run_dir / f"selection-{index:03d}.json",
                           {"policy": coverage_policy, "iteration": index, "allowed_ids": [c.key for c in allowed]})
            if index == 0:
                config = Configuration()  # Every comparison pays for the same baseline.
            elif planner == "ollama":
                observations = [{"id": item["id"], "status": item["status"], "metrics": item.get("metrics"),
                                 "error": (item.get("error") or "")[:600],
                                 "diagnostics": [line[:240] for line in item.get("diagnostics", [])[:3]]} for item in records]
                target = {"part": DEVICE["part"], "clock_ns": clock_ns, "resource_units": DEVICE["resource_units"],
                          "transform": {"loop": benchmark.loop, "cyclic_partition_arrays": benchmark.arrays}}
                config = propose_ollama(allowed, source, observations, model, run_dir / f"proposal-{index:03d}",
                                        goal, limits, feedback, seed=seed, expected_identity=identity, target=target,
                                        candidate_order_seed=None if candidate_order_seed is None else candidate_order_seed + index)
            elif coverage:
                config = rng.choice(allowed)
            else:
                config = remaining[0]
            if coverage and config not in allowed:
                raise ValueError("Proposal outside the coverage candidate pool")
        except Exception as exc:
            failure = f"Proposal failed: {type(exc).__name__}: {exc}"
            write_json(run_dir / "planner-error.json", {"error": failure, "iteration": index})
            break
        remaining.remove(config)
        candidate_dir = run_dir / config.key
        candidate_dir.mkdir()
        candidate = candidate_dir / "kernel.cpp"
        candidate.write_text(render_candidate(benchmark, config), encoding="utf-8")
        record = {"iteration": index, **config_record(config), "status": "created", "source_sha256": file_hash(candidate)}
        evaluation_started = time.monotonic()
        try:
            record["verification"] = verify_native(candidate, tb, candidate_dir / "native")
            record["status"] = "verified" if record["verification"]["passed"] else "verification_failed"
            if record["verification"]["passed"] and backend == "hls":
                synthesis = run_synthesis(candidate, benchmark_name, candidate_dir / "hls", part=DEVICE["part"],
                                          clock_ns=clock_ns, testbench=tb, cosim=cosim)
                record.update(status="synthesized", synthesis=synthesis, metrics=synthesis["metrics"])
                report = Path(synthesis["report"])
                record["report_sha256"] = file_hash(report)
                log = (Path(synthesis["run_dir"]) / "vitis.stdout.log").read_text(encoding="utf-8")
                record["diagnostics"] = [line for line in log.splitlines() if any(token in line.lower() for token in ("warning", "violation", "unable", "cannot"))][:40]
        except Exception as exc:
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            # This candidate directory is fresh: never read another evaluation's logs.
            lines = []
            for log_path in sorted((candidate_dir / "hls").glob("*/vitis.*.log")):
                for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if any(token in line.lower() for token in ("error:", "cannot", "unable")):
                        for source_path in (candidate, tb):
                            line = line.replace(str(source_path), source_path.name).replace(source_path.as_posix(), source_path.name)
                        lines.append(line)
            record["diagnostics"] = sorted(lines, key=lambda line: "error:" not in line.lower())[:40]
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        record["evaluation_seconds"] = round(time.monotonic() - evaluation_started, 6)
        write_json(candidate_dir / "result.json", record)
        records.append(record)
        with (run_dir / "history.jsonl").open("a", encoding="utf-8") as journal:
            journal.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        print(f"[{benchmark_name}] {index+1}/{budget} {config.key}: {record['status']}", flush=True)
        write_json(run_dir / "summary.json", summarize(records, limits, goal))
    summary = summarize(records, limits, goal)
    summary.update(completed_budget=len(records) == budget, planner_error=failure,
                   wall_seconds=round(time.monotonic() - started, 3))
    proposals = [json.loads(path.read_text(encoding="utf-8")) for path in run_dir.glob("proposal-*/trace.json")]
    summary.update(model_attempts=len(proposals), model_failures=sum(not item["success"] for item in proposals),
                   model_wall_seconds=sum(item["wall_seconds"] for item in proposals),
                   evaluation_seconds=sum(item["evaluation_seconds"] for item in records))
    write_json(run_dir / "summary.json", summary)
    with (run_dir / "results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "status", "native_passed", "latency_us", "LUT", "FF", "DSP", "BRAM_18K", "pareto", "elapsed_seconds"])
        for item in records:
            metrics = item.get("metrics", {})
            writer.writerow([item["id"], item["status"], item.get("verification", {}).get("passed", False),
                             metrics.get("latency_us"), *[metrics.get("resources", {}).get(key) for key in ("lut", "ff", "dsp", "bram")],
                             item["id"] in summary["pareto_ids"], item["elapsed_seconds"]])
    # Hash every evidence file except binaries/objects; useful for archival integrity, not authenticity proof.
    evidence = {str(path.relative_to(run_dir)).replace("\\", "/"): file_hash(path) for path in run_dir.rglob("*")
                if path.is_file() and path.suffix in {".json", ".jsonl", ".csv", ".cpp", ".log", ".xml", ".rpt", ".tcl"}}
    write_json(run_dir / "checksums.json", evidence)
    return run_dir
