"""Design-space exploration primitives for the AutoHLS demo.

The simulator is deliberately deterministic: the same source and configuration
always produce the same report.  It lets the whole optimization loop be shown
without pretending that a local FPGA synthesis has taken place.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable


DEVICE = {
    "name": "TUL PYNQ-Z2",
    "part": "xc7z020clg400-1",
    "clock_ns": 10.0,
    "capacity": {"lut": 53_200, "ff": 106_400, "dsp": 220, "bram": 280},
    "resource_units": {"bram": "BRAM_18K"},
}

GOAL_LABELS = {
    "balanced": "综合平衡",
    "latency": "最低时延",
    "resource": "最低资源",
}


@dataclass(frozen=True)
class SourceProfile:
    top_function: str
    lines: int
    loops: int
    max_loop_depth: int
    arrays: int
    arithmetic_ops: int
    branches: int
    has_float: bool


@dataclass(frozen=True)
class Strategy:
    key: str
    name: str
    description: str
    pipeline_ii: int | None = None
    unroll: int = 1
    partition: int = 1
    dataflow: bool = False


STRATEGIES = (
    Strategy("baseline", "基线方案", "保持原始结构，建立性能与资源基准"),
    Strategy("pipe", "循环流水", "主循环 PIPELINE，目标 II=2", pipeline_ii=2),
    Strategy("pipe_u2", "流水 + 2路展开", "平衡并行度与片上资源", pipeline_ii=1, unroll=2),
    Strategy("pipe_u4", "流水 + 4路展开", "提高吞吐率，适合计算密集内核", pipeline_ii=1, unroll=4),
    Strategy("partition_u4", "分区 + 4路展开", "消除数组端口冲突并提升并行访存", pipeline_ii=1, unroll=4, partition=4),
    Strategy("dataflow", "任务级数据流", "函数级 DATAFLOW 与双缓冲并行", pipeline_ii=1, unroll=2, partition=2, dataflow=True),
    Strategy("area", "资源复用", "限制并行度，优先降低逻辑与 DSP 占用", pipeline_ii=3),
    Strategy("max_perf", "激进并行", "8路展开与分区，探索性能上界", pipeline_ii=1, unroll=8, partition=8, dataflow=True),
)


def analyze_source(source: str, requested_top: str | None = None) -> SourceProfile:
    """Extract a lightweight structural profile from C/C++ source."""
    clean = re.sub(r"//.*?$|/\*.*?\*/", "", source, flags=re.MULTILINE | re.DOTALL)
    functions = re.findall(
        r"(?:void|int|float|double|ap_(?:u)?int<\d+>|[A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{",
        clean,
    )
    top = requested_top.strip() if requested_top and requested_top.strip() else (functions[-1] if functions else "kernel")
    loop_positions = [m.start() for m in re.finditer(r"\b(?:for|while)\s*\(", clean)]
    max_depth = _estimate_loop_depth(clean, loop_positions)
    arrays = len(re.findall(r"\b[A-Za-z_]\w*\s*\[[^\]]+\]", clean))
    ops = len(re.findall(r"(?<![+\-*/])(?:\+|-|\*|/)(?!=)", clean))
    branches = len(re.findall(r"\b(?:if|switch)\s*\(", clean))
    return SourceProfile(
        top_function=top,
        lines=max(1, len(source.splitlines())),
        loops=len(loop_positions),
        max_loop_depth=max_depth,
        arrays=arrays,
        arithmetic_ops=ops,
        branches=branches,
        has_float=bool(re.search(r"\b(?:float|double)\b", clean)),
    )


def _estimate_loop_depth(source: str, positions: list[int]) -> int:
    if not positions:
        return 0
    depth = 0
    max_depth = 1
    loop_starts = set(positions)
    pending_loop = False
    for index, char in enumerate(source):
        if index in loop_starts:
            pending_loop = True
        if char == "{" and pending_loop:
            depth += 1
            max_depth = max(max_depth, depth)
            pending_loop = False
        elif char == "}" and depth:
            depth -= 1
    return max_depth


def _noise(source: str, key: str) -> float:
    digest = hashlib.sha256(f"{source}|{key}".encode("utf-8")).digest()
    return 0.96 + int.from_bytes(digest[:2], "big") / 65535 * 0.08


def simulate_synthesis(source: str, profile: SourceProfile, strategy: Strategy) -> dict[str, Any]:
    """Estimate one HLS result while preserving realistic trade-offs."""
    complexity = (
        700
        + profile.lines * 42
        + profile.loops * 1_600
        + profile.arithmetic_ops * 70
        + profile.arrays * 310
        + (1_800 if profile.has_float else 0)
    )
    parallel = strategy.unroll * (1.18 if strategy.partition > 1 else 1.0)
    pipe_gain = 2.4 if strategy.pipeline_ii == 1 else 1.7 if strategy.pipeline_ii == 2 else 1.22 if strategy.pipeline_ii else 1.0
    dataflow_gain = 1.28 if strategy.dataflow and profile.loops > 1 else 1.0
    cycles = int(complexity / (parallel * pipe_gain * dataflow_gain) * _noise(source, strategy.key))
    cycles = max(72, cycles + profile.branches * 45)

    base_lut = 1_100 + profile.lines * 11 + profile.arithmetic_ops * 21 + profile.arrays * 120
    lut_factor = 0.72 if strategy.key == "area" else 1 + 0.19 * (strategy.unroll - 1) + 0.035 * (strategy.partition - 1) + (0.16 if strategy.dataflow else 0)
    lut = int(base_lut * lut_factor * _noise(source, strategy.key + "lut"))
    ff = int(lut * (1.42 + (0.08 if strategy.pipeline_ii else 0)) * _noise(source, strategy.key + "ff"))
    dsp_base = max(2, math.ceil(profile.arithmetic_ops / 3)) * (3 if profile.has_float else 1)
    dsp_factor = 0.72 if strategy.key == "area" else max(1, strategy.unroll * 0.82)
    dsp = max(1, int(dsp_base * dsp_factor * _noise(source, strategy.key + "dsp")))
    bram_base = max(2, profile.arrays * 2 + profile.max_loop_depth)
    bram = max(1, int(bram_base * (1 + 0.22 * (strategy.partition - 1) + (0.35 if strategy.dataflow else 0))))
    ii = strategy.pipeline_ii or max(4, profile.max_loop_depth * 3)
    clock = DEVICE["clock_ns"] * (1 + max(0, strategy.unroll - 4) * 0.018)
    latency_us = round(cycles * clock / 1_000, 3)
    throughput = round(1_000 / (ii * clock), 2)
    resources = {"lut": lut, "ff": ff, "dsp": dsp, "bram": bram}
    utilization = {
        key: round(value / DEVICE["capacity"][key] * 100, 2)
        for key, value in resources.items()
    }
    warnings: list[str] = []
    if strategy.partition > 1 and profile.arrays == 0:
        warnings.append("未识别到静态数组，ARRAY_PARTITION 收益可能有限")
    if strategy.dataflow and profile.loops < 2:
        warnings.append("任务级阶段不足，DATAFLOW 可能无法形成有效重叠")
    if strategy.unroll >= 8:
        warnings.append("激进展开可能导致布线拥塞与时钟收敛压力")
    status = "warning" if warnings else "success"
    return {
        "cycles": cycles,
        "latency_us": latency_us,
        "ii": ii,
        "throughput_mops": throughput,
        "clock_ns": round(clock, 3),
        "resources": resources,
        "utilization": utilization,
        "status": status,
        "warnings": warnings,
    }


def pareto_frontier(candidates: Iterable[dict[str, Any]]) -> set[str]:
    """Return IDs not dominated in latency and four resource dimensions."""
    items = list(candidates)
    frontier: set[str] = set()
    for candidate in items:
        a = _objectives(candidate)
        dominated = False
        for other in items:
            if other["id"] == candidate["id"]:
                continue
            b = _objectives(other)
            if all(x <= y for x, y in zip(b, a)) and any(x < y for x, y in zip(b, a)):
                dominated = True
                break
        if not dominated:
            frontier.add(candidate["id"])
    return frontier


def _objectives(candidate: dict[str, Any]) -> tuple[float, ...]:
    metrics = candidate["metrics"]
    resources = metrics["resources"]
    return (
        metrics["latency_us"],
        resources["lut"],
        resources["ff"],
        resources["dsp"],
        resources["bram"],
    )


def _score(candidate: dict[str, Any], goal: str, candidates: list[dict[str, Any]]) -> float:
    latencies = [item["metrics"]["latency_us"] for item in candidates]
    latency_norm = candidate["metrics"]["latency_us"] / max(latencies)
    utilizations = candidate["metrics"]["utilization"].values()
    resource_norm = sum(utilizations) / (len(DEVICE["capacity"]) * 100)
    congestion = max(utilizations) / 100
    if goal == "latency":
        raw = latency_norm * 0.78 + resource_norm * 0.12 + congestion * 0.10
    elif goal == "resource":
        raw = latency_norm * 0.18 + resource_norm * 0.62 + congestion * 0.20
    else:
        raw = latency_norm * 0.48 + resource_norm * 0.34 + congestion * 0.18
    return round(raw, 5)


def _directives(strategy: Strategy, top: str) -> list[str]:
    # The demo's statistical profile is insufficient to resolve valid HLS paths.
    # Executable transformations live in benchmarks.render_candidate instead.
    return ["# 演示策略不生成可执行 Tcl；使用 research 命令生成经过标注校验的候选源码。"]


def _insights(profile: SourceProfile, best: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, str]]:
    speedup = baseline["metrics"]["latency_us"] / best["metrics"]["latency_us"]
    lut_change = (best["metrics"]["resources"]["lut"] / baseline["metrics"]["resources"]["lut"] - 1) * 100
    insights = [
        {
            "tone": "positive",
            "title": f"推荐方案获得 {speedup:.2f}× 加速",
            "body": f"相对基线，{best['name']} 将估算时延降至 {best['metrics']['latency_us']:.3f} μs，LUT 变化 {lut_change:+.1f}%。",
        }
    ]
    if profile.arrays and profile.max_loop_depth >= 2:
        insights.append(
            {
                "tone": "info",
                "title": "访存端口是关键约束",
                "body": "检测到嵌套循环与数组访问；建议在真实综合中重点检查 II violation 与存储端口冲突。",
            }
        )
    if profile.has_float:
        insights.append(
            {
                "tone": "warning",
                "title": "浮点运算提高 DSP 压力",
                "body": "可在误差允许时评估 ap_fixed 定点化，进一步降低 DSP 使用量与流水级数。",
            }
        )
    return insights


def explore(source: str, top: str | None = None, goal: str = "balanced") -> dict[str, Any]:
    """Run deterministic design-space exploration and rank candidates."""
    if not source.strip():
        raise ValueError("源代码不能为空")
    if goal not in GOAL_LABELS:
        raise ValueError(f"未知优化目标: {goal}")
    profile = analyze_source(source, top)
    candidates: list[dict[str, Any]] = []
    for index, strategy in enumerate(STRATEGIES, start=1):
        candidates.append(
            {
                "id": f"S{index:02d}",
                "strategy": strategy.key,
                "name": strategy.name,
                "description": strategy.description,
                "directives": _directives(strategy, profile.top_function),
                "metrics": simulate_synthesis(source, profile, strategy),
            }
        )
    frontier = pareto_frontier(candidates)
    for candidate in candidates:
        candidate["pareto"] = candidate["id"] in frontier
        candidate["score"] = _score(candidate, goal, candidates)
    candidates.sort(key=lambda item: item["score"])
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
        candidate["recommended"] = rank == 1
    baseline = next(item for item in candidates if item["strategy"] == "baseline")
    best = candidates[0]
    return {
        "engine": "deterministic-demo",
        "goal": goal,
        "goal_label": GOAL_LABELS[goal],
        "device": DEVICE,
        "profile": asdict(profile),
        "summary": {
            "explored": len(candidates),
            "pareto_count": len(frontier),
            "best_id": best["id"],
            "best_name": best["name"],
            "speedup": round(baseline["metrics"]["latency_us"] / best["metrics"]["latency_us"], 2),
            "latency_reduction": round((1 - best["metrics"]["latency_us"] / baseline["metrics"]["latency_us"]) * 100, 1),
        },
        "candidates": candidates,
        "insights": _insights(profile, best, baseline),
    }
