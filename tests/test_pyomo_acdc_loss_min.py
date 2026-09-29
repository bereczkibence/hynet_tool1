from __future__ import annotations

import copy
import unittest

import pyomo.environ as pyo

from acdcpf_opf.data.pyflow_to_pyomo import PyflowToPyomoOptions, convert_pyflow_grid_to_opf_data
from acdcpf_opf.opf.formulations.acdc_opf_pyomo_loss_min import (
    build_acdc_opf_model,
    extract_active_loss_breakdown,
    extract_opf_results,
    validate_required_data,
)
from acdcpf_opf.opf.pyomo_acdc_loss_min import (
    PyomoACDCOPFConfig,
    build_pyomo_acdc_loss_min_model,
)


def _build_stagg5_grid():
    try:
        import pyflow_acdc as pyf
    except ImportError as exc:
        raise unittest.SkipTest(f"pyflow_acdc is not importable: {exc}") from exc

    pyf.initialize_pyflowacdc()
    grid, _ = pyf.Stagg5MATACDC()
    return grid


class PyomoACDCLossMinTests(unittest.TestCase):
    def test_pyflow_conversion_contains_required_sections(self) -> None:
        grid = _build_stagg5_grid()

        data = convert_pyflow_grid_to_opf_data(grid)

        validate_required_data(data)
        self.assertIn("ac_buses", data)
        self.assertIn("dc_buses", data)
        self.assertIn("converters", data)
        self.assertEqual(data["base_mva"], 100.0)

    def test_pyomo_model_builds_for_stagg5(self) -> None:
        grid = _build_stagg5_grid()
        model, data, base_pf = build_pyomo_acdc_loss_min_model(
            grid,
            config=PyomoACDCOPFConfig(use_acdcpf_initialization=False),
        )

        self.assertIsNone(base_pf)
        self.assertEqual(len(model.AC_BUS), len(data["ac_buses"]))
        self.assertEqual(len(model.DC_BUS), len(data["dc_buses"]))
        self.assertEqual(len(model.CONV), len(data["converters"]))

    def test_objective_is_total_active_loss_expression(self) -> None:
        grid = _build_stagg5_grid()
        data = convert_pyflow_grid_to_opf_data(grid)
        model = build_acdc_opf_model(data)

        self.assertIs(model.objective.expr, model.total_active_losses)
        self.assertEqual(model.objective.sense, pyo.minimize)

    def test_extracted_loss_breakdown_matches_objective(self) -> None:
        grid = _build_stagg5_grid()
        data = convert_pyflow_grid_to_opf_data(grid)
        model = build_acdc_opf_model(data)

        for branch in model.AC_BRANCH:
            model.P_ac_f[branch].set_value(0.010)
            model.P_ac_t[branch].set_value(0.020)
        for branch in model.DC_BRANCH:
            model.Pdc_loss[branch].set_value(0.030)
        for converter in model.CONV:
            model.Ptf_if[converter].set_value(0.040)
            model.Ptf_fi[converter].set_value(0.010)
            model.Ppr_fc[converter].set_value(0.020)
            model.Ppr_cf[converter].set_value(0.005)
            model.Pcv_loss[converter].set_value(0.006)

        breakdown_pu = extract_active_loss_breakdown(model)
        results = extract_opf_results(model, base_mva=data["base_mva"])
        breakdown_mw = results["active_loss_breakdown_mw"]

        self.assertAlmostEqual(
            breakdown_pu["total_active_losses_pu"],
            pyo.value(model.total_active_losses),
        )
        self.assertAlmostEqual(
            breakdown_mw["total_active_losses_mw"],
            results["objective_total_active_losses_mw"],
        )
        self.assertAlmostEqual(
            breakdown_mw["converter_mw"],
            breakdown_mw["converter_transformer_mw"]
            + breakdown_mw["converter_phase_reactor_mw"]
            + breakdown_mw["converter_electronic_mw"],
        )

    def test_fixed_variables_are_fixed_correctly(self) -> None:
        grid = _build_stagg5_grid()
        data = convert_pyflow_grid_to_opf_data(grid)
        model = build_acdc_opf_model(data)

        for index, value in data["fixed"].get("Vmag", {}).items():
            self.assertTrue(model.Vmag[index].fixed)
            self.assertAlmostEqual(pyo.value(model.Vmag[index]), value)
        for index, value in data["fixed"].get("Vdc", {}).items():
            self.assertTrue(model.Vdc[index].fixed)
            self.assertAlmostEqual(pyo.value(model.Vdc[index]), value)

    def test_default_conversion_fixes_non_slack_generator_pg(self) -> None:
        grid = _build_stagg5_grid()
        data = convert_pyflow_grid_to_opf_data(grid)
        fixed_pg = data["fixed"].get("Pg", {})

        for gen_key, gen in data["generators"].items():
            if gen["is_slack"]:
                self.assertNotIn(gen_key, fixed_pg)
            else:
                self.assertIn(gen_key, fixed_pg)
                self.assertAlmostEqual(fixed_pg[gen_key], gen["pg0"])

    def test_pyflow_default_generator_reactive_limits_are_treated_as_missing(self) -> None:
        grid = _build_stagg5_grid()
        data = convert_pyflow_grid_to_opf_data(grid)

        for gen in data["generators"].values():
            self.assertLess(gen["qg_min"], 0.0)
            self.assertGreater(gen["qg_max"], 0.0)

    def test_parallel_converters_are_converted_to_station_equivalent(self) -> None:
        grid = _build_stagg5_grid()
        converter = grid.Converters_ACDC[0]
        converter.NumConvP = 2

        data = convert_pyflow_grid_to_opf_data(grid)
        converted = data["converters"]["CONV0"]

        self.assertAlmostEqual(converted["parallel_converters"], 2.0)
        self.assertAlmostEqual(
            converted["s_ac_rated"],
            converter.MVA_max * 2.0 / grid.S_base,
        )
        self.assertAlmostEqual(converted["transformer"]["r"], converter.R_t / 2.0)
        self.assertAlmostEqual(converted["transformer"]["x"], converter.X_t / 2.0)
        self.assertAlmostEqual(converted["phase_reactor"]["r"], converter.PR_R / 2.0)
        self.assertAlmostEqual(converted["phase_reactor"]["x"], converter.PR_X / 2.0)
        self.assertAlmostEqual(converted["filter"]["b"], converter.Bf * 2.0)

    def test_parallel_converter_loss_constraint_matches_pyflow_scaling(self) -> None:
        grid = _build_stagg5_grid()
        grid.Converters_ACDC[0].NumConvP = 2
        data = convert_pyflow_grid_to_opf_data(grid)
        data["converters"]["CONV0"]["loss_mode"] = "fixed"
        model = build_acdc_opf_model(data)

        converter = "CONV0"
        converted = data["converters"][converter]
        current = 0.35
        expected_loss = (
            converted["parallel_converters"] * converted["loss_a"]
            + converted["loss_b"] * current
            + converted["loss_c"] * current**2 / converted["parallel_converters"]
        )

        model.Icv_ac[converter].set_value(current)
        model.Pcv_loss[converter].set_value(expected_loss)

        self.assertAlmostEqual(pyo.value(model.converter_loss[converter].body), 0.0)

    def test_directional_converter_loss_uses_solved_p_ac_sign(self) -> None:
        grid = _build_stagg5_grid()
        data = convert_pyflow_grid_to_opf_data(
            grid,
            options=PyflowToPyomoOptions(converter_loss_switch_sharpness=100.0),
        )
        model = build_acdc_opf_model(data)

        converter = "CONV0"
        converted = data["converters"][converter]
        current = 0.35
        parallel_converters = converted["parallel_converters"]

        for p_ac, loss_c_key in (
            (1.0, "loss_c_positive_p_ac"),
            (-1.0, "loss_c_negative_p_ac"),
        ):
            expected_loss = (
                parallel_converters * converted["loss_a"]
                + converted["loss_b"] * current
                + converted[loss_c_key] * current**2 / parallel_converters
            )
            model.Pcv_ac[converter].set_value(p_ac)
            model.Icv_ac[converter].set_value(current)
            model.Pcv_loss[converter].set_value(expected_loss)

            self.assertAlmostEqual(
                pyo.value(model.converter_loss[converter].body),
                0.0,
                places=8,
            )

    def test_converter_droop_control_is_converted_with_canonical_sign(self) -> None:
        grid = _build_stagg5_grid()
        converter = grid.Converters_ACDC[0]
        converter.type = "Droop"
        converter.P_DC = 0.25
        converter.Droop_rate = 0.15
        converter.Node_DC.V_ini = 1.03

        data = convert_pyflow_grid_to_opf_data(grid)
        droop = data["converters"]["CONV0"]["droop"]

        self.assertTrue(droop["enabled"])
        self.assertAlmostEqual(droop["pdc_set"], -0.25)
        self.assertAlmostEqual(droop["vdc_set"], 1.03)
        self.assertAlmostEqual(droop["k"], 0.15)

    def test_converter_droop_constraint_matches_pyflow_sign_convention(self) -> None:
        grid = _build_stagg5_grid()
        converter = grid.Converters_ACDC[0]
        converter.type = "Droop"
        converter.P_DC = 0.25
        converter.Droop_rate = 0.15
        converter.Node_DC.V_ini = 1.03
        data = convert_pyflow_grid_to_opf_data(grid)
        model = build_acdc_opf_model(data)
        dc_bus = data["converters"]["CONV0"]["dc_bus"]

        model.Vdc[dc_bus].set_value(1.01)
        model.Pcv_dc["CONV0"].set_value(-0.25 + 0.15 * (1.01 - 1.03))

        self.assertAlmostEqual(pyo.value(model.converter_droop["CONV0"].body), 0.0)

    def test_converter_selection_fixes_unselected_converter_controls(self) -> None:
        grid = _build_stagg5_grid()
        data = convert_pyflow_grid_to_opf_data(
            grid,
            options=PyflowToPyomoOptions(
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

    def test_pyflow_p_control_converter_is_treated_as_active_power_controlled(self) -> None:
        grid = _build_stagg5_grid()
        converter = grid.Converters_ACDC[0]
        converter.type = "P"
        converter.AC_type = "PQ"

        data = convert_pyflow_grid_to_opf_data(
            grid,
            options=PyflowToPyomoOptions(
                optimize_converter_active_power=True,
                converter_active_power_indices=(2,),
                optimize_converter_reactive_power=True,
                converter_reactive_power_indices=(2,),
            ),
        )

        self.assertIn("CONV0", data["fixed"].get("Ptf_if", {}))
        self.assertAlmostEqual(data["fixed"]["Ptf_if"]["CONV0"], -float(converter.P_AC))

    def test_control_margin_limits_converter_terminal_controls_around_starting_point(self) -> None:
        grid = _build_stagg5_grid()
        converter = grid.Converters_ACDC[0]
        data = convert_pyflow_grid_to_opf_data(
            grid,
            options=PyflowToPyomoOptions(control_margin_percent=10.0),
        )

        converted = data["converters"]["CONV0"]
        s_rated = float(converter.MVA_max) * float(converter.NumConvP) / float(grid.S_base)
        expected_margin = 0.10 * s_rated
        expected_p0 = -float(converter.P_AC)
        expected_q0 = -float(converter.Q_AC)

        self.assertAlmostEqual(converted["p_terminal_min"], max(-s_rated, expected_p0 - expected_margin))
        self.assertAlmostEqual(converted["p_terminal_max"], min(s_rated, expected_p0 + expected_margin))
        self.assertAlmostEqual(converted["q_terminal_min"], max(-s_rated, expected_q0 - expected_margin))
        self.assertAlmostEqual(converted["q_terminal_max"], min(s_rated, expected_q0 + expected_margin))

    def test_conversion_does_not_modify_original_case(self) -> None:
        grid = _build_stagg5_grid()
        snapshot = copy.deepcopy(
            [
                (float(node.V), float(node.theta))
                for node in grid.nodes_AC
            ]
        )

        convert_pyflow_grid_to_opf_data(grid)

        after = [
            (float(node.V), float(node.theta))
            for node in grid.nodes_AC
        ]
        self.assertEqual(snapshot, after)


if __name__ == "__main__":
    unittest.main()
