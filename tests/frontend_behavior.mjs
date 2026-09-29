// Run with Node.js. These are application-logic tests, not visual browser tests.
import fs from "node:fs/promises";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = await fs.readFile(new URL("../dashboard/static/dashboard.js", import.meta.url), "utf8");
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {value: "", textContent: "", innerHTML: "", dataset: {},
    classList: {toggle() {}}, addEventListener() {}, querySelectorAll() {return []}});
  return nodes.get(id);
}
const memory = new Map();
const context = vm.createContext({
  window: {TOOL1_CONFIG: {apiBaseUrl: "http://localhost:8510/tools/tool1"}, location: {href: "http://localhost:8511/demo/tool1/"}},
  document: {addEventListener() {}, getElementById: node, querySelectorAll() {return []}},
  URL, localStorage: {getItem: k => memory.get(k), setItem: (k,v) => memory.set(k,v)},
  fetch: async () => {throw new Error("offline");}, setInterval, clearInterval,
});
vm.runInContext(source, context);
const evaluate = code => vm.runInContext(code, context);
assert.equal(evaluate("apiUrl('/api/runs/current')"), "http://localhost:8510/tools/tool1/api/runs/current");
assert.equal(await evaluate("responseText({text: async () => 'Internal Server Error', status: 500})"), "Internal Server Error");
assert.match(await evaluate("apiFetch('/api/config').catch(e => e.message)"), /Cannot reach/);
evaluate("renderRunStatus([{label:'ACDCPF PF',display_label:'Tool5 (acdcpf) PF',success:true}])");
assert.match(node("runStatusTable").innerHTML, /Tool5/);
node("gridCaseSelect").value = "custom_test";
node("vscScenarioSelect").value = "original";
evaluate(`state.config={session_id:'s1'};
  state.networkInputs={tables:{dc_gen:{rows:[{element_id:3,p_mw:1,source_bus_id:35}],editable_columns:['p_mw']}}};
  updateTableEditFromCell({dataset:{table:'dc_gen',row:'0',col:'p_mw'},textContent:'0.4'});
  buildRunPayload = () => ({grid_case:'custom_test',vsc_setpoint_scenario:'original'});
  saveSettings(); state.tableEdits={}; state.tableEdits=savedTableEditsForCurrentCase();`);
assert.equal(evaluate("state.tableEdits.dc_gen[0].p_mw"), .4);
assert.equal(evaluate("state.tableEdits.dc_gen[0].source_bus_id"), 35);
evaluate("state.config.session_id='restarted'");
assert.equal(evaluate("Object.keys(savedTableEditsForCurrentCase()).length"), 0);
console.log("Frontend logic checks passed: routing, connection errors, plain-text errors, labels, edits, refresh persistence, restart isolation.");
