(() => {
  'use strict';
  const $ = id => document.querySelector('#' + id);
  const captions = {
    matched: ['正常补偿：模型与误差匹配', '增益与串扰来自已知模型，逆矩阵应恢复原始信号。此零残差结论仅覆盖这组合成输入，不代表真实传感器的校准精度。'],
    bypass: ['关闭补偿：保留原有误差', '使用单位矩阵作为对照，输入信号原样通过。计算仍可完全正确，但应用质量不达标；正确执行与有效补偿是不同问题。'],
    drift: ['额外偏置：暴露模型失配', '前 8 个通道额外增加 24 counts 的偏置，系数保持不变。残差会留下来，提示需要重新辨识或引入偏置项，而不是把告警自动清零。']
  };
  let sequence = 0, active = null, timer = null;
  function pause() {if (timer !== null) clearInterval(timer); timer = null; $('play').textContent = '播放回放';}
  const format = value => Number(value).toFixed(2);
  for (let n = 0; n < 32; n++) {
    const option = document.createElement('option'); option.value = String(n); option.textContent = 'CH ' + String(n).padStart(2, '0'); $('channel').append(option);
  }
  $('channel').value = '0';
  function svg(name, attributes, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    $('plot').append(node); return node;
  }
  function draw() {
    if (!active) return;
    const channel = Number($('channel').value), position = Number($('cursor').value);
    const x = t => 54 + t / 255 * 1000, y = v => 139 - v / 600 * 112;
    $('plot').replaceChildren();
    for (const value of [-600, -300, 0, 300, 600]) {
      svg('line', {x1:54, x2:1054, y1:y(value), y2:y(value), stroke:'#e6ebe5'});
      svg('text', {x:44, y:y(value)+4, 'text-anchor':'end', fill:'#748174', 'font-size':11}, String(value));
    }
    for (const t of [0, 64, 128, 192, 255]) svg('text', {x:x(t), y:279, 'text-anchor':'middle', fill:'#748174', 'font-size':11}, String(t));
    svg('text', {x:54, y:12, fill:'#748174', 'font-size':11}, 'counts');
    for (const [key, color, dash] of [['raw','#bd7131',''], ['target','#7385ba','6 5'], ['corrected','#137968','']]) {
      svg('polyline', {points:active[key].map((row,t)=>x(t).toFixed(1)+','+y(row[channel]).toFixed(1)).join(' '), fill:'none', stroke:color, 'stroke-width':key==='corrected'?2:1.5, 'stroke-dasharray':dash});
    }
    svg('line', {x1:x(position), x2:x(position), y1:20, y2:253, stroke:'#426559', 'stroke-dasharray':'3 4'});
    svg('circle', {cx:x(position), cy:y(active.corrected[position][channel]), r:4, fill:'#137968', stroke:'#fff', 'stroke-width':2});
    $('sample').textContent = '样本 ' + position + ' / 255';
    $('channels').replaceChildren();
    for (let n = 0; n < 32; n++) {
      const value = active.corrected[position][n] - active.target[position][n];
      const cell = document.createElement('div'), label = document.createElement('small'), count = document.createElement('span');
      cell.className = 'channelcell' + (Math.abs(value) > 1 ? ' bad' : '');
      label.textContent = 'CH ' + String(n).padStart(2,'0'); count.textContent = String(value);
      cell.append(label, count); $('channels').append(cell);
    }
  }
  function valid(data, scenario) {
    if (!data.available || data.mode !== 'read_only_application_replay' || !data.software_audit?.software_verified) throw Error('missing');
    const d = data.data;
    if (d.scenario !== scenario || d.data_source !== 'synthetic_deterministic' || d.samples !== 256 || d.channels !== 32 || d.board_verified !== false || d.cpu_speedup !== null) throw Error('contract');
    for (const key of ['raw','target','corrected']) if (d[key].length !== 256 || d[key].some(row=>row.length!==32 || row.some(v=>!Number.isFinite(v)))) throw Error('shape');
    if (![d.before.rmse_counts, d.after.rmse_counts, d.after.max_abs_counts].every(v=>Number.isFinite(v)&&v>=0) || d.quality_target_met !== (d.after.max_abs_counts <= 1)) throw Error('metric');
    if (data.board_verified !== false && data.board_verified !== true) throw Error('board');
    if (data.board_verified && (!data.board?.board_verified || data.board.scenarios !== 3 || data.board.output_checks !== 24576 || data.board.cpu_speedup !== null)) throw Error('board');
    if (!data.board_verified && data.board !== null) throw Error('unverified');
  }
  async function load() {
    const request = ++sequence, scenario = $('scenario').value;
    pause(); active = null; $('content').hidden = true; $('cursor').value = '0';
    $('status').textContent = '正在重新计算并核验本地证据…';
    try {
      const response = await fetch('/api/channel-replay?scenario=' + encodeURIComponent(scenario), {cache:'no-store'});
      if (!response.ok) throw Error('http');
      const data = await response.json();
      if (request !== sequence) return;
      valid(data, scenario); active = data.data;
      $('before').textContent = format(active.before.rmse_counts); $('after').textContent = format(active.after.rmse_counts);
      $('maxError').textContent = format(active.after.max_abs_counts);
      $('quality').textContent = active.quality_target_met ? '达到阈值' : '未达到阈值'; $('quality').className = 'quality' + (active.quality_target_met ? '' : ' warn');
      $('scenarioTitle').textContent = captions[scenario][0]; $('explanation').textContent = captions[scenario][1];
      $('coefficients').textContent = scenario==='bypass' ? '对照：WQ8 = 256 × I，输出等于输入。' : '每对通道：y偶 = x偶 − x奇 / 8；y奇 = x奇 / 2。输入以 0 为起始通道编号。';
      $('inputHash').textContent = active.input_sha256; $('manifestHash').textContent = data.software_audit.manifest_sha256;
      $('bitHash').textContent = data.board_verified ? data.board.bit_sha256 : '尚无本应用实板记录';
      $('boardState').textContent = data.board_verified ? '3 / 3 场景 · 实板逐点功能验证通过' : '本应用尚未上板验证';
      $('boardDetail').textContent = data.board_verified ? '归档记录：24,576 次输出比较、49,152 次输入完整性比较、9,216 次保护区比较通过。每场景 1 次启动、8 帧；页面不连接板卡，不是实时状态。' : '目前只有可复算的软件参考，不沿用其他任务的板测结果。';
      $('jsonLink').href = '/api/channel-replay?scenario=' + scenario;
      $('status').textContent = '本地软件数据已逐项复算。当前场景：' + captions[scenario][0] + '。';
      $('content').hidden = false; draw();
    } catch (_) {
      if (request !== sequence) return;
      active = null; $('content').hidden = true;
      $('status').textContent = '证据缺失或校验失败，已隐藏结果；不使用模拟成功状态替代。';
    }
  }
  $('scenario').addEventListener('change', load); $('refresh').addEventListener('click', load);
  $('channel').addEventListener('change', draw);
  $('cursor').addEventListener('input', () => {pause(); draw();});
  $('rewind').addEventListener('click', () => {pause(); $('cursor').value='0'; draw();});
  $('play').addEventListener('click', () => {
    if (!active) return;
    if (timer !== null) {pause(); return;}
    if (Number($('cursor').value) >= 255) $('cursor').value='0';
    $('play').textContent='暂停回放';
    timer=setInterval(() => {const next=Math.min(255,Number($('cursor').value)+1); $('cursor').value=String(next); draw(); if(next===255) pause();},80);
  });
  load();
})();
