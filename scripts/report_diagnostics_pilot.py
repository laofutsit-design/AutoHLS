"""Publish the audited diagnostic pilot separately from the frozen 27-job suite."""
import csv
from pathlib import Path
import sys

from autohls.experiments import file_hash, write_json
from scripts.audit_model_suite import read, require
from scripts.report_model_suite import trajectories


def render(audit, checksum):
    lines = ["# 失败诊断反馈：开发集对照结果", "", f"审计 SHA-256：`{checksum}`。", "",
             "固定 conv2d、30 个配置、每组预算 8（含基线），顺序为旧→新→新→旧。模型、约束、候选和排序不变；只补齐综合失败诊断。两次重复不是独立统计样本，结果不能代表未见内核泛化。", "",
             "| 运行 | 完成预算 | 综合失败 | 可行数 | HLS 最佳 μs | RTL 筛选后 HLS μs | 搜索墙钟 s | 模型调用 s |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for run in audit["runs"]:
        summary, rtl = run["summary"], run["rtl"]
        value = run["best_metrics"]["latency_us"] if run["best_metrics"] else None
        checked = rtl.get("best_metrics", {}).get("latency_us")
        lines.append(f'| {run["id"]} | {summary["evaluated"]}/8 | {run["statuses"].get("failed", 0)} | '
                     f'{summary["feasible_synthesized"]} | {value} | {checked} | {summary["wall_seconds"]:.3f} | {summary["model_wall_seconds"]:.3f} |')
    lines += ["", "以上时延仍是 HLS 估计，不是 RTL 实测周期或板上时间。独立 RTL 验收不计入搜索预算或搜索墙钟。", "",
              "## 信息是否真正送达", "",
              "| 运行 | 分区错误总次数 | 首次错误后同类次数 | 有下一次请求的失败 | 具体诊断送达次数 |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for run in audit["runs"]:
        d = run["diagnostic_evidence"]
        lines.append(f'| {run["id"]} | {d["partition_failure_count"]} | {d["partition_failures_after_first"]} | '
                     f'{d["errors_with_next_request"]} | {d["errors_delivered_to_next_request"]} |')
    lines += ["", "同类错误按原始日志中的 `incorrect partition factor` 计数；“首次后”不等于模型已理解原因。最后一次评估没有后续请求时，不计为反馈送达失败。审计验证每条诊断来自该候选原始日志，且只能出现在后续请求。", "",
              "## 候选与 RTL 决策", ""]
    for run in audit["runs"]:
        rtl = run["rtl"]
        lines += [f'### {run["id"]}', "", " → ".join(f'`{key}`' for key in run["order"]), "",
                  f'HLS 最佳：`{run["summary"]["best_id"]}`；RTL 最终：`{rtl.get("best_rtl_id")}`；'
                  f'拒绝：{", ".join(rtl["rejected_ids"]) or "无"}。', ""]
        for failed in run["diagnostic_evidence"]["failures"]:
            lines.append(f'- 第 {failed["evaluation"]} 次 `{failed["id"]}`：分区因子 {failed["partition_factors"]}；'
                         f'后续收到 {len(failed["next_request_diagnostics"])} 行诊断。')
        lines.append("")
    lines += ["## 结论边界与复算", "",
              "- 信息传递正确性与搜索质量是两个验收项：送达错误不自动证明能减少失败或改善时延。", 
              "- 原始失败、所有模型请求/响应、顺序和耗时均保留。推理加载/缓存、不同运行路径及其他系统负载可能影响输出或时间；不是隔离的因果或冷启动性能基准。",
              "- 启动失败的 v1 为 0 候选，不混入本轮 v2 的 32 次真实评估；该失败留档，未覆盖。",
              "- `trajectory.csv` 每行只使用当时已经观测的结果，失败也占预算。`provenance.json` 记录源和输出哈希。",
              "- 本次不连接板卡，不产生新板级、功耗、布线时序或模型整体优势结论。旧 27 组报告保持不变。", ""]
    return "\n".join(lines)


def publish(audit_path, evidence, output):
    audit = read(audit_path)
    require(audit["all_budgets_completed"], "Incomplete pilot budget")
    rows = trajectories(evidence, audit)
    arms = {run["id"]: run["arm"] for run in audit["runs"]}
    for row in rows:
        row["arm"] = arms[row["run"]]
    output.mkdir(parents=True, exist_ok=False)
    with (output / "trajectory.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / "REPORT.md").write_text(render(audit, file_hash(audit_path)), encoding="utf-8")
    write_json(output / "provenance.json", {"audit_sha256": file_hash(audit_path),
        "generator_sha256": file_hash(Path(__file__)), "trajectory_helper_sha256": file_hash(Path(__file__).with_name("report_model_suite.py")),
        "export_manifest_sha256": file_hash(evidence / "export-checksums.json"), "rows": len(rows), "board_verified": False,
        "outputs": {name: file_hash(output / name) for name in ("trajectory.csv", "REPORT.md")}})
    return len(rows)


if __name__ == "__main__":
    print(publish(*map(Path, sys.argv[1:])))
