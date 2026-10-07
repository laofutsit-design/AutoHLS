const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = { status: null, result: null, filter: "all", running: false };
const sourceEditor = $("#sourceEditor");
const lineNumbers = $("#lineNumbers");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 2400);
}

function updateLines() {
  const count = Math.max(1, sourceEditor.value.split("\n").length);
  lineNumbers.textContent = Array.from({ length: count }, (_, index) => index + 1).join("\n");
  $("#lineCount").textContent = `${count} 行`;
  lineNumbers.scrollTop = sourceEditor.scrollTop;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

async function loadStatus() {
  try {
    state.status = await api("/api/status");
    const real = state.status.vitis.available;
    const status = $("#toolStatus");
    status.classList.add("ready");
    status.lastElementChild.textContent = real ? "检测到 HLS · 当前页面为演示" : "确定性演示引擎";
    $("#sideMode").textContent = "演示引擎在线";
    $("#deviceName").textContent = state.status.device.name;
    $("#sideDevice").textContent = state.status.device.name.replace("AMD ", "").replace(" SOM", "");
  } catch (error) {
    $("#toolStatus").lastElementChild.textContent = "后端未连接";
    toast(error.message);
  }
}

async function loadEvidence() {
  try {
    const data = await api("/api/research");
    $("#researchEvidence").innerHTML = data.runs.length ? data.runs.map(run =>
      `<div><strong>${escapeHtml(run.benchmark)}</strong> · ${escapeHtml(run.backend)} · ` +
      `${run.summary.correctness_passed}/${run.summary.evaluated} 个候选通过 C++ 测试 · ` +
      `${run.summary.feasible_synthesized} 个可行综合结果 · RTL ${run.summary.best_rtl_verified ? "已验证" : "待验证"} · ` +
      `板卡 ${run.summary.board_verified ? "已验证" : "待验证"}</div>`).join("") : "尚无真实实验；请通过 research 命令执行。";
  } catch (error) {
    $("#researchEvidence").textContent = `实验记录读取失败：${error.message}`;
  }
}

async function loadExample(name, autoRun = false) {
  try {
    const payload = await api(`/api/examples/${encodeURIComponent(name)}`);
    sourceEditor.value = payload.source;
    $("#editorFilename").textContent = `${payload.name}.cpp`;
    $("#topFunction").value = payload.name;
    updateLines();
    if (autoRun) await runExploration();
  } catch (error) {
    toast(error.message);
  }
}

function setRunning(running) {
  state.running = running;
  const button = $("#runButton");
  button.disabled = running;
  button.classList.toggle("running", running);
  $$(".flow-list li").forEach((item, index) => item.classList.toggle("done", running ? index < 3 : Boolean(state.result)));
}

async function runExploration() {
  if (state.running) return;
  if (!sourceEditor.value.trim()) {
    toast("请先输入 C/C++ 算法代码");
    sourceEditor.focus();
    return;
  }
  setRunning(true);
  $("#resultSubtitle").textContent = "正在分析代码结构并评估候选方案…";
  try {
    const goal = $('input[name="goal"]:checked').value;
    const startedAt = performance.now();
    const result = await api("/api/explore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: sourceEditor.value, top: $("#topFunction").value, goal }),
    });
    const wait = Math.max(0, 720 - (performance.now() - startedAt));
    await new Promise(resolve => setTimeout(resolve, wait));
    state.result = result;
    renderResult(result);
    toast(`探索完成：已评估 ${result.summary.explored} 组候选方案`);
  } catch (error) {
    $("#resultSubtitle").textContent = "任务执行失败，请检查输入";
    toast(error.message);
  } finally {
    setRunning(false);
  }
}

function renderResult(result) {
  $("#emptyState").hidden = true;
  $("#resultContent").hidden = false;
  $("#completedTag").classList.add("visible");
  $("#exportButton").disabled = false;
  $("#resultSubtitle").textContent = `${result.goal_label} · ${result.device.name} · 确定性演示评估`;
  $("#bestName").textContent = result.summary.best_name;
  $("#bestId").textContent = `${result.summary.best_id} · 当前目标综合最优`;
  $("#speedup").textContent = result.summary.speedup.toFixed(2);
  $("#latencyReduction").textContent = result.summary.latency_reduction.toFixed(1);
  $("#paretoCount").textContent = result.summary.pareto_count;
  renderChart(result.candidates);
  renderInsights(result);
  renderTable(result.candidates);
}

function renderInsights(result) {
  const profile = result.profile;
  $("#profileStrip").innerHTML = [
    [profile.loops, "循环"],
    [profile.max_loop_depth, "最大嵌套"],
    [profile.arrays, "数组端口"],
  ].map(([value, label]) => `<div class="profile-item"><strong>${value}</strong><small>${label}</small></div>`).join("");
  $("#insightList").innerHTML = result.insights.map(item => `
    <div class="insight-item ${escapeHtml(item.tone)}">
      <strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.body)}</p>
    </div>`).join("");
}

function renderTable(candidates) {
  const visible = state.filter === "pareto" ? candidates.filter(item => item.pareto) : candidates;
  $("#candidateBody").innerHTML = visible.map(item => {
    const m = item.metrics;
    return `<tr>
      <td><span class="rank ${item.rank === 1 ? "top" : ""}">${item.rank}</span></td>
      <td class="strategy-cell"><strong>${escapeHtml(item.name)}${item.pareto ? '<span class="pareto-badge">PARETO</span>' : ""}</strong><small>${escapeHtml(item.description)}</small></td>
      <td class="number-cell">${m.latency_us.toFixed(3)} μs</td>
      <td class="number-cell">${m.ii}</td>
      <td class="number-cell">${m.resources.lut.toLocaleString()}</td>
      <td class="number-cell">${m.resources.dsp}</td>
      <td class="number-cell">${m.resources.bram}</td>
      <td><span class="status-pill ${m.status}"><i></i>${m.status === "success" ? "通过" : "需关注"}</span></td>
      <td><button class="detail-button" data-id="${item.id}">详情 →</button></td>
    </tr>`;
  }).join("");
  $$(".detail-button").forEach(button => button.addEventListener("click", () => openDetail(button.dataset.id)));
}

function renderChart(candidates) {
  const svg = $("#paretoChart");
  const width = 700, height = 300;
  const pad = { left: 56, right: 24, top: 18, bottom: 42 };
  const xs = candidates.map(item => item.metrics.resources.lut);
  const ys = candidates.map(item => item.metrics.latency_us);
  const maxX = Math.max(...xs) * 1.12;
  const maxY = Math.max(...ys) * 1.12;
  const x = value => pad.left + value / maxX * (width - pad.left - pad.right);
  const y = value => height - pad.bottom - value / maxY * (height - pad.top - pad.bottom);
  let markup = "";
  for (let tick = 0; tick <= 4; tick++) {
    const gx = pad.left + tick / 4 * (width - pad.left - pad.right);
    const gy = pad.top + tick / 4 * (height - pad.top - pad.bottom);
    markup += `<line x1="${gx}" y1="${pad.top}" x2="${gx}" y2="${height - pad.bottom}" stroke="var(--line)" stroke-width="1"/>`;
    markup += `<line x1="${pad.left}" y1="${gy}" x2="${width - pad.right}" y2="${gy}" stroke="var(--line)" stroke-width="1"/>`;
    markup += `<text x="${gx}" y="${height - 19}" text-anchor="middle" fill="var(--muted)" font-size="9">${Math.round(maxX * tick / 4 / 100) / 10}k</text>`;
    markup += `<text x="${pad.left - 10}" y="${height - pad.bottom - tick / 4 * (height - pad.top - pad.bottom) + 3}" text-anchor="end" fill="var(--muted)" font-size="9">${(maxY * tick / 4).toFixed(0)}</text>`;
  }
  const frontier = candidates.filter(item => item.pareto).sort((a, b) => a.metrics.resources.lut - b.metrics.resources.lut);
  if (frontier.length > 1) {
    markup += `<polyline points="${frontier.map(item => `${x(item.metrics.resources.lut)},${y(item.metrics.latency_us)}`).join(" ")}" fill="none" stroke="#4e8deb" stroke-width="1.5" stroke-dasharray="4 4" opacity=".55"/>`;
  }
  for (const item of candidates) {
    const color = item.pareto ? "#2d79e9" : "#a7b3c4";
    if (item.recommended) markup += `<circle cx="${x(item.metrics.resources.lut)}" cy="${y(item.metrics.latency_us)}" r="12" fill="#2d79e9" opacity=".1" stroke="none"/>`;
    markup += `<circle class="chart-point" data-id="${item.id}" cx="${x(item.metrics.resources.lut)}" cy="${y(item.metrics.latency_us)}" r="${item.recommended ? 6 : 4.5}" fill="${color}" stroke="white" stroke-width="2" style="cursor:pointer"/>`;
    markup += `<text x="${x(item.metrics.resources.lut) + 8}" y="${y(item.metrics.latency_us) - 7}" fill="var(--muted)" font-size="8">${item.id}</text>`;
  }
  markup += `<text x="${(pad.left + width - pad.right) / 2}" y="${height - 3}" text-anchor="middle" fill="var(--muted)" font-size="9">LUT 占用</text>`;
  markup += `<text x="10" y="${height / 2}" text-anchor="middle" fill="var(--muted)" font-size="9" transform="rotate(-90 10 ${height / 2})">时延 (μs)</text>`;
  svg.innerHTML = markup;
  $$(".chart-point", svg).forEach(point => {
    point.addEventListener("mouseenter", event => showChartTip(event, point.dataset.id));
    point.addEventListener("mouseleave", () => $("#chartTooltip").style.display = "none");
    point.addEventListener("click", () => openDetail(point.dataset.id));
  });
}

function showChartTip(event, id) {
  const item = state.result.candidates.find(candidate => candidate.id === id);
  const tip = $("#chartTooltip");
  const chart = $("#chartWrap").getBoundingClientRect();
  tip.innerHTML = `<strong>${escapeHtml(item.name)}</strong><br>${item.metrics.latency_us.toFixed(3)} μs · ${item.metrics.resources.lut.toLocaleString()} LUT`;
  tip.style.left = `${event.clientX - chart.left}px`;
  tip.style.top = `${event.clientY - chart.top}px`;
  tip.style.display = "block";
}

function openDetail(id) {
  const item = state.result?.candidates.find(candidate => candidate.id === id);
  if (!item) return;
  $("#dialogId").textContent = `${item.id} · RANK ${item.rank}`;
  $("#dialogName").textContent = item.name;
  $("#dialogDescription").textContent = item.description;
  $("#dialogDirectives").textContent = item.directives.length ? item.directives.join("\n") : "# 基线方案不添加优化指令";
  $("#dialogMetrics").innerHTML = [
    [`${item.metrics.latency_us.toFixed(3)} μs`, "时延"],
    [item.metrics.resources.lut.toLocaleString(), "LUT"],
    [item.metrics.resources.dsp, "DSP"],
    [item.metrics.resources.bram, "BRAM"],
  ].map(([value, label]) => `<div><strong>${value}</strong><small>${label}</small></div>`).join("");
  $("#detailDialog").showModal();
}

function exportReport() {
  if (!state.result) return;
  const blob = new Blob([JSON.stringify(state.result, null, 2)], { type: "application/json;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `autohls-${state.result.profile.top_function}-report.json`;
  link.click();
  URL.revokeObjectURL(link.href);
  toast("JSON 报告已导出");
}

function bindEvents() {
  sourceEditor.addEventListener("input", updateLines);
  sourceEditor.addEventListener("scroll", () => { lineNumbers.scrollTop = sourceEditor.scrollTop; });
  sourceEditor.addEventListener("keydown", event => {
    if (event.key === "Tab") {
      event.preventDefault();
      const start = sourceEditor.selectionStart;
      sourceEditor.setRangeText("    ", start, sourceEditor.selectionEnd, "end");
      updateLines();
    }
  });
  $("#exampleSelect").addEventListener("change", event => loadExample(event.target.value));
  $("#fileInput").addEventListener("change", async event => {
    const file = event.target.files[0];
    if (!file) return;
    sourceEditor.value = await file.text();
    $("#editorFilename").textContent = file.name;
    $("#topFunction").value = "";
    updateLines();
    toast(`已导入 ${file.name}`);
  });
  $$('.goal-card input').forEach(input => input.addEventListener("change", () => {
    $$(".goal-card").forEach(card => card.classList.toggle("selected", card.contains($('input[name="goal"]:checked'))));
  }));
  $("#runButton").addEventListener("click", runExploration);
  $("#exportButton").addEventListener("click", exportReport);
  $$(".filter").forEach(button => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    $$(".filter").forEach(item => item.classList.toggle("active", item === button));
    renderTable(state.result.candidates);
  }));
  $("#helpButton").addEventListener("click", () => $("#helpDialog").showModal());
  $("#themeButton").addEventListener("click", () => {
    document.body.classList.toggle("dark");
    localStorage.setItem("autohls-theme", document.body.classList.contains("dark") ? "dark" : "light");
  });
  if (localStorage.getItem("autohls-theme") === "dark") document.body.classList.add("dark");
}

async function init() {
  bindEvents();
  await Promise.all([loadStatus(), loadExample("matmul"), loadEvidence()]);
}

init();
