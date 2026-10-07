"""Publish an audited, RTL-checked suite as Markdown and per-evaluation CSV.

Usage: python -m scripts.report_model_suite AUDIT_JSON EVIDENCE_ROOT NEW_OUTPUT_DIR
"""
import csv
from pathlib import Path
import statistics
import sys

from autohls.experiments import eligible, file_hash, summarize, write_json
from scripts.audit_model_suite import history, read, require


LABELS = {"random": "随机五种子", "no-feedback": "模型无反馈，两次重复", "feedback": "模型有反馈，两次重复"}


def group_label(group):
    suffix = {"canonical": " · 原顺序", "shuffled": " · 固定种子重排"}.get(group.get("order_condition"), "")
    suffix += {"none": " · 无覆盖", "pipeline-warmup-v1": " · 流水覆盖"}.get(group.get("coverage_policy"), "")
    return LABELS[group["group"]] + suffix


def trajectories(root, audit):
    require(audit["complete"] and audit["all_evidence_audited"] and (audit.get("rtl") or {}).get("complete"),
            "Publish only a complete, audited suite with RTL decisions")
    plan = audit["protocol"]
    require(plan["goal"] == "latency", "This report is for the fixed latency protocol")
    require(file_hash(root / "artifacts/model-suite/status.json") == audit["snapshot_status_sha256"], "Snapshot differs from audit")
    checksums = read(root / "export-checksums.json")
    rows = []
    for run in audit["runs"]:
        directory = root / "artifacts/model-suite" / run["id"] / run["run"]
        path = directory / "history.jsonl"
        require(file_hash(path) == checksums[path.relative_to(root).as_posix()], "History differs from exported evidence")
        records = history(directory)
        require([r["id"] for r in records] == run["order"], "Trajectory differs from audit")
        require(summarize(records, plan["limits"], plan["goal"])["best_id"] == run["summary"]["best_id"], "Winner differs from audit")
        for index, record in enumerate(records):
            best = summarize(records[:index + 1], plan["limits"], plan["goal"])["best_id"]
            metrics = next(r["metrics"] for r in records[:index + 1] if r["id"] == best) if best else None
            rows.append({"run": run["id"], "benchmark": run["benchmark"], "group": run["group"],
                         "seed": run["seed"], "repeat": run["repeat"], "evaluation": index + 1,
                         "candidate": record["id"], "status": record["status"],
                         "feasible": eligible(record, plan["limits"]), "best_hls_id": best,
                         "best_hls_latency_us": metrics["latency_us"] if metrics else None})
            if "order_condition" in run:
                rows[-1].update(order_condition=run["order_condition"], candidate_order_seed=run["candidate_order_seed"])
            if "coverage_policy" in run:
                rows[-1]["coverage_policy"] = run["coverage_policy"]
    return rows


def render(audit, audit_hash):
    kernels = tuple(dict.fromkeys(j["benchmark"] for j in audit["protocol"]["jobs"]))
    no_reference = audit["protocol"].get("reference_policy") == "not_measured"
    lines = ["# 固定预算模型搜索：最终证据报告", "",
             f"审计 SHA-256：`{audit_hash}`。本报告由审计 JSON 和原始轨迹生成；不是新的测量。", "",
             f'{len(kernels)} 个内核（{", ".join(kernels)}）各 30 个受限配置；每组 {audit["protocol"]["budget"]} 次候选评估，含基线。随机 seed0–4，模型 seed0/temperature=0 各重复两次。模型重复不是独立随机样本。',
             "", "## HLS 搜索结果", "",
             "| 内核 | 组别 | 完成 / 计划 | 时延中位数 / 最差 μs | 命中 HLS 参考 | 总耗时中位数 s | 重复序列 |",
             "| --- | --- | ---: | ---: | ---: | ---: | --- |"]
    if audit["protocol"].get("candidate_order_comparison"):
        lines[4:4] = ["", "本批仅改变候选呈现顺序；有/无反馈各自比较原顺序与固定种子重排，每个条件重复两次。排列基础种子20260927，每步使用基础种子+评估序号；生成seed仍为0。两条件不能合并统计，当前为开发集实验。"]
    if audit["protocol"].get("coverage_comparison"):
        scope = "单个前瞻迁移任务；未用于本项目此前策略调参，不保证模型预训练未见，不能证明广泛泛化。" if no_reference else "均为开发集。"
        lines[4:4] = ["", "本批比较无覆盖随机、覆盖随机、覆盖无反馈模型、覆盖有反馈模型。覆盖是人工确定的前3次基线/II=1/II=2规则，不是模型学会探索。两随机策略相同seed不保证相同抽样，组中位数比较不是逐seed配对或统计显著性证明；" + scope]
    for group in audit["groups"]:
        n = group["complete_with_feasible"]
        times = f'{group["median_latency_us"]:.2f} / {group["worst_latency_us"]:.2f}' if n else "—"
        wall = f'{group["median_wall_seconds"]:.3f}' if n else "—"
        repeat = {None: "不适用", True: "一致", False: "不一致"}[group["repeat_orders_identical"]]
        hits = "未测量" if no_reference else f'{group["reference_hits"]}/{n}'
        lines.append(f'| {group["benchmark"]} | {group_label(group)} | {n}/{group["planned_runs"]} | {times} | {hits} | {wall} | {repeat} |')
    reference_note = "本批未进行30点HLS全枚举，参考最优值、命中次数和最优差距均未测量，不记为0或未命中。" if no_reference else "参考只指事后 30 点 HLS 枚举主目标，不是全设计空间或板级最优。"
    lines += ["", "中位数仅统计完成预算且有可行方案的组；缺失/失败不能作为成功样本。" + reference_note]
    if audit["protocol"].get("candidate_order_comparison"):
        lines += ["", "### 同条件A/B主比较", "",
                  "A为原顺序，B为固定种子重排；只在两侧全部预定重复都完成且有可行结果时计算相对变化。负值表示B的HLS时延较低，不代表板级加速或统计显著性。RTL交付筛选仍单独列在后文。", "",
                  "| 内核 | 反馈 | A完成 / 计划 | B完成 / 计划 | HLS时延中位数 A → B μs | B较A时延变化 | 搜索耗时中位数 A → B s |",
                  "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
        groups = {(g["benchmark"], g["group"], g["order_condition"]): g for g in audit["groups"]}
        for kernel in kernels:
            for mode in ("no-feedback", "feedback"):
                a, b = [groups[(kernel, mode, condition)] for condition in ("canonical", "shuffled")]
                complete = all(g["complete_with_feasible"] == g["planned_runs"] > 0 for g in (a, b))
                latency = " → ".join(f'{g["median_latency_us"]:.2f}' if g["median_latency_us"] is not None else "—" for g in (a, b))
                wall = " → ".join(f'{g["median_wall_seconds"]:.3f}' if g["median_wall_seconds"] is not None else "—" for g in (a, b))
                delta = f'{100 * (b["median_latency_us"] / a["median_latency_us"] - 1):+.2f}%' if complete else "—"
                feedback = "无" if mode == "no-feedback" else "有"
                lines.append(f'| {kernel} | {feedback} | {a["complete_with_feasible"]}/{a["planned_runs"]} | {b["complete_with_feasible"]}/{b["planned_runs"]} | {latency} | {delta} | {wall} |')
    if audit["protocol"].get("coverage_comparison"):
        lines += ["", "### 覆盖与模型增量", "",
                  "分别比较组中位数；两侧全部预定运行完成且有可行结果才计算变化。负值表示B的HLS时延较低，不代表板级收益。未包含无覆盖模型，不能据此估计覆盖对模型本身的因果改善。", "",
                  "| 内核 | 比较（A→B） | A完成 / 计划 | B完成 / 计划 | HLS时延中位数 A → B μs | B较A变化 |",
                  "| --- | --- | ---: | ---: | ---: | ---: |"]
        groups = {(g["benchmark"], g["group"], g["coverage_policy"]): g for g in audit["groups"]}
        comparisons = [("随机覆盖增量", ("random", "none"), ("random", "pipeline-warmup-v1")),
                       ("同覆盖模型无反馈", ("random", "pipeline-warmup-v1"), ("no-feedback", "pipeline-warmup-v1")),
                       ("同覆盖模型有反馈", ("random", "pipeline-warmup-v1"), ("feedback", "pipeline-warmup-v1")),
                       ("同覆盖反馈增量", ("no-feedback", "pipeline-warmup-v1"), ("feedback", "pipeline-warmup-v1"))]
        for kernel in kernels:
            for label, left, right in comparisons:
                a, b = groups[(kernel, *left)], groups[(kernel, *right)]
                complete = all(g["complete_with_feasible"] == g["planned_runs"] > 0 for g in (a, b))
                latency = " → ".join(f'{g["median_latency_us"]:.2f}' if g["median_latency_us"] is not None else "—" for g in (a, b))
                delta = f'{100 * (b["median_latency_us"] / a["median_latency_us"] - 1):+.2f}%' if complete else "—"
                lines.append(f'| {kernel} | {label} | {a["complete_with_feasible"]}/{a["planned_runs"]} | {b["complete_with_feasible"]}/{b["planned_runs"]} | {latency} | {delta} |')
    lines += ["", "## 逐组评估与搜索成本", "",
              "评估、原生通过、综合完成、约束可行是不同计数。执行异常对应 failed（包括编译或工具异常），校验失败对应 verification_failed；模型调用失败另计。综合完成不等于可交付硬件。所有数值来自审计，不读取模型生成理由作为指标。", "",
              "| 运行 | 评估 / 原生通过 / 综合完成 / 可行 | 执行异常 / 校验失败 | 模型调用 / 调用失败 | 模型耗时 s | 评估耗时 s |",
              "| --- | --- | ---: | --- | ---: | ---: |"]
    for run in audit["runs"]:
        s = run["summary"]
        counts = f'{s["evaluated"]} / {s["correctness_passed"]} / {run["statuses"].get("synthesized", 0)} / {s["feasible_synthesized"]}'
        lines.append(f'| {run["id"]} | {counts} | {run["statuses"].get("failed", 0)} / {run["statuses"].get("verification_failed", 0)} | {s["model_attempts"]} / {s["model_failures"]} | {s["model_wall_seconds"]:.3f} | {s["evaluation_seconds"]:.3f} |')
    lines += ["", "| 运行 | 首次达到 HLS 参考的评估序号 | HLS 最佳时延 μs | LUT / FF / DSP / BRAM_18K |",
              "| --- | ---: | ---: | --- |"]
    for run in audit["runs"]:
        metrics = run["best_metrics"]
        resources = " / ".join(str(metrics["resources"][k]) for k in ("lut", "ff", "dsp", "bram")) if metrics else "—"
        latency = f'{metrics["latency_us"]:.2f}' if metrics else "—"
        hit = "未测量" if no_reference else (run.get("first_reference_hit") or "未达到")
        lines.append(f'| {run["id"]} | {hit} | {latency} | {resources} |')
    lines += ["", "## RTL 交付筛选", "",
              f'本轮 {audit["rtl"]["passed"]} 种配置通过、{audit["rtl"]["rejected"]} 种被拒绝；{audit["rtl"]["finalists_passed"]}/{len(audit["runs"])} 组有通过 RTL 的最终候选。',
              "", "| 内核 | 组别 | RTL 通过 / 计划 | RTL 筛选后 HLS 时延中位数 / 最差 μs |",
              "| --- | --- | ---: | ---: |"]
    for group in audit["groups"]:
        selected = [r for r in audit["runs"] if r["benchmark"] == group["benchmark"] and r["group"] == group["group"]
                    and r.get("order_condition") == group.get("order_condition")
                    and r.get("coverage_policy") == group.get("coverage_policy")]
        values = [r["rtl"]["best_metrics"]["latency_us"] for r in selected if r["rtl"]["status"] == "rtl_passed"]
        times = f"{statistics.median(values):.2f} / {max(values):.2f}" if values else "—"
        lines.append(f'| {group["benchmark"]} | {group_label(group)} | {len(values)}/{group["planned_runs"]} | {times} |')
    lines += ["", "这些仍是通过 RTL 筛选的方案的 HLS 估计时延，不是 RTL 实测时延或板卡计时。相同源码/测试程序去重验证，不能当作独立重复实验。", "",
              "| 运行 | 退出码 | HLS 最佳 | RTL 最终 | 被拒绝的候选 | 状态 |",
              "| --- | ---: | --- | --- | --- | --- |"]
    for run in audit["runs"]:
        rtl = run["rtl"]
        lines.append(f'| {run["id"]} | {run["exit_code"]} | {run["summary"]["best_id"]} | {rtl.get("best_rtl_id") or "—"} | {", ".join(rtl["rejected_ids"]) or "无"} | {rtl["status"]} |')
    lines += ["", "## 边界与复算", "",
              "- `trajectory.csv` 每行对应一次实际候选评估；综合失败也占一行预算。`best_hls_latency_us` 只取当时已观测且满足约束的候选，不使用未来或历史枚举成绩。",
              "- HLS 目标 10 ns，工具 8.75 ns 有效预算警告保留；没有新布线时序、板级计时或功耗结果。",
              f'- 模型推理在已有 CPU 云主机完成，墙钟受加载、提示缓存与其他系统负载影响；不是隔离冷启动基准。RTL 验收属于额外验证，不计入搜索的 {audit["protocol"]["budget"]} 次预算或搜索墙钟。',
              "- 固定参数不保证重复序列一致。模型回答中的理由是提议而非性能证据；排名依据实际工具结果。",
              "- 不从单个迁移任务或少量开发内核推断广泛泛化、显著性、模型整体优势或对其他论文方法的优势。", ""]
    return "\n".join(lines)


def publish(audit_path, root, destination):
    audit = read(audit_path)
    rows = trajectories(root, audit)
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "trajectory.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (destination / "REPORT.md").write_text(render(audit, file_hash(audit_path)), encoding="utf-8")
    write_json(destination / "provenance.json", {"audit_sha256": file_hash(audit_path),
        "generator_sha256": file_hash(Path(__file__)), "export_manifest_sha256": file_hash(root / "export-checksums.json"),
        "rows": len(rows), "board_verified": False,
        "outputs": {name: file_hash(destination / name) for name in ("trajectory.csv", "REPORT.md")}})
    return len(rows)


if __name__ == "__main__":
    print(publish(*map(Path, sys.argv[1:])))
