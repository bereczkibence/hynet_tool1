from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest import mock

from acdcpf_opf.benchmarks.stagg5.benchmark_pf_opf_comparison import (
    BenchmarkRun,
    BenchmarkTimePoint,
    CONTROL_DEVICE_SCOPE_ALL,
    CONTROL_DEVICE_SCOPE_BENCHMARK,
    EXPORT_MARKDOWN_SUMMARY,
    EXPORT_PRINT_MARKDOWN,
    EXPORT_SUMMARY_ONLY,
    HYBRID_DCDC_ONE_DAY_LOAD_PV_PROFILE,
    ORIGINAL_STAGG5_LOAD_PROFILE,
    OPFControlSelection,
    TOOL1_OBJECTIVE_LABEL,
    TOOL1_VALIDATED_LABEL,
    STAGG5_ONE_DAY_AC_LOAD_PROFILE,
    STAGG5_HYBRID_DCDC,
    STAGG5_TWO_AREA_TRANSFORMER_DCDC,
    VSC_SETPOINT_SCENARIO_CUSTOM,
    VSC_SETPOINT_SCENARIO_ORIGINAL,
    VSC_SETPOINT_SCENARIO_PERTURBED,
    _comparison_table,
    _build_console_summary,
    _build_markdown_report,
    _csv_result_output_paths_from_args,
    _export_csv_report,
    _export_html_grid_view,
    _export_profiled_html_grid_view,
    _export_profiled_split_result_csv_reports,
    _export_split_result_csv_reports,
    _export_split_result_xlsx_reports,
    _export_xlsx_report,
    _format_number,
    _ipopt_executable_path,
    _load_profile_snapshots_from_args,
    _loss_pair_table,
    _markdown_table,
    _max_abs_diff,
    _opf_loss_change_table,
    _opf_conversion_options_for_case,
    _parse_custom_vsc_setpoint,
    _parse_opf_controls,
    _pyflow_opf_can_mirror_control_selection,
    _resolve_project_path,
    _run_acdcpf_pf,
    _run_tool1_pyomo_ipopt,
    _select_grid_case,
    _select_vsc_setpoint_scenario,
    _select_export_mode,
    _should_open_html_report,
    _snapshot_from_acdcpf_pf,
    _snapshot_from_tool1,
    _vsc_setpoint_scenario_table,
    _vsc_control_change_table,
    _write_markdown_report,
    _xlsx_result_output_paths_from_args,
)
from acdcpf_opf.benchmarks.stagg5.case_variants import (
    PYFLOW_IEEE39_ACDC,
    PYFLOW_IEEE39_SLACK_VSC_RATING_MVA,
    create_case5_stagg_mtdc_hybrid_dcdc,
    create_two_area_stagg5_transformer_dcdc,
    create_acdcpf_network_for_stagg5_case,
    create_pyflow_grid_for_stagg5_case,
    get_stagg5_grid_case,
)
from acdcpf_opf.opf.pyomo_acdc_loss_min import PyomoACDCOPFResult
from acdcpf_opf.powerflow.acdcpf_network_adapter import ACDCPFNetworkAdapter


def _normalize_table_spacing(text: str) -> str:
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def _sample_benchmark_runs() -> list[BenchmarkRun]:
    return [
        BenchmarkRun(
            label="ACDCPF PF",
            success=True,
            losses_mw={
                "ac_branch_mw": 1.0,
                "dc_branch_mw": 2.0,
                "converter_mw": 3.0,
                "total_active_losses_mw": 6.0,
            },
            tables={
                "ac_buses": [
                    {"id": 0, "name": "AC1", "v_pu": 1.0, "angle_deg": 0.0},
                    {"id": 1, "name": "AC2", "v_pu": 0.99, "angle_deg": -1.0},
                ],
                "dc_buses": [
                    {"id": 0, "name": "DC1", "v_pu": 1.0},
                    {"id": 1, "name": "DC2", "v_pu": 0.98},
                ],
                "generators": [{"id": 0, "name": "Gen 1", "bus": 0, "p_mw": 10.0, "q_mvar": 3.0}],
                "loads": [{"id": 0, "name": "Load 2", "bus": 1, "p_mw": 8.0, "q_mvar": 2.0}],
                "converters": [
                    {
                        "id": 0,
                        "name": "VSC 1",
                        "ac_bus": 0,
                        "dc_bus": 0,
                        "control_mode": "p_q",
                        "p_ac_mw": 60.0,
                        "q_ac_mvar": 40.0,
                        "p_dc_mw": 58.0,
                        "p_loss_mw": 1.0,
                    }
                ],
                "ac_branches": [
                    {
                        "id": 0,
                        "name": "Line AC1-AC2",
                        "from_bus": 0,
                        "to_bus": 1,
                        "p_from_mw": 9.0,
                        "q_from_mvar": 1.0,
                        "p_to_mw": -8.8,
                        "q_to_mvar": -0.8,
                        "p_loss_mw": 0.2,
                    }
                ],
                "dc_branches": [
                    {
                        "id": 0,
                        "name": "Line DC1-DC2",
                        "from_bus": 0,
                        "to_bus": 1,
                        "p_from_mw": 5.0,
                        "p_to_mw": -4.9,
                        "p_loss_mw": 0.1,
                    }
                ],
            },
        ),
        BenchmarkRun(
            label=TOOL1_VALIDATED_LABEL,
            success=True,
            losses_mw={"total_active_losses_mw": 5.5},
            tables={
                "ac_buses": [
                    {"id": 0, "name": "AC1", "v_pu": 1.01, "angle_deg": 0.0},
                    {"id": 1, "name": "AC2", "v_pu": 1.0, "angle_deg": -0.8},
                ],
                "dc_buses": [
                    {"id": 0, "name": "DC1", "v_pu": 1.01},
                    {"id": 1, "name": "DC2", "v_pu": 0.99},
                ],
                "generators": [{"id": 0, "name": "Gen 1", "bus": 0, "p_mw": 9.5, "q_mvar": 2.8}],
                "loads": [{"id": 0, "name": "Load 2", "bus": 1, "p_mw": 8.0, "q_mvar": 2.0}],
                "converters": [
                    {
                        "id": 0,
                        "name": "VSC 1",
                        "ac_bus": 0,
                        "dc_bus": 0,
                        "control_mode": "p_q",
                        "p_ac_mw": 55.0,
                        "q_ac_mvar": 10.0,
                        "p_dc_mw": 54.0,
                        "p_loss_mw": 0.9,
                    }
                ],
                "ac_branches": [
                    {
                        "id": 0,
                        "name": "Line AC1-AC2",
                        "from_bus": 0,
                        "to_bus": 1,
                        "p_from_mw": 8.5,
                        "q_from_mvar": 0.9,
                        "p_to_mw": -8.3,
                        "q_to_mvar": -0.7,
                        "p_loss_mw": 0.18,
                    }
                ],
                "dc_branches": [
                    {
                        "id": 0,
                        "name": "Line DC1-DC2",
                        "from_bus": 0,
                        "to_bus": 1,
                        "p_from_mw": 4.8,
                        "p_to_mw": -4.7,
                        "p_loss_mw": 0.09,
                    }
                ],
            },
        ),
        BenchmarkRun(
            label="PyFlow PF",
            success=True,
            losses_mw={"total_active_losses_mw": 6.2},
            tables={"converters": [{"id": 0, "p_ac_mw": 60.0, "q_ac_mvar": 40.0}]},
        ),
        BenchmarkRun(
            label="PyFlow OPF VSC-only",
            success=True,
            losses_mw={"total_active_losses_mw": 5.7},
            tables={"converters": [{"id": 0, "p_ac_mw": 56.0, "q_ac_mvar": 12.0}]},
        ),
    ]


def _sample_split_export_runs() -> list[BenchmarkRun]:
    runs = _sample_benchmark_runs()
    opf_objective = BenchmarkRun(
        label=TOOL1_OBJECTIVE_LABEL,
        success=True,
        message="optimal",
        losses_mw={"total_active_losses_mw": 5.6},
        tables=runs[1].tables,
    )
    return [runs[0], opf_objective, runs[1]]


class Stagg5BenchmarkReportTests(unittest.TestCase):
    def test_max_abs_diff_returns_none_for_mismatched_lengths(self) -> None:
        self.assertIsNone(_max_abs_diff([1.0, 2.0], [1.0]))

    def test_max_abs_diff_returns_largest_absolute_difference(self) -> None:
        self.assertAlmostEqual(_max_abs_diff([1.0, 2.5, -4.0], [1.5, 2.0, -1.0]), 3.0)

    def test_loss_pair_table_reports_left_difference_from_right_reference(self) -> None:
        table = _loss_pair_table(
            BenchmarkRun(
                label="A",
                success=True,
                losses_mw={"ac_branch_mw": 1.25, "total_active_losses_mw": 2.5},
            ),
            BenchmarkRun(
                label="B",
                success=True,
                losses_mw={"ac_branch_mw": 2.25, "total_active_losses_mw": 3.0},
            ),
            left_label="A",
            right_label="B",
        )

        rendered = _normalize_table_spacing("\n".join(table))
        self.assertIn("| Quantity | A | B | A - B | A - B % of B |", rendered)
        self.assertIn(
            "| AC branch loss MW | 1.250000 | 2.250000 | -1.000000 | -44.444% |",
            rendered,
        )

    def test_markdown_table_pads_columns_for_terminal_readability(self) -> None:
        table = _markdown_table(["Long header", "B"], [["x", "value"]])

        self.assertEqual(table[0], "| Long header | B     |")
        self.assertEqual(table[1], "| ----------- | ----- |")
        self.assertEqual(table[2], "| x           | value |")

    def test_comparison_table_aligns_rows_by_element_id(self) -> None:
        table = _comparison_table(
            [
                BenchmarkRun(
                    label="A",
                    success=True,
                    tables={"generators": [{"id": 0, "p_mw": 10.0}]},
                ),
                BenchmarkRun(
                    label="B",
                    success=True,
                    tables={"generators": [{"id": 0, "p_mw": 12.0}]},
                ),
            ],
            "generators",
            ["p_mw"],
        )

        rendered = _normalize_table_spacing("\n".join(table))
        self.assertIn("| Element | Field | A | B |", rendered)
        self.assertIn("| 0 | p_mw | 10.000000 | 12.000000 |", rendered)

    def test_format_number_handles_missing_values(self) -> None:
        self.assertEqual(_format_number(None), "n/a")

    def test_parse_opf_controls_supports_presets_and_custom_lists(self) -> None:
        original = get_stagg5_grid_case(None)
        hybrid = get_stagg5_grid_case(STAGG5_HYBRID_DCDC)

        self.assertEqual(
            _parse_opf_controls("benchmark", original),
            OPFControlSelection(vsc_p=True, vsc_q=True),
        )
        self.assertEqual(
            _parse_opf_controls("benchmark", hybrid),
            OPFControlSelection(vsc_p=True, vsc_q=True, dcdc_ratio=True),
        )
        self.assertEqual(_parse_opf_controls("none", original), OPFControlSelection())
        self.assertEqual(
            _parse_opf_controls("vsc_p, vsc_p, ac-gen-q", original),
            OPFControlSelection(vsc_p=True, ac_gen_q=True),
        )

    def test_parse_opf_controls_rejects_unknown_tokens(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            _parse_opf_controls("vsc_p,tap_changer", get_stagg5_grid_case(None))

    def test_opf_conversion_options_follow_runtime_control_selection(self) -> None:
        selected_case = get_stagg5_grid_case(None)

        options = _opf_conversion_options_for_case(
            selected_case,
            opf_control_selection=OPFControlSelection(
                vsc_p=True,
                vsc_q=False,
                ac_gen_p=True,
                ac_gen_q=False,
                dc_gen_curtailment=True,
            ),
            control_device_scope=CONTROL_DEVICE_SCOPE_BENCHMARK,
            control_margin_percent=None,
        )

        self.assertTrue(options.optimize_converter_active_power)
        self.assertEqual(options.converter_active_power_indices, (0, 2))
        self.assertFalse(options.optimize_converter_reactive_power)
        self.assertEqual(options.converter_reactive_power_indices, ())
        self.assertTrue(options.optimize_non_slack_generator_active_power)
        self.assertIsNone(options.ac_generator_active_power_indices)
        self.assertFalse(options.optimize_non_slack_generator_reactive_power)
        self.assertEqual(options.ac_generator_reactive_power_indices, ())
        self.assertTrue(options.optimize_dc_generator_active_power)
        self.assertTrue(options.dc_generator_curtailment_only)

    def test_opf_conversion_options_can_select_all_eligible_devices(self) -> None:
        selected_case = get_stagg5_grid_case(STAGG5_TWO_AREA_TRANSFORMER_DCDC)

        options = _opf_conversion_options_for_case(
            selected_case,
            opf_control_selection=OPFControlSelection(vsc_p=True, dcdc_ratio=True),
            control_device_scope=CONTROL_DEVICE_SCOPE_ALL,
            control_margin_percent=10.0,
        )

        self.assertIsNone(options.converter_active_power_indices)
        self.assertEqual(options.converter_reactive_power_indices, ())
        self.assertIsNone(options.dcdc_voltage_ratio_indices)
        self.assertEqual(options.control_margin_percent, 10.0)

    def test_pyflow_opf_reference_is_comparable_only_for_vsc_pq_controls(self) -> None:
        self.assertTrue(
            _pyflow_opf_can_mirror_control_selection(OPFControlSelection(vsc_p=True, vsc_q=True))
        )
        self.assertFalse(
            _pyflow_opf_can_mirror_control_selection(
                OPFControlSelection(vsc_p=True, vsc_q=True, ac_gen_p=True)
            )
        )

    def test_vsc_control_change_table_reports_selected_converter_changes(self) -> None:
        table = _vsc_control_change_table(
            BenchmarkRun(
                label="ACDCPF PF",
                success=True,
                tables={"converters": [{"id": 0, "p_ac_mw": 60.0, "q_ac_mvar": 40.0}]},
            ),
            BenchmarkRun(
                label="Tool1 (acdcopf)",
                success=True,
                tables={"converters": [{"id": 0, "p_ac_mw": 55.0, "q_ac_mvar": 10.0}]},
            ),
            BenchmarkRun(
                label="PyFlow PF",
                success=True,
                tables={"converters": [{"id": 0, "p_ac_mw": 60.0, "q_ac_mvar": 40.0}]},
            ),
            BenchmarkRun(
                label="PyFlow OPF",
                success=True,
                tables={"converters": [{"id": 0, "p_ac_mw": 50.0, "q_ac_mvar": 5.0}]},
            ),
        )

        rendered = _normalize_table_spacing("\n".join(table))
        self.assertIn("| 0 | p_ac_mw | 60.000000 | 55.000000 | -5.000000 |", rendered)

    def test_write_markdown_report_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "nested" / "report.md"

            written_path = _write_markdown_report("# Report\n", output_path)

            self.assertEqual(written_path, output_path)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "# Report\n")

    def test_console_summary_points_to_markdown_and_keeps_key_losses(self) -> None:
        summary = _build_console_summary(
            [
                BenchmarkRun(
                    label="ACDCPF PF",
                    success=True,
                    losses_mw={"total_active_losses_mw": 8.5},
                ),
                BenchmarkRun(
                    label=TOOL1_OBJECTIVE_LABEL,
                    success=True,
                    losses_mw={"total_active_losses_mw": 8.45},
                ),
                BenchmarkRun(
                    label=TOOL1_VALIDATED_LABEL,
                    success=True,
                    losses_mw={"total_active_losses_mw": 8.4},
                ),
                BenchmarkRun(
                    label="PyFlow PF",
                    success=True,
                    losses_mw={"total_active_losses_mw": 8.6},
                ),
                BenchmarkRun(
                    label="PyFlow OPF VSC-only",
                    success=True,
                    losses_mw={"total_active_losses_mw": 8.3},
                ),
            ],
            Path("report.md"),
            opf_control_selection=OPFControlSelection(vsc_p=True, vsc_q=True),
            control_device_scope=CONTROL_DEVICE_SCOPE_BENCHMARK,
        )

        self.assertIn("Markdown report written to: `report.md`", summary)
        self.assertIn("Enabled OPF controls: VSC active power, VSC reactive power", summary)
        normalized_summary = _normalize_table_spacing(summary)
        self.assertIn(
            "| Run | Total active loss MW | Change vs PF MW | Change vs PF % |",
            normalized_summary,
        )
        self.assertIn(
            "| Tool1 (acdcopf) objective | 8.450000 | -0.050000 | -0.588% |",
            normalized_summary,
        )
        self.assertIn(
            "| PyFlow OPF VSC-only | 8.300000 | -0.300000 | -3.488% |",
            normalized_summary,
        )

    def test_console_summary_points_to_csv_and_xlsx_reports_when_available(self) -> None:
        summary = _build_console_summary(
            [
                BenchmarkRun(
                    label="ACDCPF PF",
                    success=True,
                    losses_mw={"total_active_losses_mw": 8.5},
                )
            ],
            Path("report.md"),
            csv_path=Path("reports/latest.csv"),
            xlsx_path=Path("reports/latest.xlsx"),
        )

        self.assertIn(f"CSV report written to: `{Path('reports/latest.csv')}`", summary)
        self.assertIn(f"Excel workbook written to: `{Path('reports/latest.xlsx')}`", summary)

    def test_relative_output_path_prefers_existing_cwd_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cwd = Path(tmp_dir)
            relative_path = Path(
                "pyflow_test/acdcpf_opf/benchmarks/stagg5/reports/validation_runs/report.md"
            )
            (cwd / relative_path.parent).mkdir(parents=True)

            with mock.patch("pathlib.Path.cwd", return_value=cwd):
                self.assertEqual(_resolve_project_path(relative_path), cwd / relative_path)

    def test_console_summary_mentions_html_only_when_html_report_exists(self) -> None:
        runs = [
            BenchmarkRun(
                label="ACDCPF PF",
                success=True,
                losses_mw={"total_active_losses_mw": 8.5},
            )
        ]

        summary_without_html = _build_console_summary(runs, Path("report.md"))
        summary_with_html = _build_console_summary(
            runs,
            Path("report.md"),
            html_path=Path("grid.html"),
        )

        self.assertNotIn("HTML grid view", summary_without_html)
        self.assertIn("HTML grid view", summary_with_html)

    def test_markdown_report_hides_validation_run_as_visible_result(self) -> None:
        runs = [*_sample_split_export_runs(), *_sample_benchmark_runs()[2:]]
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
        )

        markdown = _build_markdown_report(
            runs,
            matacdc_validation={"available": False, "message": "skipped"},
            generated_at="2026-07-14T12:00:00",
            vsc_setpoint_scenario=scenario,
        )

        self.assertIn(TOOL1_OBJECTIVE_LABEL, markdown)
        self.assertIn("Internal ACDCPF re-solve mismatch", markdown)
        self.assertIn("Enabled OPF controls: VSC active power, VSC reactive power.", markdown)
        self.assertIn("Fixed OPF controls from the selectable list", markdown)
        self.assertNotIn(TOOL1_VALIDATED_LABEL, markdown)

    def test_export_csv_report_writes_named_load_flow_and_optimization_sections(self) -> None:
        runs = _sample_benchmark_runs()
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "benchmark.csv"

            written_path = _export_csv_report(
                runs,
                output_path,
                generated_at="2026-07-14T12:00:00",
                matacdc_validation={"available": False, "message": "skipped"},
                vsc_setpoint_scenario=scenario,
            )
            content = written_path.read_text(encoding="utf-8-sig")
            lines = content.splitlines()

            self.assertEqual(written_path, output_path)
            self.assertNotIn("csv_index", content)
            self.assertNotIn("description,", content)
            self.assertIn("metadata", lines)
            self.assertIn("pf_output", lines)
            self.assertIn("opf_main_values", lines)
            self.assertIn("res_bus", lines)
            self.assertIn("res_line", lines)
            self.assertIn("res_trafo", lines)
            self.assertIn("res_vsc", lines)
            self.assertNotIn("column_legend", lines)
            self.assertNotIn("loss_summary", lines)
            self.assertNotIn("optimized_vsc_controls", lines)
            self.assertNotIn("converter_outputs", lines)
            self.assertNotIn("cross_solver_loss_differences", lines)
            self.assertIn("timestamp;t;case;scenario", content)
            self.assertIn("opf_controls", content)
            self.assertIn("fixed_controls", content)
            self.assertIn("ctrl_scope", content)
            self.assertIn("VSC active power, VSC reactive power", content)
            self.assertIn("pf_loss_total_mw", content)
            self.assertIn("opf_loss_total_mw", content)
            self.assertIn("loss_d_pct", content)
            self.assertIn("vsc0_p_opf_mw", content)
            self.assertIn("vsc0_p_d_pct", content)
            self.assertIn("ac0_v_pf_pu", content)
            self.assertIn("ac0_v_opf_pu", content)
            self.assertIn("ac0_v_d_pu", content)
            self.assertIn("loading_pct", content)
            self.assertIn("pl_mw", content)
            self.assertNotIn("py_pf", content)
            self.assertNotIn("py_opf", content)
            self.assertNotIn("opf_vs_py", content)
            self.assertNotIn("vsc_0_p_ac_mw_tool1_minus_pyflow_opf_percent_of_pyflow", content)

    def test_export_csv_report_preserves_dynamic_element_counts(self) -> None:
        runs = [
            BenchmarkRun(
                label="ACDCPF PF",
                success=True,
                losses_mw={"total_active_losses_mw": 1.0},
                tables={
                    "ac_buses": [
                        {
                            "id": idx,
                            "name": f"Bus {idx}",
                            "v_pu": 1.0,
                            "angle_deg": -float(idx),
                            "p_mw": float(idx),
                            "q_mvar": -float(idx),
                        }
                        for idx in range(8)
                    ],
                    "converters": [
                        {
                            "id": idx,
                            "name": f"VSC {idx}",
                            "ac_bus": idx,
                            "dc_bus": idx,
                            "control_mode": "p_q",
                            "p_ac_mw": 10.0 * idx,
                            "q_ac_mvar": 2.0 * idx,
                            "p_dc_mw": 9.5 * idx,
                            "p_loss_mw": 0.5 * idx,
                            "s_mva": 100.0,
                            "loading_percent": float(idx),
                        }
                        for idx in range(6)
                    ],
                    "ac_branches": [
                        {
                            "id": idx,
                            "name": f"Line {idx}",
                            "from_bus": idx,
                            "to_bus": idx + 1,
                            "p_from_mw": float(idx),
                            "q_from_mvar": 0.0,
                            "p_to_mw": -float(idx),
                            "q_to_mvar": 0.0,
                            "p_loss_mw": 0.1,
                        }
                        for idx in range(7)
                    ],
                },
            )
        ]
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "benchmark.csv"

            written_path = _export_csv_report(
                runs,
                output_path,
                generated_at="2026-07-14T12:00:00",
                matacdc_validation={"available": False, "message": "skipped"},
                vsc_setpoint_scenario=scenario,
            )
            content = written_path.read_text(encoding="utf-8-sig")

            self.assertIn("Bus 7", content)
            self.assertIn("VSC 5", content)
            self.assertIn("Line 6", content)

    def test_result_output_paths_split_pf_and_opf_names(self) -> None:
        csv_pf, csv_opf = _csv_result_output_paths_from_args(
            argparse.Namespace(csv_output=Path("report.csv"), csv_output_dir=None)
        )
        xlsx_pf, xlsx_opf = _xlsx_result_output_paths_from_args(
            argparse.Namespace(xlsx_output=Path("report.xlsx"), xlsx_output_dir=None)
        )

        self.assertEqual(csv_pf, Path("report_pf.csv"))
        self.assertEqual(csv_opf, Path("report_opf.csv"))
        self.assertEqual(xlsx_pf, Path("report_pf.xlsx"))
        self.assertEqual(xlsx_opf, Path("report_opf.xlsx"))

    def test_split_csv_exports_write_separate_pf_and_opf_result_files(self) -> None:
        runs = _sample_split_export_runs()
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            pf_path = Path(tmp_dir) / "pf.csv"
            opf_path = Path(tmp_dir) / "opf.csv"

            written_paths = _export_split_result_csv_reports(
                runs,
                (pf_path, opf_path),
                generated_at="2026-07-14T12:00:00",
                vsc_setpoint_scenario=scenario,
            )

            self.assertEqual(written_paths, [pf_path, opf_path])
            pf_content = pf_path.read_text(encoding="utf-8-sig")
            opf_content = opf_path.read_text(encoding="utf-8-sig")

            self.assertIn("res_bus", pf_content)
            self.assertIn("res_line", pf_content)
            self.assertIn("ACDCPF PF", pf_content)
            self.assertNotIn(TOOL1_OBJECTIVE_LABEL, pf_content)
            self.assertNotIn(TOOL1_VALIDATED_LABEL, pf_content)

            self.assertIn("res_bus", opf_content)
            self.assertIn("res_line", opf_content)
            self.assertIn(TOOL1_OBJECTIVE_LABEL, opf_content)
            self.assertIn("Original Stagg5 MTDC", opf_content)
            self.assertNotIn("grid_case_name", opf_content)
            self.assertNotIn("export_scope", opf_content)
            self.assertNotIn("case5_stagg_mtdc_slack", opf_content)
            self.assertNotIn("ACDCPF PF;0", opf_content)
            self.assertNotIn(TOOL1_VALIDATED_LABEL, opf_content)

    def test_profiled_split_csv_exports_write_multiple_time_indices(self) -> None:
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
        )
        time_points = [
            BenchmarkTimePoint(
                time_index=0,
                timestamp="2026-01-01T00:00:00",
                runs=_sample_split_export_runs(),
            ),
            BenchmarkTimePoint(
                time_index=1,
                timestamp="2026-01-01T01:00:00",
                runs=_sample_split_export_runs(),
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            pf_path = Path(tmp_dir) / "pf.csv"
            opf_path = Path(tmp_dir) / "opf.csv"

            written_paths = _export_profiled_split_result_csv_reports(
                time_points,
                (pf_path, opf_path),
                generated_at="2026-07-14T12:00:00",
                vsc_setpoint_scenario=scenario,
            )

            self.assertEqual(written_paths, [pf_path, opf_path])
            pf_content = pf_path.read_text(encoding="utf-8-sig")
            opf_content = opf_path.read_text(encoding="utf-8-sig")
            for content in (pf_content, opf_content):
                self.assertIn("2026-01-01T00:00:00", content)
                self.assertIn("2026-01-01T01:00:00", content)
                self.assertIn(";0;", content)
                self.assertIn(";1;", content)

    def test_split_xlsx_exports_write_matching_pf_and_opf_result_workbooks(self) -> None:
        runs = _sample_split_export_runs()
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            pf_path = Path(tmp_dir) / "pf.xlsx"
            opf_path = Path(tmp_dir) / "opf.xlsx"

            written_paths = _export_split_result_xlsx_reports(
                runs,
                (pf_path, opf_path),
                generated_at="2026-07-14T12:00:00",
                vsc_setpoint_scenario=scenario,
            )

            self.assertEqual(written_paths, [pf_path, opf_path])
            for path, expected_run, excluded_run in (
                (pf_path, "ACDCPF PF", TOOL1_OBJECTIVE_LABEL),
                (opf_path, TOOL1_OBJECTIVE_LABEL, TOOL1_VALIDATED_LABEL),
            ):
                with zipfile.ZipFile(path) as workbook:
                    workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
                    worksheet_xml = "\n".join(
                        workbook.read(name).decode("utf-8")
                        for name in workbook.namelist()
                        if name.startswith("xl/worksheets/sheet")
                    )

                self.assertIn('name="res_bus"', workbook_xml)
                self.assertIn('name="res_line"', workbook_xml)
                self.assertNotIn('name="opf_main_values"', workbook_xml)
                self.assertIn(expected_run, worksheet_xml)
                self.assertNotIn(excluded_run, worksheet_xml)

    def test_opf_snapshot_populates_bus_injections_and_currents(self) -> None:
        data = {
            "base_mva": 100.0,
            "ac_buses": {
                "AC0": {
                    "name": "AC bus",
                    "v0": 1.0,
                    "theta0": 0.0,
                    "v_base_kv": 100.0,
                    "g_shunt": 0.0,
                    "b_shunt": 0.0,
                }
            },
            "dc_buses": {"DC0": {"name": "DC bus", "v0": 1.0, "v_base_kv": 200.0}},
            "generators": {"G0": {"name": "Gen", "bus": "AC0"}},
            "ac_loads": {"L0": {"name": "Load", "bus": "AC0", "p": 0.2, "q": 0.05}},
            "dc_generators": {},
            "dc_loads": {},
            "converters": {
                "CONV0": {
                    "name": "VSC",
                    "ac_bus": "AC0",
                    "dc_bus": "DC0",
                    "control_mode": "p_q",
                    "s_ac_rated": 1.2,
                }
            },
            "ac_branches": {
                "AC_LINE0": {
                    "name": "AC line",
                    "from": "AC0",
                    "to": "AC0",
                    "rate": 2.0,
                }
            },
            "dc_branches": {
                "DC_LINE0": {
                    "name": "DC line",
                    "from": "DC0",
                    "to": "DC0",
                    "rate": 1.0,
                }
            },
            "dcdc_converters": {},
            "fixed": {},
        }
        extracted = {
            "objective_total_active_losses_mw": 1.0,
            "active_loss_breakdown_mw": {"total_active_losses_mw": 1.0},
            "ac_bus_voltage_magnitude_pu": {"AC0": 1.0},
            "ac_bus_voltage_angle_rad": {"AC0": 0.0},
            "dc_bus_voltage_pu": {"DC0": 1.0},
            "generator_pg_pu": {"G0": 1.0},
            "generator_qg_pu": {"G0": 0.3},
            "dc_generator_pg_pu": {},
            "ac_branch_p_from_pu": {"AC_LINE0": 0.5},
            "ac_branch_q_from_pu": {"AC_LINE0": 0.1},
            "ac_branch_p_to_pu": {"AC_LINE0": -0.49},
            "ac_branch_q_to_pu": {"AC_LINE0": -0.09},
            "dc_branch_p_from_pu": {"DC_LINE0": 0.2},
            "dc_branch_p_to_pu": {"DC_LINE0": -0.19},
            "dc_branch_loss_pu": {"DC_LINE0": 0.01},
            "dc_branch_current_pu": {"DC_LINE0": 0.2},
            "converter_p_ac_terminal_absorbed_pu": {"CONV0": 0.4},
            "converter_q_ac_terminal_absorbed_pu": {"CONV0": 0.1},
            "converter_p_dc_pu": {"CONV0": -0.39},
            "converter_loss_pu": {"CONV0": 0.01},
            "converter_transformer_loss_pu": {"CONV0": 0.0},
            "converter_phase_reactor_loss_pu": {"CONV0": 0.0},
            "converter_i_ac_pu": {"CONV0": 0.5},
            "converter_i_dc_pu": {"CONV0": -0.2},
        }

        run = _snapshot_from_tool1(
            TOOL1_OBJECTIVE_LABEL,
            PyomoACDCOPFResult(
                success=True,
                message="optimal",
                data=data,
                extracted_results=extracted,
            ),
        )

        ac_bus = run.tables["ac_buses"][0]
        dc_bus = run.tables["dc_buses"][0]
        gen = run.tables["generators"][0]
        ac_line = run.tables["ac_branches"][0]
        dc_line = run.tables["dc_branches"][0]
        vsc = run.tables["converters"][0]

        self.assertAlmostEqual(ac_bus["p_mw"], 80.0)
        self.assertAlmostEqual(ac_bus["q_mvar"], 25.0)
        self.assertAlmostEqual(dc_bus["p_mw"], 39.0)
        self.assertEqual(gen["v_pu"], 1.0)
        self.assertGreater(ac_line["i_ka"], 0.0)
        self.assertAlmostEqual(dc_line["i_ka"], 0.1)
        self.assertGreater(vsc["i_ac_ka"], 0.0)
        self.assertAlmostEqual(vsc["i_dc_ka"], 0.1)
        self.assertEqual(vsc["v_ac_pu"], 1.0)
        self.assertEqual(vsc["v_dc_pu"], 1.0)

    def test_export_csv_report_can_include_detailed_sections(self) -> None:
        runs = _sample_benchmark_runs()
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "benchmark.csv"

            written_path = _export_csv_report(
                runs,
                output_path,
                generated_at="2026-07-14T12:00:00",
                matacdc_validation={"available": False, "message": "skipped"},
                vsc_setpoint_scenario=scenario,
                include_detailed_export=True,
            )
            lines = written_path.read_text(encoding="utf-8-sig").splitlines()

            self.assertIn("loss_summary", lines)
            self.assertIn("optimized_vsc_controls", lines)
            self.assertIn("converter_outputs", lines)

    def test_export_csv_report_can_include_pyflow_reference_columns(self) -> None:
        runs = _sample_benchmark_runs()
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "benchmark.csv"

            written_path = _export_csv_report(
                runs,
                output_path,
                generated_at="2026-07-14T12:00:00",
                matacdc_validation={"available": False, "message": "skipped"},
                vsc_setpoint_scenario=scenario,
                include_pyflow_reference=True,
            )
            content = written_path.read_text(encoding="utf-8-sig")
            lines = content.splitlines()

            self.assertIn("cross_solver_loss_differences", lines)
            self.assertIn("py_pf", content)
            self.assertIn("py_opf", content)
            self.assertIn("vsc0_p_mw_opf_vs_py_d_pct", content)

    def test_export_xlsx_report_writes_one_sheet_per_group(self) -> None:
        runs = _sample_benchmark_runs()
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "benchmark.xlsx"

            written_path = _export_xlsx_report(
                runs,
                output_path,
                generated_at="2026-07-14T12:00:00",
                matacdc_validation={"available": False, "message": "skipped"},
                vsc_setpoint_scenario=scenario,
            )

            self.assertEqual(written_path, output_path)
            with zipfile.ZipFile(written_path) as workbook:
                names = set(workbook.namelist())
                self.assertIn("xl/workbook.xml", names)
                self.assertIn("xl/worksheets/sheet1.xml", names)

                workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
                self.assertIn('name="pf_output"', workbook_xml)
                self.assertIn('name="opf_main_values"', workbook_xml)
                self.assertIn('name="res_bus"', workbook_xml)
                self.assertIn('name="res_line"', workbook_xml)
                self.assertIn('name="res_trafo"', workbook_xml)
                self.assertIn('name="res_vsc"', workbook_xml)
                self.assertNotIn('name="column_legend"', workbook_xml)
                self.assertNotIn('name="loss_summary"', workbook_xml)
                self.assertNotIn('name="optimized_vsc_controls"', workbook_xml)
                self.assertNotIn('name="converter_outputs"', workbook_xml)
                self.assertNotIn('name="cross_solver_loss_differences"', workbook_xml)

                worksheet_xml = "\n".join(
                    workbook.read(name).decode("utf-8")
                    for name in sorted(names)
                    if name.startswith("xl/worksheets/sheet")
                )
                self.assertIn("timestamp", worksheet_xml)
                self.assertIn("pf_loss_total_mw", worksheet_xml)
                self.assertIn("opf_loss_total_mw", worksheet_xml)
                self.assertIn("vsc0_p_opf_mw", worksheet_xml)
                self.assertIn("vsc0_p_d_pct", worksheet_xml)
                self.assertIn("ac0_v_pf_pu", worksheet_xml)
                self.assertIn("ac0_v_opf_pu", worksheet_xml)
                self.assertIn("ac0_v_d_pu", worksheet_xml)
                self.assertIn("loading_pct", worksheet_xml)
                self.assertIn("pl_mw", worksheet_xml)
                self.assertNotIn("py_pf", worksheet_xml)
                self.assertNotIn("py_opf", worksheet_xml)
                self.assertNotIn("opf_vs_py", worksheet_xml)
                self.assertNotIn(
                    "vsc_0_p_ac_mw_tool1_minus_pyflow_opf_percent_of_pyflow",
                    worksheet_xml,
                )

    def test_export_html_grid_view_writes_interactive_topology(self) -> None:
        runs = _sample_benchmark_runs()
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "grid" / "view.html"

            written_path = _export_html_grid_view(
                runs,
                output_path,
                generated_at="2026-07-14T12:00:00",
                vsc_setpoint_scenario=scenario,
            )

            content = written_path.read_text(encoding="utf-8")
            self.assertEqual(written_path, output_path)
            self.assertIn("Tool1 (acdcopf) Interactive Grid View", content)
            self.assertIn("const RAW_DATA =", content)
            self.assertIn("let DATA = activeGridData(RAW_DATA)", content)
            self.assertIn('"ac_branches"', content)
            self.assertIn('"from_bus": 0', content)
            self.assertIn('"to_bus": 1', content)
            self.assertIn('"control_mode": "p_q"', content)
            self.assertIn('"pyflow_pf"', content)
            self.assertIn('"pyflow_opf"', content)
            self.assertIn('"pyflow_pf_total_loss_mw": 6.2', content)
            self.assertIn("equipment-edge", content)
            self.assertIn("branchPathData", content)
            self.assertIn("Branch names: hidden", content)
            self.assertIn("placeBranchLabel", content)
            self.assertIn("labelScore", content)
            self.assertIn("Delta % vs PF", content)
            self.assertIn("PyFlow reference", content)
            self.assertIn("PF baseline", content)
            self.assertIn("OPF result", content)
            self.assertIn("OPF controls:", content)

    def test_profiled_html_grid_view_writes_selectable_profile_curves(self) -> None:
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
        )
        time_points = [
            BenchmarkTimePoint(
                time_index=0,
                timestamp="2026-01-01T00:00:00",
                runs=[
                    BenchmarkRun(
                        label="ACDCPF PF",
                        success=True,
                        losses_mw={"total_active_losses_mw": 1.0},
                        tables={
                            "ac_buses": [
                                {"id": 0, "name": "AC Bus 1", "v_pu": 1.0, "angle_deg": 0.0},
                                {"id": 1, "name": "AC Bus 2", "v_pu": 0.99, "angle_deg": -1.0},
                            ],
                            "dc_buses": [
                                {"id": 0, "name": "DC Bus 1", "v_pu": 1.01},
                            ],
                            "loads": [
                                {"id": 0, "name": "AC Load", "bus": 1, "p_mw": 10.0, "q_mvar": 2.0}
                            ],
                            "dc_loads": [
                                {"id": 0, "name": "DC Load", "bus": 0, "p_mw": 5.0}
                            ],
                            "dc_generators": [
                                {"id": 0, "name": "DC PV", "bus": 0, "p_mw": 0.0}
                            ],
                            "storage_units": [
                                {
                                    "id": 0,
                                    "name": "Battery",
                                    "bus": 0,
                                    "bus_type": "dc",
                                    "p_mw": -2.0,
                                    "p_charge_mw": 2.0,
                                    "p_discharge_mw": 0.0,
                                    "q_mvar": 0.5,
                                    "soc_percent": 50.0,
                                }
                            ],
                        },
                    ),
                    BenchmarkRun(
                        label=TOOL1_OBJECTIVE_LABEL,
                        success=True,
                        losses_mw={"total_active_losses_mw": 0.9},
                        tables={
                            "ac_buses": [
                                {"id": 0, "name": "AC Bus 1", "v_pu": 1.0, "angle_deg": 0.0},
                                {"id": 1, "name": "AC Bus 2", "v_pu": 0.995, "angle_deg": -0.8},
                            ],
                            "dc_buses": [
                                {"id": 0, "name": "DC Bus 1", "v_pu": 1.02},
                            ],
                            "loads": [
                                {"id": 0, "name": "AC Load", "bus": 1, "p_mw": 10.0, "q_mvar": 2.0}
                            ],
                            "dc_loads": [
                                {"id": 0, "name": "DC Load", "bus": 0, "p_mw": 5.0}
                            ],
                            "dc_generators": [
                                {"id": 0, "name": "DC PV", "bus": 0, "p_mw": 0.0}
                            ],
                            "storage_units": [
                                {
                                    "id": 0,
                                    "name": "Battery",
                                    "bus": 0,
                                    "bus_type": "dc",
                                    "p_mw": 1.0,
                                    "p_charge_mw": 0.0,
                                    "p_discharge_mw": 1.0,
                                    "q_mvar": -0.2,
                                    "soc_percent": 48.0,
                                }
                            ],
                        },
                    ),
                ],
            ),
            BenchmarkTimePoint(
                time_index=1,
                timestamp="2026-01-01T01:00:00",
                runs=[
                    BenchmarkRun(
                        label="ACDCPF PF",
                        success=True,
                        losses_mw={"total_active_losses_mw": 1.2},
                        tables={
                            "ac_buses": [
                                {"id": 0, "name": "AC Bus 1", "v_pu": 1.0, "angle_deg": 0.0},
                                {"id": 1, "name": "AC Bus 2", "v_pu": 0.98, "angle_deg": -1.5},
                            ],
                            "dc_buses": [
                                {"id": 0, "name": "DC Bus 1", "v_pu": 1.015},
                            ],
                            "loads": [
                                {"id": 0, "name": "AC Load", "bus": 1, "p_mw": 12.0, "q_mvar": 2.4}
                            ],
                            "dc_loads": [
                                {"id": 0, "name": "DC Load", "bus": 0, "p_mw": 6.0}
                            ],
                            "dc_generators": [
                                {"id": 0, "name": "DC PV", "bus": 0, "p_mw": 8.0}
                            ],
                            "storage_units": [
                                {
                                    "id": 0,
                                    "name": "Battery",
                                    "bus": 0,
                                    "bus_type": "dc",
                                    "p_mw": -2.0,
                                    "p_charge_mw": 2.0,
                                    "p_discharge_mw": 0.0,
                                    "q_mvar": 0.5,
                                    "soc_percent": 50.0,
                                }
                            ],
                        },
                    ),
                    BenchmarkRun(
                        label=TOOL1_OBJECTIVE_LABEL,
                        success=True,
                        losses_mw={"total_active_losses_mw": 1.1},
                        tables={
                            "ac_buses": [
                                {"id": 0, "name": "AC Bus 1", "v_pu": 1.0, "angle_deg": 0.0},
                                {"id": 1, "name": "AC Bus 2", "v_pu": 0.985, "angle_deg": -1.2},
                            ],
                            "dc_buses": [
                                {"id": 0, "name": "DC Bus 1", "v_pu": 1.025},
                            ],
                            "loads": [
                                {"id": 0, "name": "AC Load", "bus": 1, "p_mw": 12.0, "q_mvar": 2.4}
                            ],
                            "dc_loads": [
                                {"id": 0, "name": "DC Load", "bus": 0, "p_mw": 6.0}
                            ],
                            "dc_generators": [
                                {"id": 0, "name": "DC PV", "bus": 0, "p_mw": 7.5}
                            ],
                            "storage_units": [
                                {
                                    "id": 0,
                                    "name": "Battery",
                                    "bus": 0,
                                    "bus_type": "dc",
                                    "p_mw": -1.0,
                                    "p_charge_mw": 1.0,
                                    "p_discharge_mw": 0.0,
                                    "q_mvar": -0.1,
                                    "soc_percent": 52.0,
                                }
                            ],
                        },
                    ),
                ],
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "grid" / "profiled_view.html"

            written_path = _export_profiled_html_grid_view(
                time_points,
                output_path,
                generated_at="2026-07-14T12:00:00",
                vsc_setpoint_scenario=scenario,
            )

            content = written_path.read_text(encoding="utf-8")

        payload_start = content.index("const RAW_DATA = ") + len("const RAW_DATA = ")
        payload_end = content.index(";\n    let DATA", payload_start)
        payload = json.loads(content[payload_start:payload_end])
        curve_elements = payload["profile_curves"]["elements"]
        labels_by_group = {element["table_name"]: element["group_label"] for element in curve_elements}
        ac_bus = next(element for element in curve_elements if element["table_name"] == "ac_buses" and element["id"] == "1")
        dc_bus = next(element for element in curve_elements if element["table_name"] == "dc_buses")
        dc_gen = next(element for element in curve_elements if element["table_name"] == "dc_generators")
        storage = next(element for element in curve_elements if element["table_name"] == "storage_units")
        losses = next(element for element in curve_elements if element["table_name"] == "losses")

        self.assertIn("Profile Curves", content)
        self.assertIn("profileCurveGroupSelect", content)
        self.assertIn("profileCurveTooltip", content)
        self.assertIn("curve-hit-point", content)
        self.assertIn(".curve-line.curve-opf { fill: none; }", content)
        self.assertIn("powerAxis", content)
        self.assertIn("timeLabel", content)
        self.assertIn("renderProfileCurve", content)
        self.assertNotIn("position: sticky", content)
        self.assertEqual(payload["profile_curves"]["timeline"][1]["time_index"], 1)
        self.assertEqual(labels_by_group["ac_buses"], "AC bus voltages")
        self.assertEqual(labels_by_group["dc_buses"], "DC bus voltages")
        self.assertEqual(labels_by_group["loads"], "AC loads")
        self.assertEqual(labels_by_group["dc_loads"], "DC loads")
        self.assertEqual(labels_by_group["dc_generators"], "DC generators")
        self.assertEqual(labels_by_group["storage_units"], "Storage")
        self.assertEqual(labels_by_group["losses"], "Losses")
        self.assertEqual(ac_bus["series"]["v_pu"]["pf"], [0.99, 0.98])
        self.assertEqual(ac_bus["series"]["v_pu"]["opf"], [0.995, 0.985])
        self.assertEqual(dc_bus["series"]["v_pu"]["opf"], [1.02, 1.025])
        self.assertEqual(dc_gen["series"]["p_mw"]["pf"], [0.0, 8.0])
        self.assertEqual(dc_gen["series"]["p_mw"]["opf"], [0.0, 7.5])
        self.assertEqual(storage["series"]["q_mvar"]["pf"], [0.5, 0.5])
        self.assertEqual(storage["series"]["q_mvar"]["opf"], [-0.2, -0.1])
        self.assertEqual(storage["series"]["p_charge_mw"]["pf"], [2.0, 2.0])
        self.assertEqual(storage["series"]["p_charge_mw"]["opf"], [0.0, 1.0])
        self.assertEqual(storage["series"]["p_discharge_mw"]["pf"], [0.0, 0.0])
        self.assertEqual(storage["series"]["p_discharge_mw"]["opf"], [1.0, 0.0])
        self.assertEqual(storage["series"]["soc_percent"]["pf"], [50.0, 50.0])
        self.assertEqual(storage["series"]["soc_percent"]["opf"], [48.0, 52.0])
        self.assertEqual(losses["series"]["total_active_losses_mw"]["pf"], [1.0, 1.2])
        self.assertEqual(losses["series"]["total_active_losses_mw"]["opf"], [0.9, 1.1])

    def test_opf_loss_change_table_reports_percent_change_from_pf(self) -> None:
        table = _opf_loss_change_table(
            BenchmarkRun(
                label="ACDCPF PF",
                success=True,
                losses_mw={"total_active_losses_mw": 10.0},
            ),
            BenchmarkRun(
                label=TOOL1_VALIDATED_LABEL,
                success=True,
                losses_mw={"total_active_losses_mw": 8.5},
            ),
            BenchmarkRun(
                label="PyFlow PF",
                success=True,
                losses_mw={"total_active_losses_mw": 12.0},
            ),
            BenchmarkRun(
                label="PyFlow OPF VSC-only",
                success=True,
                losses_mw={"total_active_losses_mw": 9.0},
            ),
        )

        rendered = _normalize_table_spacing("\n".join(table))
        self.assertIn("| Run | PF baseline MW | OPF result MW | Change MW | Change % |", rendered)
        self.assertIn("| Tool1 (acdcopf) | 10.000000 | 8.500000 | -1.500000 | -15.000% |", rendered)
        self.assertIn("| PyFlow OPF | 12.000000 | 9.000000 | -3.000000 | -25.000% |", rendered)

    def test_select_export_mode_honors_explicit_flags(self) -> None:
        self.assertEqual(
            _select_export_mode(
                argparse.Namespace(
                    print_markdown=True,
                    no_markdown_output=False,
                    markdown_output=None,
                )
            ),
            EXPORT_PRINT_MARKDOWN,
        )
        self.assertEqual(
            _select_export_mode(
                argparse.Namespace(
                    print_markdown=False,
                    no_markdown_output=True,
                    markdown_output=None,
                )
            ),
            EXPORT_SUMMARY_ONLY,
        )

    def test_select_export_mode_defaults_without_prompt_when_not_interactive(self) -> None:
        with mock.patch("sys.stdin.isatty", return_value=False):
            mode = _select_export_mode(
                argparse.Namespace(
                    print_markdown=False,
                    no_markdown_output=False,
                    markdown_output=None,
                )
            )

        self.assertEqual(mode, EXPORT_MARKDOWN_SUMMARY)

    def test_select_grid_case_honors_explicit_argument(self) -> None:
        grid_case = _select_grid_case(argparse.Namespace(grid_case=STAGG5_HYBRID_DCDC))

        self.assertEqual(grid_case.key, STAGG5_HYBRID_DCDC)
        self.assertTrue(grid_case.optimize_dcdc_voltage_ratio)

    def test_select_grid_case_accepts_ieee39_alias(self) -> None:
        grid_case = _select_grid_case(argparse.Namespace(grid_case="ieee39"))

        self.assertEqual(grid_case.key, PYFLOW_IEEE39_ACDC)
        self.assertEqual(grid_case.source, "pyflow")
        self.assertIsNone(grid_case.converter_active_power_indices)
        self.assertIsNone(grid_case.converter_reactive_power_indices)

    def test_select_grid_case_accepts_two_area_transformer_dcdc_alias(self) -> None:
        grid_case = _select_grid_case(argparse.Namespace(grid_case="two_area"))

        self.assertEqual(grid_case.key, STAGG5_TWO_AREA_TRANSFORMER_DCDC)
        self.assertEqual(grid_case.source, "acdcpf")
        self.assertFalse(grid_case.supports_pyflow)
        self.assertEqual(grid_case.converter_active_power_indices, (0, 2, 3, 5))
        self.assertEqual(grid_case.dcdc_voltage_ratio_indices, (0,))

    def test_select_grid_case_defaults_without_prompt_when_not_interactive(self) -> None:
        with mock.patch("sys.stdin.isatty", return_value=False):
            grid_case = _select_grid_case(argparse.Namespace(grid_case=None))

        self.assertEqual(grid_case.case_name, "case5_stagg_mtdc_slack")

    def test_profile_loader_defaults_to_single_snapshot_when_not_interactive(self) -> None:
        args = argparse.Namespace(
            profile_input=None,
            profile_sheet=None,
            profile_format="auto",
        )
        with (
            mock.patch("sys.stdin.isatty", return_value=False),
            mock.patch("builtins.input", side_effect=AssertionError("input should not be called")),
        ):
            snapshots = _load_profile_snapshots_from_args(args, get_stagg5_grid_case(None))

        self.assertIsNone(snapshots)
        self.assertIsNone(args.profile_input)

    def test_profile_loader_accepts_bundled_profile_from_terminal_prompt(self) -> None:
        args = argparse.Namespace(
            profile_input=None,
            profile_sheet=None,
            profile_format="auto",
        )
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="2"),
        ):
            snapshots = _load_profile_snapshots_from_args(args, get_stagg5_grid_case(None))

        self.assertIsNotNone(snapshots)
        assert snapshots is not None
        self.assertEqual(len(snapshots), 3)
        self.assertEqual(args.profile_input, ORIGINAL_STAGG5_LOAD_PROFILE)
        self.assertEqual(args.profile_format, "auto")

    def test_profile_loader_accepts_one_day_profile_from_terminal_prompt(self) -> None:
        args = argparse.Namespace(
            profile_input=None,
            profile_sheet=None,
            profile_format="auto",
        )
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="3"),
        ):
            snapshots = _load_profile_snapshots_from_args(args, get_stagg5_grid_case(None))

        self.assertIsNotNone(snapshots)
        assert snapshots is not None
        self.assertEqual(len(snapshots), 24)
        self.assertEqual(args.profile_input, STAGG5_ONE_DAY_AC_LOAD_PROFILE)
        self.assertEqual(args.profile_format, "auto")

    def test_profile_loader_accepts_hybrid_one_day_profile_from_terminal_prompt(self) -> None:
        args = argparse.Namespace(
            profile_input=None,
            profile_sheet=None,
            profile_format="auto",
        )
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="3"),
        ):
            snapshots = _load_profile_snapshots_from_args(
                args,
                get_stagg5_grid_case(STAGG5_HYBRID_DCDC),
            )

        self.assertIsNotNone(snapshots)
        assert snapshots is not None
        self.assertEqual(len(snapshots), 24)
        self.assertEqual(len(snapshots[0].changes), 5)
        self.assertEqual(args.profile_input, HYBRID_DCDC_ONE_DAY_LOAD_PV_PROFILE)
        self.assertEqual(args.profile_format, "auto")

    def test_profile_loader_accepts_custom_profile_from_terminal_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_path = Path(tmp_dir) / "custom_profile.csv"
            profile_path.write_text(
                "\n".join(
                    [
                        "time_index;timestamp;element_type;element_id;scale",
                        "0;2026-01-01T00:00:00;ac_load;0;1.0",
                    ]
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                profile_input=None,
                profile_sheet=None,
                profile_format="auto",
            )

            with (
                mock.patch("sys.stdin.isatty", return_value=True),
                mock.patch("builtins.input", side_effect=["4", str(profile_path), "", "auto"]),
            ):
                snapshots = _load_profile_snapshots_from_args(args, get_stagg5_grid_case(None))

        self.assertIsNotNone(snapshots)
        assert snapshots is not None
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(args.profile_input, profile_path)
        self.assertEqual(args.profile_sheet, None)
        self.assertEqual(args.profile_format, "auto")

    def test_profile_loader_skips_prompted_profiles_for_pyflow_sourced_case(self) -> None:
        args = argparse.Namespace(
            profile_input=None,
            profile_sheet=None,
            profile_format="auto",
        )
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", side_effect=AssertionError("input should not be called")),
        ):
            snapshots = _load_profile_snapshots_from_args(
                args,
                get_stagg5_grid_case(PYFLOW_IEEE39_ACDC),
            )

        self.assertIsNone(snapshots)
        self.assertIsNone(args.profile_input)

    def test_original_stagg5_pf_loss_is_frozen_with_tolerance(self) -> None:
        pf_run, _ = _run_acdcpf_pf(VSC_SETPOINT_SCENARIO_ORIGINAL)

        self.assertTrue(pf_run.success)
        self.assertAlmostEqual(
            pf_run.losses_mw["total_active_losses_mw"],
            8.626045,
            delta=0.05,
        )

    def test_original_stagg5_opf_loss_is_frozen_with_tolerance(self) -> None:
        if not _ipopt_executable_path().exists():
            self.skipTest("IPOPT executable is not available in the local solver bundle.")

        opf_run, _ = _run_tool1_pyomo_ipopt(VSC_SETPOINT_SCENARIO_ORIGINAL)

        self.assertTrue(opf_run.success)
        self.assertAlmostEqual(
            opf_run.losses_mw["total_active_losses_mw"],
            8.624743,
            delta=0.05,
        )

    def test_hybrid_stagg5_case_contains_dc_resources_and_converges(self) -> None:
        net = create_case5_stagg_mtdc_hybrid_dcdc()

        self.assertEqual(len(net.dc_gen), 1)
        self.assertEqual(len(net.dc_load), 0)
        self.assertEqual(len(net.storage), 1)
        self.assertEqual(len(net.dcdc), 2)
        self.assertIn("d_ratio_min", net.dcdc.columns)
        self.assertIn("d_ratio_max", net.dcdc.columns)
        self.assertAlmostEqual(net.storage.at[0, "sn_mva"], 25.0)
        self.assertAlmostEqual(net.storage.at[0, "energy_mwh"], 50.0)
        self.assertAlmostEqual(net.storage.at[0, "soc_percent"], 50.0)

        result = ACDCPFNetworkAdapter(max_iter_outer=80, tolerance=1e-8).solve(
            net,
            copy_case=True,
            write_back=False,
        )

        self.assertTrue(result.converged)
        self.assertIn("dcdc_converter_mw", result.active_loss_breakdown())

    def test_two_area_stagg5_case_contains_transformers_dcdc_and_converges(self) -> None:
        net = create_two_area_stagg5_transformer_dcdc()

        self.assertEqual(len(net.ac_bus), 11)
        self.assertEqual(len(net.dc_bus), 6)
        self.assertEqual(len(net.trafo), 2)
        self.assertEqual(len(net.dcdc), 1)
        self.assertEqual(list(net.ac_bus.index[net.ac_bus["is_slack"] == True]), [0])
        self.assertIn("d_ratio_min", net.dcdc.columns)
        self.assertIn("d_ratio_max", net.dcdc.columns)

        result = ACDCPFNetworkAdapter(max_iter_outer=80, tolerance=1e-8).solve(
            net,
            copy_case=True,
            write_back=False,
        )
        run = _snapshot_from_acdcpf_pf("ACDCPF PF", result)

        self.assertTrue(result.converged)
        self.assertIn("transformer_mw", result.active_loss_breakdown())
        self.assertEqual(len(run.tables["transformers"]), 2)
        self.assertGreater(run.tables["transformers"][0]["loading_percent"], 0.0)
        self.assertEqual(run.tables["transformers"][0]["tap"], 1.0)

    def test_two_area_stagg5_opf_reduces_loss_with_dcdc_ratio_control(self) -> None:
        if not _ipopt_executable_path().exists():
            self.skipTest("IPOPT executable is not available in the local solver bundle.")

        pf_run, _ = _run_acdcpf_pf(
            VSC_SETPOINT_SCENARIO_ORIGINAL,
            STAGG5_TWO_AREA_TRANSFORMER_DCDC,
        )
        opf_run, _ = _run_tool1_pyomo_ipopt(
            VSC_SETPOINT_SCENARIO_ORIGINAL,
            STAGG5_TWO_AREA_TRANSFORMER_DCDC,
        )

        self.assertTrue(pf_run.success)
        self.assertTrue(opf_run.success)
        self.assertLessEqual(
            opf_run.losses_mw["total_active_losses_mw"],
            pf_run.losses_mw["total_active_losses_mw"],
        )
        self.assertIn("dcdc_converter_mw", opf_run.losses_mw)
        self.assertIn("transformer_mw", opf_run.losses_mw)

    def test_acdcpf_snapshot_includes_pandapower_style_result_fields(self) -> None:
        net = create_case5_stagg_mtdc_hybrid_dcdc()
        result = ACDCPFNetworkAdapter(max_iter_outer=80, tolerance=1e-8).solve(
            net,
            copy_case=True,
            write_back=False,
        )
        run = _snapshot_from_acdcpf_pf("ACDCPF PF", result)

        self.assertEqual(len(run.tables["generators"]), 2)
        self.assertGreater(run.tables["generators"][0]["p_mw"], 100.0)
        self.assertAlmostEqual(
            run.tables["ac_buses"][0]["p_mw"],
            run.tables["generators"][0]["p_mw"],
        )
        self.assertAlmostEqual(
            run.tables["ac_buses"][0]["q_mvar"],
            run.tables["generators"][0]["q_mvar"],
        )
        self.assertAlmostEqual(run.tables["ac_buses"][1]["p_mw"], 20.0)
        self.assertEqual(run.tables["transformers"], [])
        self.assertAlmostEqual(run.tables["ac_branches"][0]["rate_mva"], 150.0)
        self.assertIsNotNone(run.tables["ac_branches"][0]["loading_percent"])
        self.assertAlmostEqual(run.tables["dc_branches"][0]["rate_mw"], 100.0)
        self.assertIsNotNone(run.tables["dc_branches"][0]["loading_percent"])
        dc_branch = run.tables["dc_branches"][0]
        self.assertAlmostEqual(
            dc_branch["loading_percent"],
            100.0
            * max(abs(dc_branch["p_from_mw"]), abs(dc_branch["p_to_mw"]))
            / dc_branch["rate_mw"],
        )
        self.assertIn("s_mva", run.tables["converters"][0])
        self.assertGreater(run.tables["converters"][0]["loading_percent"], 0.0)
        self.assertGreater(run.tables["converters"][0]["i_dc_ka"], 0.0)
        self.assertEqual(len(run.tables["storage_units"]), 1)
        self.assertAlmostEqual(run.tables["storage_units"][0]["p_mw"], -15.0)
        self.assertAlmostEqual(run.tables["storage_units"][0]["p_charge_mw"], 15.0)
        self.assertAlmostEqual(run.tables["storage_units"][0]["p_discharge_mw"], 0.0)
        self.assertEqual(run.tables["storage_units"][0]["mode"], "charging")
        self.assertAlmostEqual(run.tables["storage_units"][0]["soc_percent"], 50.0)
        self.assertAlmostEqual(run.tables["storage_units"][0]["energy_capacity_mwh"], 50.0)

        solved_net = result.raw_result
        solved_net.ac_line.at[0, "tap"] = 1.05
        solved_net.ac_line.at[0, "max_i_ka"] = 1.0
        transformer_like_run = _snapshot_from_acdcpf_pf("ACDCPF PF", result)

        self.assertEqual(len(transformer_like_run.tables["transformers"]), 1)
        self.assertIsNotNone(transformer_like_run.tables["transformers"][0]["loading_percent"])

    def test_original_stagg5_benchmark_adds_reference_line_ratings(self) -> None:
        net = create_acdcpf_network_for_stagg5_case()

        self.assertAlmostEqual(net.ac_line.at[0, "rate_mva"], 150.0)
        self.assertAlmostEqual(net.ac_line.at[1, "rate_mva"], 100.0)
        self.assertAlmostEqual(net.dc_line.at[0, "rate_mw"], 100.0)
        self.assertGreater(net.ac_line.at[0, "max_i_ka"], 0.0)
        self.assertGreater(net.dc_line.at[0, "max_i_ka"], 0.0)

    def test_html_report_opens_by_default_unless_disabled(self) -> None:
        self.assertTrue(_should_open_html_report(argparse.Namespace(no_open_html=False)))
        self.assertFalse(_should_open_html_report(argparse.Namespace(no_open_html=True)))

    def test_select_vsc_setpoint_scenario_honors_explicit_argument(self) -> None:
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(
                vsc_setpoint_scenario=VSC_SETPOINT_SCENARIO_PERTURBED,
                vsc_setpoint=[],
            )
        )

        self.assertEqual(scenario.name, VSC_SETPOINT_SCENARIO_PERTURBED)
        self.assertEqual(scenario.setpoints[0]["p_ac_mw"], 90.0)

    def test_select_vsc_setpoint_scenario_defaults_without_prompt_when_not_interactive(self) -> None:
        with mock.patch("sys.stdin.isatty", return_value=False):
            scenario = _select_vsc_setpoint_scenario(
                argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[])
            )

        self.assertEqual(scenario.name, VSC_SETPOINT_SCENARIO_ORIGINAL)

    def test_vsc_setpoint_scenario_table_contains_perturbed_values(self) -> None:
        table = _normalize_table_spacing(
            "\n".join(_vsc_setpoint_scenario_table(VSC_SETPOINT_SCENARIO_PERTURBED))
        )

        self.assertIn("| 0 | 90.000000 | 50.000000 |", table)
        self.assertIn("| 2 | -80.000000 | 40.000000 |", table)

    def test_parse_custom_vsc_setpoint_accepts_colon_and_comma_formats(self) -> None:
        self.assertEqual(
            _parse_custom_vsc_setpoint("0:90:50"),
            (0, {"p_ac_mw": 90.0, "q_ac_mvar": 50.0}),
        )
        self.assertEqual(
            _parse_custom_vsc_setpoint("VSC2,-80,40"),
            (2, {"p_ac_mw": -80.0, "q_ac_mvar": 40.0}),
        )

    def test_parse_custom_vsc_setpoint_accepts_case_defined_indices(self) -> None:
        self.assertEqual(
            _parse_custom_vsc_setpoint("9:10:5"),
            (9, {"p_ac_mw": 10.0, "q_ac_mvar": 5.0}),
        )

    def test_ieee39_predefined_scenario_keeps_case_factory_setpoints(self) -> None:
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(vsc_setpoint_scenario=None, vsc_setpoint=[]),
            get_stagg5_grid_case(PYFLOW_IEEE39_ACDC),
        )

        self.assertEqual(scenario.name, VSC_SETPOINT_SCENARIO_ORIGINAL)
        self.assertEqual(scenario.setpoints, {})

    def test_ieee39_pyflow_case_is_normalized_for_pf_and_opf(self) -> None:
        try:
            grid = create_pyflow_grid_for_stagg5_case(PYFLOW_IEEE39_ACDC)
        except ImportError as exc:
            self.skipTest(f"pyflow_acdc is not importable: {exc}")

        self.assertEqual(len(grid.nodes_AC), 39)
        self.assertEqual(len(grid.nodes_DC), 10)
        self.assertEqual(len(grid.Converters_ACDC), 10)
        self.assertEqual(grid.Converters_ACDC[0].type, "Slack")
        self.assertEqual(grid.Converters_ACDC[0].Node_DC.type, "Slack")
        self.assertGreaterEqual(grid.Converters_ACDC[0].MVA_max, PYFLOW_IEEE39_SLACK_VSC_RATING_MVA)

    def test_select_vsc_setpoint_scenario_uses_custom_overrides(self) -> None:
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(
                vsc_setpoint_scenario=None,
                vsc_setpoint=["0:12.5:-3.0"],
            )
        )

        self.assertEqual(scenario.name, VSC_SETPOINT_SCENARIO_CUSTOM)
        self.assertEqual(scenario.setpoints[0]["p_ac_mw"], 12.5)
        self.assertEqual(scenario.setpoints[0]["q_ac_mvar"], -3.0)
        self.assertEqual(scenario.setpoints[2]["p_ac_mw"], -35.0)

    def test_vsc_setpoint_scenario_table_contains_custom_values(self) -> None:
        scenario = _select_vsc_setpoint_scenario(
            argparse.Namespace(
                vsc_setpoint_scenario=None,
                vsc_setpoint=["0:12.5:-3.0", "2:-70:15"],
            )
        )
        table = _normalize_table_spacing("\n".join(_vsc_setpoint_scenario_table(scenario)))

        self.assertIn("| 0 | 12.500000 | -3.000000 |", table)
        self.assertIn("| 2 | -70.000000 | 15.000000 |", table)


if __name__ == "__main__":
    unittest.main()
