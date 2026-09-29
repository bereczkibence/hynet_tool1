from __future__ import annotations

import unittest

import numpy as np
import pyomo.environ as pyo

from acdcpf_opf.data.acdcpf_to_pyomo import (
    ACDCPFToPyomoOptions,
    convert_acdcpf_network_to_opf_data,
)
from acdcpf_opf.opf.formulations.acdc_opf_pyomo_loss_min import (
    build_acdc_opf_model,
    build_multiperiod_acdc_opf_model,
    extract_active_loss_breakdown,
    extract_multiperiod_opf_results,
    validate_required_data,
)
from acdcpf_opf.opf.pyomo_acdc_loss_min import (
    PyomoACDCOPFConfig,
    apply_pyomo_results_to_acdcpf_network,
    build_pyomo_acdc_loss_min_model,
)
from acdcpf_opf.powerflow.acdcpf_network_adapter import ACDCPFNetworkAdapter


def _build_stagg5_network():
    from acdcpf.networks import create_case5_stagg_mtdc_slack

    return create_case5_stagg_mtdc_slack()


def _build_case33_ext_network():
    from acdcpf.networks import create_case33_ieee_ext

    return create_case33_ieee_ext()


def _build_hybrid_stagg5_network():
    from acdcpf_opf.benchmarks.stagg5.case_variants import (
        create_case5_stagg_mtdc_hybrid_dcdc,
    )

    return create_case5_stagg_mtdc_hybrid_dcdc()


def _build_two_area_stagg5_network():
    from acdcpf_opf.benchmarks.stagg5.case_variants import (
        create_two_area_stagg5_transformer_dcdc,
    )

    return create_two_area_stagg5_transformer_dcdc()


class ACDCPFToPyomoTests(unittest.TestCase):
    def test_native_network_conversion_contains_required_sections(self) -> None:
        net = _build_stagg5_network()
        pf_result = ACDCPFNetworkAdapter().solve(net, copy_case=True, write_back=False)

        data = convert_acdcpf_network_to_opf_data(net, pf_result=pf_result)

        validate_required_data(data)
        self.assertEqual(data["base_mva"], 100.0)
        self.assertEqual(data["metadata"]["source"], "acdcpf.Network")
        self.assertEqual(len(data["ac_buses"]), 5)
        self.assertEqual(len(data["dc_buses"]), 3)
        self.assertEqual(len(data["converters"]), 3)

    def test_dc_line_resistance_keeps_conductor_value_and_pole_count(self) -> None:
        net = _build_stagg5_network()
        data = convert_acdcpf_network_to_opf_data(net)

        branch = data["dc_branches"]["DC_LINE0"]

        self.assertAlmostEqual(branch["r"], 0.052)
        self.assertAlmostEqual(branch["poles"], 2.0)
        self.assertAlmostEqual(branch["r"] / branch["poles"], 0.026)

    def test_line_rating_columns_are_converted_to_opf_limits(self) -> None:
        net = _build_stagg5_network()
        net.ac_line.at[0, "rate_mva"] = 150.0
        net.dc_line.at[0, "rate_mw"] = 100.0

        data = convert_acdcpf_network_to_opf_data(net)

        self.assertAlmostEqual(data["ac_branches"]["AC_LINE0"]["rate"], 1.5)
        self.assertAlmostEqual(data["dc_branches"]["DC_LINE0"]["rate"], 1.0)
        # A power rating is not an independent current rating at off-nominal V.
        self.assertIsNone(data["dc_branches"]["DC_LINE0"]["i_max"])

    def test_standalone_transformers_are_converted_as_transformer_branches(self) -> None:
        net = _build_two_area_stagg5_network()

        data = convert_acdcpf_network_to_opf_data(net)
        transformer = data["ac_branches"]["TRAFO0"]
        model = build_acdc_opf_model(data)

        self.assertEqual(transformer["kind"], "transformer")
        self.assertEqual(transformer["from"], "AC0")
        self.assertAlmostEqual(transformer["rate"], 3.0)
        self.assertAlmostEqual(transformer["r"], 0.002 / 3.0)
        self.assertAlmostEqual(transformer["x"], 0.08 / 3.0)
        self.assertIn("TRAFO0", set(model.AC_TRANSFORMER_BRANCH))
        self.assertIn("AC_LINE0", set(model.AC_LINE_BRANCH))
        self.assertTrue(data["ac_buses"]["AC0"]["is_slack"])
        self.assertTrue(data["generators"]["G0"]["is_slack"])

    def test_converter_signs_and_loss_coefficients_match_acdcpf_convention(self) -> None:
        net = _build_stagg5_network()
        pf_result = ACDCPFNetworkAdapter().solve(net, copy_case=True, write_back=False)
        data = convert_acdcpf_network_to_opf_data(net, pf_result=pf_result)

        conv0 = data["converters"]["CONV0"]
        conv2 = data["converters"]["CONV2"]
        current_base_ka = 100.0 / (np.sqrt(3.0) * 345.0)

        self.assertGreater(conv0["initial"]["p_ac"], 0.0)
        self.assertLess(conv2["initial"]["p_ac"], 0.0)
        self.assertLess(conv0["initial"]["p_dc"], 0.0)
        self.assertAlmostEqual(conv0["loss_c"], 4.371 * current_base_ka**2 / 100.0)
        self.assertAlmostEqual(conv2["loss_c"], 2.885 * current_base_ka**2 / 100.0)
        self.assertEqual(conv0["loss_mode"], "smooth_directional")
        self.assertAlmostEqual(
            conv0["loss_c_positive_p_ac"],
            4.371 * current_base_ka**2 / 100.0,
        )
        self.assertAlmostEqual(
            conv0["loss_c_negative_p_ac"],
            2.885 * current_base_ka**2 / 100.0,
        )

    def test_default_conversion_fixes_non_slack_generation(self) -> None:
        net = _build_stagg5_network()
        data = convert_acdcpf_network_to_opf_data(net)
        fixed_pg = data["fixed"].get("Pg", {})
        fixed_qg = data["fixed"].get("Qg", {})

        self.assertNotIn("G0", fixed_pg)
        self.assertIn("G1", fixed_pg)
        self.assertAlmostEqual(fixed_pg["G1"], 0.4)
        self.assertEqual(fixed_qg, {})

    def test_conversion_can_fix_non_slack_generator_reactive_power(self) -> None:
        net = _build_stagg5_network()
        pf_result = ACDCPFNetworkAdapter().solve(net, copy_case=True, write_back=False)

        data = convert_acdcpf_network_to_opf_data(
            net,
            pf_result=pf_result,
            options=ACDCPFToPyomoOptions(
                optimize_non_slack_generator_reactive_power=False,
                ac_generator_reactive_power_indices=(),
            ),
        )

        fixed_qg = data["fixed"].get("Qg", {})
        self.assertNotIn("G0", fixed_qg)
        self.assertIn("G1", fixed_qg)
        self.assertAlmostEqual(fixed_qg["G1"], data["generators"]["G1"]["qg0"])

    def test_converter_selection_fixes_unselected_pq_controls(self) -> None:
        net = _build_stagg5_network()
        data = convert_acdcpf_network_to_opf_data(
            net,
            options=ACDCPFToPyomoOptions(
                optimize_converter_active_power=True,
                converter_active_power_indices=(2,),
                optimize_converter_reactive_power=True,
                converter_reactive_power_indices=(2,),
            ),
        )

        self.assertIn("CONV0", data["fixed"].get("Ptf_if", {}))
        self.assertNotIn("CONV2", data["fixed"].get("Ptf_if", {}))
        self.assertIn("CONV0", data["fixed"].get("Qtf_if", {}))
        self.assertNotIn("CONV2", data["fixed"].get("Qtf_if", {}))

    def test_control_margin_limits_selected_vsc_terminal_controls(self) -> None:
        net = _build_stagg5_network()

        data = convert_acdcpf_network_to_opf_data(
            net,
            options=ACDCPFToPyomoOptions(control_margin_percent=10.0),
        )
        converter = data["converters"]["CONV0"]
        margin = 0.10 * float(converter["s_ac_rated"])

        self.assertAlmostEqual(
            converter["p_terminal_min"],
            converter["initial"]["p_ac"] - margin,
        )
        self.assertAlmostEqual(
            converter["p_terminal_max"],
            converter["initial"]["p_ac"] + margin,
        )
        self.assertAlmostEqual(
            converter["q_terminal_min"],
            converter["initial"]["q_ac"] - margin,
        )
        self.assertAlmostEqual(
            converter["q_terminal_max"],
            converter["initial"]["q_ac"] + margin,
        )

        model = build_acdc_opf_model(data)
        self.assertEqual(
            model.Ptf_if["CONV0"].bounds,
            (converter["p_terminal_min"], converter["p_terminal_max"]),
        )
        self.assertEqual(
            model.Qtf_if["CONV0"].bounds,
            (converter["q_terminal_min"], converter["q_terminal_max"]),
        )

    def test_native_storage_converts_to_signed_opf_variable(self) -> None:
        net = _build_hybrid_stagg5_network()

        data = convert_acdcpf_network_to_opf_data(net)

        self.assertEqual(len(data["storage_units"]), 1)
        self.assertEqual(len(data["dc_loads"]), 0)
        self.assertEqual(len(data["dc_generators"]), 1)
        storage = data["storage_units"]["STORAGE0"]
        self.assertEqual(storage["bus_type"], "dc")
        self.assertAlmostEqual(storage["p0"], -0.15)
        self.assertAlmostEqual(storage["q0"], 0.0)
        self.assertAlmostEqual(storage["p_min"], -0.25)
        self.assertAlmostEqual(storage["p_max"], 0.25)
        self.assertAlmostEqual(storage["s_rating"], 0.25)
        self.assertAlmostEqual(storage["energy_mwh"], 50.0)
        self.assertAlmostEqual(storage["soc0"], 0.50)
        self.assertAlmostEqual(storage["soc_min"], 0.0)
        self.assertAlmostEqual(storage["soc_max"], 1.0)
        self.assertAlmostEqual(storage["eta_charge"], 0.95)
        self.assertAlmostEqual(storage["eta_discharge"], 0.95)
        self.assertNotIn("Pst", data["fixed"])
        self.assertIn("Qst", data["fixed"])
        self.assertAlmostEqual(data["fixed"]["Qst"]["STORAGE0"], 0.0)

    def test_control_margin_limits_storage_active_power(self) -> None:
        net = _build_hybrid_stagg5_network()

        data = convert_acdcpf_network_to_opf_data(
            net,
            options=ACDCPFToPyomoOptions(control_margin_percent=10.0),
        )

        storage = data["storage_units"]["STORAGE0"]
        margin = 0.10 * float(storage["s_rating"])
        self.assertAlmostEqual(storage["p_min"], storage["p0"] - margin)
        self.assertAlmostEqual(storage["p_max"], storage["p0"] + margin)

    def test_storage_apparent_limit_is_in_pyomo_model(self) -> None:
        net = _build_hybrid_stagg5_network()
        data = convert_acdcpf_network_to_opf_data(net)
        model = build_acdc_opf_model(data)

        self.assertEqual(len(model.STORAGE), 1)
        self.assertFalse(model.Pst["STORAGE0"].fixed)
        self.assertTrue(model.Qst["STORAGE0"].fixed)

        model.Pst["STORAGE0"].set_value(0.20)
        model.Qst["STORAGE0"].set_value(0.0)
        self.assertLessEqual(
            pyo.value(model.storage_apparent_power["STORAGE0"].body),
            pyo.value(model.storage_apparent_power["STORAGE0"].upper),
        )

    def test_multiperiod_storage_soc_transition_uses_efficiencies(self) -> None:
        net = _build_hybrid_stagg5_network()
        data = convert_acdcpf_network_to_opf_data(net)
        data["metadata"]["snapshot_duration_hours"] = 1.0

        model = build_multiperiod_acdc_opf_model({0: data, 1: data})
        period0 = model._period_models[0]
        period1 = model._period_models[1]

        period0.Pst_dis["STORAGE0"].set_value(0.10)
        period0.Pst_ch["STORAGE0"].set_value(0.0)
        period1.Pst_dis["STORAGE0"].set_value(0.0)
        period1.Pst_ch["STORAGE0"].set_value(0.10)
        model.E_storage[0, "STORAGE0"].set_value(25.0)
        model.E_storage[1, "STORAGE0"].set_value(25.0 - 10.0 / 0.95)
        model.E_storage[2, "STORAGE0"].set_value(model.E_storage[0, "STORAGE0"].value)

        self.assertAlmostEqual(
            pyo.value(model.storage_energy_transition[0, "STORAGE0"].body),
            0.0,
        )
        self.assertFalse(period0.storage_single_period_soc.active)

    def test_multiperiod_storage_results_expose_soc_and_split_power(self) -> None:
        net = _build_hybrid_stagg5_network()
        data = convert_acdcpf_network_to_opf_data(net)
        data["metadata"]["snapshot_duration_hours"] = 1.0

        model = build_multiperiod_acdc_opf_model({0: data, 1: data})
        period0 = model._period_models[0]
        period1 = model._period_models[1]

        period0.Pst["STORAGE0"].set_value(0.10)
        period0.Pst_dis["STORAGE0"].set_value(0.10)
        period0.Pst_ch["STORAGE0"].set_value(0.0)
        period0.Qst["STORAGE0"].set_value(0.0)
        period1.Pst["STORAGE0"].set_value(-0.10)
        period1.Pst_dis["STORAGE0"].set_value(0.0)
        period1.Pst_ch["STORAGE0"].set_value(0.10)
        period1.Qst["STORAGE0"].set_value(0.0)
        model.E_storage[0, "STORAGE0"].set_value(25.0)
        model.E_storage[1, "STORAGE0"].set_value(25.0 - 10.0 / 0.95)
        model.E_storage[2, "STORAGE0"].set_value(25.0)

        extracted = extract_multiperiod_opf_results(model, {0: 100.0, 1: 100.0})

        self.assertAlmostEqual(extracted[0]["storage_p_pu"]["STORAGE0"], 0.10)
        self.assertAlmostEqual(extracted[0]["storage_discharge_p_pu"]["STORAGE0"], 0.10)
        self.assertAlmostEqual(extracted[0]["storage_charge_p_pu"]["STORAGE0"], 0.0)
        self.assertAlmostEqual(extracted[0]["storage_energy_mwh"]["STORAGE0"], 25.0 - 10.0 / 0.95)
        self.assertAlmostEqual(
            extracted[0]["storage_soc_percent"]["STORAGE0"],
            100.0 * (25.0 - 10.0 / 0.95) / 50.0,
        )
        self.assertAlmostEqual(extracted[1]["storage_p_pu"]["STORAGE0"], -0.10)
        self.assertAlmostEqual(extracted[1]["storage_discharge_p_pu"]["STORAGE0"], 0.0)
        self.assertAlmostEqual(extracted[1]["storage_charge_p_pu"]["STORAGE0"], 0.10)
        self.assertAlmostEqual(extracted[1]["storage_energy_mwh"]["STORAGE0"], 25.0)
        self.assertAlmostEqual(extracted[1]["storage_soc_percent"]["STORAGE0"], 50.0)

    def test_storage_result_updates_native_acdcpf_storage_row(self) -> None:
        net = _build_hybrid_stagg5_network()

        apply_pyomo_results_to_acdcpf_network(
            net,
            {
                "storage_p_pu": {"STORAGE0": 0.10},
                "storage_q_pu": {"STORAGE0": 0.0},
                "storage_soc_percent": {"STORAGE0": 42.0},
            },
        )

        self.assertAlmostEqual(net.storage.at[0, "p_mw"], 10.0)
        self.assertAlmostEqual(net.storage.at[0, "soc_percent"], 42.0)

        apply_pyomo_results_to_acdcpf_network(
            net,
            {
                "storage_p_pu": {"STORAGE0": -0.12},
                "storage_q_pu": {"STORAGE0": 0.0},
                "storage_soc_percent": {"STORAGE0": 65.0},
            },
        )

        self.assertAlmostEqual(net.storage.at[0, "p_mw"], -12.0)
        self.assertAlmostEqual(net.storage.at[0, "soc_percent"], 65.0)

    def test_dcdc_and_dc_generators_are_converted_from_native_network(self) -> None:
        net = _build_case33_ext_network()

        data = convert_acdcpf_network_to_opf_data(net)

        self.assertGreater(len(data["dcdc_converters"]), 0)
        self.assertGreater(len(data["dc_generators"]), 0)

        dcdc = data["dcdc_converters"]["DCDC0"]
        row = net.dcdc.loc[0]
        from_bus = int(row["from_bus"])
        to_bus = int(row["to_bus"])
        v_from_base = float(net.dc_bus.loc[from_bus, "v_base"])
        v_to_base = float(net.dc_bus.loc[to_bus, "v_base"])
        expected_d_pu = float(row["d_ratio"]) * v_from_base / v_to_base
        expected_g_series = v_to_base**2 / float(net.s_base) / float(row["r_ohm"])

        self.assertAlmostEqual(dcdc["d_pu"], expected_d_pu)
        self.assertAlmostEqual(dcdc["g_series"], expected_g_series)
        self.assertFalse(dcdc["optimize_ratio"])
        self.assertIn("DCG0", data["fixed"].get("Pdcg", {}))

    def test_dc_generator_curtailment_allows_downward_dispatch_only(self) -> None:
        net = _build_case33_ext_network()
        p0_mw = 5.0
        net.dc_gen.at[0, "p_mw"] = p0_mw
        net.dc_gen.at[0, "p_min_mw"] = -10.0
        net.dc_gen.at[0, "p_max_mw"] = p0_mw + 10.0

        data = convert_acdcpf_network_to_opf_data(
            net,
            options=ACDCPFToPyomoOptions(
                optimize_dc_generator_active_power=True,
                dc_generator_active_power_indices=(0,),
                dc_generator_curtailment_only=True,
            ),
        )

        dc_generator = data["dc_generators"]["DCG0"]
        self.assertAlmostEqual(dc_generator["p_min"], 0.0)
        self.assertAlmostEqual(dc_generator["p_max"], p0_mw / float(net.s_base))
        self.assertNotIn("DCG0", data["fixed"].get("Pdcg", {}))

    def test_dc_generator_curtailment_respects_control_margin(self) -> None:
        net = _build_hybrid_stagg5_network()
        p0_mw = float(net.dc_gen.at[0, "p_mw"])

        data = convert_acdcpf_network_to_opf_data(
            net,
            options=ACDCPFToPyomoOptions(
                optimize_dc_generator_active_power=True,
                dc_generator_active_power_indices=(0,),
                dc_generator_curtailment_only=True,
                control_margin_percent=80.0,
            ),
        )

        dc_generator = data["dc_generators"]["DCG0"]
        self.assertAlmostEqual(dc_generator["p_min"], 0.20 * p0_mw / float(net.s_base))
        self.assertAlmostEqual(dc_generator["p_max"], p0_mw / float(net.s_base))
        self.assertNotIn("DCG0", data["fixed"].get("Pdcg", {}))

    def test_dcdc_equations_and_losses_are_in_pyomo_model(self) -> None:
        net = _build_case33_ext_network()
        data = convert_acdcpf_network_to_opf_data(net)
        model = build_acdc_opf_model(data)
        converter = "DCDC0"
        converted = data["dcdc_converters"][converter]
        from_bus = converted["from"]
        to_bus = converted["to"]

        self.assertEqual(len(model.DCDC), len(data["dcdc_converters"]))
        self.assertTrue(model.Ddcdc[converter].fixed)

        v_from = 1.02
        v_to = 0.99
        d_pu = float(converted["d_pu"])
        g_series = float(converted["g_series"])
        g_shunt = float(converted["g_shunt"])
        poles = float(converted["poles"])
        p_from = poles * v_from * (
            d_pu**2 * g_series * v_from
            - d_pu * g_series * v_to
            + g_shunt * v_from
        )
        p_to = poles * v_to * (-d_pu * g_series * v_from + g_series * v_to)

        model.Vdc[from_bus].set_value(v_from)
        model.Vdc[to_bus].set_value(v_to)
        model.Pdcdc_from[converter].set_value(p_from)
        model.Pdcdc_to[converter].set_value(p_to)
        model.Pdcdc_loss[converter].set_value(p_from + p_to)

        self.assertAlmostEqual(pyo.value(model.dcdc_from_power[converter].body), 0.0)
        self.assertAlmostEqual(pyo.value(model.dcdc_to_power[converter].body), 0.0)
        self.assertAlmostEqual(pyo.value(model.dcdc_loss[converter].body), 0.0)

        for other_converter in model.DCDC:
            if other_converter != converter:
                model.Pdcdc_loss[other_converter].set_value(0.0)

        breakdown = extract_active_loss_breakdown(model)
        self.assertAlmostEqual(breakdown["dcdc_converter_pu"], p_from + p_to)

    def test_dcdc_ratio_can_be_selected_as_opf_control_when_bounds_exist(self) -> None:
        net = _build_case33_ext_network()
        net.dcdc["d_ratio_min"] = net.dcdc["d_ratio"].astype(float) * 0.95
        net.dcdc["d_ratio_max"] = net.dcdc["d_ratio"].astype(float) * 1.05

        data = convert_acdcpf_network_to_opf_data(
            net,
            options=ACDCPFToPyomoOptions(
                optimize_dcdc_voltage_ratio=True,
                dcdc_voltage_ratio_indices=(0,),
            ),
        )
        model = build_acdc_opf_model(data)

        self.assertFalse(model.Ddcdc["DCDC0"].fixed)
        self.assertTrue(model.Ddcdc["DCDC1"].fixed)
        lower, upper = model.Ddcdc["DCDC0"].bounds
        self.assertLess(lower, pyo.value(model.Ddcdc["DCDC0"]))
        self.assertGreater(upper, pyo.value(model.Ddcdc["DCDC0"]))

    def test_dcdc_ratio_optimization_requires_explicit_bounds(self) -> None:
        net = _build_case33_ext_network()

        with self.assertRaisesRegex(ValueError, "needs d_ratio_min/d_ratio_max"):
            convert_acdcpf_network_to_opf_data(
                net,
                options=ACDCPFToPyomoOptions(
                    optimize_dcdc_voltage_ratio=True,
                    dcdc_voltage_ratio_indices=(0,),
                ),
            )

    def test_apply_pyomo_results_updates_native_dc_generator_and_dcdc_ratio(self) -> None:
        net = _build_case33_ext_network()
        from_bus = int(net.dcdc.loc[0, "from_bus"])
        to_bus = int(net.dcdc.loc[0, "to_bus"])
        v_from_base = float(net.dc_bus.loc[from_bus, "v_base"])
        v_to_base = float(net.dc_bus.loc[to_bus, "v_base"])

        apply_pyomo_results_to_acdcpf_network(
            net,
            {
                "dc_generator_pg_pu": {"DCG0": 0.005},
                "dcdc_ratio_pu": {"DCDC0": 1.02},
            },
        )

        self.assertAlmostEqual(net.dc_gen.at[0, "p_mw"], 0.005 * float(net.s_base))
        self.assertAlmostEqual(net.dcdc.at[0, "d_ratio"], 1.02 * v_to_base / v_from_base)

    def test_high_level_builder_uses_native_acdcpf_network(self) -> None:
        net = _build_stagg5_network()
        model, data, base_pf = build_pyomo_acdc_loss_min_model(
            net,
            config=PyomoACDCOPFConfig(use_acdcpf_initialization=True),
        )

        self.assertTrue(base_pf.converged)
        self.assertEqual(data["metadata"]["source"], "acdcpf.Network")
        self.assertEqual(len(model.CONV), 3)


if __name__ == "__main__":
    unittest.main()
