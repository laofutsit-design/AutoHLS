const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
function setup() {
  const nodes = new Map(), requests = [], timers = new Map(); let timerId = 0;
  const element = () => ({textContent:'',value:'',hidden:false,children:[],attributes:{},
    append(...items){this.children.push(...items);},replaceChildren(){this.children=[];},
    addEventListener(name,fn){this[name]=fn;},setAttribute(name,value){this.attributes[name]=value;}});
  const find = id => {if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  find('#scenario').value='matched';find('#cursor').value='0';
  const context=vm.createContext({document:{querySelector:find,createElement:element,createElementNS:element},
    fetch:url=>new Promise((resolve,reject)=>requests.push({url,resolve,reject})),
    setInterval:fn=>{timers.set(++timerId,fn);return timerId;},clearInterval:id=>timers.delete(id)});
  vm.runInContext(fs.readFileSync('web/replay.js','utf8'),context);
  return {find,requests,timers};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const response=value=>({ok:true,json:async()=>value});
function fixture(scenario='matched') {
  const rows=()=>Array.from({length:256},()=>Array(32).fill(0));
  return {available:true,mode:'read_only_application_replay',software_audit:{software_verified:true,manifest_sha256:'fixture'},board_verified:false,board:null,
    data:{scenario,data_source:'synthetic_deterministic',samples:256,channels:32,board_verified:false,cpu_speedup:null,
      raw:rows(),target:rows(),corrected:rows(),before:{rmse_counts:1},after:{rmse_counts:0,max_abs_counts:0},quality_target_met:true,input_sha256:'fixture'}};
}
test('software curves never imply a board check',async()=>{
  const ui=setup();ui.requests[0].resolve(response(fixture()));await settle();
  assert.equal(ui.find('#content').hidden,false);assert.match(ui.find('#boardState').textContent,/尚未上板/);
  assert.equal(ui.find('#channels').children.length,32);assert.equal(ui.find('#channel').children.length,32);
});
test('refresh failure hides stale success and pauses playback',async()=>{
  const ui=setup();ui.requests[0].resolve(response(fixture()));await settle();ui.find('#play').click();
  assert.equal(ui.timers.size,1);ui.find('#refresh').click();assert.equal(ui.timers.size,0);
  assert.equal(ui.find('#content').hidden,true);ui.requests[1].resolve({ok:false});await settle();
  assert.match(ui.find('#status').textContent,/校验失败/);assert.equal(ui.find('#content').hidden,true);
});
test('stale scenario response cannot replace newer condition',async()=>{
  const ui=setup();ui.find('#scenario').value='drift';ui.find('#scenario').change();
  ui.requests[1].resolve(response(fixture('drift')));await settle();ui.requests[0].resolve(response(fixture()));await settle();
  assert.match(ui.find('#scenarioTitle').textContent,/额外偏置/);
});
test('invalid or missing evidence never displays successful results',async()=>{
  for(const change of [d=>d.available=false,d=>d.data.scenario='bypass',d=>d.data.raw=[],
    d=>d.data.cpu_speedup=1.3,d=>d.data.after.rmse_counts=NaN,d=>d.board_verified=true]) {
    const ui=setup(),data=fixture();change(data);ui.requests[0].resolve(response(data));await settle();assert.equal(ui.find('#content').hidden,true);
  }
});
test('playback ends at last sample and rewind resets it',async()=>{
  const ui=setup();ui.requests[0].resolve(response(fixture()));await settle();
  ui.find('#cursor').value='254';ui.find('#play').click();[...ui.timers.values()][0]();
  assert.equal(ui.find('#cursor').value,'255');assert.equal(ui.timers.size,0);
  ui.find('#rewind').click();assert.equal(ui.find('#cursor').value,'0');
});
test('quality failure is shown separately from exact-input board acceptance',async()=>{
  const ui=setup(),data=fixture('matched');data.data.after={rmse_counts:4,max_abs_counts:21};data.data.quality_target_met=false;
  data.board_verified=true;data.board={board_verified:true,scenarios:3,output_checks:24576,cpu_speedup:null,bit_sha256:'fixture'};
  ui.requests[0].resolve(response(data));await settle();assert.match(ui.find('#quality').textContent,/未达到/);assert.match(ui.find('#boardState').textContent,/实板/);
});
