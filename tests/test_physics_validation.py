"""Source-data checks must catch violations absent from the Pyomo model."""

import copy
from dataclasses import replace
import json
import math
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import acdcpf
import pytest

from acdcpf_opf.benchmarks.stagg5 import benchmark_pf_opf_comparison as benchmark
from acdcpf_opf.benchmarks.stagg5.case_variants import create_acdcpf_network_for_stagg5_case, get_stagg5_grid_case
from acdcpf_opf.data.acdcpf_to_pyomo import ACDCPFToPyomoOptions
from acdcpf_opf.dashboard.web import _result_tables
from acdcpf_opf.opf import pyomo_acdc_loss_min as workflow
from acdcpf_opf.opf.physics_report import PhysicsReport, PhysicsTolerances
from acdcpf_opf.opf.physics_storage import check_storage, check_storage_horizon
from acdcpf_opf.opf.physics_validation import validate_network_physics, validate_time_series_physics
from acdcpf_opf.opf.pyomo_acdc_loss_min import PyomoACDCOPFConfig, solve_pyomo_acdc_loss_min_opf
from acdcpf_opf.opf.validation import validate_solution


@pytest.fixture(scope="module")
def solved_cases():
    benchmark._ensure_local_ipopt_on_path()
    config = PyomoACDCOPFConfig(ipopt_executable=str(benchmark._ipopt_executable_path()),
                              max_iter=1000, tolerance=1e-8, print_level=0)
    cases = {}
    for name in ("original", "hybrid_dcdc", "two_area_stagg5_dcdc"):
        net = create_acdcpf_network_for_stagg5_case(name)
        case = get_stagg5_grid_case(name)
        options = benchmark._opf_conversion_options_for_case(
            case, opf_control_selection=benchmark._parse_opf_controls("benchmark", case),
            control_margin_percent=None,
        )
        result = solve_pyomo_acdc_loss_min_opf(net, config=config, conversion_options=options)
        assert result.success, result.message
        cases[name] = (net, result, options)
    return cases


def audit(net, result, options, extracted=None):
    return validate_network_physics(
        net, result.extracted_results if extracted is None else extracted, options=options,
        baseline=result.base_pf_result.raw_result,
    )


def failed_checks(report):
    return {(row.element, row.check) for row in report.checks if row.status == "failed"}


@pytest.mark.parametrize("kind", ["ac_line", "trafo"])
@pytest.mark.parametrize("sparse_ids", [False, True])
def test_analytical_two_bus_circuit_without_solver(kind, sparse_ids):
    # V1=100 kV, V2=99 kV, R=10 ohm: P1=10 MW, P2=-9.9 MW.
    net = acdcpf.create_empty_network()
    a = acdcpf.create_ac_bus(net, vr_kv=100, is_slack=True)
    b = acdcpf.create_ac_bus(net, vr_kv=100)
    acdcpf.create_ac_gen(net, bus=a, p_mw=10, q_mvar=0, v_pu=1,
                         p_min_mw=0, p_max_mw=20, q_min_mvar=0, q_max_mvar=0)
    acdcpf.create_ac_load(net, bus=b, p_mw=9.9, q_mvar=0)
    if kind == "trafo":
        acdcpf.create_transformer(net, from_bus=a, to_bus=b, sn_mva=100, r_pu=0.1, x_pu=0)
        prefix = "TRAFO"
    else:
        acdcpf.create_ac_line(net, from_bus=a, to_bus=b, length_km=1,
                             r_ohm_per_km=10, x_ohm_per_km=0, max_i_ka=0.1)
        net.ac_line["rate_mva"] = 20.0
        prefix = "AC_LINE"
    gen, branch = 0, 0
    if sparse_ids:
        a, b, gen, branch = 1000, 9000, 42, 70
        net.ac_bus = net.ac_bus.rename(index={0: a, 1: b}).iloc[::-1]
        net.ac_gen = net.ac_gen.rename(index={0: gen})
        net.ac_gen["bus"] = a
        net.ac_load["bus"] = b
        table = getattr(net, kind).rename(index={0: branch})
        table["from_bus"], table["to_bus"] = a, b
        setattr(net, kind, table)
    key = f"{prefix}{branch}"
    result = {
        "ac_bus_voltage_magnitude_pu": {f"AC{a}": 1.0, f"AC{b}": 0.99},
        "ac_bus_voltage_angle_rad": {f"AC{a}": 0.0, f"AC{b}": 0.0},
        "generator_pg_pu": {f"G{gen}": 0.1}, "generator_qg_pu": {f"G{gen}": 0},
        "ac_branch_p_from_pu": {key: 0.1}, "ac_branch_p_to_pu": {key: -0.099},
        "ac_branch_q_from_pu": {key: 0}, "ac_branch_q_to_pu": {key: 0},
        "objective_total_active_losses_mw": 0.1,
    }
    report = validate_network_physics(net, result, options=ACDCPFToPyomoOptions())
    assert report.valid, report.failure_message()
    balance = next(c for c in report.checks if c.check == "global_p_balance")
    assert abs(balance.value) < 1e-10
    net.ac_load["p_mw"] = 8.9
    assert not validate_network_physics(net, result, options=ACDCPFToPyomoOptions()).valid


@pytest.mark.parametrize("name", ["original", "hybrid_dcdc", "two_area_stagg5_dcdc"])
def test_real_cases_pass_independent_balances(solved_cases, name):
    net, result, options = solved_cases[name]
    report = audit(net, result, options)
    assert report.valid, report.failure_message()
    assert result.physics_validation.valid
    assert report.status == "partial"  # Explicit current ratings are not all supplied.
    assert any(c.check == "global_p_balance" and c.status == "passed" for c in report.checks)
    json.dumps(report.to_dict(), allow_nan=False)


@pytest.mark.parametrize("table,column,index,limit,check", [
    ("ac_gen", "p_max_mw", 1, 39.0, "p_dispatch"),
    ("ac_gen", "q_min_mvar", 1, 0.0, "q_dispatch"),
    ("ac_bus", "v_max_pu", 0, 1.01, "voltage"),
    ("ac_line", "max_i_ka", 0, 1e-5, "i_from_rating"),
    ("ac_line", "rate_mva", 0, 1.0, "s_from_rating"),
    ("dc_line", "max_i_ka", 0, 1e-5, "current_rating"),
    ("dc_line", "rate_mw", 0, 0.001, "p_from_rating"),
    ("vsc", "s_mva", 0, 1.0, "bridge_apparent_rating"),
])
def test_source_limits_are_not_read_from_pyomo(solved_cases, table, column, index, limit, check):
    net, result, options = solved_cases["original"]
    source = copy.deepcopy(net)
    getattr(source, table).at[index, column] = limit
    assert validate_solution(result.model).valid  # Model still has its old limits.
    report = audit(source, result, options)
    assert (f"{table}[{index}]", check) in failed_checks(report)


@pytest.mark.parametrize("name,table,column,check", [
    ("hybrid_dcdc", "dcdc", "rate_mw", "p_from_rating"),
    ("two_area_stagg5_dcdc", "trafo", "sn_mva", "s_from_rating"),
])
def test_converter_and_transformer_ratings(solved_cases, name, table, column, check):
    net, result, options = solved_cases[name]
    source = copy.deepcopy(net)
    if table == "trafo":
        # Preserve the physical impedance while reducing the declared rating.
        old_rating = source.trafo.at[0, "sn_mva"]
        for field in ("r_pu", "x_pu"):
            source.trafo.at[0, field] /= old_rating
        source.trafo.at[0, "b_pu"] *= old_rating
    getattr(source, table).at[0, column] = 1.0
    assert (f"{table}[0]", check) in failed_checks(audit(source, result, options))


def test_changed_load_is_not_lost_in_balance(solved_cases):
    net, result, options = solved_cases["original"]
    source = copy.deepcopy(net)
    source.ac_load.at[0, "p_mw"] += 5.0
    report = audit(source, result, options)
    assert ("ac_bus[1]", "nodal_p_balance") in failed_checks(report)
    assert ("network", "global_p_balance") in failed_checks(report)


@pytest.mark.parametrize("field,key", [
    ("ac_branch_p_from_pu", "AC_LINE0"),
    ("converter_p_dc_pu", "CONV0"),
    ("generator_qg_pu", "G1"),
    ("dc_bus_voltage_pu", "DC0"),
])
def test_corrupt_results_fail_closed(solved_cases, field, key):
    net, result, options = solved_cases["original"]
    extracted = copy.deepcopy(result.extracted_results)
    extracted[field][key] = math.nan
    report = audit(net, result, options, extracted)
    assert not report.valid
    json.dumps(report.to_dict(), allow_nan=False)
    del extracted[field][key]
    assert not audit(net, result, options, extracted).valid


def test_unrated_equipment_is_not_certified(solved_cases):
    net, result, options = solved_cases["original"]
    source = copy.deepcopy(net)
    source.ac_line.at[0, "rate_mva"] = 0.0
    source.ac_line.at[0, "max_i_ka"] = math.nan
    report = audit(source, result, options)
    assert report.valid and report.status == "partial"
    check = next(c for c in report.checks if c.element == "ac_line[0]" and c.check == "s_from_rating")
    assert check.status == "not_checked" and check.upper is None


def test_curtailment_and_vsc_margins_are_rechecked(solved_cases):
    net, result, options = solved_cases["hybrid_dcdc"]
    extracted = copy.deepcopy(result.extracted_results)
    options = replace(options, optimize_dc_generator_active_power=True,
                      dc_generator_active_power_indices=None,
                      dc_generator_curtailment_only=True, control_margin_percent=80)
    extracted["dc_generator_pg_pu"]["DCG0"] = 0.0
    assert ("dc_gen[0]", "curtailment") in failed_checks(audit(net, result, options, extracted))
    options = replace(options, control_margin_percent=0)
    assert ("vsc[0]", "q_control_margin") in failed_checks(audit(net, result, options))


def test_storage_energy_and_exclusive_modes(solved_cases):
    net, result, options = solved_cases["hybrid_dcdc"]
    extracted = copy.deepcopy(result.extracted_results)
    extracted["storage_charge_p_pu"]["STORAGE0"] += 0.01
    extracted["storage_discharge_p_pu"]["STORAGE0"] += 0.01
    extracted["storage_energy_mwh"]["STORAGE0"] -= 1.0
    report = audit(net, result, options, extracted)
    assert ("storage[0]", "exclusive_modes") in failed_checks(report)
    assert ("storage[0]", "energy_transition") in failed_checks(report)


def test_two_batteries_with_unequal_intervals():
    net = acdcpf.create_empty_network()
    dc = acdcpf.create_dc_bus(net, v_base=20)
    ac = acdcpf.create_ac_bus(net, vr_kv=20)
    for bus, kind in ((dc, "dc"), (ac, "ac")):
        acdcpf.create_storage(net, bus=bus, bus_type=kind, sn_mva=2, energy_mwh=10,
                              soc_percent=50, eta_charge=0.9, eta_discharge=0.8)
    options, previous = ACDCPFToPyomoOptions(), None
    for dt, power, energy in ((0.25, -1.0, 5.225), (0.75, 0.24, 5.0)):
        result = {}
        for field, value in (("storage_p_pu", power/net.s_base), ("storage_q_pu", 0),
                             ("storage_charge_p_pu", max(-power, 0)/net.s_base),
                             ("storage_discharge_p_pu", max(power, 0)/net.s_base),
                             ("storage_energy_mwh", energy), ("storage_soc_percent", energy*10)):
            result[field] = {f"STORAGE{idx}": value for idx in net.storage.index}
        report = PhysicsReport()
        check_storage(net, result, report, PhysicsTolerances(), options=options,
                      duration_hours=dt, initial_energy_mwh=previous)
        assert report.valid, report.failure_message()
        previous = check_storage_horizon(net, result, report, PhysicsTolerances(),
                                         duration_hours=dt, previous_energy=previous)
    assert previous == pytest.approx({0: 5.0, 1: 5.0})


def test_horizon_uses_dispatch_not_reported_soc(solved_cases):
    net, result, options = solved_cases["hybrid_dcdc"]
    extracted = copy.deepcopy(result.extracted_results)
    extracted["storage_p_pu"]["STORAGE0"] = 0.01
    extracted["storage_charge_p_pu"]["STORAGE0"] = 0
    extracted["storage_discharge_p_pu"]["STORAGE0"] = 0.01
    extracted["storage_energy_mwh"]["STORAGE0"] = 25
    extracted["storage_soc_percent"]["STORAGE0"] = 50
    reports = validate_time_series_physics({7: net, 18: net}, {7: extracted, 18: extracted},
                                           options=options, durations={7: 0.25, 18: 0.75})
    assert ("storage[0]", "final_soc_restoration") in failed_checks(reports[18])
    assert ("storage[0]", "energy_transition") in failed_checks(reports[7])


def test_checks_export_even_when_results_are_rejected(solved_cases, tmp_path):
    net, result, options = solved_cases["original"]
    source = copy.deepcopy(net)
    source.ac_gen.at[1, "p_max_mw"] = 39
    bad = replace(result, success=False, physics_validation=audit(source, result, options))
    run = benchmark._snapshot_from_tool1(benchmark.TOOL1_OBJECTIVE_LABEL, bad)
    assert not run.tables
    sections = benchmark._single_run_result_sections(
        run, generated_at="2026-09-28", vsc_setpoint_scenario="original",
        grid_case="original", export_kind="opf",
    )
    physics = next(s for s in sections if s.name == "physics_checks")
    assert any(r["status"] == "failed" for r in physics.rows)
    csv = benchmark._write_csv_sections(tmp_path / "checks.csv", sections)
    xlsx = benchmark._write_xlsx_workbook(tmp_path / "checks.xlsx", sections, generated_at="2026-09-28")
    assert "p_dispatch" in csv.read_text(encoding=benchmark.CSV_ENCODING)
    with ZipFile(xlsx) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        assert any(sheet.attrib.get("name") == "physics_checks" for sheet in workbook.iter())
    markdown = "\n".join(benchmark._physics_markdown_section([run]))
    assert "Physics Violations" in markdown and "ac_gen[1]" in markdown
    assert any(row["status"] == "failed" for row in _result_tables((run,))["physics_checks"])


def test_physics_failure_blocks_acceptance_without_pf_replay(solved_cases, monkeypatch):
    net, _, options = solved_cases["original"]
    original = workflow._convert_case_to_opf_data

    def omit_source_limit(*args, **kwargs):
        data = original(*args, **kwargs)
        data["generators"]["G1"]["pg_max"] = 100.0
        return data

    source = copy.deepcopy(net)
    source.ac_gen.at[1, "p_max_mw"] = 39.0
    monkeypatch.setattr(workflow, "_convert_case_to_opf_data", omit_source_limit)
    result = solve_pyomo_acdc_loss_min_opf(
        source, conversion_options=options,
        config=PyomoACDCOPFConfig(ipopt_executable=str(benchmark._ipopt_executable_path()),
                                 print_level=0, tolerance=1e-8, run_final_acdcpf_validation=False),
    )
    assert result.validation.valid
    assert not result.success and not result.physics_validation.valid
    assert ("ac_gen[1]", "p_dispatch") in failed_checks(result.physics_validation)
