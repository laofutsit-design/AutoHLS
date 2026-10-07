const groups = { random: "随机搜索", "no-feedback": "模型无反馈", feedback: "模型有反馈" };
const conditions = { canonical: "原顺序A", shuffled: "固定种子重排B" };
const coverage = { none: "无覆盖", "pipeline-warmup-v1": "流水覆盖" };
const groupLabel = group => (groups[group.group] || group.group) +
  (group.order_condition && group.order_condition !== "none" ? ` · ${conditions[group.order_condition] || group.order_condition}` : "") +
  (group.coverage_policy ? ` · ${coverage[group.coverage_policy] || group.coverage_policy}` : "");
const number = value => typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";
const node = (tag, text) => { const item = document.createElement(tag); item.textContent = text; return item; };
let requestVersion = 0;

function renderOrderComparison(report) {
  const enabled = Boolean(report.protocol.candidate_order_comparison);
  document.querySelector("#orderComparison").hidden = !enabled;
  document.querySelector("#orderText").hidden = !enabled;
  const rows = document.querySelector("#orderRows");
  rows.replaceChildren();
  if (!enabled) return;
  const jobs = report.protocol.jobs;
  for (const benchmark of new Set(jobs.map(job => job.benchmark))) {
    for (const mode of ["no-feedback", "feedback"]) {
      const keys = ["canonical", "shuffled"];
      const planned = keys.map(condition => jobs.filter(job => job.benchmark === benchmark &&
        job.group === mode && job.order_condition === condition).length);
      if (!planned.some(count => count > 0)) continue;
      const pair = keys.map(condition => report.groups.find(group => group.benchmark === benchmark &&
        group.group === mode && group.order_condition === condition));
      const complete = report.complete && report.all_evidence_audited && pair.every((group, index) =>
        planned[index] > 0 && group?.planned_runs === planned[index] &&
        group.audited_runs === planned[index] && group.complete_with_feasible === planned[index] &&
        Number.isFinite(group.median_latency_us) && group.median_latency_us > 0);
      const change = complete ? 100 * (pair[1].median_latency_us / pair[0].median_latency_us - 1) : null;
      const row = document.createElement("tr");
      const values = [benchmark, mode === "feedback" ? "有" : "无",
        ...pair.map((group, index) => `${group?.complete_with_feasible ?? "—"} / ${planned[index]}`),
        pair.map(group => number(group?.median_latency_us)).join(" → "),
        change === null ? "—" : `${change >= 0 ? "+" : ""}${number(change)}%`,
        pair.map(group => number(group?.median_wall_seconds)).join(" → ")];
      values.forEach(value => row.append(node("td", value)));
      rows.append(row);
    }
  }
}

function renderCoverageComparison(report) {
  const enabled = Boolean(report.protocol.coverage_comparison);
  document.querySelector("#coverageComparison").hidden = !enabled;
  document.querySelector("#coverageText").hidden = !enabled;
  const rows = document.querySelector("#coverageRows");
  rows.replaceChildren();
  if (!enabled) return;
  const jobs = report.protocol.jobs;
  const comparisons = [
    ["随机覆盖增量", ["random", "none"], ["random", "pipeline-warmup-v1"]],
    ["同覆盖模型无反馈", ["random", "pipeline-warmup-v1"], ["no-feedback", "pipeline-warmup-v1"]],
    ["同覆盖模型有反馈", ["random", "pipeline-warmup-v1"], ["feedback", "pipeline-warmup-v1"]],
    ["同覆盖反馈增量", ["no-feedback", "pipeline-warmup-v1"], ["feedback", "pipeline-warmup-v1"]]
  ];
  for (const benchmark of new Set(jobs.map(job => job.benchmark))) {
    for (const [label, left, right] of comparisons) {
      const matches = [left, right].map(([mode, policy]) => item => item.benchmark === benchmark &&
        item.group === mode && item.coverage_policy === policy);
      const planned = matches.map(match => jobs.filter(match).length);
      const pair = matches.map(match => {
        const found = report.groups.filter(match);
        return found.length === 1 ? found[0] : null;
      });
      const complete = report.complete && report.all_evidence_audited && pair.every((group, index) =>
        planned[index] > 0 && group?.planned_runs === planned[index] &&
        group.audited_runs === planned[index] && group.complete_with_feasible === planned[index] &&
        Number.isFinite(group.median_latency_us) && group.median_latency_us > 0);
      const change = complete ? 100 * (pair[1].median_latency_us / pair[0].median_latency_us - 1) : null;
      const row = document.createElement("tr");
      const values = [benchmark, label,
        ...pair.map((group, index) => `${group?.complete_with_feasible ?? "—"} / ${planned[index]}`),
        pair.map(group => number(group?.median_latency_us)).join(" → "),
        change === null ? "—" : `${change >= 0 ? "+" : ""}${number(change)}%`,
        pair.map(group => number(group?.median_wall_seconds)).join(" → ")];
      values.forEach(value => row.append(node("td", value)));
      rows.append(row);
    }
  }
}

function renderSuite(report) {
  const status = document.querySelector("#suiteStatus");
  const audited = report.runs.filter(run => run.audited).length;
  const noReference = report.protocol.reference_policy === "not_measured";
  const transfer = report.protocol.experiment === "prefixsum-transfer-v1";
  const benchmarks = [...new Set(report.protocol.jobs.map(job => job.benchmark))];
  const coverageConditions = new Set(report.protocol.jobs.map(job => `${job.benchmark}:${job.group}:${job.coverage_policy}`)).size;
  status.textContent = `${report.complete ? "搜索批次已结束" : "搜索批次尚未完整结束"} · ${audited}/${report.protocol.jobs.length} 组证据通过审计。${report.all_evidence_audited ? "" : "存在缺失或未通过审计的数据，请查看逐组记录。"}这是归档快照，不是实时进度。审计时间：${report.audited_at_utc ?? "未记录"}`;
  status.classList.toggle("warning", !report.all_evidence_audited || !report.complete);
  document.querySelector("#protocolText").textContent = `${benchmarks.join(" / ")} · 每组 ${report.protocol.budget} 次候选评估（含基线） · 目标 ${report.protocol.goal} · ${report.protocol.part} · ${report.protocol.clock_ns} ns · 仅现有 CPU 云主机`;
  document.querySelector("#modelText").textContent = `${report.protocol.model} · Ollama ${report.protocol.ollama_version} · digest ${report.protocol.model_digest}`;
  document.querySelector("#referenceText").textContent = "随机搜索为5个种子；模型为同一seed、temperature=0的两次稳定性重复，不视作独立随机样本。" +
    (noReference ? "本批未执行30点HLS参考枚举，参考值、命中及差距均未测量，不记为0或未命中。" : "参考为历史30点HLS枚举，未进入模型提示。");
  document.querySelector("#taskScopeText").hidden = !transfer;
  document.querySelector("#taskScopeText").textContent = transfer ? "这是单个前瞻迁移任务，未用于本项目此前策略调参；不保证模型预训练未见，不能证明广泛泛化。观察结果后继续调策略将使其成为开发任务。" : "";
  document.querySelector("#coverageText").textContent = `本批${coverageConditions}个条件组、${report.protocol.jobs.length}次搜索。流水覆盖固定前3次尝试基线 / II=1 / II=2，失败仍占预算，之后恢复剩余候选。覆盖是人工规则，不是模型学会探索；没有重排或过滤搜索空间。两随机策略相同seed不保证相同抽样，不作逐seed配对。${transfer ? "" : "三个内核均为开发集。"}`;
  renderOrderComparison(report);
  renderCoverageComparison(report);
  const tbody = document.querySelector("#groupRows");
  tbody.replaceChildren();
  for (const group of report.groups) {
    const row = document.createElement("tr");
    const values = [group.benchmark, groupLabel(group),
      `${group.complete_with_feasible} / ${group.planned_runs}`, number(group.median_latency_us), number(group.worst_latency_us),
      noReference ? "未测量" : group.complete_with_feasible && Number.isInteger(group.reference_hits) ? `${group.reference_hits} / ${group.complete_with_feasible}` : "—", number(group.median_wall_seconds),
      group.repeat_orders_identical === null ? "—" : group.repeat_orders_identical ? "一致" : "不一致"];
    values.forEach(value => row.append(node("td", value)));
    tbody.append(row);
  }
  const details = document.querySelector("#runDetails");
  details.replaceChildren();
  for (const run of report.runs) {
    const detail = document.createElement("details");
    const summary = run.summary;
    const outcome = run.state === "pending" ? "尚未结束 / 未归档" : !run.audited ? "审计未通过 / 证据缺失" : summary.completed_budget ? "完成预算" : "提前失败";
    detail.append(node("summary", `${run.id} · ${outcome} · 最佳 HLS ${number(run.best_metrics?.latency_us)} μs`));
    if (!run.audited) {
      detail.append(node("p", run.error || "尚无完整证据"));
    } else {
      if (report.protocol.candidate_order_comparison) {
        detail.append(node("p", `搜索条件：${groupLabel(run)}；排列基础种子：${run.candidate_order_seed ?? "未重排"}。`));
      }
      if (report.protocol.coverage_comparison) {
        detail.append(node("p", `搜索条件：${groupLabel(run)}；覆盖为人工指定调度，不是模型学会探索。两随机策略不作逐seed配对。`));
      }
      detail.append(node("p", `评估 ${summary.evaluated}/${report.protocol.budget}，原生测试通过 ${summary.correctness_passed}，HLS 可行 ${summary.feasible_synthesized}；HLS 最佳 ${summary.best_id ?? "—"}。`));
      detail.append(node("p", `候选执行状态：${Object.entries(run.statuses).map(([key, count]) => `${key}=${count}`).join("，")}。synthesized 表示综合完成，不等于满足约束或 RTL 通过。`));
      detail.append(node("p", `模型 ${summary.model_attempts} 次，失败 ${summary.model_failures} 次；模型耗时 ${number(summary.model_wall_seconds)} s，评估耗时 ${number(summary.evaluation_seconds)} s，总耗时 ${number(summary.wall_seconds)} s。`));
      detail.append(node("p", `候选顺序：${run.order.join(" → ")}`));
      if (run.best_metrics) {
        const r = run.best_metrics.resources;
        detail.append(node("p", `最佳资源：LUT ${r.lut} / FF ${r.ff} / DSP ${r.dsp} / BRAM_18K ${r.bram}。首次达到 HLS 参考主目标：${noReference ? "未测量" : run.first_reference_hit ?? "未达到"}。`));
      }
      if (run.rtl) {
        detail.append(node("p", `RTL 验收：${run.rtl.status}；通过方案 ${run.rtl.best_rtl_id ?? "—"}，对应 HLS 估计 ${number(run.rtl.best_metrics?.latency_us)} μs；拒绝 ${run.rtl.rejected_ids.join(", ") || "无"}。不是板卡计时。`));
      }
      if (summary.planner_error) detail.append(node("p", `停止原因：${summary.planner_error}`));
    }
    details.append(detail);
  }
  document.querySelector("#rtlStatus").textContent = "新一轮 RTL 验收未在此报告中确认。";
  if (report.rtl?.complete) {
    document.querySelector("#rtlStatus").textContent = `本轮独立 RTL 验收已结束：${report.rtl.passed} 种配置通过，${report.rtl.rejected} 种配置被拒绝，${report.rtl.finalists_passed}/${report.protocol.jobs.length} 组有通过方案。HLS 最佳与 RTL 通过方案分开记录；没有上板。`;
  }
  document.querySelector("#suiteContent").hidden = false;
}

async function loadSuite() {
  const version = ++requestVersion;
  const endpoint = `/api/model-suite?suite=${document.querySelector("#suiteSelect").value}`;
  document.querySelector("#auditLink").href = endpoint;
  document.querySelector("#suiteContent").hidden = true;
  document.querySelector("#suiteStatus").textContent = "正在读取所选批次的归档审计结果…";
  document.querySelector("#suiteStatus").classList.remove("warning");
  try {
    const response = await fetch(endpoint);
    if (version !== requestVersion) return;
    if (!response.ok) throw new Error(`审计读取失败（${response.status}）`);
    const payload = await response.json();
    if (version !== requestVersion) return;
    if (!payload.available) {
      document.querySelector("#suiteStatus").textContent = "本轮尚未发布审计结果。云端实验可能仍在运行；此页不使用演示数据填充。";
      return;
    }
    renderSuite(payload.report);
  } catch (error) {
    if (version !== requestVersion) return;
    document.querySelector("#suiteStatus").textContent = error.message;
    document.querySelector("#suiteStatus").classList.add("warning");
  }
}

const initialSuite = new URLSearchParams(location.search).getAll("suite");
if (initialSuite.length === 1 && ["budget8", "budget16", "order39", "coverage42", "prefixsum14"].includes(initialSuite[0])) {
  document.querySelector("#suiteSelect").value = initialSuite[0];
}
document.querySelector("#suiteSelect").addEventListener("change", loadSuite);
loadSuite();
