"""Limit entry, conversion, independent checking and input coverage regressions."""

import copy
from contextlib import closing
import json
import math
from pathlib import Path
import shutil
import subprocess

import pandas as pd
import pytest
from openpyxl import load_workbook

from acdcpf_opf.benchmarks.stagg5 import benchmark_pf_opf_comparison as benchmark
from acdcpf_opf.benchmarks.stagg5.case_variants import create_acdcpf_network_for_stagg5_case
from acdcpf_opf.benchmarks.stagg5.service import (
    BenchmarkRequest, apply_table_overrides, changed_table_overrides,
    editable_tables_from_network, network_limit_inventory, run_benchmark_request,
)
from acdcpf_opf.data.acdcpf_to_pyomo import ACDCPFToPyomoOptions, convert_acdcpf_network_to_opf_data
from acdcpf_opf.data.equipment_limits import equipment_limit_inventory
from acdcpf_opf.opf.formulations.acdc_opf_pyomo_loss_min import build_acdc_opf_model
from acdcpf_opf.opf.physics_validation import validate_network_physics
from acdcpf_opf.opf.pyomo_acdc_loss_min import PyomoACDCOPFConfig, solve_pyomo_acdc_loss_min_opf


def test_missing_limits_are_visible_and_writable_without_mutating_views():
    net = create_acdcpf_network_for_stagg5_case("hybrid_dcdc")
    tables = editable_tables_from_network(net)
    assert "max_i_ac_ka" not in net.vsc
    assert "max_i_ac_ka" in tables["vsc"]
    assert "rate_mw" in tables["dcdc"]
    assert "p_max_mw" in tables["dc_gen"]
    assert {"ac_bus", "dc_bus", "ac_line", "dc_line"} <= tables.keys()
    edited = {key: table.copy() for key, table in tables.items()}
    edited["vsc"].loc[0, "max_i_ac_ka"] = 0.15
    overrides = changed_table_overrides(tables, edited)
    assert set(overrides) == {"vsc"}
    apply_table_overrides(net, overrides)
    assert net.vsc.at[0, "max_i_ac_ka"] == 0.15
    assert math.isnan(net.vsc.at[1, "max_i_ac_ka"])
    convert_acdcpf_network_to_opf_data(net)


def test_inventory_preserves_missing_data_and_flags_disabled_elements():
    net = create_acdcpf_network_for_stagg5_case("hybrid_dcdc")
    rows = equipment_limit_inventory(net)
    json.dumps(rows, allow_nan=False)
    pmax = next(r for r in rows if r["element"] == "ac_gen[0]" and r["parameter"] == "p_max_mw")
    assert pmax["value"] is None and pmax["status"] == "missing"
    assert "numerical" in pmax["opf_use"]
    net.vsc.at[0, "in_service"] = False
    rows = equipment_limit_inventory(net)
    assert all(r["status"] == "out_of_service" for r in rows if r["element"] == "vsc[0]")


def test_inventory_and_override_preview_use_pending_values():
    rows = network_limit_inventory("original", table_overrides={
        "vsc": [{"element_id": 0, "max_i_ac_ka": 0.15}],
        "ac_gen": [{"element_id": 1, "p_max_mw": 50}],
    })
    limit = next(r for r in rows if r["element"] == "vsc[0]" and r["parameter"] == "max_i_ac_ka")
    assert limit["value"] == 0.15 and limit["status"] == "specified"
    assert limit["physics_check"] == "ac_current_rating"
    assert "max_i_ac_ka" in limit["source"]


@pytest.mark.parametrize("overrides,match", [
    ({"vsc": [{"element_id": 0, "max_i_dc_ka": -1}]}, "positive"),
    ({"vsc": [{"element_id": 0, "v_filter_min_pu": 1.1, "v_filter_max_pu": 1.0}]}, "exceeds"),
    ({"ac_gen": [{"element_id": 1, "p_min_mw": 100, "p_max_mw": 50}]}, "exceeds"),
    ({"dc_bus": [{"element_id": 0, "v_min": 0}]}, "positive"),
    ({"vsc": [{"element_id": 0, "max_i_ac_ka": math.inf}]}, "infinite"),
    ({"ac_gen": [{"element_id": 1, "p_min_mw": math.inf}]}, "infinite"),
])
def test_invalid_limits_are_rejected_atomically(overrides, match):
    net = create_acdcpf_network_for_stagg5_case("original")
    before = copy.deepcopy(net)
    overrides = {"ac_load": [{"element_id": 0, "p_mw": 99}], **overrides}
    with pytest.raises(ValueError, match=match):
        apply_table_overrides(net, overrides)
    for name in overrides:
        pd.testing.assert_frame_equal(getattr(net, name), getattr(before, name))


def test_clearing_optional_generator_limits_restores_unbounded_native_convention():
    net = create_acdcpf_network_for_stagg5_case("original")
    apply_table_overrides(net, {"ac_gen": [{"element_id": 1, "p_min_mw": "", "q_max_mvar": None}]})
    assert net.ac_gen.at[1, "p_min_mw"] == -math.inf
    assert net.ac_gen.at[1, "q_max_mvar"] == math.inf
    convert_acdcpf_network_to_opf_data(net)


def test_legacy_rating_is_visible_and_clearing_it_does_not_reactivate_alias():
    net = create_acdcpf_network_for_stagg5_case("original")
    net.ac_line = net.ac_line.rename(columns={"rate_mva": "rating_mva"})
    tables = editable_tables_from_network(net)
    assert tables["ac_line"].loc[0, "rate_mva"] == 150
    apply_table_overrides(net, {"ac_line": [{"element_id": 0, "rate_mva": None}]})
    data = convert_acdcpf_network_to_opf_data(net)
    assert data["ac_branches"]["AC_LINE0"]["rate"] is None
    assert math.isnan(net.ac_line.at[0, "rating_mva"])


def test_legacy_per_unit_dcdc_bounds_are_audited_and_edited_in_physical_ratio():
    net = create_acdcpf_network_for_stagg5_case("hybrid_dcdc")
    net.dcdc.at[0, "d_pu_min"] = 0.95
    net.dcdc.at[0, "d_pu_max"] = 1.05
    row = net.dcdc.loc[0]
    factor = net.dc_bus.at[row.to_bus, "v_base"] / net.dc_bus.at[row.from_bus, "v_base"]
    inventory = equipment_limit_inventory(net)
    legacy = next(r for r in inventory if r["element"] == "dcdc[0]" and r["parameter"] == "d_pu_min")
    assert legacy["value"] == 0.95 and legacy["unit"] == "pu"
    tables = editable_tables_from_network(net)
    assert tables["dcdc"].loc[0, "d_ratio_min"] == pytest.approx(0.95 * factor)
    apply_table_overrides(net, {"dcdc": [{"element_id": 0, "d_ratio_min": 0.96 * factor}]})
    options = ACDCPFToPyomoOptions(optimize_dcdc_voltage_ratio=True)
    data = convert_acdcpf_network_to_opf_data(net, options=options)
    assert data["dcdc_converters"]["DCDC0"]["d_pu_min"] == pytest.approx(0.96)
    assert data["dcdc_converters"]["DCDC0"]["d_pu_max"] == pytest.approx(1.05)


@pytest.mark.parametrize("poles", [1, 2])
def test_vsc_explicit_bounds_reach_pyomo_in_correct_units(poles):
    net = create_acdcpf_network_for_stagg5_case("original")
    net.pol = poles
    apply_table_overrides(net, {"vsc": [{
        "element_id": 0, "max_i_ac_ka": 0.15, "max_i_dc_ka": 0.2,
        "v_filter_min_pu": 0.92, "v_filter_max_pu": 1.08,
        "v_converter_min_pu": 0.91, "v_converter_max_pu": 1.09,
    }]})
    data = convert_acdcpf_network_to_opf_data(net)
    model = build_acdc_opf_model(data)
    conv = data["converters"]["CONV0"]
    ac_base = net.s_base / (math.sqrt(3) * net.ac_bus.at[net.vsc.at[0, "ac_bus"], "vr_kv"])
    dc_base = net.s_base / (poles * net.dc_bus.at[net.vsc.at[0, "dc_bus"], "v_base"])
    assert conv["i_ac_max"] == pytest.approx(0.15 / ac_base)
    assert conv["i_dc_max"] == pytest.approx(0.2 / dc_base)
    assert model.Uf["CONV0"].bounds == (0.92, 1.08)
    assert model.Ucv["CONV0"].bounds == (0.91, 1.09)
    assert model.Icv_ac["CONV0"].ub == conv["i_ac_max"]
    assert model.Icv_dc["CONV0"].bounds == (-conv["i_dc_max"], conv["i_dc_max"])


def test_one_sided_internal_voltage_bound_conflict_is_not_hidden_by_fallback():
    net = create_acdcpf_network_for_stagg5_case("original")
    net.vsc.at[0, "v_filter_min_pu"] = 1.2
    with pytest.raises(ValueError, match="inherited"):
        convert_acdcpf_network_to_opf_data(net)


@pytest.fixture(scope="module")
def limited_solution():
    net = create_acdcpf_network_for_stagg5_case("original")
    # Test-only limits deliberately restrict the converter; not benchmark ratings.
    apply_table_overrides(net, {"vsc": [{
        "element_id": 0, "max_i_ac_ka": 0.04, "max_i_dc_ka": 0.05,
        "v_filter_min_pu": 0.9, "v_filter_max_pu": 1.1,
        "v_converter_min_pu": 0.9, "v_converter_max_pu": 1.1,
    }]})
    options = ACDCPFToPyomoOptions()
    result = solve_pyomo_acdc_loss_min_opf(net, config=PyomoACDCOPFConfig(print_level=0), conversion_options=options)
    assert result.success, result.message
    return net, result, options


def test_real_ipopt_honors_explicit_vsc_currents(limited_solution):
    _, result, _ = limited_solution
    checks = {c.check: c for c in result.physics_validation.checks if c.element == "vsc[0]"}
    assert checks["ac_current_rating"].value <= 0.04 + 1e-6
    assert checks["dc_current_rating"].value <= 0.05 + 1e-6
    assert checks["filter_voltage"].status == checks["bridge_voltage"].status == "passed"


@pytest.mark.parametrize("field,value,check", [
    ("max_i_ac_ka", 1e-8, "ac_current_rating"),
    ("max_i_dc_ka", 1e-8, "dc_current_rating"),
    ("v_filter_max_pu", 0.8, "filter_voltage"),
    ("v_converter_max_pu", 0.8, "bridge_voltage"),
])
def test_source_check_catches_changed_limits_without_rebuilding_model(limited_solution, field, value, check):
    net, result, options = limited_solution
    net = copy.deepcopy(net)
    net.vsc.at[0, field] = value
    report = validate_network_physics(net, result.extracted_results, options=options, baseline=result.base_pf_result.raw_result)
    assert any(c.element == "vsc[0]" and c.check == check and c.status == "failed" for c in report.checks)


def test_inventory_is_exported_with_applied_source_values(tmp_path):
    result = run_benchmark_request(BenchmarkRequest(
        include_pyflow_reference=False, output_directory=tmp_path,
        include_html=False, table_overrides={"vsc": [{"element_id": 0, "max_i_ac_ka": 0.2}]},
    ))
    files = list(tmp_path.rglob("*opf_results.xlsx"))
    assert files
    with closing(load_workbook(files[0], read_only=True)) as book:
        rows = list(book["equipment_limits"].values)
        records = [dict(zip(rows[0], row)) for row in rows[1:]]
    current = next(r for r in records if r["element"] == "vsc[0]" and r["parameter"] == "max_i_ac_ka")
    assert current["value"] == 0.2 and current["status"] == "specified"
    assert any(run.success and run.label == benchmark.TOOL1_OBJECTIVE_LABEL for run in result.runs)
    markdown = next(tmp_path.rglob("*.md")).read_text(encoding="utf-8")
    assert "Equipment Limit Coverage" in markdown
    assert "max_i_dc_ka" in markdown


def test_complete_test_only_limits_remove_coverage_gaps():
    net = create_acdcpf_network_for_stagg5_case("original")
    # Wide, explicit test assumptions only. No production case is modified.
    apply_table_overrides(net, {
        "ac_gen": [{"element_id": int(idx), "p_min_mw": -1000, "p_max_mw": 1000} for idx in net.ac_gen.index],
        "vsc": [{"element_id": int(idx), "max_i_ac_ka": 1, "max_i_dc_ka": 1,
                 "v_filter_min_pu": 0.9, "v_filter_max_pu": 1.1,
                 "v_converter_min_pu": 0.9, "v_converter_max_pu": 1.1} for idx in net.vsc.index],
    })
    assert all(row["status"] == "specified" for row in equipment_limit_inventory(net))
    result = solve_pyomo_acdc_loss_min_opf(net, config=PyomoACDCOPFConfig(print_level=0))
    assert result.success, result.message
    assert result.physics_validation.status == "passed"


def test_dashboard_limit_check_and_saved_rows_in_javascript():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is only needed for this optional frontend test.")
    script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const elements = {};
const context = vm.createContext({
  document: {addEventListener() {}, getElementById(id) {return elements[id] ||= {}; }},
  request: {grid_case: 'original', vsc_setpoint_scenario: 'original', table_overrides: {}},
  fetch: async (url, options) => {
    assert.equal(url, '/api/network-limits/original/original');
    assert.equal(options.method, 'POST');
    return {ok: true, json: async () => ({rows: [{status: 'missing'}]})};
  },
});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
vm.runInContext(`
  state.networkInputs = {editable: true, tables: {vsc: {
    editable_columns: ['p_mw', 'max_i_ac_ka'],
    rows: [{element_id: 0, p_mw: 60, max_i_ac_ka: null}],
  }}};
  state.tableEdits = {vsc: [{element_id: 0, p_mw: 55}]};
  buildRunPayload = () => request;
  renderTable = rows => JSON.stringify(rows);
`, context);
assert.equal(vm.runInContext('editedRowsForTable("vsc")[0].max_i_ac_ka', context), null);
assert.equal(vm.runInContext('editedRowsForTable("vsc")[0].p_mw', context), 55);
(async () => {
  await vm.runInContext('refreshLimitInventory()', context);
  assert.match(elements.limitMessage.textContent, /1 missing limit fields/);
  assert.equal(elements.checkLimitsButton.disabled, false);
  context.fetch = async () => {throw Error('Invalid limit');};
  await vm.runInContext('refreshLimitInventory()', context);
  assert.match(elements.limitMessage.textContent, /Invalid limit/);
  assert.equal(elements.limitInventory.innerHTML, '');
  assert.equal(elements.checkLimitsButton.disabled, false);
})().catch(error => {console.error(error); process.exit(1);});
"""
    path = Path(__file__).resolve().parents[1] / "dashboard" / "static" / "dashboard.js"
    completed = subprocess.run([node, "-e", script, str(path)], capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stdout + completed.stderr
