"""Deterministic, offline convergence plot of audited best-so-far HLS estimates."""
import csv
import html
import math
from pathlib import Path
import statistics
import sys

from autohls.experiments import file_hash, write_json
from scripts.audit_model_suite import read, require
from scripts.report_model_suite import trajectories

STYLES = {"random": ("随机搜索", "#2563eb", ""), "no-feedback": ("模型无反馈", "#a44e08", "9 5"),
          "feedback": ("模型有反馈", "#138569", "2 5")}


def aggregate_curves(rows, plan):
    require(not plan.get("candidate_order_comparison"), "Order comparisons require condition-specific curves; do not merge A/B")
    require(not plan.get("coverage_comparison"), "Coverage comparisons require condition-specific curves; do not merge policies")
    require(plan["budget"] >= 2, "Plot requires at least two evaluation steps")
    expected = {job["id"] for job in plan["jobs"]}
    require({row["run"] for row in rows} == expected, "Plot requires all planned runs")
    by_run = {key: [row for row in rows if row["run"] == key] for key in expected}
    for job in plan["jobs"]:
        values = by_run[job["id"]]
        require([r["evaluation"] for r in values] == list(range(1, plan["budget"] + 1)), "Incomplete or repeated evaluation")
        require(all(r["benchmark"] == job["benchmark"] and r["group"] == job["group"] for r in values), "Wrong curve group")
        latency = [r["best_hls_latency_us"] for r in values]
        require(all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in latency),
                "Plot requires a finite feasible best at every step")
        require(all(b <= a for a, b in zip(latency, latency[1:])), "Best-so-far latency increased")
    curves = []
    for benchmark in ("matmul", "fir", "conv2d"):
        for group in STYLES:
            selected = [by_run[j["id"]] for j in plan["jobs"] if j["benchmark"] == benchmark and j["group"] == group]
            require(selected, "Missing planned comparison group")
            for index in range(plan["budget"]):
                values = [run[index]["best_hls_latency_us"] for run in selected]
                curves.append({"benchmark": benchmark, "group": group, "evaluation": index + 1,
                               "runs": len(values), "min_us": min(values), "median_us": statistics.median(values),
                               "max_us": max(values)})
    return curves


def render(curves, plan):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1130" viewBox="0 0 1000 1130" role="img" aria-labelledby="title description">',
             '<title id="title">固定预算搜索收敛曲线</title>',
             '<desc id="description">每步仅使用已观测的可行 HLS 结果。折线为组内中位数，阴影为最小至最大值，不是置信区间。模型重复不是独立随机样本。</desc>',
             '<rect width="1000" height="1130" fill="white"/>',
             '<g font-family="Segoe UI,Microsoft YaHei,Arial,sans-serif" fill="#1e293b">']

    def text(x, y, value, size=14, extra=""):
        parts.append(f'<text x="{x}" y="{y}" font-size="{size}" {extra}>{html.escape(value)}</text>')

    text(60, 38, f'预算 {plan["budget"]} · 最佳已观测 HLS 时延', 24)
    text(60, 64, "纵轴为对数尺度（μs），越低越好；每次失败仍消耗一次评估。")
    text(60, 87, "折线：中位数；阴影：最小—最大范围（非置信区间）。不是板级计时。")
    for panel, benchmark in enumerate(("matmul", "fir", "conv2d")):
        top, bottom = 154 + 300 * panel, 352 + 300 * panel
        selected = [c for c in curves if c["benchmark"] == benchmark]
        low, high = min(c["min_us"] for c in selected), max(c["max_us"] for c in selected)
        log_low, log_high = math.log10(low) - .08, math.log10(high) + .08
        x = lambda i: 100 + (i - 1) * 840 / (plan["budget"] - 1)
        y = lambda v: bottom - (math.log10(v) - log_low) * (bottom - top) / (log_high - log_low)
        text(60, top - 27, benchmark, 20)
        for exponent in range(math.floor(log_low), math.ceil(log_high) + 1):
            for multiplier in (1, 2, 5):
                value = multiplier * 10 ** exponent
                if log_low <= math.log10(value) <= log_high:
                    position = y(value)
                    parts.append(f'<path d="M100 {position:.2f} H940" stroke="#e2e8f0"/>')
                    text(88, round(position + 5, 2), f"{value:g}", extra='text-anchor="end"')
        parts.append(f'<path d="M100 {top} V{bottom} H940" fill="none" stroke="#64748b"/>')
        for i in sorted({1, plan["budget"], *range(4, plan["budget"], 4)}):
            text(round(x(i), 2), bottom + 23, str(i), extra='text-anchor="middle"')
        text(495, bottom + 45, "实际候选评估次数（含基线）")
        for index, (group, (label, color, dash)) in enumerate(STYLES.items()):
            points = [c for c in selected if c["group"] == group]
            legend_x = 195 + index * 248
            parts.append(f'<path d="M{legend_x} {top-32} h20" stroke="{color}" stroke-width="2.4" stroke-dasharray="{dash}"/>')
            text(220 + index * 248, top - 27, f'{label}（n={points[0]["runs"]}）', extra=f'fill="{color}"')

            def stair(field):
                coords = [(x(points[0]["evaluation"]), y(points[0][field]))]
                for previous, current in zip(points, points[1:]):
                    coords += [(x(current["evaluation"]), y(previous[field])), (x(current["evaluation"]), y(current[field]))]
                return coords

            band = stair("min_us") + list(reversed(stair("max_us")))
            parts.append(f'<polygon points="{" ".join(f"{a:.2f},{b:.2f}" for a, b in band)}" fill="{color}" opacity="0.10"/>')
            path = " L".join(f"{a:.2f},{b:.2f}" for a, b in stair("median_us"))
            parts.append(f'<path d="M{path}" fill="none" stroke="{color}" stroke-width="2.4" stroke-dasharray="{dash}"/>')
    text(60, 1032, "随机为五种子；模型为同一 seed / temperature=0 的两次重复，非独立随机样本。")
    text(60, 1058, "曲线是搜索阶段的 HLS 可行结果；最终 RTL 拒绝及后备方案另见报告，不回写搜索轨迹。")
    text(60, 1084, "不同诊断版本的批次不构成单因素预算对照；不能由这些开发内核推断泛化或显著性。")
    text(60, 1110, "相同数值的曲线会重合；精确逐点评估见 curves.csv，失败细节见原始审计。")
    return "\n".join(parts + ["</g></svg>"])


def publish(audit_path, evidence, destination):
    audit = read(audit_path)
    curves = aggregate_curves(trajectories(evidence, audit), audit["protocol"])
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "convergence.svg").write_text(render(curves, audit["protocol"]), encoding="utf-8")
    with (destination / "curves.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(curves[0]))
        writer.writeheader()
        writer.writerows(curves)
    write_json(destination / "provenance.json", {"audit_sha256": file_hash(audit_path),
        "export_manifest_sha256": file_hash(evidence / "export-checksums.json"),
        "generator_sha256": file_hash(Path(__file__)),
        "trajectory_generator_sha256": file_hash(Path(__file__).with_name("report_model_suite.py")),
        "outputs": {name: file_hash(destination / name) for name in ("convergence.svg", "curves.csv")},
        "board_verified": False})
    return len(curves)


if __name__ == "__main__":
    print(publish(*map(Path, sys.argv[1:])))
