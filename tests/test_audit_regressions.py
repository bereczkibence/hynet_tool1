"""Physical and integration regressions from the September 2026 audit."""

from dataclasses import replace
import copy
import math

import pyomo.environ as pyo
import pytest

from acdcpf.networks import create_case5_stagg_mtdc_slack
from acdcpf_opf.benchmarks.stagg5 import benchmark_pf_opf_comparison as benchmark
from acdcpf_opf.benchmarks.stagg5.case_variants import create_acdcpf_network_for_stagg5_case, get_stagg5_grid_case
from acdcpf_opf.benchmarks.stagg5.service import BenchmarkRequest, BenchmarkResultBundle
from acdcpf_opf.data.acdcpf_to_pyomo import ACDCPFToPyomoOptions, convert_acdcpf_network_to_opf_data
from acdcpf_opf.data.time_profiles import apply_profile_snapshot, load_time_profile
from acdcpf_opf.opf.formulations.acdc_opf_pyomo_loss_min import build_acdc_opf_model, build_multiperiod_acdc_opf_model
from acdcpf_opf.opf.pyomo_acdc_loss_min import PyomoACDCOPFConfig, solve_pyomo_acdc_loss_min_opf, solve_pyomo_acdc_loss_min_time_series
from acdcpf_opf.opf.validation import validate_solution


@pytest.fixture(scope="module")
def config():
    benchmark._ensure_local_ipopt_on_path()
    return PyomoACDCOPFConfig(ipopt_executable=str(benchmark._ipopt_executable_path()),
                              max_iter=1000, tolerance=1e-8, print_level=0)


def options_for(case):
    case = get_stagg5_grid_case(case)
    return benchmark._opf_conversion_options_for_case(
        case, opf_control_selection=benchmark._parse_opf_controls("benchmark", case),
        control_margin_percent=None,
    )


@pytest.mark.parametrize("case", ["original", "hybrid_dcdc", "two_area_stagg5_dcdc"])
def test_native_benchmarks_match_pf_replay(config, case):
    net = create_acdcpf_network_for_stagg5_case(case)
    result = solve_pyomo_acdc_loss_min_opf(net, config=config, conversion_options=options_for(case))
    assert result.success, result.message
    assert result.validation.valid
    assert result.replay_comparison["matches"]
    assert result.objective_total_active_losses_mw <= result.base_pf_result.total_active_losses + 1e-5
    assert result.objective_total_active_losses_mw == pytest.approx(result.validation_pf_result.total_active_losses, abs=1e-5)
    if case == "original":
        assert result.base_pf_result.total_active_losses == pytest.approx(8.63675283443, abs=1e-6)
        assert result.objective_total_active_losses_mw == pytest.approx(8.62474722201, abs=1e-5)
    for key, storage in result.data["storage_units"].items():
        m = result.model
        power = pyo.value(m.Pst[key]) * net.s_base
        expected = storage["soc0"] * storage["energy_mwh"] - max(power, 0) / storage["eta_discharge"] + max(-power, 0) * storage["eta_charge"]
        assert pyo.value(m.Est_final[key]) == pytest.approx(expected, abs=1e-5)
        assert min(pyo.value(m.Pst_ch[key]), pyo.value(m.Pst_dis[key])) * net.s_base < 1e-5


def test_generator_dispatch_obeys_source_limit(config):
    net = create_acdcpf_network_for_stagg5_case("original")
    net.ac_gen["p_min_mw"] = 0.0
    net.ac_gen["p_max_mw"] = [200.0, 41.0]
    result = solve_pyomo_acdc_loss_min_opf(net, config=config, conversion_options=ACDCPFToPyomoOptions(optimize_non_slack_generator_active_power=True))
    assert result.success, result.message
    assert 0 <= pyo.value(result.model.Pg["G1"]) * net.s_base <= 41.000001


def test_dcdc_rating_rejects_impossible_transfer(config):
    net = create_acdcpf_network_for_stagg5_case("hybrid_dcdc")
    net.dcdc["rate_mw"] = 1.0  # A fixed 20 MW source cannot pass a 1 MW terminal.
    result = solve_pyomo_acdc_loss_min_opf(net, config=config, conversion_options=options_for("hybrid_dcdc"))
    assert not result.success
    assert result.validation is not None and not result.validation.valid
    snapshot = benchmark._snapshot_from_tool1(benchmark.TOOL1_OBJECTIVE_LABEL, result)
    assert not snapshot.tables and not snapshot.losses_mw


def test_ac_only_case_uses_native_opf(config):
    net = create_case5_stagg_mtdc_slack()
    for name in ("dc_bus", "dc_line", "vsc"):
        setattr(net, name, getattr(net, name).iloc[:0])
    result = solve_pyomo_acdc_loss_min_opf(net, config=config)
    assert result.success, result.message
    assert len(result.model.DC_BUS) == 0


@pytest.mark.parametrize("mode", ["pdc_q", "pdc_vac"])
def test_fixed_pdc_is_preserved_and_replayed(config, mode):
    net = create_case5_stagg_mtdc_slack()
    net.vsc.at[0, "q_mvar"] = 0.0  # Avoid the original infeasible Ucv lower bound.
    net.vsc.at[2, "control_mode"] = mode
    net.vsc.at[2, "p_dc_set_mw"] = -30.0
    options = ACDCPFToPyomoOptions(optimize_converter_active_power=False, optimize_converter_reactive_power=False)
    result = solve_pyomo_acdc_loss_min_opf(net, config=config, conversion_options=options)
    assert result.success, result.message
    assert pyo.value(result.model.Pcv_dc["CONV2"]) * net.s_base == pytest.approx(30.0, abs=1e-7)
    assert result.validation_pf_result.raw_result.res_vsc.at[2, "p_dc_mw"] == pytest.approx(-30.0, abs=1e-5)


def test_current_limits_are_voltage_dependent_and_independent_of_power_ratings():
    net = create_case5_stagg_mtdc_slack()
    net.ac_line.at[0, "max_i_ka"] = 0.2
    net.ac_line.at[0, "rate_mva"] = 300.0
    net.dc_line.at[0, "max_i_ka"] = 0.1
    net.dc_line.at[0, "rate_mw"] = 300.0
    data = convert_acdcpf_network_to_opf_data(net)
    model = build_acdc_opf_model(data)
    key = "AC_LINE0"
    limit = 0.2 * math.sqrt(3) * 345 / 100
    model.Vmag["AC0"].set_value(0.9)
    model.P_ac_f[key].set_value(limit * 0.95)
    model.Q_ac_f[key].set_value(0)
    assert pyo.value(model.ac_branch_current[key, "from"].body) > 0
    assert data["dc_branches"]["DC_LINE0"]["i_max"] == pytest.approx(0.1 / (100 / 345))
    del data["dc_branches"]["DC_LINE0"]["i_max"]
    unrated = build_acdc_opf_model(data)
    assert unrated.Idc["DC_LINE0"].bounds == (None, None)


def test_invalid_ac_load_and_unsupported_shunt_fail_before_solve(config):
    net = create_case5_stagg_mtdc_slack()
    net.ac_load.at[0, "bus"] = 999
    result = solve_pyomo_acdc_loss_min_opf(net, config=config)
    assert not result.success and "unknown" in result.message
    assert result.solver_result is None
    net = create_case5_stagg_mtdc_slack()
    net.ac_line.at[0, "g_us_per_km"] = 1.0
    with pytest.raises(NotImplementedError, match="conductance"):
        convert_acdcpf_network_to_opf_data(net)


def test_validation_rejects_nan_and_bound_violations():
    data = convert_acdcpf_network_to_opf_data(create_case5_stagg_mtdc_slack())
    model = build_acdc_opf_model(data)
    model.Pg["G0"].set_value(model.Pg["G0"].ub + 1, skip_validation=True)
    model.Vmag["AC4"].set_value(float("nan"), skip_validation=True)
    report = validate_solution(model)
    assert not report.valid
    assert report.max_bound_violation >= 1.0
    assert "Vmag[AC4]" in report.nonfinite


def test_irregular_intervals_weight_energy_objective():
    data = convert_acdcpf_network_to_opf_data(create_case5_stagg_mtdc_slack())
    second = copy.deepcopy(data)
    data["metadata"]["snapshot_duration_hours"] = 0.25
    second["metadata"]["snapshot_duration_hours"] = 2.0
    model = build_multiperiod_acdc_opf_model({0: data, 1: second})
    before = pyo.value(model.objective)
    for period in model._period_models.values():
        period.P_ac_f["AC_LINE0"].set_value(0.01)
    assert pyo.value(model.objective) - before == pytest.approx(2.25)


def test_profile_duration_validation():
    snapshots = load_time_profile(benchmark.HYBRID_DCDC_ONE_DAY_LOAD_PV_PROFILE)[:2]
    with pytest.raises(ValueError, match="increasing"):
        benchmark._profile_snapshot_durations_hours(tuple(reversed(snapshots)))
    no_times = tuple(replace(s, timestamp=None, time_index=100 * i) for i, s in enumerate(snapshots))
    assert list(benchmark._profile_snapshot_durations_hours(no_times).values()) == [1.0, 1.0]
    with pytest.raises(ValueError, match="parseable"):
        benchmark._profile_snapshot_durations_hours((replace(snapshots[0], timestamp="bad"), snapshots[1]))


def test_dashboard_bundle_does_not_call_failed_opf_successful():
    bundle = BenchmarkResultBundle(
        request=BenchmarkRequest(), generated_at="2026-09-27", output_directory=None,
        runs=(benchmark.BenchmarkRun(label="ACDCPF PF", success=True),
              benchmark.BenchmarkRun(label=benchmark.TOOL1_OBJECTIVE_LABEL, success=False)),
    )
    assert not bundle.success
    assert replace(bundle, request=BenchmarkRequest(skip_opf=True)).success


def test_fixed_storage_is_not_relaxed_during_initialization():
    net = create_acdcpf_network_for_stagg5_case("hybrid_dcdc")
    net.storage.at[0, "p_mw"] = 0.0
    data = convert_acdcpf_network_to_opf_data(
        net, options=ACDCPFToPyomoOptions(optimize_storage_active_power=False),
    )
    model = build_acdc_opf_model(data)
    model.storage_mode_relaxation["STORAGE0"].set_value(1e-4)
    assert model.Pst_ch["STORAGE0"].fixed and model.Pst_dis["STORAGE0"].fixed
    assert pyo.value(model.storage_exclusive_mode["STORAGE0"].body) == 0
    assert pyo.value(model.storage_exclusive_mode["STORAGE0"].lower) == 0


def test_linked_day_storage_physics(config):
    snapshots = load_time_profile(benchmark.HYBRID_DCDC_ONE_DAY_LOAD_PV_PROFILE)
    durations = benchmark._profile_snapshot_durations_hours(snapshots)
    cases = {}
    for snapshot in snapshots:
        net = create_acdcpf_network_for_stagg5_case("hybrid_dcdc")
        apply_profile_snapshot(net, snapshot)
        cases[snapshot.time_index] = net
    results = solve_pyomo_acdc_loss_min_time_series(
        cases, config=config, conversion_options=options_for("hybrid_dcdc"),
        period_metadata={key: {"snapshot_duration_hours": dt} for key, dt in durations.items()},
    )
    assert len(results) == 24
    energy = 25.0
    dispatch = []
    for key, result in results.items():
        assert result.success, result.message
        assert result.validation.valid and result.replay_comparison["matches"]
        row = result.extracted_results
        power = row["storage_p_pu"]["STORAGE0"] * cases[key].s_base
        energy += (-max(power, 0) / 0.95 + max(-power, 0) * 0.95) * durations[key]
        soc = row["storage_soc_percent"]["STORAGE0"]
        assert 0 - 1e-6 <= soc <= 100 + 1e-6
        assert energy == pytest.approx(soc * 50 / 100, abs=2e-5)
        assert min(row["storage_charge_p_pu"]["STORAGE0"], row["storage_discharge_p_pu"]["STORAGE0"]) * 100 < 1e-5
        dispatch.append(power)
    assert energy == pytest.approx(25.0, abs=2e-5)
    assert min(dispatch) < -0.1 and max(dispatch) > 0.1
