const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Minimal DOM fixture for the asynchronous loader, not a substitute for browser QA.
function setup(search = '') {
  const nodes = new Map();
  const element = () => ({textContent: '', hidden: false, children: [],
    classList: {add() {}, remove() {}, toggle() {}},
    append(child) {this.children.push(child);},
    replaceChildren() {this.children = [];},
    addEventListener(name, callback) {this[name] = callback;}});
  const find = id => {if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id);};
  find('#suiteSelect').value = 'budget8';
  const requests = [];
  const context = vm.createContext({document: {querySelector: find, createElement: element}, location: {search}, URLSearchParams,
    fetch: url => new Promise((resolve, reject) => requests.push({url, resolve, reject}))});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/evidence.js'), 'utf8'), context);
  return {find, requests, select(value) {find('#suiteSelect').value = value; return find('#suiteSelect').change();}};
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const response = report => ({ok: true, json: async () => ({available: true, report})});
const report = (budget, rtl = null) => ({protocol: {budget, jobs: [], goal: 'latency'}, runs: [], groups: [],
  complete: true, all_evidence_audited: true, rtl});

// Synthetic summaries test presentation only; they are not experiment evidence.
function orderReport() {
  const result = report(8);
  result.protocol.candidate_order_comparison = true;
  for (const benchmark of ['matmul', 'fir', 'conv2d']) {
    for (const group of ['no-feedback', 'feedback']) {
      for (const order_condition of ['canonical', 'shuffled']) {
        const latency = group === 'no-feedback' ? (order_condition === 'canonical' ? 100 : 130)
          : (order_condition === 'canonical' ? 200 : 120);
        for (const repeat of [0, 1]) result.protocol.jobs.push({benchmark, group, order_condition, repeat});
        result.groups.push({benchmark, group, order_condition, planned_runs: 2, audited_runs: 2,
          complete_with_feasible: 2, median_latency_us: latency, worst_latency_us: latency,
          reference_hits: 0, median_wall_seconds: 300, repeat_orders_identical: true});
      }
    }
  }
  return result;
}
const cells = row => row.children.map(cell => cell.textContent);

function coverageReport() {
  const result = report(8);
  result.protocol.coverage_comparison = true;
  for (const benchmark of ['matmul', 'fir', 'conv2d']) {
    for (const [group, coverage_policy, planned_runs, latency] of [
      ['random', 'none', 5, 140], ['random', 'pipeline-warmup-v1', 5, 100],
      ['no-feedback', 'pipeline-warmup-v1', 2, 200], ['feedback', 'pipeline-warmup-v1', 2, 100]]) {
      for (let i = 0; i < planned_runs; i++) result.protocol.jobs.push({benchmark, group, coverage_policy});
      result.groups.push({benchmark, group, coverage_policy, planned_runs, audited_runs: planned_runs,
        complete_with_feasible: planned_runs, median_latency_us: latency, worst_latency_us: latency,
        reference_hits: 0, median_wall_seconds: 80, repeat_orders_identical: null});
    }
  }
  return result;
}

function prefixsumReport() {
  const result = coverageReport();
  result.protocol.experiment = 'prefixsum-transfer-v1';
  result.protocol.reference_policy = 'not_measured';
  result.protocol.jobs = result.protocol.jobs.filter(job => job.benchmark === 'matmul')
    .map(job => ({...job, benchmark: 'prefixsum'}));
  result.groups = result.groups.filter(group => group.benchmark === 'matmul')
    .map(group => ({...group, benchmark: 'prefixsum', reference_hits: null}));
  return result;
}

test('prefixsum deep link uses its own archive and keeps unmeasured reference explicit', async () => {
  const ui = setup('?suite=prefixsum14');
  assert.equal(ui.requests[0].url, '/api/model-suite?suite=prefixsum14');
  ui.requests[0].resolve(response(prefixsumReport()));
  await settle();
  assert.match(ui.find('#protocolText').textContent, /^prefixsum ·/);
  assert.doesNotMatch(ui.find('#protocolText').textContent, /matmul|fir|conv2d/);
  assert.match(ui.find('#coverageText').textContent, /4个条件组、14次搜索/);
  assert.equal(ui.find('#taskScopeText').hidden, false);
  assert.match(ui.find('#taskScopeText').textContent, /不保证模型预训练未见/);
  assert.match(ui.find('#referenceText').textContent, /未测量/);
  assert.doesNotMatch(ui.find('#referenceText').textContent, /参考为历史/);
  assert.deepEqual(ui.find('#groupRows').children.map(row => cells(row)[5]), Array(4).fill('未测量'));
  assert.equal(ui.find('#coverageRows').children.length, 4);
});

test('prefixsum incomplete evidence never turns missing values into a matched benefit', async () => {
  for (const mutation of ['missing', 'null', 'unaudited', 'incomplete']) {
    const ui = setup('?suite=prefixsum14');
    const fixture = prefixsumReport();
    if (mutation === 'missing') fixture.groups.splice(1, 1);
    if (mutation === 'null') fixture.groups[1].median_latency_us = null;
    if (mutation === 'unaudited') fixture.all_evidence_audited = false;
    if (mutation === 'incomplete') fixture.complete = false;
    ui.requests[0].resolve(response(fixture));
    await settle();
    assert.equal(cells(ui.find('#coverageRows').children[0])[5], '—', mutation);
    assert.ok(ui.find('#groupRows').children.every(row => cells(row)[5] === '未测量'), mutation);
    assert.match(ui.find('#coverageText').textContent, /4个条件组、14次搜索/);
  }
});

test('switching prefixsum and historical batches clears transfer and reference notes', async () => {
  const ui = setup('?suite=prefixsum14');
  ui.requests[0].resolve(response(prefixsumReport()));
  await settle();
  for (const [suite, fixture] of [['coverage42', coverageReport()], ['order39', orderReport()],
    ['budget16', report(16)], ['budget8', report(8)], ['prefixsum14', prefixsumReport()]]) {
    const loading = ui.select(suite);
    ui.requests.at(-1).resolve(response(fixture));
    await loading;
    const transfer = suite === 'prefixsum14';
    assert.equal(ui.find('#taskScopeText').hidden, !transfer);
    assert.equal(ui.find('#referenceText').textContent.includes('未测量'), transfer);
    assert.equal(ui.find('#orderComparison').hidden, suite !== 'order39');
    assert.equal(ui.find('#coverageComparison').hidden, !['coverage42', 'prefixsum14'].includes(suite));
    if (suite === 'coverage42') {
      assert.match(ui.find('#coverageText').textContent, /12个条件组、42次搜索/);
      assert.ok(ui.find('#groupRows').children.every(row => cells(row)[5] !== '未测量'));
    }
  }
});

const prefixsumArchive = path.join(__dirname, '../artifacts/model-prefixsum-suite-20260927/v1/final-audit.json');
test('published prefixsum audit shows four conditions, 14 finalists and no fictitious reference hits',
  {skip: !fs.existsSync(prefixsumArchive)}, async () => {
    const ui = setup('?suite=prefixsum14');
    ui.requests[0].resolve(response(JSON.parse(fs.readFileSync(prefixsumArchive, 'utf8'))));
    await settle();
    assert.match(ui.find('#suiteStatus').textContent, /14\/14/);
    assert.equal(ui.find('#runDetails').children.length, 14);
    assert.equal(ui.find('#groupRows').children.length, 4);
    assert.deepEqual(ui.find('#groupRows').children.map(row => cells(row)[3]), ['1.31', '1.31', '1.93', '1.93']);
    assert.deepEqual(ui.find('#coverageRows').children.map(row => cells(row)[5]),
      ['+0.00%', '+47.33%', '+47.33%', '+0.00%']);
    for (const detail of ui.find('#runDetails').children) {
      const text = detail.children.map(item => item.textContent).join('\n');
      assert.match(text, /首次达到 HLS 参考主目标：未测量/);
      assert.doesNotMatch(text, /未达到|null/);
      assert.match(text, /RTL 验收：rtl_passed/);
    }
    assert.match(ui.find('#rtlStatus').textContent, /9 种配置通过，0 种配置被拒绝，14\/14/);
  });

test('coverage42 deep link, labels and twelve comparisons keep both random policies separate', async () => {
  const ui = setup('?suite=coverage42');
  assert.equal(ui.requests[0].url, '/api/model-suite?suite=coverage42');
  const fixture = coverageReport();
  fixture.groups.reverse();
  ui.requests[0].resolve(response(fixture));
  await settle();
  assert.equal(ui.find('#coverageComparison').hidden, false);
  assert.equal(ui.find('#coverageText').hidden, false);
  assert.equal(ui.find('#orderComparison').hidden, true);
  const labels = ui.find('#groupRows').children.map(row => cells(row)[1]);
  assert.equal(labels.filter(label => label === '随机搜索 · 无覆盖').length, 3);
  assert.equal(labels.filter(label => label === '随机搜索 · 流水覆盖').length, 3);
  const comparisons = ui.find('#coverageRows').children.map(cells);
  assert.equal(comparisons.length, 12);
  assert.deepEqual(comparisons[0], ['matmul', '随机覆盖增量', '5 / 5', '5 / 5',
    '140.00 → 100.00', '-28.57%', '80.00 → 80.00']);
  assert.deepEqual(comparisons.slice(0, 4).map(row => row[5]), ['-28.57%', '+100.00%', '+0.00%', '-50.00%']);
});

test('coverage missing, unaudited, duplicate or invalid conditions never produce benefit percentages', async () => {
  for (const mutation of ['partial', 'missing', 'unaudited', 'wrong-plan', 'zero', 'nan',
    'batch-unaudited', 'batch-incomplete', 'duplicate', 'wrong-policy', 'missing-plan']) {
    const ui = setup('?suite=coverage42');
    const fixture = coverageReport();
    const group = fixture.groups[1];
    if (mutation === 'partial') group.complete_with_feasible = 4;
    if (mutation === 'missing') fixture.groups.splice(1, 1);
    if (mutation === 'unaudited') group.audited_runs = 4;
    if (mutation === 'wrong-plan') group.planned_runs = 4;
    if (mutation === 'zero') group.median_latency_us = 0;
    if (mutation === 'nan') group.median_latency_us = NaN;
    if (mutation === 'batch-unaudited') fixture.all_evidence_audited = false;
    if (mutation === 'batch-incomplete') fixture.complete = false;
    if (mutation === 'duplicate') fixture.groups.push({...group});
    if (mutation === 'wrong-policy') group.coverage_policy = 'unknown';
    if (mutation === 'missing-plan') fixture.protocol.jobs = fixture.protocol.jobs.filter(job =>
      !(job.benchmark === 'matmul' && job.group === 'random' && job.coverage_policy === 'pipeline-warmup-v1'));
    ui.requests[0].resolve(response(fixture));
    await settle();
    assert.equal(cells(ui.find('#coverageRows').children[0])[5], '—', mutation);
  }
});

const coverageArchive = path.join(__dirname, '../artifacts/model-coverage-suite-20260927/v1/final-audit.json');
test('published 42-run audit keeps twelve conditions, actual comparisons and both RTL fallbacks',
  {skip: !fs.existsSync(coverageArchive)}, async () => {
    const ui = setup('?suite=coverage42');
    ui.requests[0].resolve(response(JSON.parse(fs.readFileSync(coverageArchive, 'utf8'))));
    await settle();
    assert.match(ui.find('#suiteStatus').textContent, /42\/42/);
    assert.equal(ui.find('#groupRows').children.length, 12);
    assert.equal(ui.find('#runDetails').children.length, 42);
    assert.deepEqual(ui.find('#coverageRows').children.map(row => cells(row)[5]),
      ['-28.57%', '+120.61%', '+0.00%', '-54.67%', '+0.00%', '+0.00%', '+0.00%', '+0.00%',
        '+0.00%', '+54.92%', '+54.92%', '+0.00%']);
    for (const id of ['conv2d-random-none-s3-r0', 'conv2d-random-pipeline-warmup-v1-s2-r0']) {
      const detail = ui.find('#runDetails').children.find(item => item.children[0].textContent.startsWith(id));
      const text = detail.children.map(item => item.textContent).join('\n');
      assert.match(text, /HLS 最佳 p2-u2-a1/);
      assert.match(text, /通过方案 p1-u1-a1/);
      assert.match(text, /拒绝 p2-u2-a1/);
      assert.match(text, /搜索条件：随机搜索 · (无覆盖|流水覆盖)/);
    }
    assert.match(ui.find('#rtlStatus').textContent, /17 种配置通过，1 种配置被拒绝，42\/42/);
  });

test('switching coverage to order and old batches clears incompatible comparisons', async () => {
  const ui = setup('?suite=coverage42');
  ui.requests[0].resolve(response(coverageReport()));
  await settle();
  for (const [suite, data] of [['order39', orderReport()], ['budget16', report(16)], ['budget8', report(8)]]) {
    const loading = ui.select(suite);
    ui.requests.at(-1).resolve(response(data));
    await loading;
    assert.equal(ui.find('#coverageComparison').hidden, true);
    assert.equal(ui.find('#coverageRows').children.length, 0);
    assert.equal(ui.find('#coverageText').hidden, true);
    assert.equal(ui.find('#orderComparison').hidden, suite !== 'order39');
  }
});

test('order39 deep link selects its own audit; unknown query never becomes an endpoint', async () => {
  const ui = setup('?suite=order39');
  assert.equal(ui.requests[0].url, '/api/model-suite?suite=order39');
  ui.requests[0].resolve(response(orderReport()));
  await settle();
  assert.equal(ui.find('#orderComparison').hidden, false);
  const other = setup('?suite=../../secrets');
  assert.equal(other.requests[0].url, '/api/model-suite?suite=budget8');
});

test('order labels and six matched comparisons keep feedback conditions separate', async () => {
  const ui = setup();
  const fixture = orderReport();
  fixture.groups.reverse(); // Pair by condition, not by array adjacency.
  ui.requests[0].resolve(response(fixture));
  await settle();
  const labels = ui.find('#groupRows').children.map(row => cells(row)[1]);
  assert.equal(labels.filter(label => label.includes('原顺序A')).length, 6);
  assert.equal(labels.filter(label => label.includes('重排B')).length, 6);
  const comparisons = ui.find('#orderRows').children.map(cells);
  assert.equal(comparisons.length, 6);
  assert.deepEqual(comparisons[0], ['matmul', '无', '2 / 2', '2 / 2', '100.00 → 130.00', '+30.00%', '300.00 → 300.00']);
  assert.equal(comparisons[1][5], '-40.00%');
});

test('incomplete, missing, or invalid matched data never produces a benefit percentage', async () => {
  for (const mutation of ['partial', 'missing', 'unaudited', 'wrong-plan', 'zero', 'nan', 'batch-unaudited']) {
    const ui = setup();
    const fixture = orderReport();
    const group = fixture.groups[1];
    if (mutation === 'partial') group.complete_with_feasible = 1;
    if (mutation === 'missing') fixture.groups.splice(1, 1);
    if (mutation === 'unaudited') group.audited_runs = 1;
    if (mutation === 'wrong-plan') group.planned_runs = 1;
    if (mutation === 'zero') group.median_latency_us = 0;
    if (mutation === 'nan') group.median_latency_us = NaN;
    if (mutation === 'batch-unaudited') fixture.all_evidence_audited = false;
    ui.requests[0].resolve(response(fixture));
    await settle();
    const first = cells(ui.find('#orderRows').children[0]);
    assert.equal(first[5], '—', mutation);
    assert.match(first[3], /\/ 2$/, mutation);
  }
});

const orderArchive = path.join(__dirname, '../artifacts/model-order-suite-20260927/v1/final-audit.json');
test('published 39-run audit retains all conditions and the actual RTL fallback',
  {skip: !fs.existsSync(orderArchive)}, async () => {
    const actual = JSON.parse(fs.readFileSync(orderArchive, 'utf8'));
    const ui = setup('?suite=order39');
    ui.requests[0].resolve(response(actual));
    await settle();
    assert.equal(ui.find('#suiteContent').hidden, false);
    assert.equal(ui.find('#groupRows').children.length, 15);
    assert.equal(ui.find('#runDetails').children.length, 39);
    assert.match(ui.find('#suiteStatus').textContent, /39\/39/);
    assert.deepEqual(ui.find('#orderRows').children.map(row => cells(row)[5]),
      ['+30.00%', '-41.07%', '+0.00%', '+0.00%', '+0.00%', '+0.00%']);
    const fallback = ui.find('#runDetails').children.find(detail =>
      detail.children[0].textContent.startsWith('conv2d-random-none-s3-r0'));
    const text = fallback.children.map(item => item.textContent).join('\n');
    assert.match(text, /HLS 最佳 p2-u2-a1/);
    assert.match(text, /通过方案 p1-u1-a1/);
    assert.match(text, /拒绝 p2-u2-a1/);
    assert.match(ui.find('#rtlStatus').textContent, /17 种配置通过，1 种配置被拒绝，39\/39/);
  });

test('switching back to an old batch clears order-only presentation', async () => {
  const ui = setup('?suite=order39');
  ui.requests[0].resolve(response(orderReport()));
  await settle();
  const loading = ui.select('budget16');
  ui.requests[1].resolve(response(report(16)));
  await loading;
  assert.equal(ui.find('#orderComparison').hidden, true);
  assert.equal(ui.find('#orderRows').children.length, 0);
  assert.equal(ui.find('#orderText').hidden, true);
});

test('missing selection clears old result and does not substitute it', async () => {
  const ui = setup();
  ui.requests[0].resolve(response(report(8)));
  await settle();
  assert.equal(ui.find('#suiteContent').hidden, false);
  const loading = ui.select('budget16');
  assert.equal(ui.find('#suiteContent').hidden, true);
  assert.equal(ui.find('#auditLink').href, '/api/model-suite?suite=budget16');
  ui.requests[1].resolve({ok: true, json: async () => ({available: false})});
  await loading;
  assert.equal(ui.find('#suiteContent').hidden, true);
  assert.match(ui.find('#suiteStatus').textContent, /尚未发布/);
});

test('switching to unverified snapshot clears previous RTL completion', async () => {
  const ui = setup();
  ui.requests[0].resolve(response(report(8, {complete: true, passed: 14, rejected: 1, finalists_passed: 27})));
  await settle();
  assert.match(ui.find('#rtlStatus').textContent, /14 种配置通过/);
  const loading = ui.select('budget16');
  ui.requests[1].resolve(response(report(16)));
  await loading;
  assert.match(ui.find('#protocolText').textContent, /每组 16 次/);
  assert.match(ui.find('#rtlStatus').textContent, /未在此报告中确认/);
});

test('late response and late JSON from previous selection cannot overwrite current result', async () => {
  for (const stage of ['response', 'json']) {
    const ui = setup();
    let finishJson;
    if (stage === 'json') {
      ui.requests[0].resolve({ok: true, json: () => new Promise(resolve => {finishJson = resolve;})});
      await settle();
    }
    const loading = ui.select('budget16');
    ui.requests[1].resolve(response(report(16)));
    await loading;
    if (stage === 'json') finishJson({available: true, report: report(8)});
    else ui.requests[0].resolve(response(report(8)));
    await settle();
    assert.match(ui.find('#protocolText').textContent, /每组 16 次/);
  }
});

test('late error is ignored, current error hides prior results', async () => {
  const ui = setup();
  const loading = ui.select('budget16');
  ui.requests[1].resolve(response(report(16)));
  await loading;
  ui.requests[0].reject(new Error('stale failure'));
  await settle();
  assert.doesNotMatch(ui.find('#suiteStatus').textContent, /stale failure/);
  const bad = ui.select('budget8');
  ui.requests[2].resolve({ok: false, status: 503});
  await bad;
  assert.equal(ui.find('#suiteContent').hidden, true);
  assert.match(ui.find('#suiteStatus').textContent, /503/);
});
