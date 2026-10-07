const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function setup() {
  const nodes = new Map(), requests = [];
  const element = () => ({textContent: '', hidden: false, children: [], append(x) {this.children.push(x);},
    replaceChildren() {this.children = [];}, addEventListener(name, fn) {this[name] = fn;}});
  const find = id => {if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id);};
  const context = vm.createContext({document: {querySelector: find, createElement: element},
    fetch: url => new Promise((resolve, reject) => requests.push({url, resolve, reject}))});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/delivery.js'), 'utf8'), context);
  return {find, requests};
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const respond = value => ({ok: true, json: async () => value});
const fixture = () => ({available: true, mode: 'read_only_delivery', board_verified: false,
  state: 'remote_snapshot_available', version: 'v2', job: 'fixture', candidate: 'fixture', contract: 'fixture',
  source_sha256: 'fixture-hash', search_metrics: {latency_us: 12}, source: '<script>fixture</script>',
  observation: {phase: 'complete', observed_utc: 'fixture'}});

test('missing evidence never becomes demo success', async () => {
  const ui = setup();
  ui.requests[0].resolve(respond({available: false})); await settle();
  assert.equal(ui.find('#deliveryContent').hidden, true);
  assert.match(ui.find('#deliveryStatus').textContent, /不使用演示/);
});
test('remote complete snapshot is not board or offline acceptance', async () => {
  const ui = setup(); ui.requests[0].resolve(respond(fixture())); await settle();
  assert.match(ui.find('#deliveryStatus').textContent, /尚无/);
  assert.match(ui.find('#deliveryStages').children[3].textContent, /未验收/);
  assert.equal(ui.find('#deliverySource').textContent, '<script>fixture</script>');
  assert.match(ui.find('#firmwareHash').textContent, /尚未取得/);
});
test('verified receipt still requires board confirmation', async () => {
  const ui = setup(), data = fixture(); data.state = 'awaiting_board_confirmation';
  data.receipt = {board_verified: false, bit_sha256: 'fixture-bit'};
  ui.requests[0].resolve(respond(data)); await settle();
  assert.match(ui.find('#deliveryStatus').textContent, /等待本次板卡/);
  assert.match(ui.find('#firmwareHash').textContent, /fixture-bit/);
});
test('refresh failure hides the previous success', async () => {
  const ui = setup(); ui.requests[0].resolve(respond(fixture())); await settle();
  ui.find('#refreshDelivery').click();
  assert.equal(ui.find('#deliveryContent').hidden, true);
  ui.requests[1].resolve({ok: false}); await settle();
  assert.equal(ui.find('#deliveryContent').hidden, true);
  assert.match(ui.find('#deliveryStatus').textContent, /校验失败/);
});
test('out-of-order response cannot replace newer evidence', async () => {
  const ui = setup(); ui.find('#refreshDelivery').click();
  ui.requests[1].resolve(respond({available: false})); await settle();
  ui.requests[0].resolve(respond(fixture())); await settle();
  assert.equal(ui.find('#deliveryContent').hidden, true);
});

function boardFixture() {
  return {...fixture(), board_verified: true, state: 'board_functional_verified', receipt_sha256: 'fixture-receipt',
    receipt: {board_verified: false, bit_sha256: 'fixture-bit'},
    board: {complete: true, board_verified: true, steps: 14, bit_sha256: 'fixture-bit',
      receipt_sha256: 'fixture-receipt', cpu_speedup: null, output_checks: 626688,
      input_checks: 1253376, guard_checks: 86016, archive_sha256: 'fixture-archive',
      final_idle: true, retained_buffers: 0, clock_mhz: 100}};
}

test('audited board results are separate from immutable offline receipt', async () => {
  const ui = setup(); ui.requests[0].resolve(respond(boardFixture())); await settle();
  assert.equal(ui.find('#deliveryContent').hidden, false);
  assert.equal(ui.find('#boardAcceptance').hidden, false);
  assert.match(ui.find('#deliveryStatus').textContent, /14 \/ 14/);
  assert.match(ui.find('#boardChecks').textContent, /1966080/);
  assert.match(ui.find('#boardEndState').textContent, /不是实时监控/);
  assert.match(ui.find('#boardReceipt').textContent, /fixture-receipt/);
});

test('missing or mismatched board evidence fails closed', async () => {
  for (const change of [data => data.board = null,
    data => data.board.bit_sha256 = 'other-bit', data => data.board.receipt_sha256 = 'other-receipt',
    data => data.board.steps = 13, data => data.board.cpu_speedup = 1.263,
    data => data.receipt.board_verified = true]) {
    const ui = setup(), data = boardFixture(); change(data);
    ui.requests[0].resolve(respond(data)); await settle();
    assert.equal(ui.find('#deliveryContent').hidden, true);
  }
});

test('a newer pending release never retains previous board success', async () => {
  const ui = setup(); ui.requests[0].resolve(respond(boardFixture())); await settle();
  ui.find('#refreshDelivery').click();
  ui.requests[1].resolve(respond(fixture())); await settle();
  assert.equal(ui.find('#boardAcceptance').hidden, true);
  assert.match(ui.find('#deliveryStages').children[3].textContent, /未验收/);
});
