from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping
import warnings
import webbrowser
import zipfile
from xml.sax.saxutils import escape as xml_escape

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PARENT = PACKAGE_ROOT.parent
if __package__ in (None, "") and str(SOURCE_PARENT) not in sys.path:
    sys.path.insert(0, str(SOURCE_PARENT))
PROJECT_ROOT = PACKAGE_ROOT


def _dependency_import_error_message(exc: Exception) -> str:
    return "\n".join(
        [
            "Required benchmark dependencies are not available for the Python interpreter that ran this script.",
            "",
            f"Current Python: `{sys.executable}`",
            f"Current Python version: `{sys.version.split()[0]}`",
            "Install Tool1 (acdcopf) in this interpreter; see docs/INSTALLATION.md.",
            "",
            f"Original import error: {type(exc).__name__}: {exc}",
        ]
    )


try:
    import numpy as np
    import pyomo.environ as pyo
except Exception as exc:
    raise SystemExit(_dependency_import_error_message(exc)) from exc

from acdcpf_opf.data.acdcpf_to_pyomo import ACDCPFToPyomoOptions
from acdcpf_opf.data.pyflow_to_pyomo import PyflowToPyomoOptions
from acdcpf_opf.data.time_profiles import (
    ProfileSnapshot,
    apply_profile_snapshot,
    load_time_profile,
)
from acdcpf_opf.benchmarks.stagg5.case_variants import (
    ORIGINAL_STAGG5_CASE,
    PYFLOW_IEEE39_ACDC,
    STAGG5_GRID_CASES,
    STAGG5_HYBRID_DCDC,
    STAGG5_ORIGINAL,
    STAGG5_TWO_AREA_TRANSFORMER_DCDC,
    Stagg5GridCase,
    TWO_AREA_VSC_FACTORY_SETPOINTS_MW_MVAR,
    create_acdcpf_network_for_stagg5_case,
    create_pyflow_grid_for_stagg5_case,
    get_stagg5_grid_case,
)
from acdcpf_opf.opf.pyomo_acdc_loss_min import (
    PyomoACDCOPFConfig,
    PyomoACDCOPFResult,
    solve_pyomo_acdc_loss_min_opf,
    solve_pyomo_acdc_loss_min_time_series,
)
from acdcpf_opf.powerflow.acdcpf_adapter import ACDCPFAdapter
from acdcpf_opf.powerflow.acdcpf_network_adapter import ACDCPFNetworkAdapter
from acdcpf_opf.powerflow.result import PFResult
from acdcpf_opf.runtime_paths import ipopt_executable_path, reports_directory


CASE_NAME = ORIGINAL_STAGG5_CASE.case_name
TOOL1_OBJECTIVE_LABEL = "Tool1 (acdcopf) objective"
TOOL1_VALIDATED_LABEL = "Tool1 (acdcopf) validated by ACDCPF PF"
DEFAULT_MARKDOWN_REPORT = (
    reports_directory() / "latest_stagg5_pf_opf_benchmark.md"
)
DEFAULT_CSV_REPORT = (
    reports_directory() / "latest_stagg5_pf_opf_benchmark.csv"
)
DEFAULT_XLSX_REPORT = (
    reports_directory() / "latest_stagg5_pf_opf_benchmark.xlsx"
)
DEFAULT_PF_CSV_REPORT = (
    reports_directory() / "latest_stagg5_pf_results.csv"
)
DEFAULT_OPF_CSV_REPORT = (
    reports_directory() / "latest_stagg5_opf_results.csv"
)
DEFAULT_PF_XLSX_REPORT = (
    reports_directory() / "latest_stagg5_pf_results.xlsx"
)
DEFAULT_OPF_XLSX_REPORT = (
    reports_directory() / "latest_stagg5_opf_results.xlsx"
)
DEFAULT_HTML_REPORT = (
    reports_directory() / "latest_stagg5_grid_view.html"
)
PROFILE_DIRECTORY = Path(__file__).resolve().parent / "profiles"
ORIGINAL_STAGG5_LOAD_PROFILE = PROFILE_DIRECTORY / "original_stagg5_load_profile.csv"
STAGG5_ONE_DAY_AC_LOAD_PROFILE = PROFILE_DIRECTORY / "stagg5_one_day_ac_load_profile.csv"
HYBRID_DCDC_LOAD_PV_PROFILE = PROFILE_DIRECTORY / "hybrid_dcdc_load_pv_profile.csv"
HYBRID_DCDC_ONE_DAY_LOAD_PV_PROFILE = PROFILE_DIRECTORY / "hybrid_dcdc_one_day_load_pv_profile.csv"
PROFILE_INPUT_FORMATS = ("auto", "csv", "xlsx")
EXPORT_MARKDOWN_SUMMARY = "markdown_summary"
EXPORT_MARKDOWN_ONLY = "markdown_only"
EXPORT_SUMMARY_ONLY = "summary_only"
EXPORT_PRINT_MARKDOWN = "print_markdown"
VSC_SETPOINT_SCENARIO_ORIGINAL = "original"
VSC_SETPOINT_SCENARIO_PERTURBED = "perturbed"
VSC_SETPOINT_SCENARIO_CUSTOM = "custom"
VSC_SETPOINT_SCENARIOS = (VSC_SETPOINT_SCENARIO_ORIGINAL, VSC_SETPOINT_SCENARIO_PERTURBED)
VSC_SETPOINT_SCENARIO_DESCRIPTIONS = {
    VSC_SETPOINT_SCENARIO_ORIGINAL: "Original Stagg5 benchmark VSC setpoints.",
    VSC_SETPOINT_SCENARIO_PERTURBED: (
        "Perturbed VSC setpoints for a more visible optimization movement."
    ),
    VSC_SETPOINT_SCENARIO_CUSTOM: "Custom user-provided VSC starting setpoints.",
}
VSC_STARTING_SETPOINTS_MW_MVAR = {
    VSC_SETPOINT_SCENARIO_ORIGINAL: {
        0: {"p_ac_mw": 60.0, "q_ac_mvar": 40.0},
        2: {"p_ac_mw": -35.0, "q_ac_mvar": -5.0},
    },
    VSC_SETPOINT_SCENARIO_PERTURBED: {
        0: {"p_ac_mw": 90.0, "q_ac_mvar": 50.0},
        2: {"p_ac_mw": -80.0, "q_ac_mvar": 40.0},
    },
}
PYFLOW_STAGG_GENERATOR_Q_LIMITS_MVAR = {
    0: (-500.0, 500.0),
    1: (-300.0, 300.0),
}
LOSS_EXPORT_QUANTITIES = (
    ("AC branch loss MW", "ac_branch_mw"),
    ("Transformer loss MW", "transformer_mw"),
    ("DC branch loss MW", "dc_branch_mw"),
    ("DCDC converter loss MW", "dcdc_converter_mw"),
    ("Converter loss MW", "converter_mw"),
    ("Total active loss MW", "total_active_losses_mw"),
)
CSV_DELIMITER = ";"
CSV_ENCODING = "utf-8-sig"
TIME_SERIES_KEY_FIELDS = ["timestamp", "time_index", "case", "vsc_setpoint_scenario"]
BASE_EXPORT_COLUMN_ALIASES = {
    "time_index": "t",
    "vsc_setpoint_scenario": "scenario",
    "vsc_setpoint_description": "scenario_desc",
    "optimized_controls": "opt_controls",
    "enabled_opf_controls": "opf_controls",
    "fixed_opf_controls": "fixed_controls",
    "control_device_scope": "ctrl_scope",
    "control_scope": "ctrl_detail",
}
RUN_EXPORT_ALIASES = {
    "acdcpf_pf": "pf",
    "tool1_objective": "opf_model",
    "tool1_validated_by_acdcpf_pf": "opf",
    "pyflow_pf": "py_pf",
    "pyflow_opf_vsc_only": "py_opf",
}
LOSS_EXPORT_ALIASES = {
    "ac_branch_mw": "loss_ac_mw",
    "transformer_mw": "loss_trafo_mw",
    "dc_branch_mw": "loss_dc_mw",
    "dcdc_converter_mw": "loss_dcdc_mw",
    "converter_mw": "loss_vsc_mw",
    "total_active_losses_mw": "loss_tot_mw",
}
TABLE_EXPORT_ALIASES = {
    "ac_buses": "ac",
    "dc_buses": "dc",
    "generators": "gen",
    "dc_generators": "dcgen",
    "dc_loads": "dcload",
    "storage_units": "storage",
    "converters": "vsc",
    "dcdc_converters": "dcdc",
    "transformers": "trafo",
    "ac_branches": "acbr",
    "dc_branches": "dcbr",
}
FIELD_EXPORT_ALIASES = {
    "angle_deg": "ang_deg",
    "i_ac_ka": "i_ac_ka",
    "i_dc_ka": "i_dc_ka",
    "i_ka": "i_ka",
    "loading_percent": "loading_pct",
    "p_ac_mw": "pac_mw",
    "q_ac_mvar": "qac_mvar",
    "p_dc_mw": "pdc_mw",
    "p_from_mw": "p_fr_mw",
    "q_from_mvar": "q_fr_mvar",
    "p_to_mw": "p_to_mw",
    "q_to_mvar": "q_to_mvar",
    "p_loss_mw": "loss_mw",
    "p_elec_loss_mw": "elec_loss_mw",
    "p_mw": "p_mw",
    "q_mvar": "q_mvar",
    "v_pu": "v_pu",
    "d_ratio": "ratio",
}
PANDAPOWER_STYLE_SECTIONS: tuple[tuple[str, str, str, str, list[tuple[str, str]]], ...] = (
    (
        "res_bus",
        "Pandapower-style AC bus results. One row is written per run and AC bus.",
        "ac_buses",
        "bus",
        [
            ("name", "name"),
            ("v_pu", "vm_pu"),
            ("angle_deg", "va_degree"),
            ("p_mw", "p_mw"),
            ("q_mvar", "q_mvar"),
        ],
    ),
    (
        "res_line",
        "Pandapower-style AC line results. One row is written per run and AC line.",
        "ac_branches",
        "line",
        [
            ("name", "name"),
            ("from_bus", "from_bus"),
            ("to_bus", "to_bus"),
            ("p_from_mw", "p_from_mw"),
            ("q_from_mvar", "q_from_mvar"),
            ("p_to_mw", "p_to_mw"),
            ("q_to_mvar", "q_to_mvar"),
            ("p_loss_mw", "pl_mw"),
            ("q_loss_mvar", "ql_mvar"),
            ("s_from_mva", "s_from_mva"),
            ("s_to_mva", "s_to_mva"),
            ("i_ka", "i_ka"),
            ("rate_mva", "rate_mva"),
            ("loading_percent", "loading_pct"),
        ],
    ),
    (
        "res_trafo",
        "Pandapower-style transformer results. One row is written per run and transformer.",
        "transformers",
        "trafo",
        [
            ("name", "name"),
            ("hv_bus", "hv_bus"),
            ("lv_bus", "lv_bus"),
            ("p_hv_mw", "p_hv_mw"),
            ("q_hv_mvar", "q_hv_mvar"),
            ("p_lv_mw", "p_lv_mw"),
            ("q_lv_mvar", "q_lv_mvar"),
            ("p_loss_mw", "pl_mw"),
            ("q_loss_mvar", "ql_mvar"),
            ("s_hv_mva", "s_hv_mva"),
            ("s_lv_mva", "s_lv_mva"),
            ("i_ka", "i_ka"),
            ("rate_mva", "rate_mva"),
            ("loading_percent", "loading_pct"),
            ("tap", "tap"),
            ("shift_deg", "shift_deg"),
        ],
    ),
    (
        "res_load",
        "Pandapower-style AC load results. One row is written per run and AC load.",
        "loads",
        "load",
        [("name", "name"), ("bus", "bus"), ("p_mw", "p_mw"), ("q_mvar", "q_mvar")],
    ),
    (
        "res_gen",
        "Pandapower-style AC generator results. One row is written per run and AC generator.",
        "generators",
        "gen",
        [
            ("name", "name"),
            ("bus", "bus"),
            ("p_mw", "p_mw"),
            ("q_mvar", "q_mvar"),
            ("v_pu", "vm_pu"),
        ],
    ),
    (
        "res_dc_bus",
        "DC bus results in the same result-table style. One row is written per run and DC bus.",
        "dc_buses",
        "bus",
        [("name", "name"), ("v_pu", "vm_pu"), ("v_kv", "v_kv"), ("p_mw", "p_mw")],
    ),
    (
        "res_dc_line",
        "DC line results in the same result-table style. One row is written per run and DC line.",
        "dc_branches",
        "line",
        [
            ("name", "name"),
            ("from_bus", "from_bus"),
            ("to_bus", "to_bus"),
            ("p_from_mw", "p_from_mw"),
            ("p_to_mw", "p_to_mw"),
            ("p_loss_mw", "pl_mw"),
            ("i_ka", "i_ka"),
            ("rate_mw", "rate_mw"),
            ("loading_percent", "loading_pct"),
        ],
    ),
    (
        "res_dc_load",
        "DC load results in the same result-table style. One row is written per run and DC load.",
        "dc_loads",
        "load",
        [("name", "name"), ("bus", "bus"), ("p_mw", "p_mw")],
    ),
    (
        "res_dc_gen",
        "DC generator results in the same result-table style. One row is written per run and DC generator.",
        "dc_generators",
        "gen",
        [("name", "name"), ("bus", "bus"), ("p_mw", "p_mw")],
    ),
    (
        "res_storage",
        (
            "One-timestamp storage/BESS results. Positive p_mw means discharging "
            "into the connected bus; negative p_mw means charging."
        ),
        "storage_units",
        "storage",
        [
            ("name", "name"),
            ("bus_type", "bus_type"),
            ("bus", "bus"),
            ("p_mw", "p_mw"),
            ("p_charge_mw", "p_charge_mw"),
            ("p_discharge_mw", "p_discharge_mw"),
            ("q_mvar", "q_mvar"),
            ("s_mva", "sn_mva"),
            ("energy_mwh", "energy_mwh"),
            ("energy_capacity_mwh", "energy_capacity_mwh"),
            ("soc_percent", "soc_percent"),
            ("soc_min_percent", "soc_min_percent"),
            ("soc_max_percent", "soc_max_percent"),
            ("loading_percent", "loading_pct"),
            ("mode", "mode"),
        ],
    ),
    (
        "res_vsc",
        "VSC converter results in a compact result-table style. One row is written per run and VSC.",
        "converters",
        "vsc",
        [
            ("name", "name"),
            ("ac_bus", "ac_bus"),
            ("dc_bus", "dc_bus"),
            ("control_mode", "ctrl"),
            ("p_ac_mw", "p_ac_mw"),
            ("q_ac_mvar", "q_ac_mvar"),
            ("p_dc_mw", "p_dc_mw"),
            ("p_loss_mw", "pl_mw"),
            ("p_elec_loss_mw", "p_elec_loss_mw"),
            ("s_mva", "sn_mva"),
            ("loading_percent", "loading_pct"),
            ("i_ac_ka", "i_ac_ka"),
            ("i_dc_ka", "i_dc_ka"),
            ("v_ac_pu", "v_ac_pu"),
            ("v_dc_pu", "v_dc_pu"),
        ],
    ),
    (
        "res_dcdc",
        "DCDC converter results in a compact result-table style. One row is written per run and DCDC converter.",
        "dcdc_converters",
        "dcdc",
        [
            ("name", "name"),
            ("from_bus", "from_bus"),
            ("to_bus", "to_bus"),
            ("p_from_mw", "p_from_mw"),
            ("p_to_mw", "p_to_mw"),
            ("p_loss_mw", "pl_mw"),
            ("d_ratio", "ratio"),
            ("r_ohm", "r_ohm"),
            ("rate_mw", "rate_mw"),
            ("loading_percent", "loading_pct"),
        ],
    ),
)
VSC_CONTROL_SUFFIX_ALIASES = {
    "acdcpf_pf": "pf",
    "tool1": "opf",
    "tool1_change_from_acdcpf_pf": "opf_d",
    "tool1_change_percent_from_acdcpf_pf": "opf_d_pct",
    "pyflow_pf": "py_pf",
    "pyflow_opf": "py_opf",
    "pyflow_change_from_pf": "py_d",
    "pyflow_change_percent_from_pf": "py_d_pct",
    "tool1_minus_pyflow_opf": "opf_vs_py_d",
    "tool1_minus_pyflow_opf_percent_of_pyflow": "opf_vs_py_d_pct",
}
OPF_LOSS_CHANGE_ALIASES = {
    "tool1_pf_baseline_run": "opf_pf_run",
    "tool1_opf_result_run": "opf_run",
    "tool1_pf_baseline_mw": "pf_loss_mw",
    "tool1_opf_result_mw": "opf_loss_mw",
    "tool1_change_mw": "opf_d_mw",
    "tool1_change_percent": "opf_d_pct",
    "pyflow_opf_pf_baseline_run": "py_pf_run",
    "pyflow_opf_opf_result_run": "py_opf_run",
    "pyflow_opf_pf_baseline_mw": "py_pf_loss_mw",
    "pyflow_opf_opf_result_mw": "py_opf_loss_mw",
    "pyflow_opf_change_mw": "py_d_mw",
    "pyflow_opf_change_percent": "py_d_pct",
}
VOLTAGE_MOVEMENT_ALIASES = {
    "tool1_max_ac_v_diff_pu": "opf_ac_v_max_d_pu",
    "tool1_max_ac_angle_diff_deg": "opf_ac_ang_max_d_deg",
    "tool1_max_dc_v_diff_pu": "opf_dc_v_max_d_pu",
    "pyflow_max_ac_v_diff_pu": "py_ac_v_max_d_pu",
    "pyflow_max_ac_angle_diff_deg": "py_ac_ang_max_d_deg",
    "pyflow_max_dc_v_diff_pu": "py_dc_v_max_d_pu",
}
CONTROL_DEVICE_SCOPE_BENCHMARK = "benchmark"
CONTROL_DEVICE_SCOPE_ALL = "all"
CONTROL_DEVICE_SCOPES = (CONTROL_DEVICE_SCOPE_BENCHMARK, CONTROL_DEVICE_SCOPE_ALL)
OPF_CONTROL_PRESET_BENCHMARK = "benchmark"
OPF_CONTROL_PRESET_NONE = "none"
OPF_CONTROL_PRESET_ALL = "all"
OPF_CONTROL_TOKENS = (
    "vsc_p",
    "vsc_q",
    "dcdc_ratio",
    "ac_gen_p",
    "ac_gen_q",
    "dc_gen_curtailment",
)
OPF_CONTROL_DISPLAY_NAMES = {
    "vsc_p": "VSC active power",
    "vsc_q": "VSC reactive power",
    "dcdc_ratio": "DCDC voltage ratio",
    "ac_gen_p": "AC generator active power",
    "ac_gen_q": "AC generator reactive power",
    "dc_gen_curtailment": "DC generator curtailment",
}


@dataclass(frozen=True)
class VSCSetpointScenario:
    """Starting VSC setpoints shared by the ACDCPF and PyFlow benchmark runs."""

    name: str
    description: str
    setpoints: dict[int, dict[str, float]]


@dataclass(frozen=True)
class OPFControlSelection:
    """User-selected Tool1 (acdcopf) controls for one benchmark run."""

    vsc_p: bool = False
    vsc_q: bool = False
    dcdc_ratio: bool = False
    ac_gen_p: bool = False
    ac_gen_q: bool = False
    dc_gen_curtailment: bool = False

    def enabled_tokens(self) -> tuple[str, ...]:
        return tuple(token for token in OPF_CONTROL_TOKENS if bool(getattr(self, token)))


@dataclass
class BenchmarkRun:
    """One benchmark run converted into a common reporting shape."""

    label: str
    success: bool
    message: str = ""
    losses_mw: dict[str, float] = field(default_factory=dict)
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkTimePoint:
    """PF/OPF benchmark results for one independent time-profile snapshot."""

    time_index: int
    timestamp: str
    runs: list[BenchmarkRun]
    matacdc_validation: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimeProfileInputSelection:
    """Time-profile input selected through CLI arguments or the terminal prompt."""

    profile_input: Path
    profile_sheet: str | int | None = None
    profile_format: str = "auto"


@dataclass(frozen=True)
class TimeProfileMenuItem:
    """One terminal menu option for time-profile selection."""

    label: str
    description: str
    profile_input: Path | None = None
    is_custom: bool = False


@dataclass(frozen=True)
class ReportSection:
    """One named table exported to CSV and XLSX reports."""

    name: str
    description: str
    fieldnames: list[str]
    rows: list[Mapping[str, Any]]


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.CRITICAL, format="%(levelname)s:%(name)s:%(message)s")
    logging.getLogger("pyomo.opt").setLevel(logging.CRITICAL)
    _configure_warning_filters()

    _ensure_local_ipopt_on_path()
    grid_case = _select_grid_case(args)
    vsc_setpoint_scenario = _select_vsc_setpoint_scenario(args, grid_case)
    opf_control_selection = _select_opf_control_selection(args, grid_case)
    control_device_scope = _select_control_device_scope(args)
    control_margin_percent = _select_control_margin_percent(args)
    profile_snapshots = _load_profile_snapshots_from_args(args, grid_case)
    if profile_snapshots is not None:
        _run_time_profile_benchmark(
            args,
            profile_snapshots=profile_snapshots,
            grid_case=grid_case,
            vsc_setpoint_scenario=vsc_setpoint_scenario,
            opf_control_selection=opf_control_selection,
            control_device_scope=control_device_scope,
            control_margin_percent=control_margin_percent,
        )
        return

    runs: list[BenchmarkRun] = []
    matacdc_validation: dict[str, Any] = {}

    acdcpf_pf_run, acdcpf_pf_result = _run_acdcpf_pf(vsc_setpoint_scenario, grid_case)
    runs.append(acdcpf_pf_run)
    if (
        acdcpf_pf_result is not None
        and vsc_setpoint_scenario.name == VSC_SETPOINT_SCENARIO_ORIGINAL
        and grid_case.supports_matacdc_reference
    ):
        matacdc_validation = _compare_acdcpf_pf_to_matacdc_reference(
            acdcpf_pf_result,
            grid_case,
        )
    elif acdcpf_pf_result is not None:
        matacdc_validation = {
            "available": False,
            "message": (
                "MATACDC reference check skipped because this run uses "
                f"grid case `{grid_case.case_name}` and VSC setpoint scenario "
                f"`{vsc_setpoint_scenario.name}`."
            ),
        }

    if not args.skip_opf:
        tool1_run, validation_run = _run_tool1_pyomo_ipopt(
            vsc_setpoint_scenario,
            grid_case,
            opf_control_selection=opf_control_selection,
            control_device_scope=control_device_scope,
            control_margin_percent=control_margin_percent,
        )
        runs.append(tool1_run)
        if validation_run is not None:
            runs.append(validation_run)

    if not args.skip_pyflow:
        if grid_case.supports_pyflow:
            pyflow_pf_run = _run_pyflow_pf(vsc_setpoint_scenario, grid_case)
            runs.append(pyflow_pf_run)
            pyflow_opf_run = _run_pyflow_opf(
                vsc_setpoint_scenario,
                grid_case,
                opf_control_selection=opf_control_selection,
            )
            runs.append(pyflow_opf_run)
        else:
            runs.extend(_pyflow_unavailable_runs(grid_case))

    generated_at = datetime.now().isoformat(timespec="seconds")
    markdown = _build_markdown_report(
        runs,
        matacdc_validation=matacdc_validation,
        generated_at=generated_at,
        vsc_setpoint_scenario=vsc_setpoint_scenario,
        grid_case=grid_case,
        control_margin_percent=control_margin_percent,
        opf_control_selection=opf_control_selection,
        control_device_scope=control_device_scope,
    )
    csv_paths: list[Path] = []
    xlsx_paths: list[Path] = []
    html_path = None
    if not args.no_csv_output:
        csv_output_paths = _csv_result_output_paths_from_args(args)
        try:
            csv_paths = _export_split_result_csv_reports(
                runs,
                csv_output_paths,
                generated_at=generated_at,
                vsc_setpoint_scenario=vsc_setpoint_scenario,
                grid_case=grid_case,
                control_margin_percent=control_margin_percent,
                opf_control_selection=opf_control_selection,
                control_device_scope=control_device_scope,
            )
        except PermissionError as exc:
            raise SystemExit(_csv_permission_error_message(csv_output_paths[0])) from exc
    if not args.no_xlsx_output:
        xlsx_output_paths = _xlsx_result_output_paths_from_args(args)
        try:
            xlsx_paths = _export_split_result_xlsx_reports(
                runs,
                xlsx_output_paths,
                generated_at=generated_at,
                vsc_setpoint_scenario=vsc_setpoint_scenario,
                grid_case=grid_case,
                control_margin_percent=control_margin_percent,
                opf_control_selection=opf_control_selection,
                control_device_scope=control_device_scope,
            )
        except PermissionError as exc:
            raise SystemExit(_xlsx_permission_error_message(xlsx_output_paths[0])) from exc
    if not args.no_html_output:
        html_output_path = _html_output_path_from_args(args)
        try:
            html_path = _export_html_grid_view(
                runs,
                html_output_path,
                generated_at=generated_at,
                vsc_setpoint_scenario=vsc_setpoint_scenario,
                grid_case=grid_case,
                control_margin_percent=control_margin_percent,
                opf_control_selection=opf_control_selection,
                control_device_scope=control_device_scope,
            )
        except PermissionError as exc:
            raise SystemExit(_html_permission_error_message(html_output_path)) from exc
        if _should_open_html_report(args):
            _open_html_report(html_path)
    export_mode = _select_export_mode(args)
    _export_report(
        markdown,
        runs,
        args.markdown_output or DEFAULT_MARKDOWN_REPORT,
        export_mode,
        csv_path=csv_paths,
        xlsx_path=xlsx_paths,
        html_path=html_path,
        vsc_setpoint_scenario=vsc_setpoint_scenario,
        opf_control_selection=opf_control_selection,
        control_device_scope=control_device_scope,
    )


def _load_profile_snapshots_from_args(
    args: argparse.Namespace,
    grid_case: Stagg5GridCase,
) -> tuple[ProfileSnapshot, ...] | None:
    """Load optional time-profile snapshots requested by CLI arguments or prompt."""

    selected_case = get_stagg5_grid_case(grid_case)
    profile_input = getattr(args, "profile_input", None)
    if profile_input is None:
        if not sys.stdin.isatty():
            return None
        selection = _prompt_time_profile_selection(selected_case)
        if selection is None:
            return None
        profile_input = selection.profile_input
        setattr(args, "profile_input", selection.profile_input)
        setattr(args, "profile_sheet", selection.profile_sheet)
        setattr(args, "profile_format", selection.profile_format)

    if selected_case.source != "acdcpf":
        raise SystemExit(
            "Time-profile input is currently supported only for native ACDCPF-backed "
            "benchmark cases. PyFlow-sourced cases need a separate PyFlow profile adapter."
        )

    profile_path = _resolve_existing_input_path(profile_input)
    try:
        snapshots = load_time_profile(
            profile_path,
            sheet_name=_profile_sheet_argument(getattr(args, "profile_sheet", None)),
            profile_format=getattr(args, "profile_format", "auto"),
        )
    except Exception as exc:
        raise SystemExit(f"Could not load time profile `{profile_path}`: {exc}") from exc
    if not snapshots:
        raise SystemExit(f"Time profile `{profile_path}` did not contain any snapshots.")
    return snapshots


def _prompt_time_profile_selection(
    grid_case: Stagg5GridCase | str | None = None,
) -> TimeProfileInputSelection | None:
    """Ask the user whether a snapshot time profile should be applied."""

    selected_case = get_stagg5_grid_case(grid_case)
    if selected_case.source != "acdcpf":
        print("")
        print("Time-profile selection")
        print(
            "Time profiles are currently available only for native ACDCPF benchmark "
            f"cases. `{selected_case.display_name}` is sourced from PyFlow, so this "
            "run will continue as a single snapshot."
        )
        return None

    menu_items = _time_profile_menu_items(selected_case)
    print("")
    print("Which time profile should be applied?")
    for index, item in enumerate(menu_items, start=1):
        print(f"  {index}. {item.label}")
        print(f"     {item.description}")

    while True:
        raw_choice = input("Choose time profile [1]: ").strip()
        if raw_choice == "":
            selected_index = 1
        elif raw_choice.isdigit():
            selected_index = int(raw_choice)
        else:
            print("Please enter the number of one listed profile option.")
            continue

        if 1 <= selected_index <= len(menu_items):
            break
        print(f"Please choose a number from 1 to {len(menu_items)}.")

    selected_item = menu_items[selected_index - 1]
    if selected_item.is_custom:
        return _prompt_custom_time_profile_input()
    if selected_item.profile_input is None:
        return None
    return TimeProfileInputSelection(profile_input=selected_item.profile_input)


def _time_profile_menu_items(selected_case: Stagg5GridCase) -> list[TimeProfileMenuItem]:
    """Return the safe built-in profile choices for the selected benchmark case."""

    items = [
        TimeProfileMenuItem(
            label="No time profile (single snapshot)",
            description="Run the selected grid once with its current steady-state inputs.",
        )
    ]
    if selected_case.key == STAGG5_ORIGINAL:
        items.append(
            TimeProfileMenuItem(
                label="Original Stagg5 3-step AC load profile",
                description="Scales the original AC loads to 70%, 85%, and 100%.",
                profile_input=ORIGINAL_STAGG5_LOAD_PROFILE,
            )
        )
        items.append(
            TimeProfileMenuItem(
                label="Original Stagg5 24-hour AC load profile",
                description=(
                    "Applies representative SimBench-style hourly AC load multipliers "
                    "for one day."
                ),
                profile_input=STAGG5_ONE_DAY_AC_LOAD_PROFILE,
            )
        )
    elif selected_case.key == STAGG5_HYBRID_DCDC:
        items.append(
            TimeProfileMenuItem(
                label="Hybrid DCDC 3-step load/PV profile",
                description="Scales AC loads and sets the DC PV source to 10, 20, and 30 MW.",
                profile_input=HYBRID_DCDC_LOAD_PV_PROFILE,
            )
        )
        items.append(
            TimeProfileMenuItem(
                label="Hybrid DCDC 24-hour load/PV profile",
                description=(
                    "Applies one-day AC load multipliers and a daytime DC PV generation curve."
                ),
                profile_input=HYBRID_DCDC_ONE_DAY_LOAD_PV_PROFILE,
            )
        )
    items.append(
        TimeProfileMenuItem(
            label="Custom CSV/XLSX profile path",
            description="Use your own long-format profile file for this ACDCPF case.",
            is_custom=True,
        )
    )
    return items


def _prompt_custom_time_profile_input() -> TimeProfileInputSelection | None:
    """Ask for a user-provided profile file and optional XLSX parsing details."""

    while True:
        raw_path = input("Profile CSV/XLSX path [blank = no profile]: ").strip().strip('"')
        if raw_path == "":
            return None
        candidate_path = Path(raw_path)
        resolved_path = _resolve_existing_input_path(candidate_path)
        if resolved_path.exists():
            break
        print(f"Profile file not found: {resolved_path}")

    raw_sheet = input("XLSX sheet name/index [first sheet]: ").strip()
    profile_format = _prompt_profile_format()
    return TimeProfileInputSelection(
        profile_input=candidate_path,
        profile_sheet=_profile_sheet_argument(raw_sheet),
        profile_format=profile_format,
    )


def _prompt_profile_format() -> str:
    """Ask for the profile file format while preserving the normal auto default."""

    while True:
        raw_format = input("Profile format auto/csv/xlsx [auto]: ").strip().lower()
        profile_format = raw_format or "auto"
        if profile_format in PROFILE_INPUT_FORMATS:
            return profile_format
        print("Please enter auto, csv, or xlsx.")


def _profile_sheet_argument(value: Any) -> str | int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return int(text) if text.isdigit() else text


def _resolve_existing_input_path(path: Path) -> Path:
    """Resolve user input files from cwd first, then from the project package root."""

    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    cwd_path = Path.cwd() / expanded
    if cwd_path.exists():
        return cwd_path
    return _resolve_project_path(expanded)


def _run_time_profile_benchmark(
    args: argparse.Namespace,
    *,
    profile_snapshots: tuple[ProfileSnapshot, ...],
    grid_case: Stagg5GridCase,
    vsc_setpoint_scenario: VSCSetpointScenario,
    opf_control_selection: OPFControlSelection,
    control_device_scope: str,
    control_margin_percent: float | None,
) -> None:
    """Run profiled PF snapshots and Tool1 (acdcopf) with linked storage SOC when present."""

    generated_at = datetime.now().isoformat(timespec="seconds")
    time_points: list[BenchmarkTimePoint] = []
    for snapshot in profile_snapshots:
        runs: list[BenchmarkRun] = []
        acdcpf_pf_run, acdcpf_pf_result = _run_acdcpf_pf(
            vsc_setpoint_scenario,
            grid_case,
            profile_snapshot=snapshot,
        )
        runs.append(acdcpf_pf_run)

        matacdc_validation = {
            "available": False,
            "message": "MATACDC reference checks are skipped for time-profile snapshot runs.",
        }
        if (
            acdcpf_pf_result is not None
            and snapshot.time_index == 0
            and vsc_setpoint_scenario.name == VSC_SETPOINT_SCENARIO_ORIGINAL
            and grid_case.supports_matacdc_reference
        ):
            matacdc_validation = {
                "available": False,
                "message": (
                    "MATACDC reference checks are skipped for profile runs because the "
                    "profile may intentionally change the published base-case injections."
                ),
            }

        time_points.append(
            BenchmarkTimePoint(
                time_index=snapshot.time_index,
                timestamp=snapshot.timestamp,
                runs=runs,
                matacdc_validation=matacdc_validation,
            )
        )

    if not args.skip_opf:
        opf_runs_by_time = _run_profiled_tool1_pyomo_ipopt(
            vsc_setpoint_scenario,
            grid_case,
            profile_snapshots=profile_snapshots,
            opf_control_selection=opf_control_selection,
            control_device_scope=control_device_scope,
            control_margin_percent=control_margin_percent,
        )
        for time_point in time_points:
            opf_run, validation_run = opf_runs_by_time.get(
                time_point.time_index,
                (
                    BenchmarkRun(
                        label=TOOL1_OBJECTIVE_LABEL,
                        success=False,
                        message="Tool1 (acdcopf) profile result is missing for this timestamp.",
                    ),
                    None,
                ),
            )
            time_point.runs.append(opf_run)
            if validation_run is not None:
                time_point.runs.append(validation_run)

    if not args.skip_pyflow:
        for time_point in time_points:
            time_point.runs.extend(_pyflow_profile_unavailable_runs(grid_case))

    markdown = _build_profiled_markdown_report(
        time_points,
        generated_at=generated_at,
        vsc_setpoint_scenario=vsc_setpoint_scenario,
        grid_case=grid_case,
        control_margin_percent=control_margin_percent,
        opf_control_selection=opf_control_selection,
        control_device_scope=control_device_scope,
        profile_input=getattr(args, "profile_input", None),
    )

    csv_paths: list[Path] = []
    xlsx_paths: list[Path] = []
    html_path = None
    if not args.no_csv_output:
        csv_output_paths = _csv_result_output_paths_from_args(args)
        try:
            csv_paths = _export_profiled_split_result_csv_reports(
                time_points,
                csv_output_paths,
                generated_at=generated_at,
                vsc_setpoint_scenario=vsc_setpoint_scenario,
                grid_case=grid_case,
                control_margin_percent=control_margin_percent,
                opf_control_selection=opf_control_selection,
                control_device_scope=control_device_scope,
            )
        except PermissionError as exc:
            raise SystemExit(_csv_permission_error_message(csv_output_paths[0])) from exc
    if not args.no_xlsx_output:
        xlsx_output_paths = _xlsx_result_output_paths_from_args(args)
        try:
            xlsx_paths = _export_profiled_split_result_xlsx_reports(
                time_points,
                xlsx_output_paths,
                generated_at=generated_at,
                vsc_setpoint_scenario=vsc_setpoint_scenario,
                grid_case=grid_case,
                control_margin_percent=control_margin_percent,
                opf_control_selection=opf_control_selection,
                control_device_scope=control_device_scope,
            )
        except PermissionError as exc:
            raise SystemExit(_xlsx_permission_error_message(xlsx_output_paths[0])) from exc
    if not args.no_html_output:
        html_output_path = _html_output_path_from_args(args)
        try:
            html_path = _export_profiled_html_grid_view(
                time_points,
                html_output_path,
                generated_at=generated_at,
                vsc_setpoint_scenario=vsc_setpoint_scenario,
                grid_case=grid_case,
                control_margin_percent=control_margin_percent,
                opf_control_selection=opf_control_selection,
                control_device_scope=control_device_scope,
            )
        except PermissionError as exc:
            raise SystemExit(_html_permission_error_message(html_output_path)) from exc
        if _should_open_html_report(args):
            _open_html_report(html_path)

    export_mode = _select_export_mode(args)
    _export_profiled_report(
        markdown,
        time_points,
        args.markdown_output or DEFAULT_MARKDOWN_REPORT,
        export_mode,
        csv_path=csv_paths,
        xlsx_path=xlsx_paths,
        html_path=html_path,
        vsc_setpoint_scenario=vsc_setpoint_scenario,
        opf_control_selection=opf_control_selection,
        control_device_scope=control_device_scope,
    )


def _configure_warning_filters() -> None:
    """Keep the console benchmark focused on the comparison report."""

    warnings.filterwarnings(
        "ignore",
        message="The behavior of DataFrame concatenation with empty or all-NA entries is deprecated.*",
        category=FutureWarning,
        module=r"acdcpf\.create\.ac",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ACDCPF/PyFlow PF, Tool1 (acdcopf), and PyFlow OPF benchmark comparisons."
    )
    parser.add_argument(
        "--grid-case",
        default=None,
        help=(
            "Benchmark grid case to load: 'original' for the validated Stagg5 MTDC case, "
            "'hybrid_dcdc' for Stagg5 with DCDC-connected DC PV and battery resources, "
            "'two_area_stagg5_dcdc' for the synthetic transformer/DCDC case, "
            "or 'ieee39_acdc' for the normalized PyFlow IEEE 39 AC/DC case."
        ),
    )
    parser.add_argument(
        "--skip-pyflow",
        action="store_true",
        help="Skip PyFlow PF/OPF reference runs.",
    )
    parser.add_argument(
        "--skip-opf",
        dest="skip_opf",
        action="store_true",
        help="Skip the Tool1 (acdcopf) Pyomo/IPOPT run.",
    )
    parser.add_argument(
        "--skip-our-opf",
        dest="skip_opf",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--vsc-setpoint-scenario",
        choices=VSC_SETPOINT_SCENARIOS,
        default=None,
        help=(
            "VSC starting setpoints to use before PF/OPF. Use 'perturbed' "
            "to make the optimizer's movement more visible."
        ),
    )
    parser.add_argument(
        "--vsc-setpoint",
        action="append",
        default=[],
        type=_parse_custom_vsc_setpoint,
        metavar="VSC:P_MW:Q_MVAR",
        help=(
            "Override one starting VSC setpoint in report/ACDCPF convention, "
            "for example '--vsc-setpoint 0:90:50'. May be repeated. "
            "The index must exist in the selected grid case."
        ),
    )
    parser.add_argument(
        "--control-margin-percent",
        type=_parse_control_margin_percent,
        default=None,
        help=(
            "Limit selected OPF controls to the PF starting value plus/minus this "
            "percentage of the element apparent rating. Example: 10 means +/-10%% "
            "of S_nom. Omit for the full technical control range."
        ),
    )
    parser.add_argument(
        "--opf-controls",
        default=None,
        help=(
            "Tool1 (acdcopf) controls to enable. Use 'benchmark' for the case default, "
            "'none', 'all', or a comma-separated list from: "
            f"{', '.join(OPF_CONTROL_TOKENS)}."
        ),
    )
    parser.add_argument(
        "--control-device-scope",
        choices=CONTROL_DEVICE_SCOPES,
        default=CONTROL_DEVICE_SCOPE_BENCHMARK,
        help=(
            "Element scope for selected controls. 'benchmark' uses the case-defined "
            "controlled elements; 'all' uses every eligible element of that type."
        ),
    )
    parser.add_argument(
        "--profile-input",
        type=Path,
        default=None,
        help=(
            "Optional long-format CSV/XLSX time profile. Each time_index is solved "
            "as an independent PF/OPF snapshot."
        ),
    )
    parser.add_argument(
        "--profile-sheet",
        default=None,
        help="Worksheet name or zero-based index for XLSX profile input. Defaults to the first sheet.",
    )
    parser.add_argument(
        "--profile-format",
        choices=PROFILE_INPUT_FORMATS,
        default="auto",
        help="Profile input format. Default 'auto' infers from the file extension.",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=None,
        help=(
            "Path for the generated Markdown report. Defaults to "
            f"{DEFAULT_MARKDOWN_REPORT}."
        ),
    )
    parser.add_argument(
        "--no-markdown-output",
        action="store_true",
        help="Do not write the Markdown report file.",
    )
    parser.add_argument(
        "--print-markdown",
        action="store_true",
        help="Print the full Markdown report to the terminal instead of a short summary.",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=None,
        help=(
            "Base path for generated PF/OPF CSV result exports. If the path is "
            "benchmark.csv, the script writes benchmark_pf.csv and benchmark_opf.csv. "
            "Defaults to latest_stagg5_pf_results.csv and latest_stagg5_opf_results.csv."
        ),
    )
    parser.add_argument(
        "--csv-output-dir",
        type=Path,
        default=None,
        help=(
            "Directory for generated PF/OPF CSV result exports. Ignored when "
            "--csv-output is used."
        ),
    )
    parser.add_argument(
        "--no-csv-output",
        action="store_true",
        help="Do not write CSV exports.",
    )
    parser.add_argument(
        "--xlsx-output",
        type=Path,
        default=None,
        help=(
            "Base path for generated PF/OPF Excel result workbooks. If the path is "
            "benchmark.xlsx, the script writes benchmark_pf.xlsx and benchmark_opf.xlsx. "
            "Defaults to latest_stagg5_pf_results.xlsx and latest_stagg5_opf_results.xlsx."
        ),
    )
    parser.add_argument(
        "--xlsx-output-dir",
        type=Path,
        default=None,
        help=(
            "Directory for generated PF/OPF Excel result workbooks. Ignored when "
            "--xlsx-output is used."
        ),
    )
    parser.add_argument(
        "--no-xlsx-output",
        action="store_true",
        help="Do not write the Excel workbook export.",
    )
    parser.add_argument(
        "--html-output",
        type=Path,
        default=None,
        help=(
            "Path for the generated interactive HTML grid view. Defaults to "
            f"{DEFAULT_HTML_REPORT}."
        ),
    )
    parser.add_argument(
        "--html-output-dir",
        type=Path,
        default=None,
        help=(
            "Directory for the generated interactive HTML grid view. The filename is "
            "latest_stagg5_grid_view.html. Ignored when --html-output is used."
        ),
    )
    parser.add_argument(
        "--no-html-output",
        action="store_true",
        help="Do not write the interactive HTML grid view.",
    )
    parser.add_argument(
        "--open-html",
        action="store_true",
        help=(
            "Open the generated HTML grid view in the default browser after the run. "
            "This is now the default when HTML export is enabled."
        ),
    )
    parser.add_argument(
        "--no-open-html",
        action="store_true",
        help="Write the HTML grid view but do not open it in the browser.",
    )
    parser.add_argument(
        "--include-pyflow-export",
        action="store_true",
        help=(
            "Include PyFlow PF/OPF reference columns in CSV/XLSX exports. "
            "By default, spreadsheet exports focus on Tool1 (acdcopf) results."
        ),
    )
    parser.add_argument(
        "--detailed-export",
        action="store_true",
        help=(
            "Include detailed diagnostic sheets in CSV/XLSX exports. By default, "
            "exports contain only metadata, PF output, and OPF main values."
        ),
    )
    return parser.parse_args()


def _select_grid_case(args: argparse.Namespace) -> Stagg5GridCase:
    """Choose the Stagg5 topology used by the benchmark."""

    if getattr(args, "grid_case", None) is not None:
        return get_stagg5_grid_case(args.grid_case)
    if sys.stdin.isatty():
        return _prompt_grid_case()
    return get_stagg5_grid_case(STAGG5_ORIGINAL)


def _prompt_grid_case() -> Stagg5GridCase:
    print("")
    print("Which benchmark grid should be loaded?")
    print("  1. Original Stagg5 MTDC benchmark")
    print("  2. Hybrid Stagg5: DC PV + battery through DCDC converters")
    print("  3. PyFlow IEEE 39 AC/DC benchmark")
    print("  4. Two-area Stagg5: transformers + DCDC coupling")
    choice = input("Choose grid case [1]: ").strip()
    if choice == "2":
        return get_stagg5_grid_case(STAGG5_HYBRID_DCDC)
    if choice == "3":
        return get_stagg5_grid_case(PYFLOW_IEEE39_ACDC)
    if choice == "4":
        return get_stagg5_grid_case(STAGG5_TWO_AREA_TRANSFORMER_DCDC)
    return get_stagg5_grid_case(STAGG5_ORIGINAL)


def _select_vsc_setpoint_scenario(
    args: argparse.Namespace,
    grid_case: Stagg5GridCase | str | None = None,
) -> VSCSetpointScenario:
    """Choose the starting VSC setpoints for every benchmark run."""

    selected_case = get_stagg5_grid_case(grid_case)
    custom_specs = getattr(args, "vsc_setpoint", None) or []
    if custom_specs:
        base_name = getattr(args, "vsc_setpoint_scenario", None) or VSC_SETPOINT_SCENARIO_ORIGINAL
        return _custom_vsc_setpoint_scenario(base_name, custom_specs, selected_case)
    if getattr(args, "vsc_setpoint_scenario", None) is not None:
        return _predefined_vsc_setpoint_scenario(str(args.vsc_setpoint_scenario), selected_case)
    if sys.stdin.isatty():
        return _prompt_vsc_setpoint_scenario(selected_case)
    return _predefined_vsc_setpoint_scenario(VSC_SETPOINT_SCENARIO_ORIGINAL, selected_case)


def _prompt_vsc_setpoint_scenario(
    grid_case: Stagg5GridCase | str | None = None,
) -> VSCSetpointScenario:
    selected_case = get_stagg5_grid_case(grid_case)
    print("")
    print("Which VSC starting setpoints should be used?")
    if _case_has_predefined_stagg_vsc_setpoints(selected_case):
        print("  1. Original Stagg5 setpoints (benchmark/default)")
        print("  2. Perturbed setpoints, same for ACDCPF and PyFlow, to show larger OPF movement")
        print("  3. Custom setpoints typed now")
    else:
        print("  1. Keep built-in case setpoints (benchmark/default)")
        print("  2. Custom setpoints typed now")
    choice = input("Choose VSC setpoint scenario [1]: ").strip()
    if _case_has_predefined_stagg_vsc_setpoints(selected_case):
        if choice == "2":
            return _predefined_vsc_setpoint_scenario(VSC_SETPOINT_SCENARIO_PERTURBED, selected_case)
        if choice == "3":
            return _prompt_custom_vsc_setpoints(selected_case)
    elif choice == "2":
        return _prompt_custom_vsc_setpoints(selected_case)
    return _predefined_vsc_setpoint_scenario(VSC_SETPOINT_SCENARIO_ORIGINAL, selected_case)


def _select_control_margin_percent(args: argparse.Namespace) -> float | None:
    """Choose how far selected OPF controls may move from the PF starting value."""

    if getattr(args, "control_margin_percent", None) is not None:
        return args.control_margin_percent
    if sys.stdin.isatty():
        return _prompt_control_margin_percent()
    return None


def _prompt_control_margin_percent() -> float | None:
    print("")
    print("How far may selected OPF controls move from the PF starting value?")
    print("Blank input keeps the full technical control range.")
    print("Example: 10 means +/-10% of each controlled element apparent rating.")
    while True:
        raw_value = input("Control margin percent [full range]: ").strip()
        if raw_value == "":
            return None
        try:
            return _parse_control_margin_percent(raw_value)
        except argparse.ArgumentTypeError as exc:
            print(str(exc))


def _prompt_custom_vsc_setpoints(
    grid_case: Stagg5GridCase | str | None = None,
) -> VSCSetpointScenario:
    base = _predefined_vsc_setpoint_scenario(VSC_SETPOINT_SCENARIO_ORIGINAL, grid_case)
    setpoints = _copy_vsc_setpoints(base.setpoints)

    print("")
    print("Enter starting VSC setpoints in the same convention used in the report.")
    if not setpoints:
        print("No built-in VSC overrides are defined for this case; use --vsc-setpoint for CLI runs.")
    print("Blank input keeps the value shown in brackets.")
    for idx in sorted(setpoints):
        values = setpoints[idx]
        p_default = values["p_ac_mw"]
        q_default = values["q_ac_mvar"]
        values["p_ac_mw"] = _prompt_float(f"VSC {idx} p_ac_mw [{p_default:g}]: ", p_default)
        values["q_ac_mvar"] = _prompt_float(f"VSC {idx} q_ac_mvar [{q_default:g}]: ", q_default)

    return VSCSetpointScenario(
        name=VSC_SETPOINT_SCENARIO_CUSTOM,
        description=(
            "Custom user-provided VSC starting setpoints. Blank entries kept the "
            "case default values shown above."
        ),
        setpoints=setpoints,
    )


def _select_opf_control_selection(
    args: argparse.Namespace,
    grid_case: Stagg5GridCase | str | None = None,
) -> OPFControlSelection:
    """Choose which Tool1 (acdcopf) controls are enabled for this run."""

    raw_controls = getattr(args, "opf_controls", None)
    selected_case = get_stagg5_grid_case(grid_case)
    if raw_controls is not None:
        try:
            return _parse_opf_controls(raw_controls, selected_case)
        except argparse.ArgumentTypeError as exc:
            raise SystemExit(f"error: argument --opf-controls: {exc}") from exc
    if sys.stdin.isatty():
        return _prompt_opf_control_selection(selected_case)
    return _benchmark_opf_control_selection(selected_case)


def _select_control_device_scope(args: argparse.Namespace) -> str:
    scope = getattr(args, "control_device_scope", CONTROL_DEVICE_SCOPE_BENCHMARK)
    if scope not in CONTROL_DEVICE_SCOPES:
        raise SystemExit(
            "error: argument --control-device-scope: "
            f"choose from {', '.join(CONTROL_DEVICE_SCOPES)}"
        )
    return str(scope)


def _prompt_opf_control_selection(
    grid_case: Stagg5GridCase | str | None = None,
) -> OPFControlSelection:
    selected_case = get_stagg5_grid_case(grid_case)
    print("")
    print("Which Tool1 (acdcopf) controls should be enabled?")
    print("  1. Benchmark default (case-defined VSC P/Q, plus DCDC ratio where this case enables it)")
    print("  2. VSC P/Q only")
    print("  3. VSC P/Q + DCDC voltage ratio")
    print("  4. All listed controls")
    print("  5. Custom control selection")
    choice = input("Choose OPF controls [1]: ").strip()
    if choice == "2":
        return OPFControlSelection(vsc_p=True, vsc_q=True)
    if choice == "3":
        return OPFControlSelection(vsc_p=True, vsc_q=True, dcdc_ratio=True)
    if choice == "4":
        return _all_opf_control_selection()
    if choice == "5":
        return _prompt_custom_opf_control_selection()
    return _benchmark_opf_control_selection(selected_case)


def _prompt_custom_opf_control_selection() -> OPFControlSelection:
    values = {}
    print("")
    print("Enable each Tool1 (acdcopf) control? Blank keeps the value in brackets.")
    for token in OPF_CONTROL_TOKENS:
        values[token] = _prompt_yes_no(
            f"{OPF_CONTROL_DISPLAY_NAMES[token]} [no]: ",
            default=False,
        )
    return OPFControlSelection(**values)


def _prompt_yes_no(prompt: str, *, default: bool) -> bool:
    while True:
        raw_value = input(prompt).strip().lower()
        if raw_value == "":
            return bool(default)
        if raw_value in {"y", "yes", "true", "1"}:
            return True
        if raw_value in {"n", "no", "false", "0"}:
            return False
        print("Please enter yes or no.")


def _prompt_float(prompt: str, default: float) -> float:
    while True:
        raw_value = input(prompt).strip()
        if raw_value == "":
            return float(default)
        try:
            return float(raw_value)
        except ValueError:
            print("Please enter a number, for example 90 or -35.5.")


def _parse_custom_vsc_setpoint(raw_value: Any) -> tuple[int, dict[str, float]]:
    """Parse one CLI VSC override as VSC:P_MW:Q_MVAR."""

    if isinstance(raw_value, tuple):
        idx, values = raw_value
        idx = int(idx)
        if idx < 0:
            raise argparse.ArgumentTypeError("VSC index must be nonnegative.")
        return int(idx), {
            "p_ac_mw": float(values["p_ac_mw"]),
            "q_ac_mvar": float(values["q_ac_mvar"]),
        }

    value = str(raw_value).strip()
    normalized = value.replace("=", ":").replace(",", ":")
    parts = [part.strip() for part in normalized.split(":")]
    if len(parts) != 3 or any(part == "" for part in parts):
        raise argparse.ArgumentTypeError(
            "Use VSC:P_MW:Q_MVAR, for example 0:90:50 or 2:-80:40."
        )

    index_text = parts[0].lower()
    if index_text.startswith("vsc"):
        index_text = index_text[3:].strip()

    try:
        idx = int(index_text)
        p_ac_mw = float(parts[1])
        q_ac_mvar = float(parts[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "VSC setpoint must contain an integer VSC index and numeric P/Q values."
        ) from exc

    if idx < 0:
        raise argparse.ArgumentTypeError("VSC index must be nonnegative.")

    return idx, {"p_ac_mw": p_ac_mw, "q_ac_mvar": q_ac_mvar}


def _parse_control_margin_percent(raw_value: Any) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("Control margin percent must be a number.") from exc
    if value < 0.0:
        raise argparse.ArgumentTypeError("Control margin percent must be nonnegative.")
    return value


def _parse_opf_controls(
    raw_value: Any,
    grid_case: Stagg5GridCase | str | None = None,
) -> OPFControlSelection:
    """Parse ``--opf-controls`` into a concrete Tool1 (acdcopf) control selection."""

    if isinstance(raw_value, OPFControlSelection):
        return raw_value

    value = str(raw_value).strip().lower().replace("-", "_")
    if value in {"", OPF_CONTROL_PRESET_BENCHMARK}:
        return _benchmark_opf_control_selection(grid_case)
    if value == OPF_CONTROL_PRESET_NONE:
        return OPFControlSelection()
    if value == OPF_CONTROL_PRESET_ALL:
        return _all_opf_control_selection()

    tokens = tuple(
        token.strip().lower().replace("-", "_")
        for token in value.replace(";", ",").split(",")
        if token.strip()
    )
    if not tokens:
        return _benchmark_opf_control_selection(grid_case)
    invalid_tokens = sorted(set(tokens) - set(OPF_CONTROL_TOKENS))
    if invalid_tokens:
        raise argparse.ArgumentTypeError(
            "Unknown OPF control token(s): "
            f"{', '.join(invalid_tokens)}. Valid tokens: {', '.join(OPF_CONTROL_TOKENS)}."
        )

    selected = set(tokens)
    return OPFControlSelection(
        vsc_p="vsc_p" in selected,
        vsc_q="vsc_q" in selected,
        dcdc_ratio="dcdc_ratio" in selected,
        ac_gen_p="ac_gen_p" in selected,
        ac_gen_q="ac_gen_q" in selected,
        dc_gen_curtailment="dc_gen_curtailment" in selected,
    )


def _benchmark_opf_control_selection(
    grid_case: Stagg5GridCase | str | None = None,
) -> OPFControlSelection:
    selected_case = get_stagg5_grid_case(grid_case)
    return OPFControlSelection(
        vsc_p=bool(selected_case.converter_active_power_indices) if selected_case.network_factory else True,
        vsc_q=bool(selected_case.converter_reactive_power_indices) if selected_case.network_factory else True,
        dcdc_ratio=bool(selected_case.optimize_dcdc_voltage_ratio),
    )


def _all_opf_control_selection() -> OPFControlSelection:
    return OPFControlSelection(
        vsc_p=True,
        vsc_q=True,
        dcdc_ratio=True,
        ac_gen_p=True,
        ac_gen_q=True,
        dc_gen_curtailment=True,
    )


def _opf_control_selection_description(selection: OPFControlSelection) -> str:
    names = [
        OPF_CONTROL_DISPLAY_NAMES[token]
        for token in selection.enabled_tokens()
    ]
    if not names:
        return "No OPF controls enabled"
    return ", ".join(names)


def _opf_disabled_control_description(selection: OPFControlSelection) -> str:
    names = [
        OPF_CONTROL_DISPLAY_NAMES[token]
        for token in OPF_CONTROL_TOKENS
        if not bool(getattr(selection, token))
    ]
    if not names:
        return "No listed controls are fixed by the control selector"
    return ", ".join(names)


def _write_markdown_report(markdown: str, output_path: Path) -> Path:
    """Write the benchmark report to a Markdown file and return its path."""

    resolved_path = _resolve_project_path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(markdown, encoding="utf-8")
    return resolved_path


def _select_export_mode(args: argparse.Namespace) -> str:
    """Choose how to present the finished report."""

    if args.print_markdown:
        return EXPORT_PRINT_MARKDOWN
    if args.no_markdown_output:
        return EXPORT_SUMMARY_ONLY
    if args.markdown_output is not None:
        return EXPORT_MARKDOWN_SUMMARY
    if sys.stdin.isatty():
        return _prompt_export_mode()
    return EXPORT_MARKDOWN_SUMMARY


def _should_open_html_report(args: argparse.Namespace) -> bool:
    """Return whether the generated HTML view should be opened after export."""

    return not getattr(args, "no_open_html", False)


def _prompt_export_mode() -> str:
    print("")
    print("How should the benchmark results be exported?")
    print("  1. Markdown file + small summary here (recommended)")
    print("  2. Markdown file only")
    print("  3. Small summary here only")
    print("  4. Full Markdown printed here")
    print("Split PF/OPF CSV, Excel, and HTML grid reports are written unless disabled by CLI flags.")
    choice = input("Choose export format [1]: ").strip()
    return {
        "": EXPORT_MARKDOWN_SUMMARY,
        "1": EXPORT_MARKDOWN_SUMMARY,
        "2": EXPORT_MARKDOWN_ONLY,
        "3": EXPORT_SUMMARY_ONLY,
        "4": EXPORT_PRINT_MARKDOWN,
    }.get(choice, EXPORT_MARKDOWN_SUMMARY)


def _export_report(
    markdown: str,
    runs: list[BenchmarkRun],
    output_path: Path,
    export_mode: str,
    *,
    csv_path: Path | Iterable[Path] | None = None,
    xlsx_path: Path | Iterable[Path] | None = None,
    html_path: Path | None = None,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> None:
    markdown_path = None
    if export_mode in {EXPORT_MARKDOWN_SUMMARY, EXPORT_MARKDOWN_ONLY}:
        markdown_path = _write_markdown_report(markdown, output_path)

    if export_mode == EXPORT_PRINT_MARKDOWN:
        print(markdown)
        for message in _csv_export_messages(csv_path):
            print(message)
        for message in _xlsx_export_messages(xlsx_path):
            print(message)
        if html_path is not None:
            print(_html_export_message(html_path))
    elif export_mode == EXPORT_SUMMARY_ONLY:
        print(
            _build_console_summary(
                runs,
                None,
                csv_path=csv_path,
                xlsx_path=xlsx_path,
                html_path=html_path,
                vsc_setpoint_scenario=vsc_setpoint_scenario,
                opf_control_selection=opf_control_selection,
                control_device_scope=control_device_scope,
            )
        )
    elif export_mode == EXPORT_MARKDOWN_ONLY:
        lines = [f"Markdown report written to: `{markdown_path}`"]
        lines.extend(_csv_export_messages(csv_path))
        lines.extend(_xlsx_export_messages(xlsx_path))
        if html_path is not None:
            lines.append(_html_export_message(html_path))
        print("\n".join(lines))
    else:
        print(
            _build_console_summary(
                runs,
                markdown_path,
                csv_path=csv_path,
                xlsx_path=xlsx_path,
                html_path=html_path,
                vsc_setpoint_scenario=vsc_setpoint_scenario,
                opf_control_selection=opf_control_selection,
                control_device_scope=control_device_scope,
            )
        )


def _build_console_summary(
    runs: list[BenchmarkRun],
    markdown_path: Path | None,
    *,
    csv_path: Path | Iterable[Path] | None = None,
    xlsx_path: Path | Iterable[Path] | None = None,
    html_path: Path | None = None,
    vsc_setpoint_scenario: VSCSetpointScenario | str | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> str:
    """Return a compact terminal summary for the generated benchmark report."""

    acdcpf_pf = _run_by_label(runs, "ACDCPF PF")
    tool1_objective = _run_by_label(runs, TOOL1_OBJECTIVE_LABEL)
    pyflow_pf = _run_by_label(runs, "PyFlow PF")
    pyflow_opf = _run_by_label(runs, "PyFlow OPF VSC-only")

    lines = [
        "# PF/OPF Benchmark Comparison",
        "",
    ]
    if vsc_setpoint_scenario is not None:
        lines.extend(
            [
                "VSC starting setpoint scenario: "
                f"`{_vsc_setpoint_scenario_name(vsc_setpoint_scenario)}`",
                "",
            ]
        )
    if opf_control_selection is not None:
        lines.extend(
            [
                "Enabled OPF controls: "
                f"{_opf_control_selection_description(opf_control_selection)} "
                f"(device scope: `{control_device_scope}`)",
                "",
            ]
        )
    if markdown_path is not None:
        lines.extend(
            [
                f"Markdown report written to: `{markdown_path}`",
                "",
            ]
        )
    else:
        lines.extend(["Markdown report writing disabled.", ""])
    for message in _csv_export_messages(csv_path):
        lines.extend([message, ""])
    for message in _xlsx_export_messages(xlsx_path):
        lines.extend([message, ""])
    if html_path is not None:
        lines.extend([_html_export_message(html_path), ""])

    lines.extend(
        [
            "## Key Loss Summary",
            "",
            *_markdown_table(
                ["Run", "Total active loss MW", "Change vs PF MW", "Change vs PF %"],
                [
                    ["ACDCPF PF", _format_number(_total_loss(acdcpf_pf)), "baseline", "baseline"],
                    [
                        TOOL1_OBJECTIVE_LABEL,
                        _format_number(_total_loss(tool1_objective)),
                        _format_number(_difference(_total_loss(tool1_objective), _total_loss(acdcpf_pf))),
                        _format_percent_change(
                            _total_loss(tool1_objective),
                            _total_loss(acdcpf_pf),
                        ),
                    ],
                    ["PyFlow PF", _format_number(_total_loss(pyflow_pf)), "baseline", "baseline"],
                    [
                        "PyFlow OPF VSC-only",
                        _format_number(_total_loss(pyflow_opf)),
                        _format_number(_difference(_total_loss(pyflow_opf), _total_loss(pyflow_pf))),
                        _format_percent_change(_total_loss(pyflow_opf), _total_loss(pyflow_pf)),
                    ],
                ],
            ),
            "",
        ]
    )
    if tool1_objective is not None:
        lines.extend(_physics_markdown_section([tool1_objective]))
    if markdown_path is not None:
        lines.append(
            "Open the Markdown file for full PF element tables, OPF setpoint changes, diagnostics, and reference checks."
        )
    if html_path is not None:
        lines.append(
            "Open the HTML grid view for an interactive physical schematic with PF/OPF hover details."
        )
    return "\n".join(lines) + "\n"


def _export_profiled_report(
    markdown: str,
    time_points: list[BenchmarkTimePoint],
    output_path: Path,
    export_mode: str,
    *,
    csv_path: Path | Iterable[Path] | None = None,
    xlsx_path: Path | Iterable[Path] | None = None,
    html_path: Path | None = None,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> None:
    markdown_path = None
    if export_mode in {EXPORT_MARKDOWN_SUMMARY, EXPORT_MARKDOWN_ONLY}:
        markdown_path = _write_markdown_report(markdown, output_path)

    if export_mode == EXPORT_PRINT_MARKDOWN:
        print(markdown)
        for message in _csv_export_messages(csv_path):
            print(message)
        for message in _xlsx_export_messages(xlsx_path):
            print(message)
        if html_path is not None:
            print(_html_export_message(html_path))
    elif export_mode == EXPORT_SUMMARY_ONLY:
        print(
            _build_profiled_console_summary(
                time_points,
                None,
                csv_path=csv_path,
                xlsx_path=xlsx_path,
                html_path=html_path,
                vsc_setpoint_scenario=vsc_setpoint_scenario,
                opf_control_selection=opf_control_selection,
                control_device_scope=control_device_scope,
            )
        )
    elif export_mode == EXPORT_MARKDOWN_ONLY:
        lines = [f"Markdown report written to: `{markdown_path}`"]
        lines.extend(_csv_export_messages(csv_path))
        lines.extend(_xlsx_export_messages(xlsx_path))
        if html_path is not None:
            lines.append(_html_export_message(html_path))
        print("\n".join(lines))
    else:
        print(
            _build_profiled_console_summary(
                time_points,
                markdown_path,
                csv_path=csv_path,
                xlsx_path=xlsx_path,
                html_path=html_path,
                vsc_setpoint_scenario=vsc_setpoint_scenario,
                opf_control_selection=opf_control_selection,
                control_device_scope=control_device_scope,
            )
        )


def _build_profiled_console_summary(
    time_points: list[BenchmarkTimePoint],
    markdown_path: Path | None,
    *,
    csv_path: Path | Iterable[Path] | None = None,
    xlsx_path: Path | Iterable[Path] | None = None,
    html_path: Path | None = None,
    vsc_setpoint_scenario: VSCSetpointScenario | str | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> str:
    lines = [
        "# Tool1 (acdcopf) Snapshot Time-Series Benchmark",
        "",
    ]
    if vsc_setpoint_scenario is not None:
        lines.extend(
            [
                "VSC starting setpoint scenario: "
                f"`{_vsc_setpoint_scenario_name(vsc_setpoint_scenario)}`",
                "",
            ]
        )
    if opf_control_selection is not None:
        lines.extend(
            [
                "Enabled OPF controls: "
                f"{_opf_control_selection_description(opf_control_selection)} "
                f"(device scope: `{control_device_scope}`)",
                "",
            ]
        )
    if markdown_path is not None:
        lines.extend([f"Markdown report written to: `{markdown_path}`", ""])
    else:
        lines.extend(["Markdown report writing disabled.", ""])
    for message in _csv_export_messages(csv_path):
        lines.extend([message, ""])
    for message in _xlsx_export_messages(xlsx_path):
        lines.extend([message, ""])
    if html_path is not None:
        lines.extend([_html_export_message(html_path), ""])

    lines.extend(["## Snapshot Loss Summary", ""])
    lines.extend(_profiled_loss_summary_table(time_points))
    lines.extend(_physics_markdown_section([run for point in time_points for run in point.runs]))
    lines.extend(
        [
            "",
            "PyFlow reference is not comparable for profile runs unless the same "
            "timestamp-by-timestamp injections are mirrored in PyFlow.",
        ]
    )
    return "\n".join(lines) + "\n"


def _csv_output_path_from_args(args: argparse.Namespace) -> Path:
    """Return the single CSV report path requested by CLI options."""

    csv_output = getattr(args, "csv_output", None)
    if csv_output is not None:
        return csv_output

    csv_output_dir = getattr(args, "csv_output_dir", None)
    if csv_output_dir is not None:
        return csv_output_dir / DEFAULT_CSV_REPORT.name

    return DEFAULT_CSV_REPORT


def _csv_result_output_paths_from_args(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return PF and OPF CSV result paths requested by CLI options."""

    csv_output = getattr(args, "csv_output", None)
    if csv_output is not None:
        return _split_result_output_paths(csv_output)

    csv_output_dir = getattr(args, "csv_output_dir", None)
    if csv_output_dir is not None:
        return (
            csv_output_dir / DEFAULT_PF_CSV_REPORT.name,
            csv_output_dir / DEFAULT_OPF_CSV_REPORT.name,
        )

    return DEFAULT_PF_CSV_REPORT, DEFAULT_OPF_CSV_REPORT


def _xlsx_output_path_from_args(args: argparse.Namespace) -> Path:
    """Return the Excel workbook path requested by CLI options."""

    xlsx_output = getattr(args, "xlsx_output", None)
    if xlsx_output is not None:
        return xlsx_output

    xlsx_output_dir = getattr(args, "xlsx_output_dir", None)
    if xlsx_output_dir is not None:
        return xlsx_output_dir / DEFAULT_XLSX_REPORT.name

    return DEFAULT_XLSX_REPORT


def _xlsx_result_output_paths_from_args(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return PF and OPF Excel result paths requested by CLI options."""

    xlsx_output = getattr(args, "xlsx_output", None)
    if xlsx_output is not None:
        return _split_result_output_paths(xlsx_output)

    xlsx_output_dir = getattr(args, "xlsx_output_dir", None)
    if xlsx_output_dir is not None:
        return (
            xlsx_output_dir / DEFAULT_PF_XLSX_REPORT.name,
            xlsx_output_dir / DEFAULT_OPF_XLSX_REPORT.name,
        )

    return DEFAULT_PF_XLSX_REPORT, DEFAULT_OPF_XLSX_REPORT


def _split_result_output_paths(base_path: Path) -> tuple[Path, Path]:
    """Derive PF and OPF file names from one user-provided base path."""

    suffix = base_path.suffix
    stem = base_path.stem if suffix else base_path.name
    parent = base_path.parent
    if suffix:
        return parent / f"{stem}_pf{suffix}", parent / f"{stem}_opf{suffix}"
    return parent / f"{stem}_pf", parent / f"{stem}_opf"


def _html_output_path_from_args(args: argparse.Namespace) -> Path:
    """Return the interactive HTML grid-view path requested by CLI options."""

    html_output = getattr(args, "html_output", None)
    if html_output is not None:
        return html_output

    html_output_dir = getattr(args, "html_output_dir", None)
    if html_output_dir is not None:
        return html_output_dir / DEFAULT_HTML_REPORT.name

    return DEFAULT_HTML_REPORT


def _export_csv_report(
    runs: list[BenchmarkRun],
    output_path: Path,
    *,
    generated_at: str,
    matacdc_validation: dict[str, Any] | None,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
    include_pyflow_reference: bool = False,
    include_detailed_export: bool = False,
) -> Path:
    """Write one multi-section CSV report for Excel and tool-to-tool checks."""

    resolved_path = _resolve_project_path(output_path)
    sections = _csv_report_sections(
        runs,
        generated_at=generated_at,
        matacdc_validation=matacdc_validation,
        vsc_setpoint_scenario=vsc_setpoint_scenario,
        grid_case=grid_case,
        control_margin_percent=control_margin_percent,
        opf_control_selection=opf_control_selection,
        control_device_scope=control_device_scope,
        include_pyflow_reference=include_pyflow_reference,
        include_detailed_export=include_detailed_export,
    )
    _write_csv_sections(resolved_path, sections)
    return resolved_path


def _export_xlsx_report(
    runs: list[BenchmarkRun],
    output_path: Path,
    *,
    generated_at: str,
    matacdc_validation: dict[str, Any] | None,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
    include_pyflow_reference: bool = False,
    include_detailed_export: bool = False,
) -> Path:
    """Write one Excel workbook with one worksheet per report section."""

    resolved_path = _resolve_project_path(output_path)
    sections = _report_sections(
        runs,
        generated_at=generated_at,
        matacdc_validation=matacdc_validation,
        vsc_setpoint_scenario=vsc_setpoint_scenario,
        grid_case=grid_case,
        control_margin_percent=control_margin_percent,
        opf_control_selection=opf_control_selection,
        control_device_scope=control_device_scope,
        include_pyflow_reference=include_pyflow_reference,
        include_detailed_export=include_detailed_export,
    )
    _write_xlsx_workbook(resolved_path, sections, generated_at=generated_at)
    return resolved_path


def _export_split_result_csv_reports(
    runs: list[BenchmarkRun],
    output_paths: tuple[Path, Path],
    *,
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> list[Path]:
    """Write separate PF and OPF CSV result files with matching ``res_*`` sections."""

    written_paths: list[Path] = []
    for run_label, output_path, export_kind in _split_result_export_specs(output_paths):
        run = _run_by_label(runs, run_label)
        if run is None:
            continue
        resolved_path = _resolve_project_path(output_path)
        sections = _single_run_result_sections(
            run,
            generated_at=generated_at,
            vsc_setpoint_scenario=vsc_setpoint_scenario,
            grid_case=grid_case,
            control_margin_percent=control_margin_percent,
            opf_control_selection=opf_control_selection,
            control_device_scope=control_device_scope,
            export_kind=export_kind,
        )
        _write_csv_sections(resolved_path, sections)
        written_paths.append(resolved_path)
    return written_paths


def _export_profiled_split_result_csv_reports(
    time_points: list[BenchmarkTimePoint],
    output_paths: tuple[Path, Path],
    *,
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> list[Path]:
    """Write split PF/OPF CSV reports containing every profiled timestamp."""

    written_paths: list[Path] = []
    for run_label, output_path, export_kind in _split_result_export_specs(output_paths):
        sections = _profiled_single_run_result_sections(
            time_points,
            run_label,
            generated_at=generated_at,
            vsc_setpoint_scenario=vsc_setpoint_scenario,
            grid_case=grid_case,
            control_margin_percent=control_margin_percent,
            opf_control_selection=opf_control_selection,
            control_device_scope=control_device_scope,
            export_kind=export_kind,
        )
        if not sections:
            continue
        resolved_path = _resolve_project_path(output_path)
        _write_csv_sections(resolved_path, sections)
        written_paths.append(resolved_path)
    return written_paths


def _export_split_result_xlsx_reports(
    runs: list[BenchmarkRun],
    output_paths: tuple[Path, Path],
    *,
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> list[Path]:
    """Write separate PF and OPF Excel result workbooks with matching ``res_*`` sheets."""

    written_paths: list[Path] = []
    for run_label, output_path, export_kind in _split_result_export_specs(output_paths):
        run = _run_by_label(runs, run_label)
        if run is None:
            continue
        resolved_path = _resolve_project_path(output_path)
        sections = _single_run_result_sections(
            run,
            generated_at=generated_at,
            vsc_setpoint_scenario=vsc_setpoint_scenario,
            grid_case=grid_case,
            control_margin_percent=control_margin_percent,
            opf_control_selection=opf_control_selection,
            control_device_scope=control_device_scope,
            export_kind=export_kind,
        )
        _write_xlsx_workbook(resolved_path, sections, generated_at=generated_at)
        written_paths.append(resolved_path)
    return written_paths


def _export_profiled_split_result_xlsx_reports(
    time_points: list[BenchmarkTimePoint],
    output_paths: tuple[Path, Path],
    *,
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> list[Path]:
    """Write split PF/OPF XLSX reports containing every profiled timestamp."""

    written_paths: list[Path] = []
    for run_label, output_path, export_kind in _split_result_export_specs(output_paths):
        sections = _profiled_single_run_result_sections(
            time_points,
            run_label,
            generated_at=generated_at,
            vsc_setpoint_scenario=vsc_setpoint_scenario,
            grid_case=grid_case,
            control_margin_percent=control_margin_percent,
            opf_control_selection=opf_control_selection,
            control_device_scope=control_device_scope,
            export_kind=export_kind,
        )
        if not sections:
            continue
        resolved_path = _resolve_project_path(output_path)
        _write_xlsx_workbook(resolved_path, sections, generated_at=generated_at)
        written_paths.append(resolved_path)
    return written_paths


def _profiled_single_run_result_sections(
    time_points: list[BenchmarkTimePoint],
    run_label: str,
    *,
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None,
    export_kind: str,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> list[ReportSection]:
    section_sets: list[list[ReportSection]] = []
    for time_point in time_points:
        run = _run_by_label(time_point.runs, run_label)
        if run is None:
            continue
        section_sets.append(
            _single_run_result_sections(
                run,
                generated_at=generated_at,
                vsc_setpoint_scenario=vsc_setpoint_scenario,
                grid_case=grid_case,
                control_margin_percent=control_margin_percent,
                opf_control_selection=opf_control_selection,
                control_device_scope=control_device_scope,
                export_kind=export_kind,
                time_index=time_point.time_index,
                study_timestamp=time_point.timestamp,
                profile_mode=str(run.diagnostics.get("profile_mode", "independent snapshot")),
            )
        )
    return _merge_report_section_sets(section_sets)


def _merge_report_section_sets(section_sets: list[list[ReportSection]]) -> list[ReportSection]:
    """Merge same-named sections from multiple timestamps into one set of tables."""

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for sections in section_sets:
        for section in sections:
            if section.name not in merged:
                order.append(section.name)
                merged[section.name] = {
                    "description": section.description,
                    "fieldnames": list(section.fieldnames),
                    "rows": [],
                }
            merged[section.name]["rows"].extend(section.rows)
            for field in section.fieldnames:
                if field not in merged[section.name]["fieldnames"]:
                    merged[section.name]["fieldnames"].append(field)

    result: list[ReportSection] = []
    for section_name in order:
        item = merged[section_name]
        rows = item["rows"]
        preferred = item["fieldnames"]
        fieldnames = (
            _csv_fieldnames(rows, preferred_first=preferred)
            if rows
            else list(preferred)
        )
        result.append(
            ReportSection(
                section_name,
                item["description"],
                fieldnames,
                rows,
            )
        )
    return result


def _split_result_export_specs(
    output_paths: tuple[Path, Path],
) -> tuple[tuple[str, Path, str], tuple[str, Path, str]]:
    pf_path, opf_path = output_paths
    return (
        ("ACDCPF PF", pf_path, "pf"),
        (TOOL1_OBJECTIVE_LABEL, opf_path, "opf"),
    )


def _export_html_grid_view(
    runs: list[BenchmarkRun],
    output_path: Path,
    *,
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> Path:
    """Write an interactive HTML schematic for PF/OPF element inspection."""

    resolved_path = _resolve_project_path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    data = _grid_view_data(
        runs,
        generated_at=generated_at,
        vsc_setpoint_scenario=vsc_setpoint_scenario,
        grid_case=grid_case,
        control_margin_percent=control_margin_percent,
        opf_control_selection=opf_control_selection,
        control_device_scope=control_device_scope,
    )
    resolved_path.write_text(_grid_view_html(data), encoding="utf-8")
    return resolved_path


def _export_profiled_html_grid_view(
    time_points: list[BenchmarkTimePoint],
    output_path: Path,
    *,
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> Path:
    """Write an interactive HTML view with one selectable grid state per timestamp."""

    resolved_path = _resolve_project_path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    data = _profiled_grid_view_data(
        time_points,
        generated_at=generated_at,
        vsc_setpoint_scenario=vsc_setpoint_scenario,
        grid_case=grid_case,
        control_margin_percent=control_margin_percent,
        opf_control_selection=opf_control_selection,
        control_device_scope=control_device_scope,
    )
    resolved_path.write_text(_grid_view_html(data), encoding="utf-8")
    return resolved_path


def _profiled_grid_view_data(
    time_points: list[BenchmarkTimePoint],
    *,
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> dict[str, Any]:
    selected_case = get_stagg5_grid_case(grid_case)
    points = []
    for time_point in time_points:
        snapshot_data = _grid_view_data(
            time_point.runs,
            generated_at=generated_at,
            vsc_setpoint_scenario=vsc_setpoint_scenario,
            grid_case=grid_case,
            control_margin_percent=control_margin_percent,
            opf_control_selection=opf_control_selection,
            control_device_scope=control_device_scope,
        )
        profile_mode = _profile_mode_for_time_point(time_point)
        snapshot_data["meta"].update(
            {
                "time_index": time_point.time_index,
                "timestamp": time_point.timestamp,
                "profile_mode": profile_mode,
                "source": (
                    f"{snapshot_data['meta']['source']}. "
                    f"Profile timestamp selector shows {profile_mode} results"
                ),
            }
        )
        points.append(
            {
                "time_index": time_point.time_index,
                "timestamp": time_point.timestamp,
                "data": snapshot_data,
            }
        )
    return {
        "meta": {
            "title": "PF/OPF Snapshot Time-Series Grid View",
            "case": selected_case.case_name,
            "generated_at": generated_at,
            "snapshot_count": len(points),
        },
        "time_points": points,
        "profile_curves": _profile_curve_data(time_points),
    }


def _profile_mode_for_time_point(time_point: BenchmarkTimePoint) -> str:
    opf_run = _run_by_label(time_point.runs, TOOL1_OBJECTIVE_LABEL)
    if opf_run is not None:
        profile_mode = opf_run.diagnostics.get("profile_mode")
        if profile_mode:
            return str(profile_mode)
    pf_run = _run_by_label(time_point.runs, "ACDCPF PF")
    if pf_run is not None:
        profile_mode = pf_run.diagnostics.get("profile_mode")
        if profile_mode:
            return str(profile_mode)
    return "independent snapshot"


def _profile_curve_data(time_points: list[BenchmarkTimePoint]) -> dict[str, Any]:
    """Return profile-run curves for user-facing time-series inspection."""

    curve_specs = (
        ("ac_buses", "AC bus voltages", "AC bus", ("v_pu", "angle_deg")),
        ("dc_buses", "DC bus voltages", "DC bus", ("v_pu",)),
        ("loads", "AC loads", "AC load", ("p_mw", "q_mvar")),
        ("dc_loads", "DC loads", "DC load", ("p_mw",)),
        ("dc_generators", "DC generators", "DC generator", ("p_mw",)),
        (
            "storage_units",
            "Storage",
            "Storage",
            ("p_mw", "p_charge_mw", "p_discharge_mw", "q_mvar", "soc_percent"),
        ),
    )
    timeline = [
        {"time_index": time_point.time_index, "timestamp": time_point.timestamp}
        for time_point in time_points
    ]
    elements: list[dict[str, Any]] = []
    for table_name, group_label, element_label, metrics in curve_specs:
        for element_id in _profile_curve_element_ids(time_points, table_name):
            element = _profile_curve_element(
                time_points,
                table_name=table_name,
                element_id=element_id,
                group_label=group_label,
                element_label=element_label,
                metrics=metrics,
            )
            if element is not None:
                elements.append(element)
    loss_element = _profile_curve_loss_element(time_points)
    if loss_element is not None:
        elements.append(loss_element)

    return {
        "available": bool(elements),
        "source": "ACDCPF PF and Tool1 (acdcopf) result tables after applying each profile snapshot.",
        "timeline": timeline,
        "elements": elements,
    }


def _profile_curve_element_ids(
    time_points: list[BenchmarkTimePoint],
    table_name: str,
) -> list[str]:
    element_ids: set[str] = set()
    for time_point in time_points:
        for run_label in ("ACDCPF PF", TOOL1_OBJECTIVE_LABEL):
            run = _run_by_label(time_point.runs, run_label)
            element_ids.update(_table_rows_by_id(run, table_name))
    return sorted(element_ids, key=_natural_key)


def _profile_curve_element(
    time_points: list[BenchmarkTimePoint],
    *,
    table_name: str,
    element_id: str,
    group_label: str,
    element_label: str,
    metrics: tuple[str, ...],
) -> dict[str, Any] | None:
    name = None
    bus = None
    series: dict[str, dict[str, list[float | None]]] = {
        metric: {"pf": [], "opf": []}
        for metric in metrics
    }

    for time_point in time_points:
        pf_row = _table_rows_by_id(_run_by_label(time_point.runs, "ACDCPF PF"), table_name).get(
            element_id,
            {},
        )
        opf_row = _table_rows_by_id(
            _run_by_label(time_point.runs, TOOL1_OBJECTIVE_LABEL),
            table_name,
        ).get(element_id, {})
        topology_row = pf_row or opf_row
        if topology_row:
            name = name or topology_row.get("name")
            bus_candidate = topology_row.get(
                "bus",
                topology_row.get("id") if table_name in {"ac_buses", "dc_buses"} else None,
            )
            bus = bus if bus is not None else _safe_float(bus_candidate)
        for metric in metrics:
            series[metric]["pf"].append(_safe_float(pf_row.get(metric)))
            series[metric]["opf"].append(_safe_float(opf_row.get(metric)))

    has_values = any(
        value is not None
        for metric_series in series.values()
        for run_series in metric_series.values()
        for value in run_series
    )
    if not has_values:
        return None

    return {
        "uid": f"profile:{table_name}:{element_id}",
        "table_name": table_name,
        "group_label": group_label,
        "element_label": element_label,
        "id": element_id,
        "name": str(name or _default_grid_element_name(_profile_curve_kind(table_name), element_id)),
        "bus": int(bus) if bus is not None else None,
        "series": series,
    }


def _profile_curve_loss_element(time_points: list[BenchmarkTimePoint]) -> dict[str, Any] | None:
    """Return the system-loss curve used by the profiled HTML grid view."""

    pf_values: list[float | None] = []
    opf_values: list[float | None] = []
    for time_point in time_points:
        pf_values.append(_total_loss(_run_by_label(time_point.runs, "ACDCPF PF")))
        opf_values.append(_total_loss(_run_by_label(time_point.runs, TOOL1_OBJECTIVE_LABEL)))

    if not any(value is not None for value in (*pf_values, *opf_values)):
        return None

    return {
        "uid": "profile:losses:total",
        "table_name": "losses",
        "group_label": "Losses",
        "element_label": "System",
        "id": "total",
        "name": "Total active losses",
        "bus": None,
        "series": {
            "total_active_losses_mw": {
                "pf": pf_values,
                "opf": opf_values,
            }
        },
    }


def _profile_curve_kind(table_name: str) -> str:
    return {
        "ac_buses": "ac_bus",
        "dc_buses": "dc_bus",
        "loads": "load",
        "dc_loads": "dc_load",
        "dc_generators": "dc_generator",
        "storage_units": "storage",
        "losses": "loss",
    }[table_name]


def _grid_view_data(
    runs: list[BenchmarkRun],
    *,
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> dict[str, Any]:
    selected_case = get_stagg5_grid_case(grid_case)
    scenario = _coerce_vsc_setpoint_scenario(vsc_setpoint_scenario, selected_case)
    controls = opf_control_selection or _benchmark_opf_control_selection(selected_case)
    pf = _run_by_label(runs, "ACDCPF PF")
    opf = _tool1_report_run(runs)
    pyflow_pf = _run_by_label(runs, "PyFlow PF")
    pyflow_opf = _run_by_label(runs, "PyFlow OPF VSC-only")

    pf_total = _total_loss(pf)
    opf_total = _total_loss(opf)
    pyflow_pf_total = _total_loss(pyflow_pf)
    pyflow_opf_total = _total_loss(pyflow_opf)
    control_scope = _opf_control_scope_description(
        selected_case,
        controls,
        control_device_scope=control_device_scope,
    )
    return {
        "meta": {
            "title": "PF/OPF Grid View",
            "case": selected_case.case_name,
            "case_display_name": selected_case.display_name,
            "case_description": selected_case.description,
            "custom_import": selected_case.import_metadata,
            "generated_at": generated_at,
            "scenario": scenario.name,
            "scenario_description": scenario.description,
            "source": _grid_case_source_description(selected_case),
            "control_scope": control_scope,
            "enabled_opf_controls": _opf_control_selection_description(controls),
            "control_device_scope": control_device_scope,
            "control_margin": _control_margin_description(control_margin_percent),
            "fixed_controls": (
                f"{_opf_disabled_control_description(controls)}; AC generator voltage setpoints "
                "and ordinary DC load powers remain fixed"
            ),
        },
        "summary": {
            "pf_total_loss_mw": pf_total,
            "opf_total_loss_mw": opf_total,
            "opf_objective_total_loss_mw": opf_total,
            "loss_delta_mw": _difference(opf_total, pf_total),
            "loss_delta_percent": _percent_change(opf_total, pf_total),
            "pyflow_pf_total_loss_mw": pyflow_pf_total,
            "pyflow_opf_total_loss_mw": pyflow_opf_total,
            "pyflow_loss_delta_mw": _difference(pyflow_opf_total, pyflow_pf_total),
            "pyflow_loss_delta_percent": _percent_change(pyflow_opf_total, pyflow_pf_total),
        },
        "runs": [
            {
                "label": run.label,
                "success": run.success,
                "message": run.message,
                "losses_mw": run.losses_mw,
            }
            for run in runs
        ],
        "elements": {
            "ac_buses": _grid_bus_elements(
                pf,
                opf,
                pyflow_pf,
                pyflow_opf,
                "ac_buses",
                kind="ac_bus",
                metrics=("v_pu", "angle_deg", "p_mw", "q_mvar"),
            ),
            "dc_buses": _grid_bus_elements(
                pf,
                opf,
                pyflow_pf,
                pyflow_opf,
                "dc_buses",
                kind="dc_bus",
                metrics=("v_pu", "p_mw"),
            ),
            "ac_branches": _grid_branch_elements(
                pf,
                opf,
                pyflow_pf,
                pyflow_opf,
                "ac_branches",
                kind="ac_branch",
                metrics=("p_from_mw", "q_from_mvar", "p_to_mw", "q_to_mvar", "p_loss_mw"),
            ),
            "transformers": _grid_branch_elements(
                pf,
                opf,
                pyflow_pf,
                pyflow_opf,
                "transformers",
                kind="transformer",
                metrics=(
                    "p_from_mw",
                    "q_from_mvar",
                    "p_to_mw",
                    "q_to_mvar",
                    "p_loss_mw",
                    "loading_percent",
                ),
            ),
            "dc_branches": _grid_branch_elements(
                pf,
                opf,
                pyflow_pf,
                pyflow_opf,
                "dc_branches",
                kind="dc_branch",
                metrics=("p_from_mw", "p_to_mw", "p_loss_mw"),
            ),
            "dcdc_converters": _grid_branch_elements(
                pf,
                opf,
                pyflow_pf,
                pyflow_opf,
                "dcdc_converters",
                kind="dcdc_converter",
                metrics=("p_from_mw", "p_to_mw", "p_loss_mw", "d_ratio"),
            ),
            "converters": _grid_converter_elements(
                pf,
                opf,
                pyflow_pf,
                pyflow_opf,
                selected_case,
            ),
            "generators": _grid_bus_attached_elements(
                pf,
                opf,
                pyflow_pf,
                pyflow_opf,
                "generators",
                kind="generator",
                metrics=("p_mw", "q_mvar"),
                bus_field="bus",
                bus_kind="ac",
            ),
            "dc_generators": _grid_bus_attached_elements(
                pf,
                opf,
                pyflow_pf,
                pyflow_opf,
                "dc_generators",
                kind="dc_generator",
                metrics=("p_mw",),
                bus_field="bus",
                bus_kind="dc",
            ),
            "loads": _grid_bus_attached_elements(
                pf,
                opf,
                pyflow_pf,
                pyflow_opf,
                "loads",
                kind="load",
                metrics=("p_mw", "q_mvar"),
                bus_field="bus",
                bus_kind="ac",
            ),
            "dc_loads": _grid_bus_attached_elements(
                pf,
                opf,
                pyflow_pf,
                pyflow_opf,
                "dc_loads",
                kind="dc_load",
                metrics=("p_mw",),
                bus_field="bus",
                bus_kind="dc",
            ),
            "storage_units": _grid_storage_elements(pf, opf, pyflow_pf, pyflow_opf),
        },
    }


def _grid_case_control_scope(selected_case: Stagg5GridCase) -> str:
    return _opf_control_scope_description(
        selected_case,
        _benchmark_opf_control_selection(selected_case),
        control_device_scope=CONTROL_DEVICE_SCOPE_BENCHMARK,
    )


def _opf_control_scope_description(
    selected_case: Stagg5GridCase,
    selection: OPFControlSelection,
    *,
    control_device_scope: str,
) -> str:
    controls = []
    p_indices = _selected_converter_indices(
        selected_case.converter_active_power_indices,
        enabled=selection.vsc_p,
        control_device_scope=control_device_scope,
    )
    q_indices = _selected_converter_indices(
        selected_case.converter_reactive_power_indices,
        enabled=selection.vsc_q,
        control_device_scope=control_device_scope,
    )
    if selection.vsc_p and selection.vsc_q and p_indices == q_indices:
        controls.append(f"VSC P/Q controls for {_control_indices_description(p_indices)}")
    else:
        if selection.vsc_p:
            controls.append(f"VSC P controls for {_control_indices_description(p_indices)}")
        if selection.vsc_q:
            controls.append(f"VSC Q controls for {_control_indices_description(q_indices)}")
    if selection.dcdc_ratio:
        dcdc_indices = _selected_dcdc_indices(
            selected_case,
            enabled=True,
            control_device_scope=control_device_scope,
        )
        controls.append(f"DCDC voltage ratios for {_control_indices_description(dcdc_indices)}")
    if selection.ac_gen_p:
        controls.append("AC generator active power for non-slack generators")
    if selection.ac_gen_q:
        controls.append("AC generator reactive power for non-slack generators")
    if selection.dc_gen_curtailment:
        controls.append("DC generator curtailment for positive DC generators")
    if selected_case.key == STAGG5_HYBRID_DCDC:
        controls.append("storage active power")
    if not controls:
        return "No OPF controls enabled"
    return "; ".join(controls)


def _grid_case_source_description(selected_case: Stagg5GridCase) -> str:
    if selected_case.source == "pyflow":
        return "PyFlow topology with Tool1 (acdcopf) Pyomo/IPOPT results"
    return "ACDCPF topology with Tool1 (acdcopf) Pyomo/IPOPT results"


def _control_indices_description(indices: tuple[int, ...] | None) -> str:
    if indices is None:
        return "all eligible indices"
    if not indices:
        return "no indices"
    return f"indices {tuple(indices)}"


def _control_margin_description(control_margin_percent: float | None) -> str:
    if control_margin_percent is None:
        return "Full technical control range"
    return (
        f"{control_margin_percent:g}% control margin "
        "(VSC/storage: +/- margin around PF start; DC generator curtailment: "
        "maximum downward curtailment from profiled generation)"
    )


def _grid_bus_elements(
    pf: BenchmarkRun | None,
    opf: BenchmarkRun | None,
    pyflow_pf: BenchmarkRun | None,
    pyflow_opf: BenchmarkRun | None,
    table_name: str,
    *,
    kind: str,
    metrics: tuple[str, ...],
) -> list[dict[str, Any]]:
    elements = []
    for rows in _paired_grid_rows(pf, opf, pyflow_pf, pyflow_opf, table_name):
        element_id, pf_row, opf_row, pyflow_pf_row, pyflow_opf_row, topology_row = rows
        elements.append(
            _grid_element(
                kind=kind,
                element_id=element_id,
                topology_row=topology_row,
                pf_row=pf_row,
                opf_row=opf_row,
                pyflow_pf_row=pyflow_pf_row,
                pyflow_opf_row=pyflow_opf_row,
                metrics=metrics,
            )
        )
    return elements


def _grid_branch_elements(
    pf: BenchmarkRun | None,
    opf: BenchmarkRun | None,
    pyflow_pf: BenchmarkRun | None,
    pyflow_opf: BenchmarkRun | None,
    table_name: str,
    *,
    kind: str,
    metrics: tuple[str, ...],
) -> list[dict[str, Any]]:
    elements = []
    for rows in _paired_grid_rows(pf, opf, pyflow_pf, pyflow_opf, table_name):
        element_id, pf_row, opf_row, pyflow_pf_row, pyflow_opf_row, topology_row = rows
        element = _grid_element(
            kind=kind,
            element_id=element_id,
            topology_row=topology_row,
            pf_row=pf_row,
            opf_row=opf_row,
            pyflow_pf_row=pyflow_pf_row,
            pyflow_opf_row=pyflow_opf_row,
            metrics=metrics,
        )
        element["from_bus"] = _optional_int(topology_row.get("from_bus"))
        element["to_bus"] = _optional_int(topology_row.get("to_bus"))
        if kind == "transformer":
            element["tap"] = _safe_float(topology_row.get("tap"))
            element["shift_deg"] = _safe_float(topology_row.get("shift_deg"))
        elements.append(element)
    return elements


def _grid_converter_elements(
    pf: BenchmarkRun | None,
    opf: BenchmarkRun | None,
    pyflow_pf: BenchmarkRun | None,
    pyflow_opf: BenchmarkRun | None,
    selected_case: Stagg5GridCase,
) -> list[dict[str, Any]]:
    elements = []
    for rows in _paired_grid_rows(pf, opf, pyflow_pf, pyflow_opf, "converters"):
        element_id, pf_row, opf_row, pyflow_pf_row, pyflow_opf_row, topology_row = rows
        element = _grid_element(
            kind="converter",
            element_id=element_id,
            topology_row=topology_row,
            pf_row=pf_row,
            opf_row=opf_row,
            pyflow_pf_row=pyflow_pf_row,
            pyflow_opf_row=pyflow_opf_row,
            metrics=("p_ac_mw", "q_ac_mvar", "p_dc_mw", "p_loss_mw"),
        )
        element["ac_bus"] = _optional_int(topology_row.get("ac_bus"))
        element["dc_bus"] = _optional_int(topology_row.get("dc_bus"))
        element["control_mode"] = topology_row.get("control_mode")
        element["s_mva"] = _safe_float(topology_row.get("s_mva"))
        element["controlled"] = _is_controlled_converter_index(
            _optional_int(element_id),
            selected_case,
        )
        elements.append(element)
    return elements


def _grid_bus_attached_elements(
    pf: BenchmarkRun | None,
    opf: BenchmarkRun | None,
    pyflow_pf: BenchmarkRun | None,
    pyflow_opf: BenchmarkRun | None,
    table_name: str,
    *,
    kind: str,
    metrics: tuple[str, ...],
    bus_field: str,
    bus_kind: str = "ac",
) -> list[dict[str, Any]]:
    elements = []
    for rows in _paired_grid_rows(pf, opf, pyflow_pf, pyflow_opf, table_name):
        element_id, pf_row, opf_row, pyflow_pf_row, pyflow_opf_row, topology_row = rows
        element = _grid_element(
            kind=kind,
            element_id=element_id,
            topology_row=topology_row,
            pf_row=pf_row,
            opf_row=opf_row,
            pyflow_pf_row=pyflow_pf_row,
            pyflow_opf_row=pyflow_opf_row,
            metrics=metrics,
        )
        element["bus"] = _optional_int(topology_row.get(bus_field))
        element["bus_kind"] = bus_kind
        elements.append(element)
    return elements


def _grid_storage_elements(
    pf: BenchmarkRun | None,
    opf: BenchmarkRun | None,
    pyflow_pf: BenchmarkRun | None,
    pyflow_opf: BenchmarkRun | None,
) -> list[dict[str, Any]]:
    elements = []
    for rows in _paired_grid_rows(pf, opf, pyflow_pf, pyflow_opf, "storage_units"):
        element_id, pf_row, opf_row, pyflow_pf_row, pyflow_opf_row, topology_row = rows
        element = _grid_element(
            kind="storage",
            element_id=element_id,
            topology_row=topology_row,
            pf_row=pf_row,
            opf_row=opf_row,
            pyflow_pf_row=pyflow_pf_row,
            pyflow_opf_row=pyflow_opf_row,
            metrics=("p_mw", "q_mvar", "soc_percent", "energy_mwh", "loading_percent"),
        )
        element["bus"] = _optional_int(topology_row.get("bus"))
        element["bus_kind"] = str(topology_row.get("bus_type", "dc")).lower()
        element["s_mva"] = _safe_float(topology_row.get("s_mva"))
        elements.append(element)
    return elements


def _paired_grid_rows(
    pf: BenchmarkRun | None,
    opf: BenchmarkRun | None,
    pyflow_pf: BenchmarkRun | None,
    pyflow_opf: BenchmarkRun | None,
    table_name: str,
) -> list[tuple[
    str,
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
]]:
    pf_rows = _table_rows_by_id(pf, table_name)
    opf_rows = _table_rows_by_id(opf, table_name)
    pyflow_pf_rows = _table_rows_by_id(pyflow_pf, table_name)
    pyflow_opf_rows = _table_rows_by_id(pyflow_opf, table_name)
    element_ids = sorted(
        set(pf_rows) | set(opf_rows) | set(pyflow_pf_rows) | set(pyflow_opf_rows),
        key=_natural_key,
    )
    pairs = []
    for element_id in element_ids:
        pf_row = pf_rows.get(element_id, {})
        opf_row = opf_rows.get(element_id, {})
        pyflow_pf_row = pyflow_pf_rows.get(element_id, {})
        pyflow_opf_row = pyflow_opf_rows.get(element_id, {})
        topology_row = pf_row or opf_row or pyflow_pf_row or pyflow_opf_row
        pairs.append((element_id, pf_row, opf_row, pyflow_pf_row, pyflow_opf_row, topology_row))
    return pairs


def _table_rows_by_id(
    run: BenchmarkRun | None,
    table_name: str,
) -> dict[str, Mapping[str, Any]]:
    if run is None:
        return {}
    return {
        str(row.get("id")): row
        for row in run.tables.get(table_name, [])
        if row.get("id") is not None
    }


def _grid_element(
    *,
    kind: str,
    element_id: str,
    topology_row: Mapping[str, Any],
    pf_row: Mapping[str, Any],
    opf_row: Mapping[str, Any],
    pyflow_pf_row: Mapping[str, Any],
    pyflow_opf_row: Mapping[str, Any],
    metrics: tuple[str, ...],
) -> dict[str, Any]:
    name = topology_row.get("name") or _default_grid_element_name(kind, element_id)
    return {
        "uid": f"{kind}:{element_id}",
        "id": element_id,
        "kind": kind,
        "name": str(name),
        "pf": _grid_metrics(pf_row, metrics),
        "opf": _grid_metrics(opf_row, metrics),
        "pyflow_pf": _grid_metrics(pyflow_pf_row, metrics),
        "pyflow_opf": _grid_metrics(pyflow_opf_row, metrics),
    }


def _grid_metrics(row: Mapping[str, Any], metrics: tuple[str, ...]) -> dict[str, float]:
    return {
        field_name: value
        for field_name in metrics
        if (value := _safe_float(row.get(field_name))) is not None
    }


def _default_grid_element_name(kind: str, element_id: str) -> str:
    labels = {
        "ac_bus": "AC bus",
        "dc_bus": "DC bus",
        "ac_branch": "AC branch",
        "transformer": "Transformer",
        "dc_branch": "DC branch",
        "dcdc_converter": "DCDC converter",
        "converter": "VSC",
        "generator": "Generator",
        "dc_generator": "DC generator",
        "load": "Load",
        "dc_load": "DC load",
        "storage": "Storage",
    }
    return f"{labels.get(kind, kind)} {element_id}"


def _optional_int(value: Any) -> int | None:
    number = _safe_float(value)
    if number is None:
        return None
    return int(number)


def _grid_view_html(data: dict[str, Any]) -> str:
    payload = json.dumps(data, indent=2, sort_keys=True, default=str).replace("</", "<\\/")
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tool1 (acdcopf) Grid View</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --panel: #ffffff;
      --ink: #1f2937;
      --muted: #667085;
      --line: #d0d5dd;
      --ac: #2563eb;
      --dc: #16a34a;
      --vsc: #f97316;
      --good: #e8f7ee;
      --warn: #fff3d6;
      --bad: #ffe4e6;
      --shadow: 0 18px 45px rgba(15, 23, 42, 0.10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, Segoe UI, Roboto, Arial, sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at top left, #eef6ff 0, var(--bg) 40%, #f8fafc 100%);
    }
    header {
      padding: 24px 32px 16px;
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-start;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 28px;
      letter-spacing: -0.02em;
    }
    .subtitle {
      margin: 0;
      color: var(--muted);
      max-width: 900px;
      line-height: 1.45;
    }
    .toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .timestamp-picker {
      display: none;
      align-items: center;
      gap: 8px;
      padding: 8px 11px;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 999px;
      font-size: 13px;
      font-weight: 650;
      color: var(--muted);
    }
    .timestamp-picker select {
      border: 0;
      color: var(--ink);
      background: transparent;
      font-weight: 700;
      outline: none;
    }
    button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      padding: 9px 13px;
      border-radius: 999px;
      font-weight: 650;
      cursor: pointer;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
    }
    button.active {
      color: #ffffff;
      background: #111827;
      border-color: #111827;
    }
    main {
      display: grid;
      grid-template-columns: minmax(680px, 1fr) 380px;
      gap: 18px;
      padding: 0 24px 24px;
    }
    .card {
      background: rgba(255, 255, 255, 0.92);
      border: 1px solid rgba(208, 213, 221, 0.95);
      border-radius: 22px;
      box-shadow: var(--shadow);
    }
    .summary {
      grid-column: 1 / -1;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      box-shadow: none;
      background: transparent;
      border: 0;
    }
    .metric-card {
      padding: 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: var(--panel);
    }
    .metric-label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .metric-value {
      margin-top: 6px;
      font-size: 22px;
      font-weight: 750;
    }
    .canvas-card {
      position: relative;
      min-height: 720px;
      overflow: hidden;
    }
    svg {
      display: block;
      width: 100%;
      height: 720px;
      background:
        linear-gradient(#eef2f7 1px, transparent 1px),
        linear-gradient(90deg, #eef2f7 1px, transparent 1px);
      background-size: 32px 32px;
      border-radius: 22px;
    }
    .edge {
      fill: none;
      stroke-linecap: round;
      opacity: 0.78;
      cursor: pointer;
      transition: opacity 140ms, stroke-width 140ms;
    }
    .edge:hover,
    .node:hover,
    .vsc:hover,
    .marker:hover {
      opacity: 1;
      filter: drop-shadow(0 6px 10px rgba(15, 23, 42, 0.18));
    }
    .ac-edge { stroke: #475467; }
    .dc-edge { stroke: var(--dc); }
    .terminal-edge {
      stroke: #98a2b3;
      stroke-dasharray: 6 6;
      stroke-width: 2;
      opacity: 0.7;
    }
    .equipment-edge {
      stroke: #64748b;
      stroke-dasharray: 4 6;
      stroke-width: 2;
      opacity: 0.75;
    }
    .node {
      stroke: #ffffff;
      stroke-width: 3;
      cursor: pointer;
    }
    .node.ac { stroke: var(--ac); }
    .node.dc { stroke: var(--dc); }
    .vsc {
      cursor: pointer;
      fill: #fff7ed;
      stroke: var(--vsc);
      stroke-width: 3;
      rx: 10;
    }
    .vsc.controlled { fill: #ffedd5; }
    .marker {
      cursor: pointer;
      stroke: #ffffff;
      stroke-width: 2;
    }
    .generator { fill: #7c3aed; }
    .load { fill: #64748b; }
    .storage { fill: #0f766e; }
    .label {
      font-size: 13px;
      font-weight: 700;
      fill: #111827;
      pointer-events: none;
      paint-order: stroke;
      stroke: rgba(255, 255, 255, 0.86);
      stroke-width: 4px;
      stroke-linejoin: round;
    }
    .line-label {
      font-size: 11px;
      fill: #475467;
      pointer-events: none;
      paint-order: stroke;
      stroke: rgba(255, 255, 255, 0.82);
      stroke-width: 3px;
    }
    .branch-label-badge {
      fill: rgba(255, 255, 255, 0.92);
      stroke: #d0d5dd;
      stroke-width: 1;
      rx: 8;
    }
    .branch-label-text {
      font-size: 11px;
      font-weight: 650;
      fill: #344054;
      pointer-events: none;
    }
    aside {
      padding: 18px;
      align-self: start;
      position: static;
      max-height: none;
      overflow: auto;
    }
    aside h2 {
      margin: 0 0 8px;
      font-size: 20px;
    }
    aside h3 {
      margin: 18px 0 4px;
      font-size: 15px;
    }
    .small {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .pill-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin: 14px 0;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 9px;
      border: 1px solid var(--line);
      border-radius: 999px;
      font-size: 12px;
      color: var(--muted);
      background: #fff;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      display: inline-block;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid #eaecf0;
      padding: 8px 6px;
      text-align: right;
      vertical-align: top;
    }
    th:first-child,
    td:first-child { text-align: left; }
    th {
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .profile-card {
      grid-column: 1 / -1;
      padding: 18px;
      position: relative;
    }
    .profile-header {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    .profile-header h2 {
      margin: 0 0 4px;
      font-size: 20px;
    }
    .profile-controls {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    .profile-controls label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .profile-controls select {
      display: block;
      min-width: 160px;
      margin-top: 4px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      color: var(--ink);
      font-weight: 650;
    }
    .curve-svg {
      height: 380px;
      background: #ffffff;
      border: 1px solid #eaecf0;
      border-radius: 16px;
    }
    .curve-grid { stroke: #eef2f7; stroke-width: 1; }
    .curve-axis { stroke: #98a2b3; stroke-width: 1.2; }
    .curve-label {
      fill: var(--muted);
      font-size: 11px;
      font-weight: 650;
      paint-order: stroke;
      stroke: rgba(255, 255, 255, 0.9);
      stroke-width: 3px;
    }
    .curve-line {
      fill: none;
      stroke-width: 3;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .curve-overlap {
      stroke-dasharray: 7 6;
      opacity: 0.86;
    }
    .curve-point { stroke: #ffffff; stroke-width: 2; }
    .curve-pf { stroke: #2563eb; fill: #2563eb; }
    .curve-opf { stroke: #f97316; fill: #f97316; }
    .curve-line.curve-pf,
    .curve-line.curve-opf { fill: none; }
    .curve-hit-point {
      fill: transparent;
      stroke: transparent;
      pointer-events: all;
      cursor: crosshair;
    }
    .curve-marker { stroke: #111827; stroke-width: 1.5; stroke-dasharray: 5 5; }
    .curve-legend {
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .curve-tooltip {
      position: fixed;
      display: none;
      z-index: 30;
      pointer-events: none;
      max-width: 280px;
      padding: 10px 12px;
      border-radius: 13px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.98);
      box-shadow: var(--shadow);
      font-size: 13px;
    }
    .curve-tooltip strong { color: var(--ink); }
    .tooltip {
      position: fixed;
      display: none;
      z-index: 20;
      pointer-events: none;
      max-width: 320px;
      padding: 12px 13px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.96);
      box-shadow: var(--shadow);
      font-size: 13px;
    }
    .tooltip-title {
      font-weight: 750;
      margin-bottom: 6px;
    }
    .footer-note {
      padding: 0 28px 24px;
      color: var(--muted);
      font-size: 13px;
    }
    @media (max-width: 1100px) {
      main { grid-template-columns: 1fr; }
      aside { position: static; max-height: none; }
      .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Tool1 (acdcopf) Interactive Grid View</h1>
      <p class="subtitle" id="subtitle"></p>
    </div>
    <div class="toolbar" aria-label="View mode">
      <label class="timestamp-picker" id="timestampPickerWrap">
        Timestamp
        <select id="timestampSelect"></select>
      </label>
      <button type="button" data-mode="pf" class="active">PF baseline</button>
      <button type="button" data-mode="opf">OPF result</button>
      <button type="button" data-mode="delta">Delta</button>
      <button type="button" id="branchLabelsButton" data-branch-labels="off">Branch names: hidden</button>
    </div>
  </header>
  <main>
    <section class="summary" id="summaryCards"></section>
    <section class="card canvas-card">
      <svg id="gridSvg" viewBox="0 0 1200 720" role="img" aria-label="Interactive AC/DC grid schematic"></svg>
      <div class="tooltip" id="tooltip"></div>
    </section>
    <aside class="card" id="detailsPanel">
      <h2>Grid Details</h2>
      <p class="small">Hover over or click a bus, branch, converter, generator, storage unit, or load to inspect PF and OPF values.</p>
      <div class="pill-row">
        <span class="pill"><span class="dot" style="background:#2563eb"></span>AC bus</span>
        <span class="pill"><span class="dot" style="background:#16a34a"></span>DC bus</span>
        <span class="pill"><span class="dot" style="background:#f97316"></span>VSC</span>
        <span class="pill"><span class="dot" style="background:#475467"></span>Transformer</span>
        <span class="pill"><span class="dot" style="background:#16a34a"></span>DCDC</span>
        <span class="pill"><span class="dot" style="background:#7c3aed"></span>Generator</span>
        <span class="pill"><span class="dot" style="background:#64748b"></span>Load</span>
        <span class="pill"><span class="dot" style="background:#0f766e"></span>Storage</span>
      </div>
    </aside>
    <section class="card profile-card" id="profileCurveCard">
      <div class="profile-header">
        <div>
          <h2>Profile Curves</h2>
          <p class="small" id="profileCurveDescription">
            Select an AC load, DC load, DC generator, or storage unit to inspect values across profile snapshots.
          </p>
        </div>
        <div class="profile-controls">
          <label>
            Element type
            <select id="profileCurveGroupSelect"></select>
          </label>
          <label>
            Element
            <select id="profileCurveElementSelect"></select>
          </label>
          <label>
            Quantity
            <select id="profileCurveMetricSelect"></select>
          </label>
        </div>
      </div>
      <svg id="profileCurveSvg" class="curve-svg" viewBox="0 0 1100 380" role="img" aria-label="Profile curve chart"></svg>
      <div class="curve-tooltip" id="profileCurveTooltip"></div>
      <div class="curve-legend" id="profileCurveLegend"></div>
      <p class="small" id="profileCurveNote"></p>
    </section>
  </main>
  <p class="footer-note">
    Layout is schematic: AC buses are placed on the upper layer, DC buses on the lower layer, and VSCs at their AC/DC interface.
    Power-flow values use the existing benchmark report convention.
  </p>
  <script>
    const RAW_DATA = __GRID_DATA_JSON__;
    let DATA = activeGridData(RAW_DATA);
    const svg = document.getElementById('gridSvg');
    const tooltip = document.getElementById('tooltip');
    const detailsPanel = document.getElementById('detailsPanel');
    const profileCurveCard = document.getElementById('profileCurveCard');
    const profileCurveSvg = document.getElementById('profileCurveSvg');
    const profileCurveLegend = document.getElementById('profileCurveLegend');
    const profileCurveNote = document.getElementById('profileCurveNote');
    const profileCurveTooltip = document.getElementById('profileCurveTooltip');
    const profileCurveState = { tableName: null, elementUid: null, metric: null };
    const state = { mode: 'pf', selected: null, showBranchLabels: false };
    const metricLabels = {
      v_pu: 'Voltage (p.u.)',
      angle_deg: 'Angle (deg)',
      p_mw: 'P (MW)',
      p_charge_mw: 'Charge P (MW)',
      p_discharge_mw: 'Discharge P (MW)',
      q_mvar: 'Q (MVAr)',
      p_ac_mw: 'AC P (MW)',
      q_ac_mvar: 'AC Q (MVAr)',
      p_dc_mw: 'DC P (MW)',
      p_from_mw: 'From P (MW)',
      q_from_mvar: 'From Q (MVAr)',
      p_to_mw: 'To P (MW)',
      q_to_mvar: 'To Q (MVAr)',
      p_loss_mw: 'Station loss (MW)',
      p_elec_loss_mw: 'Electronic loss (MW)',
      d_ratio: 'DCDC ratio',
      energy_mwh: 'Energy (MWh)',
      energy_capacity_mwh: 'Energy capacity (MWh)',
      soc_percent: 'SOC (%)',
      loading_percent: 'Loading (%)',
      total_active_losses_mw: 'Total active loss (MW)'
    };

    function activeGridData(payload) {
      if (payload.time_points && payload.time_points.length) {
        return payload.time_points[0].data;
      }
      return payload;
    }

    function setupTimestampSelector() {
      const points = RAW_DATA.time_points || [];
      const wrap = document.getElementById('timestampPickerWrap');
      const select = document.getElementById('timestampSelect');
      if (!points.length) return;
      wrap.style.display = 'inline-flex';
      select.innerHTML = points.map((point, index) => {
        const label = `t=${point.time_index} ${point.timestamp}`;
        return `<option value="${index}">${esc(label)}</option>`;
      }).join('');
      select.addEventListener('change', () => {
        DATA = points[Number(select.value)].data;
        state.selected = null;
        refreshView();
        renderProfileCurve();
      });
    }

    function profileCurveData() {
      return RAW_DATA.profile_curves || { available: false, timeline: [], elements: [] };
    }

    function setupProfileCurveControls() {
      const curves = profileCurveData();
      const elements = curves.elements || [];
      if (!curves.available || !elements.length) {
        profileCurveCard.style.display = 'none';
        return;
      }

      const groupSelect = document.getElementById('profileCurveGroupSelect');
      const elementSelect = document.getElementById('profileCurveElementSelect');
      const metricSelect = document.getElementById('profileCurveMetricSelect');
      const groups = [...new Map(elements.map(element => [element.table_name, element.group_label])).entries()];
      groupSelect.innerHTML = groups
        .map(([tableName, label]) => `<option value="${esc(tableName)}">${esc(label)}</option>`)
        .join('');
      profileCurveState.tableName = groups[0]?.[0] || null;
      groupSelect.value = profileCurveState.tableName || '';

      groupSelect.addEventListener('change', () => {
        profileCurveState.tableName = groupSelect.value;
        profileCurveState.elementUid = null;
        profileCurveState.metric = null;
        populateProfileCurveElements();
        renderProfileCurve();
      });
      elementSelect.addEventListener('change', () => {
        profileCurveState.elementUid = elementSelect.value;
        profileCurveState.metric = null;
        populateProfileCurveMetrics();
        renderProfileCurve();
      });
      metricSelect.addEventListener('change', () => {
        profileCurveState.metric = metricSelect.value;
        renderProfileCurve();
      });

      populateProfileCurveElements();
      renderProfileCurve();
    }

    function profileCurveElementsForSelectedGroup() {
      const curves = profileCurveData();
      return (curves.elements || []).filter(element => element.table_name === profileCurveState.tableName);
    }

    function populateProfileCurveElements() {
      const elementSelect = document.getElementById('profileCurveElementSelect');
      const elements = profileCurveElementsForSelectedGroup();
      elementSelect.innerHTML = elements.map(element => {
        const bus = element.bus === null || element.bus === undefined ? '' : ` | bus ${element.bus}`;
        return `<option value="${esc(element.uid)}">${esc(element.name)}${esc(bus)}</option>`;
      }).join('');
      profileCurveState.elementUid = elements[0]?.uid || null;
      elementSelect.value = profileCurveState.elementUid || '';
      populateProfileCurveMetrics();
    }

    function selectedProfileCurveElement() {
      const curves = profileCurveData();
      return (curves.elements || []).find(element => element.uid === profileCurveState.elementUid) || null;
    }

    function populateProfileCurveMetrics() {
      const metricSelect = document.getElementById('profileCurveMetricSelect');
      const element = selectedProfileCurveElement();
      const metrics = element ? Object.keys(element.series || {}) : [];
      metricSelect.innerHTML = metrics
        .map(metric => `<option value="${esc(metric)}">${esc(metricLabels[metric] || metric)}</option>`)
        .join('');
      profileCurveState.metric = metrics[0] || null;
      metricSelect.value = profileCurveState.metric || '';
    }

    function renderProfileCurve() {
      if (!profileCurveCard || profileCurveCard.style.display === 'none') return;
      const curves = profileCurveData();
      const element = selectedProfileCurveElement();
      const metric = profileCurveState.metric;
      profileCurveSvg.replaceChildren();
      profileCurveLegend.innerHTML = '';
      if (!element || !metric) {
        profileCurveNote.textContent = 'No profile curve data is available for this selection.';
        return;
      }

      const timeline = curves.timeline || [];
      const metricSeries = element.series?.[metric] || {};
      const pfValues = metricSeries.pf || [];
      const opfValues = metricSeries.opf || [];
      const allValues = [...pfValues, ...opfValues].filter(isFiniteValue).map(Number);
      if (!allValues.length || !timeline.length) {
        profileCurveNote.textContent = 'No numeric values are available for this curve.';
        return;
      }

      const chart = { left: 64, right: 26, top: 34, bottom: 72, width: 1100, height: 380 };
      chart.innerWidth = chart.width - chart.left - chart.right;
      chart.innerHeight = chart.height - chart.top - chart.bottom;
      const rawMin = Math.min(...allValues);
      const rawMax = Math.max(...allValues);
      const yAxis = powerAxis(rawMin, rawMax, profileCurveAxisShouldIncludeZero(metric));
      const yMin = yAxis.min;
      const yMax = yAxis.max;
      const xScale = index => chart.left + (timeline.length === 1 ? chart.innerWidth / 2 : (chart.innerWidth * index) / (timeline.length - 1));
      const yScale = value => chart.top + chart.innerHeight * (1 - (Number(value) - yMin) / (yMax - yMin));
      const seriesOverlap = comparableSeriesEqual(pfValues, opfValues);

      drawCurveAxes(chart, timeline, yAxis, xScale, yScale, metric);
      drawCurveSeries(pfValues, xScale, yScale, 'curve-line curve-pf', 'PF baseline', timeline, element, metric, pfValues, opfValues);
      if (opfValues.some(isFiniteValue)) {
        const opfClass = seriesOverlap ? 'curve-line curve-opf curve-overlap' : 'curve-line curve-opf';
        drawCurveSeries(opfValues, xScale, yScale, opfClass, 'TOOL1', timeline, element, metric, pfValues, opfValues);
      }
      drawSelectedTimestampMarker(chart, timeline, xScale);

      profileCurveLegend.innerHTML = [
        '<span class="legend-item"><span class="dot" style="background:#2563eb"></span>PF baseline after profile scaling</span>',
        opfValues.some(isFiniteValue)
          ? '<span class="legend-item"><span class="dot" style="background:#f97316"></span>Tool1 (acdcopf) result</span>'
          : ''
      ].filter(Boolean).join('');
      profileCurveNote.textContent =
        `${element.element_label} ${element.name}: ${metricLabels[metric] || metric}. `
        + profileCurveExplanation(element, metric, pfValues, opfValues);
    }

    function powerAxis(rawMin, rawMax, includeZero = true) {
      if (!Number.isFinite(rawMin) || !Number.isFinite(rawMax)) return { min: 0, max: 1, ticks: [0, 0.25, 0.5, 0.75, 1] };
      const includesZero = includeZero
        ? (rawMin >= 0 ? { min: 0, max: rawMax } : rawMax <= 0 ? { min: rawMin, max: 0 } : { min: rawMin, max: rawMax })
        : { min: rawMin, max: rawMax };
      const rawRange = Math.max(includesZero.max - includesZero.min, Math.max(Math.abs(rawMax), 1) * 0.02);
      const step = niceStep(rawRange / 4);
      const min = Math.floor(includesZero.min / step) * step;
      const max = Math.ceil(includesZero.max / step) * step;
      const ticks = [];
      for (let value = min; value <= max + step * 0.5; value += step) {
        ticks.push(Math.abs(value) < step * 1e-9 ? 0 : Number(value.toFixed(10)));
      }
      return { min, max: max === min ? min + step : max, ticks };
    }

    function profileCurveAxisShouldIncludeZero(metric) {
      return !['v_pu', 'angle_deg', 'soc_percent'].includes(metric);
    }

    function niceStep(rawStep) {
      if (!Number.isFinite(rawStep) || rawStep <= 0) return 1;
      const exponent = Math.floor(Math.log10(rawStep));
      const fraction = rawStep / Math.pow(10, exponent);
      const niceFraction = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
      return niceFraction * Math.pow(10, exponent);
    }

    function drawCurveAxes(chart, timeline, yAxis, xScale, yScale, metric) {
      for (const value of yAxis.ticks) {
        const y = yScale(value);
        add('line', { x1: chart.left, y1: y, x2: chart.width - chart.right, y2: y, class: 'curve-grid' }, profileCurveSvg);
        add('text', { x: chart.left - 10, y: y + 4, class: 'curve-label', 'text-anchor': 'end' }, profileCurveSvg).textContent = fmtAxis(value);
      }
      add('line', { x1: chart.left, y1: chart.top, x2: chart.left, y2: chart.height - chart.bottom, class: 'curve-axis' }, profileCurveSvg);
      add('line', { x1: chart.left, y1: chart.height - chart.bottom, x2: chart.width - chart.right, y2: chart.height - chart.bottom, class: 'curve-axis' }, profileCurveSvg);
      add('text', { x: chart.left - 42, y: chart.top + 8, class: 'curve-label', 'text-anchor': 'start' }, profileCurveSvg).textContent = metricLabels[metric] || metric;
      add('text', { x: chart.width - chart.right, y: chart.height - 12, class: 'curve-label', 'text-anchor': 'end' }, profileCurveSvg).textContent = 'Time';

      const labelStep = Math.max(1, Math.ceil(timeline.length / 8));
      timeline.forEach((point, index) => {
        if (index % labelStep !== 0 && index !== timeline.length - 1) return;
        const x = xScale(index);
        add('line', { x1: x, y1: chart.height - chart.bottom, x2: x, y2: chart.height - chart.bottom + 5, class: 'curve-axis' }, profileCurveSvg);
        add('text', { x, y: chart.height - 24, class: 'curve-label', 'text-anchor': 'middle' }, profileCurveSvg).textContent = timeLabel(point);
      });
    }

    function drawCurveSeries(values, xScale, yScale, className, label, timeline, element, metric, pfValues, opfValues) {
      const cleanPath = curvePath(values, xScale, yScale);
      if (!cleanPath) return;
      add('path', { d: cleanPath, class: className, 'aria-label': label }, profileCurveSvg);
      values.forEach((value, index) => {
        if (!isFiniteValue(value)) return;
        add('circle', {
          cx: xScale(index),
          cy: yScale(value),
          r: 3.5,
          class: `curve-point ${className.includes('curve-opf') ? 'curve-opf' : 'curve-pf'}`
        }, profileCurveSvg);
        const hit = add('circle', {
          cx: xScale(index),
          cy: yScale(value),
          r: 10,
          class: 'curve-hit-point'
        }, profileCurveSvg);
        hit.addEventListener('mouseenter', event => showCurveTooltip(event, {
          element,
          metric,
          point: timeline[index],
          pf: pfValues[index],
          opf: opfValues[index]
        }));
        hit.addEventListener('mousemove', moveCurveTooltip);
        hit.addEventListener('mouseleave', hideCurveTooltip);
      });
    }

    function curvePath(values, xScale, yScale) {
      let d = '';
      let openSegment = false;
      values.forEach((value, index) => {
        if (!isFiniteValue(value)) {
          openSegment = false;
          return;
        }
        d += `${openSegment ? ' L' : ' M'} ${xScale(index)} ${yScale(value)}`;
        openSegment = true;
      });
      return d.trim();
    }

    function drawSelectedTimestampMarker(chart, timeline, xScale) {
      const selectedIndex = currentTimestampSelectionIndex();
      if (selectedIndex === null || selectedIndex < 0 || selectedIndex >= timeline.length) return;
      const x = xScale(selectedIndex);
      add('line', {
        x1: x,
        y1: chart.top,
        x2: x,
        y2: chart.height - chart.bottom,
        class: 'curve-marker'
      }, profileCurveSvg);
      add('text', {
        x: Math.min(chart.width - chart.right - 20, x + 8),
        y: chart.top + 14,
        class: 'curve-label',
        'text-anchor': 'start'
      }, profileCurveSvg).textContent = `selected ${timeLabel(timeline[selectedIndex])}`;
    }

    function currentTimestampSelectionIndex() {
      const points = RAW_DATA.time_points || [];
      if (!points.length) return null;
      const select = document.getElementById('timestampSelect');
      return Number(select.value || 0);
    }

    function isFiniteValue(value) {
      return value !== null && value !== undefined && Number.isFinite(Number(value));
    }

    function comparableSeriesEqual(left, right) {
      if (!left.some(isFiniteValue) || !right.some(isFiniteValue)) return false;
      if (left.length !== right.length) return false;
      for (let index = 0; index < left.length; index += 1) {
        const a = left[index];
        const b = right[index];
        if (!isFiniteValue(a) && !isFiniteValue(b)) continue;
        if (!isFiniteValue(a) || !isFiniteValue(b)) return false;
        if (Math.abs(Number(a) - Number(b)) > 1e-6) return false;
      }
      return true;
    }

    function profileCurveExplanation(element, metric, pfValues, opfValues) {
      if (!opfValues.some(isFiniteValue)) {
        return 'PF values are the profile-applied snapshot results. OPF values are unavailable because OPF was not run or did not solve for this element.';
      }
      if (comparableSeriesEqual(pfValues, opfValues)) {
        if (element.table_name === 'dc_generators' && !(DATA.meta.enabled_opf_controls || '').includes('DC generator curtailment')) {
          return 'PF and OPF overlap because DC generator curtailment is not enabled in this run. Enable `dc_gen_curtailment` to let OPF reduce available PV generation.';
        }
        return 'PF and OPF overlap for this quantity; OPF either did not control this element or chose the same value.';
      }
      if (element.table_name === 'dc_generators') {
        return 'Blue is the available/profiled DC generation from PF. Orange is the Tool1 (acdcopf) DC generation after curtailment or redispatch limits.';
      }
      if (element.table_name === 'storage_units') {
        return 'Blue is the PF/reference battery value. Orange is the Tool1 (acdcopf) storage dispatch with linked SOC constraints across timestamps.';
      }
      if (element.table_name === 'losses') {
        return 'Blue is the total ACDCPF PF active loss. Orange is the total Tool1 (acdcopf) active loss for the same timestamp.';
      }
      return 'Blue is the profile-applied PF value. Orange is the Tool1 (acdcopf) result for the same timestamp.';
    }

    function showCurveTooltip(event, data) {
      const delta = isFiniteValue(data.pf) && isFiniteValue(data.opf) ? Number(data.opf) - Number(data.pf) : null;
      const deltaPct = isFiniteValue(delta) && Number(data.pf) !== 0 ? 100 * delta / Math.abs(Number(data.pf)) : null;
      profileCurveTooltip.innerHTML = `
        <div class="tooltip-title">${esc(data.element.name)}</div>
        <div>${esc(data.point.timestamp || `t=${data.point.time_index}`)}</div>
        <div>${esc(metricLabels[data.metric] || data.metric)}</div>
        <div>PF: <strong>${fmt(data.pf)}</strong></div>
        <div>Tool1 (acdcopf): <strong>${fmt(data.opf)}</strong></div>
        <div>Delta: <strong>${fmt(delta)}</strong> (${fmtPercent(deltaPct)})</div>
      `;
      profileCurveTooltip.style.display = 'block';
      moveCurveTooltip(event);
    }

    function moveCurveTooltip(event) {
      profileCurveTooltip.style.left = `${event.clientX + 14}px`;
      profileCurveTooltip.style.top = `${event.clientY + 14}px`;
    }

    function hideCurveTooltip() {
      profileCurveTooltip.style.display = 'none';
    }

    function timeLabel(point) {
      const text = String(point.timestamp || point.time_index);
      const match = text.match(/T([0-9]{2}:[0-9]{2})/);
      return match ? match[1] : `t=${point.time_index}`;
    }

    function fmtAxis(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) return 'n/a';
      if (Math.abs(number) >= 100) return number.toFixed(0);
      if (Math.abs(number) >= 10) return number.toFixed(1);
      return number.toFixed(2);
    }

    function allElements() {
      const groups = DATA.elements;
      return [
        ...groups.ac_buses,
        ...groups.dc_buses,
        ...groups.ac_branches,
        ...groups.transformers,
        ...groups.dc_branches,
        ...groups.dcdc_converters,
        ...groups.converters,
        ...groups.generators,
        ...groups.dc_generators,
        ...groups.loads,
        ...groups.dc_loads,
        ...groups.storage_units
      ];
    }

    function elementByUid(uid) {
      return allElements().find(item => item.uid === uid);
    }

    function fmt(value, digits = 4) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return 'n/a';
      const number = Number(value);
      if (Math.abs(number) >= 1000 || (Math.abs(number) > 0 && Math.abs(number) < 0.0001)) {
        return number.toExponential(4);
      }
      return number.toFixed(digits);
    }

    function fmtPercent(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return 'n/a';
      return `${Number(value).toFixed(3)}%`;
    }

    function percentChange(value, baseline) {
      if (value === null || value === undefined || baseline === null || baseline === undefined) return null;
      const base = Number(baseline);
      if (!Number.isFinite(base) || base === 0) return null;
      return 100 * (Number(value) - base) / Math.abs(base);
    }

    function esc(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }

    function metricsFor(element, mode = state.mode) {
      if (mode === 'delta') {
        const result = {};
        const keys = new Set([...Object.keys(element.pf || {}), ...Object.keys(element.opf || {})]);
        for (const key of keys) {
          const pf = element.pf?.[key];
          const opf = element.opf?.[key];
          if (pf !== undefined && opf !== undefined) result[key] = Number(opf) - Number(pf);
        }
        return result;
      }
      return element[mode] || {};
    }

    function elementVoltage(element) {
      const metrics = metricsFor(element, state.mode === 'delta' ? 'opf' : state.mode);
      return metrics.v_pu;
    }

    function voltageColor(value) {
      if (value === undefined || value === null) return '#ffffff';
      const v = Number(value);
      if (v < 0.95 || v > 1.05) return '#ffe4e6';
      if (v < 0.98 || v > 1.02) return '#fff3d6';
      return '#e8f7ee';
    }

    function edgeWidth(element) {
      const metrics = metricsFor(element, state.mode === 'delta' ? 'opf' : state.mode);
      const candidates = [metrics.p_from_mw, metrics.p_to_mw, metrics.p_ac_mw, metrics.p_dc_mw]
        .filter(value => value !== undefined)
        .map(value => Math.abs(Number(value)));
      const flow = candidates.length ? Math.max(...candidates) : 0;
      return Math.max(2, Math.min(9, 2 + flow / 25));
    }

    function computeLayout() {
      const positions = {};
      const acBuses = DATA.elements.ac_buses;
      const dcBuses = DATA.elements.dc_buses;
      spread(acBuses, 'ac', 165, positions);
      spread(dcBuses, 'dc', 470, positions);

      for (const converter of DATA.elements.converters) {
        const ac = positions[`ac:${converter.ac_bus}`];
        const dc = positions[`dc:${converter.dc_bus}`];
        if (ac && dc) {
          positions[converter.uid] = { x: (ac.x + dc.x) / 2, y: (ac.y + dc.y) / 2 };
        }
      }

      for (const generator of DATA.elements.generators) {
        const bus = positions[`ac:${generator.bus}`];
        if (bus) positions[generator.uid] = { x: bus.x - 34, y: bus.y - 82 };
      }
      for (const generator of DATA.elements.dc_generators) {
        const bus = positions[`dc:${generator.bus}`];
        if (bus) positions[generator.uid] = { x: bus.x - 34, y: bus.y + 82 };
      }
      for (const load of DATA.elements.loads) {
        const bus = positions[`ac:${load.bus}`];
        if (bus) positions[load.uid] = { x: bus.x + 34, y: bus.y + 82 };
      }
      for (const load of DATA.elements.dc_loads) {
        const bus = positions[`dc:${load.bus}`];
        if (bus) positions[load.uid] = { x: bus.x + 34, y: bus.y + 82 };
      }
      for (const storage of DATA.elements.storage_units) {
        const busPrefix = storage.bus_kind || 'dc';
        const bus = positions[`${busPrefix}:${storage.bus}`];
        if (bus) positions[storage.uid] = { x: bus.x + 82, y: bus.y + (busPrefix === 'dc' ? 82 : -82) };
      }
      return positions;
    }

    function spread(items, prefix, y, positions) {
      const margin = 120;
      const width = 1200 - 2 * margin;
      const count = Math.max(items.length, 1);
      items.forEach((item, index) => {
        const x = count === 1 ? 600 : margin + (width * index) / (count - 1);
        positions[`${prefix}:${item.id}`] = { x, y };
      });
    }

    function add(tag, attrs = {}, parent = svg) {
      const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [key, value] of Object.entries(attrs)) {
        if (value !== undefined && value !== null) node.setAttribute(key, value);
      }
      parent.appendChild(node);
      return node;
    }

    function bind(node, element) {
      node.addEventListener('mouseenter', event => showTooltip(event, element));
      node.addEventListener('mousemove', moveTooltip);
      node.addEventListener('mouseleave', hideTooltip);
      node.addEventListener('click', () => {
        state.selected = element.uid;
        renderDetails(element);
      });
    }

    function draw() {
      svg.replaceChildren();
      const positions = computeLayout();
      const occupiedLabelBoxes = schematicObstacleBoxes(positions);
      drawBranches(DATA.elements.ac_branches, positions, 'ac', occupiedLabelBoxes);
      drawBranches(DATA.elements.transformers, positions, 'ac', occupiedLabelBoxes);
      drawBranches(DATA.elements.dc_branches, positions, 'dc', occupiedLabelBoxes);
      drawBranches(DATA.elements.dcdc_converters, positions, 'dcdc', occupiedLabelBoxes);
      drawConverterLinks(positions);
      drawAttachedEquipmentLinks(DATA.elements.generators, positions);
      drawAttachedEquipmentLinks(DATA.elements.dc_generators, positions);
      drawAttachedEquipmentLinks(DATA.elements.loads, positions);
      drawAttachedEquipmentLinks(DATA.elements.dc_loads, positions);
      drawAttachedEquipmentLinks(DATA.elements.storage_units, positions);
      drawBuses(DATA.elements.ac_buses, positions, 'ac');
      drawBuses(DATA.elements.dc_buses, positions, 'dc');
      drawConverters(positions);
      drawMarkers(DATA.elements.generators, positions, 'generator');
      drawMarkers(DATA.elements.dc_generators, positions, 'generator');
      drawMarkers(DATA.elements.loads, positions, 'load');
      drawMarkers(DATA.elements.dc_loads, positions, 'load');
      drawMarkers(DATA.elements.storage_units, positions, 'storage');
    }

    function schematicObstacleBoxes(positions) {
      const boxes = [];
      for (const bus of DATA.elements.ac_buses) addObstacleBox(boxes, positions[`ac:${bus.id}`], 34, 52);
      for (const bus of DATA.elements.dc_buses) addObstacleBox(boxes, positions[`dc:${bus.id}`], 34, 52);
      for (const converter of DATA.elements.converters) addObstacleBox(boxes, positions[converter.uid], 48, 36);
      for (const generator of DATA.elements.generators) addObstacleBox(boxes, positions[generator.uid], 42, 34);
      for (const generator of DATA.elements.dc_generators) addObstacleBox(boxes, positions[generator.uid], 42, 34);
      for (const load of DATA.elements.loads) addObstacleBox(boxes, positions[load.uid], 42, 34);
      for (const load of DATA.elements.dc_loads) addObstacleBox(boxes, positions[load.uid], 42, 34);
      for (const storage of DATA.elements.storage_units) addObstacleBox(boxes, positions[storage.uid], 46, 34);
      return boxes;
    }

    function addObstacleBox(boxes, pos, halfWidth, halfHeight) {
      if (!pos) return;
      boxes.push({
        x1: pos.x - halfWidth,
        y1: pos.y - halfHeight,
        x2: pos.x + halfWidth,
        y2: pos.y + halfHeight
      });
    }

    function drawBranches(branches, positions, type, occupiedLabelBoxes) {
      const prefix = type === 'ac' ? 'ac' : 'dc';
      for (const branch of branches) {
        const from = positions[`${prefix}:${branch.from_bus}`];
        const to = positions[`${prefix}:${branch.to_bus}`];
        if (!from || !to) continue;
        const branchPath = branchPathData(from, to, branch, type);
        const path = add('path', {
          d: branchPath.d,
          class: `edge ${type === 'dcdc' ? 'dc' : type}-edge`,
          'stroke-width': edgeWidth(branch)
        });
        bind(path, branch);
        if (state.showBranchLabels) {
          placeBranchLabel(branch, branchPath.label, occupiedLabelBoxes);
        }
      }
    }

    function placeBranchLabel(branch, anchor, occupiedLabelBoxes) {
      const text = String(branch.name || branch.id);
      const width = Math.max(58, text.length * 7 + 18);
      const height = 22;
      const candidates = labelCandidates(anchor, width, height);
      let best = candidates[0];
      let bestScore = Number.POSITIVE_INFINITY;
      for (const candidate of candidates) {
        const score = labelScore(candidate, occupiedLabelBoxes);
        if (score < bestScore) {
          best = candidate;
          bestScore = score;
        }
      }
      occupiedLabelBoxes.push(best.box);
      const group = add('g', { class: 'branch-label' });
      add('rect', {
        x: best.box.x1,
        y: best.box.y1,
        width,
        height,
        class: 'branch-label-badge'
      }, group);
      add('text', {
        x: best.x,
        y: best.y + 4,
        class: 'branch-label-text',
        'text-anchor': 'middle'
      }, group).textContent = text;
    }

    function labelCandidates(anchor, width, height) {
      const offsets = [
        [0, 0], [0, -28], [0, 28], [44, 0], [-44, 0],
        [44, -28], [-44, -28], [44, 28], [-44, 28],
        [0, -56], [0, 56]
      ];
      return offsets.map(([dx, dy]) => {
        const x = anchor.x + dx;
        const y = anchor.y + dy;
        return {
          x,
          y,
          distance: Math.hypot(dx, dy),
          box: {
            x1: x - width / 2,
            y1: y - height / 2,
            x2: x + width / 2,
            y2: y + height / 2
          }
        };
      });
    }

    function labelScore(candidate, occupiedBoxes) {
      const canvasPenalty =
        outsidePenalty(candidate.box.x1, 18, 1200 - 18) +
        outsidePenalty(candidate.box.x2, 18, 1200 - 18) +
        outsidePenalty(candidate.box.y1, 18, 720 - 18) +
        outsidePenalty(candidate.box.y2, 18, 720 - 18);
      const overlapPenalty = occupiedBoxes.reduce(
        (total, box) => total + overlapArea(candidate.box, box),
        0
      );
      return overlapPenalty * 50 + canvasPenalty * 20 + candidate.distance;
    }

    function outsidePenalty(value, min, max) {
      if (value < min) return min - value;
      if (value > max) return value - max;
      return 0;
    }

    function overlapArea(a, b) {
      const width = Math.max(0, Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1));
      const height = Math.max(0, Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1));
      return width * height;
    }

    function branchPathData(from, to, branch, type) {
      const dx = to.x - from.x;
      const dy = to.y - from.y;
      const length = Math.max(Math.hypot(dx, dy), 1);
      const mid = { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
      const nx = -dy / length;
      const ny = dx / length;
      const span = Math.abs(Number(branch.to_bus) - Number(branch.from_bus));
      const shouldCurve = span > 1;
      if (!shouldCurve) {
        const offset = type === 'dc' ? -24 : -28;
        return {
          d: `M ${from.x} ${from.y} L ${to.x} ${to.y}`,
          label: { x: mid.x + nx * offset, y: mid.y + ny * offset }
        };
      }

      let curve = 70 + 18 * Math.max(0, span - 2);
      if (mid.y + ny * curve > mid.y) curve = -curve;
      const control = { x: mid.x + nx * curve, y: mid.y + ny * curve };
      return {
        d: `M ${from.x} ${from.y} Q ${control.x} ${control.y} ${to.x} ${to.y}`,
        label: { x: control.x, y: control.y - 14 }
      };
    }

    function drawConverterLinks(positions) {
      for (const converter of DATA.elements.converters) {
        const pos = positions[converter.uid];
        const ac = positions[`ac:${converter.ac_bus}`];
        const dc = positions[`dc:${converter.dc_bus}`];
        if (pos && ac) add('line', { x1: ac.x, y1: ac.y, x2: pos.x, y2: pos.y, class: 'terminal-edge' });
        if (pos && dc) add('line', { x1: dc.x, y1: dc.y, x2: pos.x, y2: pos.y, class: 'terminal-edge' });
      }
    }

    function drawAttachedEquipmentLinks(items, positions) {
      for (const item of items) {
        const pos = positions[item.uid];
        const busPrefix = item.bus_kind || 'ac';
        const bus = positions[`${busPrefix}:${item.bus}`];
        if (!pos || !bus) continue;
        const line = add('line', {
          x1: bus.x, y1: bus.y, x2: pos.x, y2: pos.y,
          class: 'equipment-edge'
        });
        bind(line, item);
      }
    }

    function drawBuses(buses, positions, type) {
      for (const bus of buses) {
        const pos = positions[`${type}:${bus.id}`];
        if (!pos) continue;
        const node = add('circle', {
          cx: pos.x, cy: pos.y, r: 21,
          class: `node ${type}`,
          fill: voltageColor(elementVoltage(bus))
        });
        bind(node, bus);
        add('text', {
          x: pos.x,
          y: pos.y + 43,
          class: 'label',
          'text-anchor': 'middle'
        }).textContent = bus.name;
      }
    }

    function drawConverters(positions) {
      for (const converter of DATA.elements.converters) {
        const pos = positions[converter.uid];
        if (!pos) continue;
        const box = add('rect', {
          x: pos.x - 36, y: pos.y - 25, width: 72, height: 50, rx: 10, ry: 10,
          class: `vsc ${converter.controlled ? 'controlled' : ''}`
        });
        bind(box, converter);
        add('text', {
          x: pos.x,
          y: pos.y + 5,
          class: 'label',
          'text-anchor': 'middle'
        }).textContent = converter.name;
      }
    }

    function drawMarkers(items, positions, type) {
      for (const item of items) {
        const pos = positions[item.uid];
        if (!pos) continue;
        const marker = add('circle', {
          cx: pos.x, cy: pos.y, r: 15,
          class: `marker ${type}`
        });
        bind(marker, item);
        add('text', {
          x: pos.x,
          y: pos.y + (type === 'generator' ? -25 : 31),
          class: 'line-label',
          'text-anchor': 'middle'
        }).textContent = item.name;
      }
    }

    function metricTable(element) {
      const sections = [
        comparisonTable(
          element,
          'Tool1 (acdcopf) objective',
          'pf',
          'opf',
          'ACDCPF PF',
          'TOOL1'
        ),
        comparisonTable(
          element,
          'PyFlow reference',
          'pyflow_pf',
          'pyflow_opf',
          'PyFlow PF',
          'PyFlow OPF'
        )
      ].filter(Boolean);
      if (!sections.length) return '<p class="small">No numeric values reported for this element.</p>';
      return sections.join('');
    }

    function comparisonTable(element, title, beforeKey, afterKey, beforeLabel, afterLabel) {
      const beforeMetrics = element[beforeKey] || {};
      const afterMetrics = element[afterKey] || {};
      const keys = [...new Set([...Object.keys(beforeMetrics), ...Object.keys(afterMetrics)])];
      if (!keys.length) return '';
      const rows = keys.map(key => {
        const before = beforeMetrics[key];
        const after = afterMetrics[key];
        const delta = before !== undefined && after !== undefined ? Number(after) - Number(before) : null;
        const deltaPercent = percentChange(after, before);
        return `<tr><td>${esc(metricLabels[key] || key)}</td><td>${fmt(before)}</td><td>${fmt(after)}</td><td>${fmt(delta)}</td><td>${fmtPercent(deltaPercent)}</td></tr>`;
      }).join('');
      return `
        <h3>${esc(title)}</h3>
        <table>
          <thead>
            <tr><th>Metric</th><th>${esc(beforeLabel)}</th><th>${esc(afterLabel)}</th><th>Delta</th><th>Delta % vs PF</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    }

    function extraFacts(element) {
      const facts = [];
      if (element.kind === 'converter') {
        facts.push(['AC bus', element.ac_bus], ['DC bus', element.dc_bus], ['Control mode', element.control_mode || 'n/a'], ['Controlled by TOOL1', element.controlled ? 'yes' : 'no']);
      }
      if (element.kind === 'ac_branch' || element.kind === 'dc_branch') {
        facts.push(['From bus', element.from_bus], ['To bus', element.to_bus]);
      }
      if (element.kind === 'transformer') {
        facts.push(['From AC bus', element.from_bus], ['To AC bus', element.to_bus], ['Tap', element.tap ?? 'n/a']);
      }
      if (element.kind === 'dcdc_converter') {
        facts.push(['From DC bus', element.from_bus], ['To DC bus', element.to_bus]);
      }
      if (element.kind === 'generator' || element.kind === 'load' || element.kind === 'dc_generator' || element.kind === 'dc_load') {
        facts.push([element.bus_kind === 'dc' ? 'DC bus' : 'AC bus', element.bus]);
      }
      if (element.kind === 'storage') {
        facts.push([element.bus_kind === 'dc' ? 'DC bus' : 'AC bus', element.bus], ['S_nom MVA', element.s_mva ?? 'n/a']);
      }
      if (!facts.length) return '';
      return '<div class="pill-row">' + facts.map(([label, value]) => `<span class="pill">${esc(label)}: ${esc(value ?? 'n/a')}</span>`).join('') + '</div>';
    }

    function renderDetails(element) {
      detailsPanel.innerHTML = `
        <h2>${esc(element.name)}</h2>
        <p class="small">${esc(kindLabel(element.kind))} - ID ${esc(element.id)}</p>
        ${extraFacts(element)}
        ${metricTable(element)}
      `;
    }

    function kindLabel(kind) {
      return {
        ac_bus: 'AC bus',
        dc_bus: 'DC bus',
        ac_branch: 'AC branch',
        transformer: 'Transformer',
        dc_branch: 'DC branch',
        dcdc_converter: 'DCDC converter',
        converter: 'Voltage-source converter',
        generator: 'Synchronous generator',
        dc_generator: 'DC generator',
        load: 'Load',
        dc_load: 'DC load',
        storage: 'Storage/BESS'
      }[kind] || kind;
    }

    function showTooltip(event, element) {
      const metrics = metricsFor(element);
      const keys = Object.keys(metrics).slice(0, 4);
      tooltip.innerHTML = `
        <div class="tooltip-title">${esc(element.name)}</div>
        <div>${esc(kindLabel(element.kind))}</div>
        ${keys.map(key => `<div>${esc(metricLabels[key] || key)}: <strong>${fmt(metrics[key])}</strong></div>`).join('')}
      `;
      tooltip.style.display = 'block';
      moveTooltip(event);
      renderDetails(element);
    }

    function moveTooltip(event) {
      tooltip.style.left = `${event.clientX + 14}px`;
      tooltip.style.top = `${event.clientY + 14}px`;
    }

    function hideTooltip() {
      tooltip.style.display = 'none';
      if (state.selected) {
        const selected = elementByUid(state.selected);
        if (selected) renderDetails(selected);
      }
    }

    function renderSummary() {
      const summary = DATA.summary;
      const cards = [
        ['ACDCPF PF loss', `${fmt(summary.pf_total_loss_mw)} MW`],
        ['Tool1 (acdcopf) loss', `${fmt(summary.opf_total_loss_mw)} MW`],
        ['Tool1 (acdcopf) change', `${fmt(summary.loss_delta_mw)} MW`],
        ['Tool1 (acdcopf) change', fmtPercent(summary.loss_delta_percent)],
        ['PyFlow PF loss', `${fmt(summary.pyflow_pf_total_loss_mw)} MW`],
        ['PyFlow OPF loss', `${fmt(summary.pyflow_opf_total_loss_mw)} MW`],
        ['PyFlow change', `${fmt(summary.pyflow_loss_delta_mw)} MW`],
        ['PyFlow change', fmtPercent(summary.pyflow_loss_delta_percent)]
      ];
      document.getElementById('summaryCards').innerHTML = cards.map(([label, value]) => `
        <div class="metric-card">
          <div class="metric-label">${esc(label)}</div>
          <div class="metric-value">${esc(value)}</div>
        </div>
      `).join('');
    }

    function firstElement() {
      return DATA.elements.converters[0] || DATA.elements.ac_buses[0] || allElements()[0];
    }

    function renderCurrentSelection() {
      const selected = state.selected ? elementByUid(state.selected) : null;
      const element = selected || firstElement();
      if (!element) return;
      state.selected = element.uid;
      renderDetails(element);
    }

    function refreshView() {
      const snapshotText = DATA.meta.timestamp !== undefined
        ? ` Snapshot t=${DATA.meta.time_index} (${DATA.meta.timestamp}).`
        : '';
      document.getElementById('subtitle').textContent =
        `${DATA.meta.case} - scenario: ${DATA.meta.scenario}. ${DATA.meta.source}. `
        + `OPF controls: ${DATA.meta.enabled_opf_controls} `
        + `(scope: ${DATA.meta.control_device_scope}).${snapshotText} Generated ${DATA.meta.generated_at}.`;
      renderSummary();
      draw();
      renderCurrentSelection();
    }

    function initialize() {
      setupTimestampSelector();
      setupProfileCurveControls();
      refreshView();
      document.querySelectorAll('button[data-mode]').forEach(button => {
        button.addEventListener('click', () => {
          state.mode = button.dataset.mode;
          document.querySelectorAll('button[data-mode]').forEach(item => item.classList.remove('active'));
          button.classList.add('active');
          draw();
          renderCurrentSelection();
        });
      });
      document.getElementById('branchLabelsButton').addEventListener('click', event => {
        state.showBranchLabels = !state.showBranchLabels;
        event.currentTarget.classList.toggle('active', state.showBranchLabels);
        event.currentTarget.textContent = state.showBranchLabels
          ? 'Branch names: shown'
          : 'Branch names: hidden';
        draw();
      });
    }

    initialize();
  </script>
</body>
</html>
"""
    return html.replace("__GRID_DATA_JSON__", payload)


def _csv_report_sections(
    runs: list[BenchmarkRun],
    *,
    generated_at: str,
    matacdc_validation: dict[str, Any] | None,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
    include_pyflow_reference: bool = False,
    include_detailed_export: bool = False,
) -> list[ReportSection]:
    return _report_sections(
        runs,
        generated_at=generated_at,
        matacdc_validation=matacdc_validation,
        vsc_setpoint_scenario=vsc_setpoint_scenario,
        grid_case=grid_case,
        control_margin_percent=control_margin_percent,
        opf_control_selection=opf_control_selection,
        control_device_scope=control_device_scope,
        include_pyflow_reference=include_pyflow_reference,
        include_detailed_export=include_detailed_export,
    )


def _report_sections(
    runs: list[BenchmarkRun],
    *,
    generated_at: str,
    matacdc_validation: dict[str, Any] | None,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
    include_pyflow_reference: bool = False,
    include_detailed_export: bool = False,
) -> list[ReportSection]:
    selected_case = get_stagg5_grid_case(grid_case)
    scenario = _coerce_vsc_setpoint_scenario(vsc_setpoint_scenario, selected_case)
    controls = opf_control_selection or _benchmark_opf_control_selection(selected_case)
    base_row = _base_time_series_row(generated_at, scenario, selected_case)
    export_runs = _export_runs(runs, include_pyflow_reference=include_pyflow_reference)
    write_detailed_sections = include_detailed_export or include_pyflow_reference
    sections = [
        _time_series_section(
            "metadata",
            "Benchmark case, scenario, and control-scope metadata. One row is written per timestamp.",
            _metadata_time_series_row(
                generated_at,
                scenario,
                selected_case,
                control_margin_percent=control_margin_percent,
                opf_control_selection=controls,
                control_device_scope=control_device_scope,
                include_pyflow_reference=include_pyflow_reference,
            ),
        ),
        _time_series_section(
            "pf_output",
            "ACDCPF baseline power-flow output. One row is written per timestamp.",
            _pf_output_time_series_row(runs, base_row),
        ),
        _time_series_section(
            "opf_main_values",
            "Main Tool1 (acdcopf) optimized values and changes from PF. One row is written per timestamp.",
            _opf_main_values_time_series_row(runs, base_row),
        ),
    ]
    sections.extend(_pandapower_style_sections(export_runs, base_row))
    if write_detailed_sections:
        sections.extend(
            [
                _time_series_section(
                    "run_status",
                    "Solver status and messages. One row is written per timestamp.",
                    _run_status_time_series_row(export_runs, base_row),
                ),
                _time_series_section(
                    "loss_summary",
                    "Active-loss breakdown for every run. One row is written per timestamp.",
                    _loss_summary_time_series_row(export_runs, base_row),
                ),
                _time_series_section(
                    "pf_opf_loss_changes",
                    "PF-to-OPF loss changes. One row is written per timestamp.",
                    _opf_loss_change_time_series_row(
                        runs,
                        base_row,
                        include_pyflow_reference=include_pyflow_reference,
                    ),
                ),
                _time_series_section(
                    "optimized_vsc_controls",
                    "VSC P/Q controls before and after OPF, including deltas. One row is written per timestamp.",
                    _vsc_control_change_time_series_row(
                        runs,
                        base_row,
                        include_pyflow_reference=include_pyflow_reference,
                    ),
                ),
                _time_series_section(
                    "vsc_starting_setpoints",
                    "Starting VSC setpoints applied to the benchmark. One row is written per timestamp.",
                    _vsc_starting_setpoint_time_series_row(scenario, base_row),
                ),
                _time_series_section(
                    "voltage_movement",
                    "Maximum voltage movement from PF baseline to OPF result. One row is written per timestamp.",
                    _voltage_movement_time_series_row(
                        runs,
                        base_row,
                        include_pyflow_reference=include_pyflow_reference,
                    ),
                ),
                _time_series_section(
                    "ac_bus_voltages",
                    "AC bus voltage magnitudes and angles for each run. One row is written per timestamp.",
                    _table_time_series_row(
                        export_runs,
                        base_row,
                        "ac_buses",
                        ["v_pu", "angle_deg"],
                    ),
                ),
                _time_series_section(
                    "dc_bus_voltages",
                    "DC bus voltage magnitudes for each run. One row is written per timestamp.",
                    _table_time_series_row(export_runs, base_row, "dc_buses", ["v_pu"]),
                ),
                _time_series_section(
                    "generator_outputs",
                    "Generator active and reactive outputs for each run. One row is written per timestamp.",
                    _table_time_series_row(
                        export_runs,
                        base_row,
                        "generators",
                        ["p_mw", "q_mvar"],
                    ),
                ),
                _time_series_section(
                    "dc_generator_outputs",
                    "DC generator active outputs for each run. One row is written per timestamp.",
                    _table_time_series_row(
                        export_runs,
                        base_row,
                        "dc_generators",
                        ["p_mw"],
                    ),
                ),
                _time_series_section(
                    "dc_load_outputs",
                    "DC load active powers for each run. One row is written per timestamp.",
                    _table_time_series_row(
                        export_runs,
                        base_row,
                        "dc_loads",
                        ["p_mw"],
                    ),
                ),
                _time_series_section(
                    "storage_outputs",
                    (
                        "Storage active/reactive powers for each run. Positive P means "
                        "discharging; negative P means charging. One row is written per timestamp."
                    ),
                    _table_time_series_row(
                        export_runs,
                        base_row,
                        "storage_units",
                        ["p_mw", "q_mvar", "loading_percent"],
                    ),
                ),
                _time_series_section(
                    "converter_outputs",
                    "Converter active/reactive powers and losses for each run. One row is written per timestamp.",
                    _table_time_series_row(
                        export_runs,
                        base_row,
                        "converters",
                        ["p_ac_mw", "q_ac_mvar", "p_dc_mw", "p_loss_mw"],
                    ),
                ),
                _time_series_section(
                    "dcdc_converter_outputs",
                    "DCDC converter terminal powers, losses, and voltage ratios for each run. One row is written per timestamp.",
                    _table_time_series_row(
                        export_runs,
                        base_row,
                        "dcdc_converters",
                        ["p_from_mw", "p_to_mw", "p_loss_mw", "d_ratio", "rate_mw", "loading_percent"],
                    ),
                ),
                _time_series_section(
                    "transformer_flows",
                    "Transformer terminal powers, losses, loading, tap, and phase shift for each run. One row is written per timestamp.",
                    _table_time_series_row(
                        export_runs,
                        base_row,
                        "transformers",
                        [
                            "p_from_mw",
                            "q_from_mvar",
                            "p_to_mw",
                            "q_to_mvar",
                            "p_loss_mw",
                            "loading_percent",
                            "tap",
                            "shift_deg",
                        ],
                    ),
                ),
                _time_series_section(
                    "ac_branch_flows",
                    "AC branch terminal powers and active losses for each run. One row is written per timestamp.",
                    _table_time_series_row(
                        export_runs,
                        base_row,
                        "ac_branches",
                        ["p_from_mw", "q_from_mvar", "p_to_mw", "q_to_mvar", "p_loss_mw"],
                    ),
                ),
                _time_series_section(
                    "dc_branch_flows",
                    "DC branch terminal powers and active losses for each run. One row is written per timestamp.",
                    _table_time_series_row(
                        export_runs,
                        base_row,
                        "dc_branches",
                        ["p_from_mw", "p_to_mw", "p_loss_mw"],
                    ),
                ),
                _time_series_section(
                    "matacdc_reference_checks",
                    "ACDCPF PF comparison against MATACDC reference data when available. One row is written per timestamp.",
                    _matacdc_reference_time_series_row(matacdc_validation or {}, base_row),
                ),
            ]
        )
    if include_pyflow_reference:
        sections.insert(
            6,
            _time_series_section(
                "cross_solver_loss_differences",
                (
                    "ACDCPF and Tool1 (acdcopf) values compared against PyFlow references. "
                    "One row is written per timestamp."
                ),
                _cross_solver_loss_difference_time_series_row(runs, base_row),
            ),
        )
    return sections


def _single_run_result_sections(
    run: BenchmarkRun,
    *,
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None,
    export_kind: str,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
    time_index: int = 0,
    study_timestamp: str | None = None,
    profile_mode: str | None = None,
) -> list[ReportSection]:
    """Return one-run result sections for PF-only or OPF-only exports."""

    selected_case = get_stagg5_grid_case(grid_case)
    scenario = _coerce_vsc_setpoint_scenario(vsc_setpoint_scenario, selected_case)
    controls = opf_control_selection or _benchmark_opf_control_selection(selected_case)
    base_row = _base_time_series_row(
        generated_at,
        scenario,
        selected_case,
        time_index=time_index,
        study_timestamp=study_timestamp,
    )
    return [
        _time_series_section(
            "metadata",
            "Benchmark case, scenario, and selected result-run metadata.",
            _single_run_metadata_row(
                generated_at,
                scenario,
                selected_case,
                run,
                export_kind=export_kind,
                control_margin_percent=control_margin_percent,
                opf_control_selection=controls,
                control_device_scope=control_device_scope,
                time_index=time_index,
                study_timestamp=study_timestamp,
                profile_mode=profile_mode,
            ),
        ),
        _time_series_section(
            "run_status",
            "Solver status and message for this result file.",
            _single_run_status_row(run, base_row),
        ),
        *_pandapower_style_sections([run], base_row),
        *_physics_report_sections(run, base_row),
        *_equipment_limit_sections(run, base_row),
    ]


def _equipment_limit_sections(run: BenchmarkRun, base_row: Mapping[str, Any]) -> list[ReportSection]:
    limits = run.diagnostics.get("equipment_limits", [])
    if not limits:
        return []
    rows = [{**base_row, "run": run.label, **limit} for limit in limits]
    return [ReportSection(
        "equipment_limits", "Source limits and coverage; specified does not certify nameplate provenance or feasibility.",
        [*TIME_SERIES_KEY_FIELDS, "run", *limits[0].keys()], rows,
    )]


def _physics_report_sections(run: BenchmarkRun, base_row: Mapping[str, Any]) -> list[ReportSection]:
    physics = run.diagnostics.get("physics_validation")
    if not physics:
        return []
    fields = ["check", "element", "status", "value", "lower", "upper", "violation", "tolerance", "unit", "note"]
    rows = [{**base_row, "run": run.label, **check} for check in physics["checks"]]
    return [ReportSection(
        "physics_checks", "Source-network checks independent of Pyomo constraints. Missing ratings are not checked.",
        [*TIME_SERIES_KEY_FIELDS, "run", *fields], rows,
    )]


def _single_run_metadata_row(
    generated_at: str,
    scenario: VSCSetpointScenario,
    grid_case: Stagg5GridCase,
    run: BenchmarkRun,
    *,
    export_kind: str,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
    time_index: int = 0,
    study_timestamp: str | None = None,
    profile_mode: str | None = None,
) -> dict[str, Any]:
    row = _base_time_series_row(
        generated_at,
        scenario,
        grid_case,
        time_index=time_index,
        study_timestamp=study_timestamp,
    )
    controls = opf_control_selection or _benchmark_opf_control_selection(grid_case)
    row.update(
        {
            "description": grid_case.description,
            "result_type": "Power Flow" if export_kind == "pf" else "Optimal Power Flow",
            "result_run": run.label,
            "report_generated_at": generated_at,
            "profile_mode": profile_mode,
            "vsc_setpoint_description": scenario.description,
            "enabled_opf_controls": _opf_control_selection_description(controls),
            "fixed_opf_controls": _opf_disabled_control_description(controls),
            "control_device_scope": control_device_scope,
            "control_scope": _opf_control_scope_description(
                grid_case,
                controls,
                control_device_scope=control_device_scope,
            ),
            "control_margin_pct": control_margin_percent,
            "control_margin": _control_margin_description(control_margin_percent),
            "csv_delimiter": CSV_DELIMITER,
        }
    )
    row.update(_custom_import_metadata(grid_case))
    return row


def _custom_import_metadata(grid_case):
    if not grid_case.import_metadata:
        return {}
    import json
    meta = grid_case.import_metadata
    return {
        "import_source_files": json.dumps(meta["source_files"]),
        "import_format": meta["format"],
        "import_loss_units": meta["loss_units"],
        "import_id_maps": json.dumps(meta["id_maps"]),
        "import_report": json.dumps(meta),
    }


def _single_run_status_row(
    run: BenchmarkRun,
    base_row: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(base_row)
    row.update(
        {
            "run": run.label,
            "success": run.success,
            "message": run.message,
            "loss_total_mw": run.losses_mw.get("total_active_losses_mw"),
            "solver_status": run.diagnostics.get("solver_status"),
            "max_pyomo_constraint_residual": run.diagnostics.get("max_pyomo_constraint_residual"),
            "max_mismatch": run.diagnostics.get("max_mismatch"),
            "physics_status": run.diagnostics.get("physics_validation", {}).get("status", "not_checked"),
            "physics_failed": run.diagnostics.get("physics_validation", {}).get("failed"),
            "physics_not_checked": run.diagnostics.get("physics_validation", {}).get("not_checked"),
        }
    )
    return row


def _pandapower_style_sections(
    runs: list[BenchmarkRun],
    base_row: Mapping[str, Any],
) -> list[ReportSection]:
    """Return compact result tables using pandapower's ``res_*`` naming style."""

    sections: list[ReportSection] = []
    for section_name, description, table_name, index_field, field_map in PANDAPOWER_STYLE_SECTIONS:
        rows = _pandapower_style_rows(
            runs,
            base_row,
            table_name=table_name,
            index_field=index_field,
            field_map=field_map,
        )
        fieldnames = [
            *TIME_SERIES_KEY_FIELDS,
            "run",
            index_field,
            *[export_field for _, export_field in field_map],
        ]
        sections.append(ReportSection(section_name, description, fieldnames, rows))
    return sections


def _pandapower_style_rows(
    runs: list[BenchmarkRun],
    base_row: Mapping[str, Any],
    *,
    table_name: str,
    index_field: str,
    field_map: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in runs:
        table_rows = sorted(
            run.tables.get(table_name, []),
            key=lambda item: _natural_key(str(item.get("id", item.get("name", "")))),
        )
        for table_row in table_rows:
            export_row = dict(base_row)
            export_row["run"] = run.label
            export_row[index_field] = table_row.get("id")
            for source_field, export_field in field_map:
                export_row[export_field] = table_row.get(source_field)
            rows.append(export_row)
    return rows


def _export_runs(
    runs: list[BenchmarkRun],
    *,
    include_pyflow_reference: bool,
) -> list[BenchmarkRun]:
    if include_pyflow_reference:
        return runs
    return [run for run in runs if not _is_pyflow_run(run)]


def _is_pyflow_run(run: BenchmarkRun) -> bool:
    return run.label in {"PyFlow PF", "PyFlow OPF VSC-only"}


def _metadata_time_series_row(
    generated_at: str,
    scenario: VSCSetpointScenario,
    grid_case: Stagg5GridCase,
    *,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
    include_pyflow_reference: bool,
) -> dict[str, Any]:
    row = _base_time_series_row(generated_at, scenario, grid_case)
    controls = opf_control_selection or _benchmark_opf_control_selection(grid_case)
    row.update(
        {
            "description": grid_case.description,
            "vsc_setpoint_description": scenario.description,
            "enabled_opf_controls": _opf_control_selection_description(controls),
            "fixed_opf_controls": _opf_disabled_control_description(controls),
            "control_device_scope": control_device_scope,
            "control_scope": _opf_control_scope_description(
                grid_case,
                controls,
                control_device_scope=control_device_scope,
            ),
            "control_margin_pct": control_margin_percent,
            "control_margin": _control_margin_description(control_margin_percent),
            "row_granularity": (
                "Each data section uses one row per timestamp; this benchmark writes time_index 0."
            ),
            "csv_delimiter": CSV_DELIMITER,
        }
    )
    row.update(_custom_import_metadata(grid_case))
    return row


def _pf_output_time_series_row(
    runs: list[BenchmarkRun],
    base_row: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(base_row)
    pf = _run_by_label(runs, "ACDCPF PF")
    row["pf_success"] = pf.success if pf is not None else None
    row["pf_message"] = pf.message if pf is not None else ""
    _add_loss_columns(row, "pf", pf)
    _add_table_columns(
        row,
        pf,
        prefix="pf",
        table_name="ac_buses",
        element_prefix="ac",
        fields={"v_pu": "v_pu", "angle_deg": "ang_deg"},
    )
    _add_table_columns(
        row,
        pf,
        prefix="pf",
        table_name="dc_buses",
        element_prefix="dc",
        fields={"v_pu": "v_pu"},
    )
    _add_table_columns(
        row,
        pf,
        prefix="pf",
        table_name="generators",
        element_prefix="gen",
        fields={"p_mw": "p_mw", "q_mvar": "q_mvar"},
    )
    _add_table_columns(
        row,
        pf,
        prefix="pf",
        table_name="converters",
        element_prefix="vsc",
        fields={
            "p_ac_mw": "pac_mw",
            "q_ac_mvar": "qac_mvar",
            "p_dc_mw": "pdc_mw",
            "p_loss_mw": "loss_mw",
        },
    )
    _add_table_columns(
        row,
        pf,
        prefix="pf",
        table_name="ac_branches",
        element_prefix="acbr",
        fields={
            "p_from_mw": "p_fr_mw",
            "q_from_mvar": "q_fr_mvar",
            "p_to_mw": "p_to_mw",
            "q_to_mvar": "q_to_mvar",
            "p_loss_mw": "loss_mw",
        },
    )
    _add_table_columns(
        row,
        pf,
        prefix="pf",
        table_name="dc_branches",
        element_prefix="dcbr",
        fields={"p_from_mw": "p_fr_mw", "p_to_mw": "p_to_mw", "p_loss_mw": "loss_mw"},
    )
    return row


def _opf_main_values_time_series_row(
    runs: list[BenchmarkRun],
    base_row: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(base_row)
    pf = _run_by_label(runs, "ACDCPF PF")
    opf = _tool1_report_run(runs)
    opf_model = _run_by_label(runs, TOOL1_OBJECTIVE_LABEL)

    _add_loss_columns(row, "pf", pf)
    _add_loss_columns(row, "opf", opf)
    _add_loss_columns(row, "opf_model", opf_model)
    pf_total = _total_loss(pf)
    opf_total = _total_loss(opf)
    row["loss_d_mw"] = _difference(opf_total, pf_total)
    row["loss_d_pct"] = _percent_change(opf_total, pf_total)
    row["ac_v_max_d_pu"] = _max_table_delta(pf, opf, "ac_buses", "v_pu")
    row["ac_ang_max_d_deg"] = _max_table_delta(pf, opf, "ac_buses", "angle_deg")
    row["dc_v_max_d_pu"] = _max_table_delta(pf, opf, "dc_buses", "v_pu")

    for converter_index in _converter_indices_from_runs(pf, opf):
        for source_field, export_field in (("p_ac_mw", "p"), ("q_ac_mvar", "q")):
            pf_value = _table_value(pf, "converters", converter_index, source_field)
            opf_value = _table_value(opf, "converters", converter_index, source_field)
            stem = f"vsc{converter_index}_{export_field}"
            unit = "mw" if export_field == "p" else "mvar"
            row[f"{stem}_pf_{unit}"] = pf_value
            row[f"{stem}_opf_{unit}"] = opf_value
            row[f"{stem}_d_{unit}"] = _difference(opf_value, pf_value)
            row[f"{stem}_d_pct"] = _percent_change(opf_value, pf_value)

    _add_bus_voltage_comparison_columns(row, pf, opf)
    _add_table_columns(
        row,
        opf,
        prefix="opf",
        table_name="converters",
        element_prefix="vsc",
        fields={
            "p_ac_mw": "pac_mw",
            "q_ac_mvar": "qac_mvar",
            "p_dc_mw": "pdc_mw",
            "p_loss_mw": "loss_mw",
        },
    )
    return row


def _add_bus_voltage_comparison_columns(
    row: dict[str, Any],
    pf: BenchmarkRun | None,
    opf: BenchmarkRun | None,
) -> None:
    _add_table_comparison_columns(
        row,
        pf,
        opf,
        table_name="ac_buses",
        element_prefix="ac",
        fields={
            "v_pu": ("v", "pu"),
            "angle_deg": ("ang", "deg"),
        },
    )
    _add_table_comparison_columns(
        row,
        pf,
        opf,
        table_name="dc_buses",
        element_prefix="dc",
        fields={"v_pu": ("v", "pu")},
    )


def _add_table_comparison_columns(
    row: dict[str, Any],
    before: BenchmarkRun | None,
    after: BenchmarkRun | None,
    *,
    table_name: str,
    element_prefix: str,
    fields: Mapping[str, tuple[str, str]],
) -> None:
    before_rows = _table_rows_by_token(before, table_name)
    after_rows = _table_rows_by_token(after, table_name)
    for element_token in sorted(set(before_rows) | set(after_rows), key=_natural_key):
        before_row = before_rows.get(element_token, {})
        after_row = after_rows.get(element_token, {})
        for source_field, (quantity_name, unit) in fields.items():
            pf_value = before_row.get(source_field)
            opf_value = after_row.get(source_field)
            stem = f"{element_prefix}{element_token}_{quantity_name}"
            row[f"{stem}_pf_{unit}"] = pf_value
            row[f"{stem}_opf_{unit}"] = opf_value
            row[f"{stem}_d_{unit}"] = _difference(opf_value, pf_value)


def _table_rows_by_token(
    run: BenchmarkRun | None,
    table_name: str,
) -> dict[str, Mapping[str, Any]]:
    if run is None:
        return {}
    return {_element_token(row): row for row in run.tables.get(table_name, [])}


def _add_loss_columns(row: dict[str, Any], prefix: str, run: BenchmarkRun | None) -> None:
    for _, key in LOSS_EXPORT_QUANTITIES:
        if key == "total_active_losses_mw":
            export_name = "loss_total_mw"
        else:
            export_name = LOSS_EXPORT_ALIASES.get(key, key)
        row[f"{prefix}_{export_name}"] = _loss_value(run, key)


def _add_table_columns(
    row: dict[str, Any],
    run: BenchmarkRun | None,
    *,
    prefix: str,
    table_name: str,
    element_prefix: str,
    fields: Mapping[str, str],
) -> None:
    if run is None:
        return
    table_rows = sorted(
        run.tables.get(table_name, []),
        key=lambda item: _natural_key(str(item.get("id", item.get("name", "")))),
    )
    for table_row in table_rows:
        element_token = _element_token(table_row)
        for source_field, export_field in fields.items():
            if source_field in table_row:
                row[f"{prefix}_{element_prefix}{element_token}_{export_field}"] = table_row[
                    source_field
                ]


def _time_series_section(name: str, description: str, row: dict[str, Any]) -> ReportSection:
    return ReportSection(
        name,
        description,
        _csv_fieldnames([row], preferred_first=TIME_SERIES_KEY_FIELDS),
        [row],
    )


def _base_time_series_row(
    generated_at: str,
    scenario: VSCSetpointScenario,
    grid_case: Stagg5GridCase | str | None = None,
    *,
    time_index: int = 0,
    study_timestamp: str | None = None,
) -> dict[str, Any]:
    selected_case = get_stagg5_grid_case(grid_case)
    return {
        "time_index": time_index,
        "timestamp": study_timestamp or generated_at,
        "case": selected_case.display_name,
        "vsc_setpoint_scenario": scenario.name,
    }


def _run_status_time_series_row(
    runs: list[BenchmarkRun],
    base_row: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(base_row)
    for run in runs:
        prefix = _run_slug(run.label)
        row[f"{prefix}_success"] = run.success
        row[f"{prefix}_message"] = run.message
    return row


def _loss_summary_time_series_row(
    runs: list[BenchmarkRun],
    base_row: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(base_row)
    for run in runs:
        prefix = _run_slug(run.label)
        for _, key in LOSS_EXPORT_QUANTITIES:
            row[f"{prefix}_{key}"] = _loss_value(run, key)
    return row


def _opf_loss_change_time_series_row(
    runs: list[BenchmarkRun],
    base_row: Mapping[str, Any],
    *,
    include_pyflow_reference: bool,
) -> dict[str, Any]:
    row = dict(base_row)
    tool1 = _tool1_report_run(runs)
    comparisons = [("tool1", "ACDCPF PF", tool1.label if tool1 else TOOL1_OBJECTIVE_LABEL)]
    if include_pyflow_reference:
        comparisons.append(("pyflow_opf", "PyFlow PF", "PyFlow OPF VSC-only"))
    for prefix, baseline_label, optimized_label in comparisons:
        baseline = _run_by_label(runs, baseline_label)
        optimized = _run_by_label(runs, optimized_label)
        baseline_total = _total_loss(baseline)
        optimized_total = _total_loss(optimized)
        row[f"{prefix}_pf_baseline_run"] = baseline_label
        row[f"{prefix}_opf_result_run"] = optimized_label
        row[f"{prefix}_pf_baseline_mw"] = baseline_total
        row[f"{prefix}_opf_result_mw"] = optimized_total
        row[f"{prefix}_change_mw"] = _difference(optimized_total, baseline_total)
        row[f"{prefix}_change_percent"] = _percent_change(optimized_total, baseline_total)
    return row


def _cross_solver_loss_difference_time_series_row(
    runs: list[BenchmarkRun],
    base_row: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(base_row)
    tool1 = _tool1_report_run(runs)
    tool1_label = tool1.label if tool1 else TOOL1_OBJECTIVE_LABEL
    for comparison, left_label, right_label in (
        ("PF baseline", "ACDCPF PF", "PyFlow PF"),
        ("Final OPF", tool1_label, "PyFlow OPF VSC-only"),
    ):
        left = _run_by_label(runs, left_label)
        right = _run_by_label(runs, right_label)
        comparison_prefix = _safe_filename(comparison)
        left_prefix = _run_slug(left_label)
        right_prefix = _run_slug(right_label)
        for _, key in LOSS_EXPORT_QUANTITIES:
            left_value = _loss_value(left, key)
            right_value = _loss_value(right, key)
            stem = f"{comparison_prefix}_{key}"
            row[f"{stem}_{left_prefix}"] = left_value
            row[f"{stem}_{right_prefix}"] = right_value
            row[f"{stem}_{left_prefix}_minus_{right_prefix}_mw"] = _difference(
                left_value,
                right_value,
            )
            row[f"{stem}_{left_prefix}_minus_{right_prefix}_percent_of_{right_prefix}"] = (
                _percent_change(left_value, right_value)
            )
    return row


def _vsc_control_change_time_series_row(
    runs: list[BenchmarkRun],
    base_row: Mapping[str, Any],
    *,
    include_pyflow_reference: bool,
) -> dict[str, Any]:
    row = dict(base_row)
    acdcpf_pf = _run_by_label(runs, "ACDCPF PF")
    tool1 = _tool1_report_run(runs)
    pyflow_pf = _run_by_label(runs, "PyFlow PF") if include_pyflow_reference else None
    pyflow_opf = _run_by_label(runs, "PyFlow OPF VSC-only") if include_pyflow_reference else None

    for converter_index in _converter_indices_from_runs(acdcpf_pf, tool1, pyflow_pf, pyflow_opf):
        for field_name in ("p_ac_mw", "q_ac_mvar"):
            acdcpf_before = _table_value(acdcpf_pf, "converters", converter_index, field_name)
            tool1_after = _table_value(tool1, "converters", converter_index, field_name)
            stem = f"vsc_{converter_index}_{field_name}"
            row[f"{stem}_acdcpf_pf"] = acdcpf_before
            row[f"{stem}_tool1"] = tool1_after
            row[f"{stem}_tool1_change_from_acdcpf_pf"] = _difference(
                tool1_after,
                acdcpf_before,
            )
            row[f"{stem}_tool1_change_percent_from_acdcpf_pf"] = _percent_change(
                tool1_after,
                acdcpf_before,
            )
            if include_pyflow_reference:
                pyflow_before = _table_value(pyflow_pf, "converters", converter_index, field_name)
                pyflow_after = _table_value(pyflow_opf, "converters", converter_index, field_name)
                row[f"{stem}_pyflow_pf"] = pyflow_before
                row[f"{stem}_pyflow_opf"] = pyflow_after
                row[f"{stem}_pyflow_change_from_pf"] = _difference(
                    pyflow_after,
                    pyflow_before,
                )
                row[f"{stem}_pyflow_change_percent_from_pf"] = _percent_change(
                    pyflow_after,
                    pyflow_before,
                )
                row[f"{stem}_tool1_minus_pyflow_opf"] = _difference(
                    tool1_after,
                    pyflow_after,
                )
                row[f"{stem}_tool1_minus_pyflow_opf_percent_of_pyflow"] = _percent_change(
                    tool1_after,
                    pyflow_after,
                )
    return row


def _vsc_starting_setpoint_time_series_row(
    scenario: VSCSetpointScenario | str,
    base_row: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(base_row)
    for idx, values in sorted(_vsc_setpoints_for_scenario(scenario).items()):
        row[f"vsc_{idx}_starting_p_ac_mw"] = values["p_ac_mw"]
        row[f"vsc_{idx}_starting_q_ac_mvar"] = values["q_ac_mvar"]
    return row


def _voltage_movement_time_series_row(
    runs: list[BenchmarkRun],
    base_row: Mapping[str, Any],
    *,
    include_pyflow_reference: bool,
) -> dict[str, Any]:
    row = dict(base_row)
    tool1 = _tool1_report_run(runs)
    comparisons = [("tool1", "ACDCPF PF", tool1.label if tool1 else TOOL1_OBJECTIVE_LABEL)]
    if include_pyflow_reference:
        comparisons.append(("pyflow", "PyFlow PF", "PyFlow OPF VSC-only"))
    for prefix, before_label, after_label in comparisons:
        before = _run_by_label(runs, before_label)
        after = _run_by_label(runs, after_label)
        if before is None or after is None:
            continue
        prefix = _safe_filename(prefix)
        row[f"{prefix}_max_ac_v_diff_pu"] = _max_table_delta(before, after, "ac_buses", "v_pu")
        row[f"{prefix}_max_ac_angle_diff_deg"] = _max_table_delta(
            before,
            after,
            "ac_buses",
            "angle_deg",
        )
        row[f"{prefix}_max_dc_v_diff_pu"] = _max_table_delta(before, after, "dc_buses", "v_pu")
    return row


def _table_time_series_row(
    runs: list[BenchmarkRun],
    base_row: Mapping[str, Any],
    table_name: str,
    fields: list[str],
) -> dict[str, Any]:
    row = dict(base_row)
    for run in runs:
        run_prefix = _run_slug(run.label)
        table_rows = sorted(
            run.tables.get(table_name, []),
            key=lambda item: _natural_key(str(item.get("id", item.get("name", "")))),
        )
        for table_row in table_rows:
            element_token = _element_token(table_row)
            for field_name in fields:
                if field_name in table_row:
                    row[f"{run_prefix}_{table_name}_{element_token}_{field_name}"] = table_row[
                        field_name
                    ]
    return row


def _matacdc_reference_time_series_row(
    validation: dict[str, Any],
    base_row: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(base_row)
    if not validation or not validation.get("available"):
        row["available"] = False
        row["message"] = validation.get("message", "MATACDC reference was not checked.")
        return row

    row["available"] = True
    row["reference"] = validation.get("reference")
    for key, value in sorted(validation.get("checks", {}).items()):
        row[f"{_safe_filename(key)}_max_abs_difference"] = value
    return row


def _write_csv_sections(
    output_path: Path,
    sections: list[ReportSection],
) -> Path:
    resolved_path = _resolve_project_path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with resolved_path.open("w", encoding=CSV_ENCODING, newline="") as handle:
        writer = csv.writer(handle, delimiter=CSV_DELIMITER)
        for section in sections:
            writer.writerow([section.name])
            writer.writerow(_export_column_headers(section))
            for row in section.rows:
                writer.writerow([_csv_value(row.get(field)) for field in section.fieldnames])
            writer.writerow([])
    return resolved_path


def _export_column_headers(section: ReportSection) -> list[str]:
    """Return compact, unique headers for human-facing CSV/XLSX exports."""

    headers = [_export_column_name(section.name, field) for field in section.fieldnames]
    seen: dict[str, int] = {}
    unique_headers = []
    for header in headers:
        count = seen.get(header, 0) + 1
        seen[header] = count
        unique_headers.append(header if count == 1 else f"{header}_{count}")
    return unique_headers


def _export_column_name(section_name: str, field: str) -> str:
    if field in BASE_EXPORT_COLUMN_ALIASES:
        return BASE_EXPORT_COLUMN_ALIASES[field]
    if field in OPF_LOSS_CHANGE_ALIASES:
        return OPF_LOSS_CHANGE_ALIASES[field]
    if field in VOLTAGE_MOVEMENT_ALIASES:
        return VOLTAGE_MOVEMENT_ALIASES[field]

    if section_name == "optimized_vsc_controls":
        vsc_control_name = _short_vsc_control_column(field)
        if vsc_control_name is not None:
            return vsc_control_name

    if section_name == "vsc_starting_setpoints":
        starting_name = _short_vsc_starting_column(field)
        if starting_name is not None:
            return starting_name

    cross_solver_name = _short_cross_solver_column(field)
    if cross_solver_name is not None:
        return cross_solver_name

    table_name = _short_table_column(field)
    if table_name is not None:
        return table_name

    run_prefixed_name = _short_run_prefixed_column(field)
    if run_prefixed_name is not None:
        return run_prefixed_name

    if field.endswith("_max_abs_difference"):
        return f"{field.removesuffix('_max_abs_difference')}_max_abs_d"

    return field.replace("percent", "pct").replace("difference", "diff")


def _short_vsc_control_column(field: str) -> str | None:
    for source_name, export_name in (("p_ac_mw", "p_mw"), ("q_ac_mvar", "q_mvar")):
        separator = f"_{source_name}_"
        if not field.startswith("vsc_") or separator not in field:
            continue
        converter_index, suffix = field.removeprefix("vsc_").split(separator, 1)
        suffix_alias = VSC_CONTROL_SUFFIX_ALIASES.get(suffix)
        if suffix_alias is None:
            return None
        return f"vsc{converter_index}_{export_name}_{suffix_alias}"
    return None


def _short_vsc_starting_column(field: str) -> str | None:
    for source_name, export_name in (("p_ac_mw", "p_mw"), ("q_ac_mvar", "q_mvar")):
        suffix = f"_starting_{source_name}"
        if field.startswith("vsc_") and field.endswith(suffix):
            converter_index = field.removeprefix("vsc_").removesuffix(suffix)
            return f"vsc{converter_index}_{export_name}_start"
    return None


def _short_cross_solver_column(field: str) -> str | None:
    comparison_aliases = {"pf_baseline": "pf", "final_opf": "opf"}
    for comparison_name, comparison_alias in comparison_aliases.items():
        for loss_key, loss_alias in LOSS_EXPORT_ALIASES.items():
            prefix = f"{comparison_name}_{loss_key}_"
            if not field.startswith(prefix):
                continue
            suffix = field.removeprefix(prefix)
            run_alias = RUN_EXPORT_ALIASES.get(suffix)
            if run_alias is not None:
                return f"{comparison_alias}_{loss_alias}_{run_alias}"
            diff_name = _short_run_difference_suffix(suffix)
            if diff_name is not None:
                return f"{comparison_alias}_{loss_alias}_{diff_name}"
    return None


def _short_run_difference_suffix(suffix: str) -> str | None:
    for left_slug, left_alias in RUN_EXPORT_ALIASES.items():
        for right_slug, right_alias in RUN_EXPORT_ALIASES.items():
            mw_suffix = f"{left_slug}_minus_{right_slug}_mw"
            if suffix == mw_suffix:
                return f"{left_alias}_vs_{right_alias}_mw"
            pct_suffix = f"{left_slug}_minus_{right_slug}_percent_of_{right_slug}"
            if suffix == pct_suffix:
                return f"{left_alias}_vs_{right_alias}_pct"
    return None


def _short_table_column(field: str) -> str | None:
    for table_name, table_alias in TABLE_EXPORT_ALIASES.items():
        marker = f"_{table_name}_"
        if marker not in field:
            continue
        run_slug, remainder = field.split(marker, 1)
        run_alias = RUN_EXPORT_ALIASES.get(run_slug)
        if run_alias is None:
            continue
        parsed = _split_element_and_field(remainder)
        if parsed is None:
            continue
        element_token, field_name = parsed
        field_alias = FIELD_EXPORT_ALIASES.get(field_name, field_name)
        return f"{run_alias}_{table_alias}{element_token}_{field_alias}"
    return None


def _split_element_and_field(value: str) -> tuple[str, str] | None:
    for field_name in sorted(FIELD_EXPORT_ALIASES, key=len, reverse=True):
        suffix = f"_{field_name}"
        if value.endswith(suffix):
            element_token = value.removesuffix(suffix)
            return element_token, field_name
    return None


def _short_run_prefixed_column(field: str) -> str | None:
    for run_slug, run_alias in RUN_EXPORT_ALIASES.items():
        prefix = f"{run_slug}_"
        if not field.startswith(prefix):
            continue
        suffix = field.removeprefix(prefix)
        if suffix in LOSS_EXPORT_ALIASES:
            return f"{run_alias}_{LOSS_EXPORT_ALIASES[suffix]}"
        if suffix in {"success", "message"}:
            return f"{run_alias}_{suffix}"
    return None


def _write_xlsx_workbook(
    output_path: Path,
    sections: list[ReportSection],
    *,
    generated_at: str,
) -> Path:
    """Write a minimal XLSX workbook using only the Python standard library."""

    resolved_path = _resolve_project_path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = _xlsx_sheet_names(sections)

    with zipfile.ZipFile(resolved_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types_xml(len(sections)))
        archive.writestr("_rels/.rels", _xlsx_root_relationships_xml())
        archive.writestr("docProps/app.xml", _xlsx_app_properties_xml(sheet_names))
        archive.writestr("docProps/core.xml", _xlsx_core_properties_xml(generated_at))
        archive.writestr("xl/workbook.xml", _xlsx_workbook_xml(sheet_names))
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            _xlsx_workbook_relationships_xml(len(sections)),
        )
        archive.writestr("xl/styles.xml", _xlsx_styles_xml())
        for index, section in enumerate(sections, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _xlsx_worksheet_xml(section),
            )
    return resolved_path


def _xlsx_sheet_names(sections: list[ReportSection]) -> list[str]:
    used: set[str] = set()
    names = []
    for section in sections:
        base_name = _xlsx_sheet_name(section.name)
        candidate = base_name
        counter = 2
        while candidate.lower() in used:
            suffix = f"_{counter}"
            candidate = f"{base_name[:31 - len(suffix)]}{suffix}"
            counter += 1
        used.add(candidate.lower())
        names.append(candidate)
    return names


def _xlsx_sheet_name(name: str) -> str:
    invalid_chars = set("[]:*?/\\")
    cleaned = "".join("_" if char in invalid_chars else char for char in name).strip()
    return (cleaned or "sheet")[:31]


def _xlsx_content_types_xml(sheet_count: int) -> str:
    worksheet_overrides = "\n".join(
        (
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        for index in range(1, sheet_count + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{worksheet_overrides}
</Types>"""


def _xlsx_root_relationships_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""


def _xlsx_app_properties_xml(sheet_names: list[str]) -> str:
    sheet_titles = "".join(f"<vt:lpstr>{_xml_text(name)}</vt:lpstr>" for name in sheet_names)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>Tool1 (acdcopf) benchmark exporter</Application>
<DocSecurity>0</DocSecurity>
<ScaleCrop>false</ScaleCrop>
<HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant><vt:variant><vt:i4>{len(sheet_names)}</vt:i4></vt:variant></vt:vector></HeadingPairs>
<TitlesOfParts><vt:vector size="{len(sheet_names)}" baseType="lpstr">{sheet_titles}</vt:vector></TitlesOfParts>
<Company></Company>
<LinksUpToDate>false</LinksUpToDate>
<SharedDoc>false</SharedDoc>
<HyperlinksChanged>false</HyperlinksChanged>
<AppVersion>16.0000</AppVersion>
</Properties>"""


def _xlsx_core_properties_xml(generated_at: str) -> str:
    timestamp = generated_at if generated_at.endswith("Z") else f"{generated_at}Z"
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>Stagg5 PF/OPF Benchmark Report</dc:title>
<dc:creator>Tool1 (acdcopf) benchmark exporter</dc:creator>
<cp:lastModifiedBy>Tool1 (acdcopf) benchmark exporter</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{_xml_text(timestamp)}</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">{_xml_text(timestamp)}</dcterms:modified>
</cp:coreProperties>"""


def _xlsx_workbook_xml(sheet_names: list[str]) -> str:
    sheets = "\n".join(
        (
            f'<sheet name="{_xml_attr(name)}" sheetId="{index}" r:id="rId{index}"/>'
        )
        for index, name in enumerate(sheet_names, start=1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
{sheets}
</sheets>
</workbook>"""


def _xlsx_workbook_relationships_xml(sheet_count: int) -> str:
    worksheet_relationships = "\n".join(
        (
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
        for index in range(1, sheet_count + 1)
    )
    styles_rid = sheet_count + 1
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{worksheet_relationships}
<Relationship Id="rId{styles_rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def _xlsx_styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
<font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>
<font><b/><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>
</fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
<dxfs count="0"/>
<tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>"""


def _xlsx_worksheet_xml(section: ReportSection) -> str:
    rows = [
        _export_column_headers(section),
        *[_section_row_values(section, row) for row in section.rows],
    ]
    row_xml = "\n".join(
        _xlsx_row_xml(row_index, row, header=row_index == 1)
        for row_index, row in enumerate(rows, start=1)
    )
    column_xml = _xlsx_columns_xml(rows)
    autofilter_ref = _xlsx_range_reference(len(section.fieldnames), max(len(rows), 1))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
{column_xml}
<sheetData>
{row_xml}
</sheetData>
<autoFilter ref="{autofilter_ref}"/>
</worksheet>"""


def _section_row_values(section: ReportSection, row: Mapping[str, Any]) -> list[Any]:
    return [row.get(field) for field in section.fieldnames]


def _xlsx_columns_xml(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    widths = []
    column_count = max(len(row) for row in rows)
    for column_index in range(column_count):
        values = [row[column_index] for row in rows if column_index < len(row)]
        width = min(45, max(10, max(len(str(_csv_value(value))) for value in values) + 2))
        widths.append(width)
    columns = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, start=1)
    )
    return f"<cols>{columns}</cols>"


def _xlsx_row_xml(row_index: int, values: list[Any], *, header: bool) -> str:
    cells = "".join(
        _xlsx_cell_xml(row_index, column_index, value, header=header)
        for column_index, value in enumerate(values, start=1)
    )
    return f'<row r="{row_index}">{cells}</row>'


def _xlsx_cell_xml(row_index: int, column_index: int, value: Any, *, header: bool) -> str:
    if value is None:
        return ""
    cell_ref = f"{_xlsx_column_name(column_index)}{row_index}"
    style = ' s="1"' if header else ""
    if isinstance(value, bool):
        return f'<c r="{cell_ref}" t="b"{style}><v>{1 if value else 0}</v></c>'
    number = _safe_float(value)
    if number is not None and not isinstance(value, str):
        return f'<c r="{cell_ref}"{style}><v>{number:.15g}</v></c>'
    text = _xml_text(str(_csv_value(value)))
    return f'<c r="{cell_ref}" t="inlineStr"{style}><is><t>{text}</t></is></c>'


def _xlsx_column_name(column_index: int) -> str:
    name = ""
    while column_index:
        column_index, remainder = divmod(column_index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xlsx_range_reference(column_count: int, row_count: int) -> str:
    end_column = _xlsx_column_name(max(column_count, 1))
    end_row = max(row_count, 1)
    return f"A1:{end_column}{end_row}"


def _xml_text(value: str) -> str:
    return xml_escape(value, {'"': "&quot;"})


def _xml_attr(value: str) -> str:
    return _xml_text(value)


def _csv_fieldnames(
    rows: Iterable[Mapping[str, Any]],
    *,
    preferred_first: Iterable[str] = (),
) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            field = str(key)
            if field not in seen:
                seen.add(field)
                keys.append(field)

    ordered = [field for field in preferred_first if field in seen]
    ordered.extend([field for field in keys if field not in set(ordered)])
    return ordered


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return value


def _csv_export_message(csv_path: Path) -> str:
    return f"CSV report written to: `{csv_path}`"


def _csv_export_messages(csv_path: Path | Iterable[Path] | None) -> list[str]:
    return [_csv_export_message(path) for path in _export_path_list(csv_path)]


def _csv_permission_error_message(csv_path: Path) -> str:
    resolved_path = _resolve_project_path(csv_path)
    return (
        f"Could not write CSV report to `{resolved_path}`. "
        "Close the file in Excel or choose a different --csv-output path, then rerun."
    )


def _xlsx_export_message(xlsx_path: Path) -> str:
    return f"Excel workbook written to: `{xlsx_path}`"


def _xlsx_export_messages(xlsx_path: Path | Iterable[Path] | None) -> list[str]:
    return [_xlsx_export_message(path) for path in _export_path_list(xlsx_path)]


def _export_path_list(paths: Path | Iterable[Path] | None) -> list[Path]:
    if paths is None:
        return []
    if isinstance(paths, Path):
        return [paths]
    return list(paths)


def _xlsx_permission_error_message(xlsx_path: Path) -> str:
    resolved_path = _resolve_project_path(xlsx_path)
    return (
        f"Could not write Excel workbook to `{resolved_path}`. "
        "Close the file in Excel or choose a different --xlsx-output path, then rerun."
    )


def _html_export_message(html_path: Path) -> str:
    return f"Interactive HTML grid view written to: `{html_path}`"


def _html_permission_error_message(html_path: Path) -> str:
    resolved_path = _resolve_project_path(html_path)
    return (
        f"Could not write HTML grid view to `{resolved_path}`. "
        "Close the file in your browser/editor or choose a different --html-output path, then rerun."
    )


def _open_html_report(html_path: Path) -> None:
    resolved_path = _resolve_project_path(html_path)
    webbrowser.open(resolved_path.as_uri())


def _run_slug(label: str) -> str:
    return _safe_filename(label)


def _element_token(row: Mapping[str, Any]) -> str:
    identifier = row.get("id", row.get("name", "unknown"))
    return _safe_filename(str(identifier))


def _resolve_project_path(path: Path) -> Path:
    resolved_path = path.expanduser()
    if not resolved_path.is_absolute():
        cwd_path = Path.cwd() / resolved_path
        if cwd_path.exists() or cwd_path.parent.exists():
            return cwd_path
        resolved_path = PROJECT_ROOT / resolved_path
    return resolved_path


def _safe_filename(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")
    while "__" in text:
        text = text.replace("__", "_")
    return text or "unnamed"


def _predefined_vsc_setpoint_scenario(
    scenario_name: str,
    grid_case: Stagg5GridCase | str | None = None,
) -> VSCSetpointScenario:
    if scenario_name not in VSC_STARTING_SETPOINTS_MW_MVAR:
        raise ValueError(f"Unsupported predefined VSC setpoint scenario: {scenario_name}")
    selected_case = get_stagg5_grid_case(grid_case)
    if selected_case.key == STAGG5_TWO_AREA_TRANSFORMER_DCDC:
        description = (
            "Synthetic two-area Stagg5 factory VSC setpoints. These values are "
            "mildly perturbed to make OPF movement visible while generator "
            "dispatch remains fixed."
        )
        if scenario_name == VSC_SETPOINT_SCENARIO_PERTURBED:
            description += " The generic Stagg perturbed scenario is not used for this case."
        return VSCSetpointScenario(
            name=scenario_name,
            description=description,
            setpoints=_copy_vsc_setpoints(TWO_AREA_VSC_FACTORY_SETPOINTS_MW_MVAR),
        )
    if not _case_has_predefined_stagg_vsc_setpoints(selected_case):
        description = (
            f"{selected_case.display_name} uses the VSC setpoints provided by its "
            "case factory. No Stagg-specific VSC overrides are applied."
        )
        if scenario_name == VSC_SETPOINT_SCENARIO_PERTURBED:
            description += " The perturbed Stagg scenario is not defined for this grid."
        return VSCSetpointScenario(
            name=scenario_name,
            description=description,
            setpoints={},
        )
    return VSCSetpointScenario(
        name=scenario_name,
        description=VSC_SETPOINT_SCENARIO_DESCRIPTIONS[scenario_name],
        setpoints=_copy_vsc_setpoints(VSC_STARTING_SETPOINTS_MW_MVAR[scenario_name]),
    )


def _custom_vsc_setpoint_scenario(
    base_scenario_name: str,
    custom_specs: Iterable[Any],
    grid_case: Stagg5GridCase | str | None = None,
) -> VSCSetpointScenario:
    base = _predefined_vsc_setpoint_scenario(base_scenario_name, grid_case)
    setpoints = _copy_vsc_setpoints(base.setpoints)
    overridden_indices: list[int] = []

    for raw_spec in custom_specs:
        idx, values = _parse_custom_vsc_setpoint(raw_spec)
        setpoints[idx] = {
            "p_ac_mw": float(values["p_ac_mw"]),
            "q_ac_mvar": float(values["q_ac_mvar"]),
        }
        overridden_indices.append(idx)

    overridden = ", ".join(str(idx) for idx in sorted(set(overridden_indices)))
    description = (
        "Custom user-provided VSC starting setpoints. Values not provided "
        f"explicitly were kept from the `{base.name}` scenario."
    )
    if overridden:
        description += f" Overridden VSC indices: {overridden}."

    return VSCSetpointScenario(
        name=VSC_SETPOINT_SCENARIO_CUSTOM,
        description=description,
        setpoints=setpoints,
    )


def _coerce_vsc_setpoint_scenario(
    scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
) -> VSCSetpointScenario:
    if isinstance(scenario, VSCSetpointScenario):
        return scenario
    return _predefined_vsc_setpoint_scenario(str(scenario), grid_case)


def _vsc_setpoint_scenario_name(scenario: VSCSetpointScenario | str) -> str:
    return _coerce_vsc_setpoint_scenario(scenario).name


def _vsc_setpoints_for_scenario(
    scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
) -> dict[int, dict[str, float]]:
    return _coerce_vsc_setpoint_scenario(scenario, grid_case).setpoints


def _copy_vsc_setpoints(
    setpoints: dict[int, dict[str, float]],
) -> dict[int, dict[str, float]]:
    return {
        int(idx): {
            "p_ac_mw": float(values["p_ac_mw"]),
            "q_ac_mvar": float(values["q_ac_mvar"]),
        }
        for idx, values in setpoints.items()
    }


def _is_controlled_converter_index(
    converter_index: int | None,
    selected_case: Stagg5GridCase,
) -> bool:
    if converter_index is None:
        return False
    p_indices = selected_case.converter_active_power_indices
    q_indices = selected_case.converter_reactive_power_indices
    return _index_selected_for_control(converter_index, p_indices) or _index_selected_for_control(
        converter_index,
        q_indices,
    )


def _index_selected_for_control(index: int, indices: tuple[int, ...] | None) -> bool:
    return indices is None or int(index) in set(indices)


def _case_has_predefined_stagg_vsc_setpoints(selected_case: Stagg5GridCase) -> bool:
    return selected_case.key in {STAGG5_ORIGINAL, STAGG5_HYBRID_DCDC}


def _apply_vsc_setpoints_to_acdcpf_network(
    net: Any,
    scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
) -> None:
    """Apply benchmark VSC P/Q setpoints using ACDCPF load convention."""

    for idx, values in _vsc_setpoints_for_scenario(scenario, grid_case).items():
        if not hasattr(net, "vsc") or idx not in net.vsc.index:
            raise ValueError(f"Selected ACDCPF grid has no VSC with index {idx}.")
        net.vsc.at[idx, "p_mw"] = float(values["p_ac_mw"])
        net.vsc.at[idx, "q_mvar"] = float(values["q_ac_mvar"])


def _apply_vsc_setpoints_to_pyflow_grid(
    grid: Any,
    scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
) -> None:
    """Apply the same reported VSC P/Q setpoints to a PyFlow grid."""

    base_mva = float(getattr(grid, "S_base", 100.0))
    for idx, values in _vsc_setpoints_for_scenario(scenario, grid_case).items():
        if idx < 0 or idx >= len(getattr(grid, "Converters_ACDC", [])):
            raise ValueError(f"Selected PyFlow grid has no VSC with index {idx}.")
        conv = grid.Converters_ACDC[int(idx)]
        conv.P_AC = -float(values["p_ac_mw"]) / base_mva
        conv.Q_AC = -float(values["q_ac_mvar"]) / base_mva
    if hasattr(grid, "Update_PQ_AC"):
        grid.Update_PQ_AC()


def _create_case_for_tool1(
    selected_case: Stagg5GridCase,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    *,
    network_overrides: Any | None = None,
) -> Any:
    if selected_case.source == "pyflow":
        grid = create_pyflow_grid_for_stagg5_case(selected_case)
        _apply_vsc_setpoints_to_pyflow_grid(grid, vsc_setpoint_scenario, selected_case)
        if network_overrides is not None:
            network_overrides(grid)
        return grid

    net = create_acdcpf_network_for_stagg5_case(selected_case)
    _apply_vsc_setpoints_to_acdcpf_network(net, vsc_setpoint_scenario, selected_case)
    if network_overrides is not None:
        network_overrides(net)
    return net


def _apply_profile_snapshot_to_case(
    selected_case: Stagg5GridCase,
    case: Any,
    profile_snapshot: ProfileSnapshot | None,
) -> None:
    if profile_snapshot is None:
        return
    if selected_case.source == "pyflow":
        raise NotImplementedError(
            "Time-profile snapshots are not yet supported for PyFlow-sourced cases."
        )
    apply_profile_snapshot(case, profile_snapshot)


def _add_profile_diagnostics(
    run: BenchmarkRun,
    profile_snapshot: ProfileSnapshot | None,
    *,
    profile_mode: str = "independent snapshot",
) -> BenchmarkRun:
    if profile_snapshot is None:
        return run
    run.diagnostics.update(
        {
            "profile_time_index": profile_snapshot.time_index,
            "profile_timestamp": profile_snapshot.timestamp,
            "profile_change_count": len(profile_snapshot.changes),
            "profile_mode": profile_mode,
        }
    )
    return run


def _opf_conversion_options_for_case(
    selected_case: Stagg5GridCase,
    *,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
    control_margin_percent: float | None,
) -> ACDCPFToPyomoOptions | PyflowToPyomoOptions:
    controls = opf_control_selection or _benchmark_opf_control_selection(selected_case)
    common_converter_options = {
        "optimize_converter_active_power": controls.vsc_p,
        "converter_active_power_indices": _selected_converter_indices(
            selected_case.converter_active_power_indices,
            enabled=controls.vsc_p,
            control_device_scope=control_device_scope,
        ),
        "optimize_converter_reactive_power": controls.vsc_q,
        "converter_reactive_power_indices": _selected_converter_indices(
            selected_case.converter_reactive_power_indices,
            enabled=controls.vsc_q,
            control_device_scope=control_device_scope,
        ),
        "control_margin_percent": control_margin_percent,
        "converter_loss_mode": "fixed",
    }
    if selected_case.source == "pyflow":
        return PyflowToPyomoOptions(
            optimize_non_slack_generator_active_power=controls.ac_gen_p,
            ac_generator_active_power_indices=_selected_generator_indices(controls.ac_gen_p),
            optimize_non_slack_generator_reactive_power=controls.ac_gen_q,
            ac_generator_reactive_power_indices=_selected_generator_indices(controls.ac_gen_q),
            optimize_generator_voltage_setpoints=False,
            ac_generator_voltage_node_indices=(),
            **common_converter_options,
        )

    return ACDCPFToPyomoOptions(
        optimize_non_slack_generator_active_power=controls.ac_gen_p,
        ac_generator_active_power_indices=_selected_generator_indices(controls.ac_gen_p),
        optimize_non_slack_generator_reactive_power=controls.ac_gen_q,
        ac_generator_reactive_power_indices=_selected_generator_indices(controls.ac_gen_q),
        optimize_generator_voltage_setpoints=False,
        ac_generator_voltage_node_indices=(),
        optimize_dcdc_voltage_ratio=controls.dcdc_ratio,
        dcdc_voltage_ratio_indices=_selected_dcdc_indices(
            selected_case,
            enabled=controls.dcdc_ratio,
            control_device_scope=control_device_scope,
        ),
        optimize_dc_generator_active_power=controls.dc_gen_curtailment,
        dc_generator_active_power_indices=_selected_generator_indices(
            controls.dc_gen_curtailment,
        ),
        dc_generator_curtailment_only=controls.dc_gen_curtailment,
        **common_converter_options,
    )


def _selected_converter_indices(
    benchmark_indices: tuple[int, ...] | None,
    *,
    enabled: bool,
    control_device_scope: str,
) -> tuple[int, ...] | None:
    if not enabled:
        return ()
    if control_device_scope == CONTROL_DEVICE_SCOPE_ALL:
        return None
    return benchmark_indices


def _selected_dcdc_indices(
    selected_case: Stagg5GridCase,
    *,
    enabled: bool,
    control_device_scope: str,
) -> tuple[int, ...] | None:
    if not enabled:
        return ()
    if control_device_scope == CONTROL_DEVICE_SCOPE_ALL:
        return None
    return selected_case.dcdc_voltage_ratio_indices


def _selected_generator_indices(enabled: bool) -> tuple[int, ...] | None:
    return None if enabled else ()


def _run_acdcpf_pf(
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    *,
    profile_snapshot: ProfileSnapshot | None = None,
    network_overrides: Any | None = None,
) -> tuple[BenchmarkRun, PFResult | None]:
    try:
        selected_case = get_stagg5_grid_case(grid_case)
        if selected_case.source == "pyflow":
            if profile_snapshot is not None:
                raise NotImplementedError(
                    "Time-profile snapshots are not yet supported for PyFlow-sourced cases."
                )
            grid = create_pyflow_grid_for_stagg5_case(selected_case)
            _apply_vsc_setpoints_to_pyflow_grid(grid, vsc_setpoint_scenario, selected_case)
            pf_result = ACDCPFAdapter(max_iter_outer=80, tolerance=1e-8).solve(
                grid,
                copy_case=True,
                write_back=False,
            )
        else:
            net = create_acdcpf_network_for_stagg5_case(selected_case)
            _apply_vsc_setpoints_to_acdcpf_network(net, vsc_setpoint_scenario, selected_case)
            if network_overrides is not None:
                network_overrides(net)
            _apply_profile_snapshot_to_case(selected_case, net, profile_snapshot)
            pf_result = ACDCPFNetworkAdapter(max_iter_outer=50, tolerance=1e-8).solve(
                net,
                copy_case=True,
                write_back=False,
            )
        run = _snapshot_from_acdcpf_pf("ACDCPF PF", pf_result)
        return _add_profile_diagnostics(run, profile_snapshot), pf_result
    except Exception as exc:  # pragma: no cover - defensive integration path
        return (
            _add_profile_diagnostics(
                BenchmarkRun(
                    label="ACDCPF PF",
                    success=False,
                    message=f"{type(exc).__name__}: {exc}",
                ),
                profile_snapshot,
            ),
            None,
        )


def _run_tool1_pyomo_ipopt(
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    *,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
    control_margin_percent: float | None = None,
    profile_snapshot: ProfileSnapshot | None = None,
    network_overrides: Any | None = None,
) -> tuple[BenchmarkRun, BenchmarkRun | None]:
    try:
        selected_case = get_stagg5_grid_case(grid_case)
        case = _create_case_for_tool1(
            selected_case,
            vsc_setpoint_scenario,
            network_overrides=network_overrides,
        )
        _apply_profile_snapshot_to_case(selected_case, case, profile_snapshot)
        config = PyomoACDCOPFConfig(
            ipopt_executable=str(_ipopt_executable_path()),
            tee=False,
            max_iter=1000,
            tolerance=1e-8,
            print_level=5,
            run_final_acdcpf_validation=True,
            logging_level=logging.CRITICAL,
        )
        conversion_options = _opf_conversion_options_for_case(
            selected_case,
            opf_control_selection=opf_control_selection,
            control_device_scope=control_device_scope,
            control_margin_percent=control_margin_percent,
        )
        result = solve_pyomo_acdc_loss_min_opf(
            case,
            config=config,
            conversion_options=conversion_options,
        )
        opf_run = _add_profile_diagnostics(
            _snapshot_from_tool1(TOOL1_OBJECTIVE_LABEL, result),
            profile_snapshot,
        )
        validation_run = None
        if result.validation_pf_result is not None:
            validation_run = _add_profile_diagnostics(
                _snapshot_from_acdcpf_pf(
                    TOOL1_VALIDATED_LABEL,
                    result.validation_pf_result,
                ),
                profile_snapshot,
            )
        return opf_run, validation_run
    except Exception as exc:  # pragma: no cover - defensive integration path
        return (
            _add_profile_diagnostics(
                BenchmarkRun(
                    label=TOOL1_OBJECTIVE_LABEL,
                    success=False,
                    message=f"{type(exc).__name__}: {exc}",
                ),
                profile_snapshot,
            ),
            None,
        )


def _run_profiled_tool1_pyomo_ipopt(
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    *,
    profile_snapshots: tuple[ProfileSnapshot, ...],
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
    control_margin_percent: float | None = None,
    network_overrides: Any | None = None,
) -> dict[int, tuple[BenchmarkRun, BenchmarkRun | None]]:
    """Run one linked profile Tool1 (acdcopf) solve and return per-timestamp report runs."""

    selected_case = get_stagg5_grid_case(grid_case)
    snapshots_by_time = {snapshot.time_index: snapshot for snapshot in profile_snapshots}
    try:
        if selected_case.source == "pyflow":
            raise NotImplementedError(
                "Profiled Tool1 (acdcopf) is currently supported for native ACDCPF cases only."
            )

        cases_by_period = {}
        period_metadata = {}
        durations = _profile_snapshot_durations_hours(profile_snapshots)
        profile_mode = _profile_opf_mode_for_case(selected_case)
        for snapshot in profile_snapshots:
            case = _create_case_for_tool1(
                selected_case,
                vsc_setpoint_scenario,
                network_overrides=network_overrides,
            )
            _apply_profile_snapshot_to_case(selected_case, case, snapshot)
            cases_by_period[snapshot.time_index] = case
            period_metadata[snapshot.time_index] = {
                "snapshot_duration_hours": durations[snapshot.time_index],
                "profile_time_index": snapshot.time_index,
                "profile_timestamp": snapshot.timestamp,
                "profile_mode": profile_mode,
            }

        config = PyomoACDCOPFConfig(
            ipopt_executable=str(_ipopt_executable_path()),
            tee=False,
            max_iter=1000,
            tolerance=1e-8,
            print_level=5,
            run_final_acdcpf_validation=True,
            logging_level=logging.CRITICAL,
        )
        conversion_options = _opf_conversion_options_for_case(
            selected_case,
            opf_control_selection=opf_control_selection,
            control_device_scope=control_device_scope,
            control_margin_percent=control_margin_percent,
        )
        results_by_period = solve_pyomo_acdc_loss_min_time_series(
            cases_by_period,
            config=config,
            conversion_options=conversion_options,
            period_metadata=period_metadata,
        )

        runs_by_time: dict[int, tuple[BenchmarkRun, BenchmarkRun | None]] = {}
        for time_index, result in results_by_period.items():
            snapshot = snapshots_by_time[time_index]
            opf_run = _add_profile_diagnostics(
                _snapshot_from_tool1(TOOL1_OBJECTIVE_LABEL, result),
                snapshot,
                profile_mode=profile_mode,
            )
            validation_run = None
            if result.validation_pf_result is not None:
                validation_run = _add_profile_diagnostics(
                    _snapshot_from_acdcpf_pf(
                        TOOL1_VALIDATED_LABEL,
                        result.validation_pf_result,
                    ),
                    snapshot,
                    profile_mode=f"{profile_mode} validation",
                )
            runs_by_time[time_index] = (opf_run, validation_run)
        return runs_by_time
    except Exception as exc:  # pragma: no cover - defensive integration path
        message = f"{type(exc).__name__}: {exc}"
        return {
            snapshot.time_index: (
                _add_profile_diagnostics(
                    BenchmarkRun(
                        label=TOOL1_OBJECTIVE_LABEL,
                        success=False,
                        message=message,
                    ),
                    snapshot,
                    profile_mode=profile_mode,
                ),
                None,
            )
            for snapshot in profile_snapshots
        }


def _profile_opf_mode_for_case(selected_case: Stagg5GridCase) -> str:
    if selected_case.key == STAGG5_HYBRID_DCDC:
        return "linked storage SOC"
    return "independent snapshot"


def _profile_snapshot_durations_hours(
    profile_snapshots: tuple[ProfileSnapshot, ...],
) -> dict[int, float]:
    """Use increasing study timestamps; repeat the last interval at the horizon.

    Without timestamps each row is explicitly a one-hour snapshot. time_index
    is an identifier, not an elapsed-hour value.
    """

    if not profile_snapshots:
        return {}
    parsed_times = [_parse_profile_timestamp(snapshot.timestamp) for snapshot in profile_snapshots]
    if any(snapshot.timestamp for snapshot in profile_snapshots) and any(t is None for t in parsed_times):
        raise ValueError("All profile timestamps must be present and parseable when any timestamp is supplied.")
    durations: dict[int, float] = {}
    previous_duration = 1.0
    for index, snapshot in enumerate(profile_snapshots):
        duration = None
        if index + 1 < len(profile_snapshots):
            current_time = parsed_times[index]
            next_time = parsed_times[index + 1]
            if current_time is not None and next_time is not None:
                try:
                    duration = (next_time - current_time).total_seconds() / 3600.0
                except TypeError as exc:
                    raise ValueError("Profile timestamps must use consistent timezone information.") from exc
                if duration <= 0:
                    raise ValueError("Profile timestamps must be strictly increasing.")
            else:
                duration = 1.0
        if duration is None or duration <= 0.0:
            duration = previous_duration
        durations[snapshot.time_index] = float(duration)
        previous_duration = float(duration)
    return durations


def _parse_profile_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for pattern in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _run_pyflow_pf(
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
) -> BenchmarkRun:
    try:
        import pyflow_acdc as pyf

        selected_case = get_stagg5_grid_case(grid_case)
        if not selected_case.supports_pyflow:
            return _pyflow_unavailable_run("PyFlow PF", selected_case)
        pyf.initialize_pyflowacdc()
        grid = create_pyflow_grid_for_stagg5_case(selected_case)
        _apply_vsc_setpoints_to_pyflow_grid(grid, vsc_setpoint_scenario, selected_case)
        _, tolerance, _ = pyf.ACDC_sequential(grid)
        final_tolerance = _safe_float(tolerance.get("final_sequential_tolerance"))
        convergence_status = tolerance.get("convergence_status", {})
        success = bool(
            convergence_status.get(
                "sequential_converged",
                final_tolerance is not None and final_tolerance < 1e-5,
            )
        )
        return _snapshot_from_pyflow_grid(
            "PyFlow PF",
            grid,
            success=success,
            message=f"final sequential tolerance={_format_number(final_tolerance)}",
            diagnostics={
                "final_sequential_tolerance": final_tolerance,
                "convergence_status": convergence_status,
            },
        )
    except Exception as exc:  # pragma: no cover - defensive integration path
        return BenchmarkRun(
            label="PyFlow PF",
            success=False,
            message=f"{type(exc).__name__}: {exc}",
            diagnostics=_pyflow_import_failure_diagnostics(),
        )


def _run_pyflow_opf(
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    *,
    opf_control_selection: OPFControlSelection | None = None,
) -> BenchmarkRun:
    try:
        import pyflow_acdc as pyf

        selected_case = get_stagg5_grid_case(grid_case)
        if not selected_case.supports_pyflow:
            return _pyflow_unavailable_run("PyFlow OPF VSC-only", selected_case)
        controls = opf_control_selection or _benchmark_opf_control_selection(selected_case)
        if not _pyflow_opf_can_mirror_control_selection(controls):
            return _pyflow_opf_not_comparable_run(selected_case, controls)
        pyf.initialize_pyflowacdc()
        grid = create_pyflow_grid_for_stagg5_case(selected_case)
        _apply_vsc_setpoints_to_pyflow_grid(grid, vsc_setpoint_scenario, selected_case)
        _restrict_pyflow_to_vsc_only_study(grid, selected_case)
        _, result, timing, solver_stats = pyf.Optimal_PF(
            grid,
            ObjRule={"AC_losses": 1, "DC_losses": 1, "Converter_Losses": 1},
            PV_set=True,
            OnlyGen=False,
        )
        termination = str(solver_stats.get("termination_condition", "unknown"))
        success = termination.lower() in {"optimal", "locallyoptimal", "locally optimal"}
        diagnostics = {
            "termination_condition": termination,
            "solver_time_s": solver_stats.get("time"),
            "timing": timing,
            "pyomo_solver_status": str(getattr(result.solver, "status", "unknown")),
                "control_note": (
                    "Non-slack generator active power fixed by bounds; generator reactive "
                    "limits adjusted only when the selected benchmark case defines them; "
                    "slack/PV voltage magnitudes fixed with PV_set=True; selected VSCs free."
                ),
        }
        return _snapshot_from_pyflow_grid(
            "PyFlow OPF VSC-only",
            grid,
            success=success,
            message=f"termination={termination}",
            diagnostics=diagnostics,
        )
    except Exception as exc:  # pragma: no cover - PyFlow/IPOPT may be unavailable
        return BenchmarkRun(
            label="PyFlow OPF VSC-only",
            success=False,
            message=f"{type(exc).__name__}: {exc}",
            diagnostics={
                **_pyflow_import_failure_diagnostics(),
                "control_note": (
                    "PyFlow OPF uses SolverFactory('ipopt'); local solvers/ is added to PATH."
                )
            },
        )


def _pyflow_opf_can_mirror_control_selection(selection: OPFControlSelection) -> bool:
    return selection == OPFControlSelection(vsc_p=True, vsc_q=True)


def _pyflow_opf_not_comparable_run(
    grid_case: Stagg5GridCase,
    selection: OPFControlSelection,
) -> BenchmarkRun:
    return BenchmarkRun(
        label="PyFlow OPF VSC-only",
        success=False,
        message=(
            "not comparable: the PyFlow reference path in this benchmark mirrors only "
            "VSC active/reactive-power OPF controls, while Tool1 (acdcopf) selected "
            f"{_opf_control_selection_description(selection)}."
        ),
        diagnostics={
            "grid_case": grid_case.case_name,
            "pyflow_reference_available": True,
            "pyflow_opf_comparable": False,
            "selected_opf_controls": _opf_control_selection_description(selection),
        },
    )


def _pyflow_import_failure_diagnostics() -> dict[str, Any]:
    return {
        "python_executable": sys.executable,
        "pyflow_module": _module_path("pyflow_acdc"),
    }


def _pyflow_unavailable_runs(grid_case: Stagg5GridCase) -> list[BenchmarkRun]:
    return [
        _pyflow_unavailable_run("PyFlow PF", grid_case),
        _pyflow_unavailable_run("PyFlow OPF VSC-only", grid_case),
    ]


def _pyflow_profile_unavailable_runs(grid_case: Stagg5GridCase) -> list[BenchmarkRun]:
    return [
        _pyflow_profile_unavailable_run("PyFlow PF", grid_case),
        _pyflow_profile_unavailable_run("PyFlow OPF VSC-only", grid_case),
    ]


def _pyflow_unavailable_run(label: str, grid_case: Stagg5GridCase) -> BenchmarkRun:
    return BenchmarkRun(
        label=label,
        success=False,
        message=(
            f"{grid_case.display_name} is a native ACDCPF-only benchmark case; "
            "no equivalent PyFlow case is available."
        ),
        diagnostics={
            "grid_case": grid_case.case_name,
            "pyflow_reference_available": False,
        },
    )


def _pyflow_profile_unavailable_run(label: str, grid_case: Stagg5GridCase) -> BenchmarkRun:
    return BenchmarkRun(
        label=label,
        success=False,
        message=(
            "PyFlow reference is not comparable for time-series profile runs unless "
            "the same timestamp-specific injections are mirrored in an equivalent PyFlow case."
        ),
        diagnostics={
            "grid_case": grid_case.case_name,
            "pyflow_reference_available": False,
            "reason": "time-profile input is applied to the native ACDCPF case only",
        },
    )


def _restrict_pyflow_to_vsc_only_study(grid: Any, selected_case: Stagg5GridCase) -> None:
    """Restrict PyFlow OPF so the comparison matches the VSC-only study.

    PyFlow's default loss OPF can redispatch generators. For this benchmark,
    keep non-slack generator active dispatch fixed, align generator reactive
    limits with the ACDCPF Stagg case, and leave the DC-slack VSC free to
    balance the DC grid.
    """
    base_mva = float(getattr(grid, "S_base", 100.0))
    for gen in getattr(grid, "Generators", []):
        gen_idx = int(getattr(gen, "genNumber"))
        if selected_case.key == STAGG5_ORIGINAL and gen_idx in PYFLOW_STAGG_GENERATOR_Q_LIMITS_MVAR:
            q_min, q_max = PYFLOW_STAGG_GENERATOR_Q_LIMITS_MVAR[gen_idx]
            gen.Min_pow_genR = q_min / base_mva
            gen.Max_pow_genR = q_max / base_mva

    for node in getattr(grid, "nodes_AC", []):
        if str(getattr(node, "type", "")) == "Slack":
            continue
        for gen in getattr(node, "connected_gen", []):
            p_value = float(getattr(gen, "PGen", getattr(gen, "Pset", 0.0)))
            gen.Min_pow_gen = p_value
            gen.Max_pow_gen = p_value
            gen.Pset = p_value

    for conv in getattr(grid, "Converters_ACDC", []):
        conv_idx = int(getattr(conv, "ConvNumber"))
        if not _is_controlled_converter_index(conv_idx, selected_case) and str(
            getattr(conv, "type", "")
        ) in {"PAC", "P"}:
            conv.OPF_fx = True
            conv.OPF_fx_type = "PQ"


def _snapshot_from_acdcpf_pf(label: str, pf_result: PFResult) -> BenchmarkRun:
    raw_result = pf_result.raw_result
    net = getattr(raw_result, "acdcpf_net", raw_result)
    losses = {
        **pf_result.active_loss_breakdown(),
        "total_active_losses_mw": float(pf_result.total_active_losses),
    }
    generator_rows = _acdcpf_generator_rows(net)
    tables = {
        "ac_buses": _acdcpf_ac_bus_rows(net, generator_rows),
        "dc_buses": _merge_static_table_columns(
            _rows_from_table(
                getattr(net, "res_dc_bus", None),
                {"v_pu": "v_dc_pu", "v_kv": "v_dc_kv", "p_mw": "p_mw"},
            ),
            getattr(net, "dc_bus", None),
            {"name": "name"},
        ),
        "generators": generator_rows,
        "dc_generators": _rows_from_table(
            getattr(net, "dc_gen", None),
            {"p_mw": "p_mw"},
            raw_columns={"bus": "bus"},
        ),
        "loads": _rows_from_table(
            getattr(net, "ac_load", None),
            {"p_mw": "p_mw", "q_mvar": "q_mvar"},
            raw_columns={"bus": "bus"},
        ),
        "dc_loads": _rows_from_table(
            getattr(net, "dc_load", None),
            {"p_mw": "p_mw"},
            raw_columns={"bus": "bus", "load_type": "load_type"},
        ),
        "storage_units": _acdcpf_storage_rows(net),
        "converters": _acdcpf_vsc_rows(net),
        "dcdc_converters": _acdcpf_dcdc_rows(net),
        "ac_branches": _acdcpf_ac_branch_rows(net),
        "dc_branches": _acdcpf_dc_branch_rows(net),
        "transformers": _acdcpf_transformer_rows(net),
    }
    return BenchmarkRun(
        label=label,
        success=bool(pf_result.converged),
        message=pf_result.message,
        losses_mw=losses,
        tables=tables,
        diagnostics={
            **pf_result.diagnostics,
            "iterations": pf_result.iterations,
            "max_mismatch": pf_result.max_mismatch,
            "acdcpf_module": _module_path("acdcpf"),
        },
    )


def _snapshot_from_tool1(label: str, result: PyomoACDCOPFResult) -> BenchmarkRun:
    extracted = result.extracted_results or {}
    base_mva = float(result.data.get("base_mva", 100.0)) if result.data else 100.0
    raw_losses = dict(result.active_loss_breakdown_mw)
    if result.objective_total_active_losses_mw is not None:
        raw_losses["total_active_losses_mw"] = result.objective_total_active_losses_mw

    if result.success:
        losses = raw_losses
        tables = {
            "ac_buses": _opf_ac_bus_rows(result.data, extracted, base_mva),
            "dc_buses": _opf_dc_bus_rows(result.data, extracted, base_mva),
            "generators": _opf_generator_rows(result.data, extracted, base_mva),
            "loads": _opf_ac_load_rows(result.data, base_mva),
            "dc_generators": _opf_dc_generator_rows(result.data, extracted, base_mva),
            "dc_loads": _opf_dc_load_rows(result.data, base_mva),
            "storage_units": _opf_storage_rows(result.data, extracted, base_mva),
            "converters": _opf_converter_rows(result.data, extracted, base_mva),
            "dcdc_converters": _opf_dcdc_converter_rows(result.data, extracted, base_mva),
            "ac_branches": _opf_ac_branch_rows(result.data, extracted, base_mva),
            "dc_branches": _opf_dc_branch_rows(result.data, extracted, base_mva),
            "transformers": _opf_transformer_rows(result.data, extracted, base_mva),
        }
    else:
        losses = {}
        tables = {}
    diagnostics = {
        "solver_status": _solver_attr(result.solver_result, "status"),
        "termination_condition": result.message,
        "ipopt_executed_through_pyomo": result.solver_result is not None,
        "ipopt_iterations": _solver_iterations(result.solver_result),
        "max_pyomo_constraint_residual": (
            _max_constraint_residual(result.model) if result.model is not None else None
        ),
        "fixed_variable_groups": sorted(result.data.get("fixed", {}).keys()) if result.data else [],
        "control_margin_percent": result.data.get("metadata", {}).get("control_margin_percent")
        if result.data
        else None,
        "control_note": _opf_result_control_note(result.data),
        "converter_loss_mode": sorted({str(c.get("loss_mode", "fixed")) for c in result.data.get("converters", {}).values()}),
        "post_solve_validation_passed": result.validation.valid if result.validation else None,
        "max_variable_bound_violation": result.validation.max_bound_violation if result.validation else None,
        "max_storage_overlap_mw": result.validation.max_storage_overlap_mw if result.validation else None,
        "replay_comparison": result.replay_comparison,
    }
    if result.physics_validation is not None:
        diagnostics["physics_validation"] = result.physics_validation.to_dict()
    if result.data.get("metadata", {}).get("equipment_limits"):
        diagnostics["equipment_limits"] = result.data["metadata"]["equipment_limits"]
    if raw_losses and not result.success:
        diagnostics["untrusted_failed_iterate_loss_breakdown_mw"] = raw_losses
    return BenchmarkRun(
        label=label,
        success=bool(result.success),
        message=result.message,
        losses_mw=losses,
        tables=tables,
        diagnostics=diagnostics,
    )


def _opf_result_control_note(data: Mapping[str, Any] | None) -> str:
    if not data:
        return "Enabled Tool1 (acdcopf) controls could not be read from result metadata."

    metadata = data.get("metadata", {})
    enabled_controls = []
    if metadata.get("optimize_converter_active_power"):
        enabled_controls.append(OPF_CONTROL_DISPLAY_NAMES["vsc_p"])
    if metadata.get("optimize_converter_reactive_power"):
        enabled_controls.append(OPF_CONTROL_DISPLAY_NAMES["vsc_q"])
    if metadata.get("optimize_dcdc_voltage_ratio"):
        enabled_controls.append(OPF_CONTROL_DISPLAY_NAMES["dcdc_ratio"])
    if metadata.get("optimize_non_slack_generator_active_power"):
        enabled_controls.append(OPF_CONTROL_DISPLAY_NAMES["ac_gen_p"])
    if metadata.get("optimize_non_slack_generator_reactive_power"):
        enabled_controls.append(OPF_CONTROL_DISPLAY_NAMES["ac_gen_q"])
    if metadata.get("optimize_dc_generator_active_power"):
        enabled_controls.append(OPF_CONTROL_DISPLAY_NAMES["dc_gen_curtailment"])

    control_text = ", ".join(enabled_controls) if enabled_controls else "none"
    extra_note = " DC generator control is curtailment-only." if metadata.get(
        "dc_generator_curtailment_only"
    ) else ""
    return (
        f"Enabled Tool1 (acdcopf) controls: {control_text}. "
        "AC generator voltage setpoints and fixed loads remain fixed."
        f"{extra_note}"
    )


def _snapshot_from_pyflow_grid(
    label: str,
    grid: Any,
    *,
    success: bool,
    message: str,
    diagnostics: dict[str, Any] | None = None,
) -> BenchmarkRun:
    base_mva = float(grid.S_base)
    ac_loss = float(sum(float(getattr(line, "P_loss", 0.0)) for line in grid.lines_AC) * base_mva)
    dc_loss = float(sum(float(getattr(line, "loss", 0.0)) for line in grid.lines_DC) * base_mva)
    converter_rows = [_pyflow_converter_row(conv, base_mva) for conv in grid.Converters_ACDC]
    converter_loss = float(
        sum(_safe_float(row.get("p_loss_mw")) or 0.0 for row in converter_rows)
    )
    losses = {
        "ac_branch_mw": ac_loss,
        "dc_branch_mw": dc_loss,
        "converter_mw": converter_loss,
        "total_active_losses_mw": ac_loss + dc_loss + converter_loss,
    }
    tables = {
        "ac_buses": [
            {
                "id": int(node.nodeNumber),
                "name": str(node.name),
                "v_pu": float(node.V),
                "angle_deg": math.degrees(float(node.theta)),
            }
            for node in grid.nodes_AC
        ],
        "dc_buses": [
            {"id": int(node.nodeNumber), "name": str(node.name), "v_pu": float(node.V)}
            for node in grid.nodes_DC
        ],
        "generators": _pyflow_generator_rows(grid, base_mva),
        "converters": converter_rows,
        "ac_branches": [
            {
                "id": int(line.lineNumber),
                "name": str(line.name),
                "p_from_mw": float(getattr(line, "fromS", 0.0).real) * base_mva,
                "q_from_mvar": float(getattr(line, "fromS", 0.0).imag) * base_mva,
                "p_to_mw": float(getattr(line, "toS", 0.0).real) * base_mva,
                "q_to_mvar": float(getattr(line, "toS", 0.0).imag) * base_mva,
                "p_loss_mw": float(getattr(line, "P_loss", 0.0)) * base_mva,
                "q_loss_mvar": None,
                "s_from_mva": abs(getattr(line, "fromS", 0.0)) * base_mva,
                "s_to_mva": abs(getattr(line, "toS", 0.0)) * base_mva,
                "loading_percent": _pyflow_branch_loading_percent(line, base_mva),
                "from_bus": int(line.fromNode.nodeNumber),
                "to_bus": int(line.toNode.nodeNumber),
            }
            for line in grid.lines_AC
        ],
        "dc_branches": [
            {
                "id": int(line.lineNumber),
                "name": str(line.name),
                "p_from_mw": float(getattr(line, "fromP", 0.0)) * base_mva,
                "p_to_mw": float(getattr(line, "toP", 0.0)) * base_mva,
                "p_loss_mw": float(getattr(line, "loss", 0.0)) * base_mva,
                "loading_percent": _pyflow_dc_branch_loading_percent(line, base_mva),
                "from_bus": int(line.fromNode.nodeNumber),
                "to_bus": int(line.toNode.nodeNumber),
            }
            for line in grid.lines_DC
        ],
        "loads": _pyflow_load_rows(grid, base_mva),
        "transformers": [],
    }
    return BenchmarkRun(
        label=label,
        success=success,
        message=message,
        losses_mw=losses,
        tables=tables,
        diagnostics={
            **(diagnostics or {}),
            "pyflow_module": _module_path("pyflow_acdc"),
            "sign_note": "PyFlow converter P_AC/Q_AC are reported with ACDCPF load convention.",
        },
    )


def _pyflow_converter_row(conv: Any, base_mva: float) -> dict[str, Any]:
    p_ac_mw = -float(conv.P_AC) * base_mva
    q_ac_mvar = -float(conv.Q_AC) * base_mva
    p_dc_mw = float(getattr(conv, "P_DC", 0.0)) * base_mva
    electronic_loss_mw = float(getattr(conv, "P_loss", 0.0)) * base_mva
    rating_mva = _pyflow_converter_rating_mva(conv, base_mva)
    return {
        "id": int(conv.ConvNumber),
        "name": str(conv.name),
        "p_ac_mw": p_ac_mw,
        "q_ac_mvar": q_ac_mvar,
        "p_dc_mw": p_dc_mw,
        "p_loss_mw": _converter_station_loss_mw(p_ac_mw, p_dc_mw, electronic_loss_mw),
        "p_elec_loss_mw": electronic_loss_mw,
        "ac_bus": int(conv.Node_AC.nodeNumber),
        "dc_bus": int(conv.Node_DC.nodeNumber),
        "control_mode": str(getattr(conv, "type", "")),
        "s_mva": rating_mva,
        "loading_percent": _safe_percent(math.hypot(p_ac_mw, q_ac_mvar), rating_mva),
    }


def _acdcpf_ac_bus_rows(net: Any, generator_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = _merge_static_table_columns(
        _rows_from_table(
            getattr(net, "res_ac_bus", None),
            {
                "v_pu": "v_pu",
                "angle_deg": "v_angle_deg",
            },
        ),
        getattr(net, "ac_bus", None),
        {"name": "name"},
    )
    if not rows:
        return rows

    generators = generator_rows or _acdcpf_static_generator_rows(net)
    loads = _rows_from_table(
        getattr(net, "ac_load", None),
        {"p_mw": "p_mw", "q_mvar": "q_mvar"},
        raw_columns={"bus": "bus"},
    )
    storage_units = [
        storage
        for storage in _acdcpf_storage_rows(net)
        if str(storage.get("bus_type", "dc")).lower() == "ac"
    ]
    ac_bus = getattr(net, "ac_bus", None)
    s_base = _safe_float(getattr(net, "s_base", 0.0)) or 0.0

    for row in rows:
        bus_id = row.get("id")
        p_gen = sum(_safe_float(gen.get("p_mw")) or 0.0 for gen in generators if gen.get("bus") == bus_id)
        q_gen = sum(_safe_float(gen.get("q_mvar")) or 0.0 for gen in generators if gen.get("bus") == bus_id)
        p_load = sum(_safe_float(load.get("p_mw")) or 0.0 for load in loads if load.get("bus") == bus_id)
        q_load = sum(_safe_float(load.get("q_mvar")) or 0.0 for load in loads if load.get("bus") == bus_id)
        p_storage = sum(
            _safe_float(storage.get("p_mw")) or 0.0
            for storage in storage_units
            if storage.get("bus") == bus_id
        )
        q_storage = sum(
            _safe_float(storage.get("q_mvar")) or 0.0
            for storage in storage_units
            if storage.get("bus") == bus_id
        )
        vm = _safe_float(row.get("v_pu")) or 1.0
        g_shunt = 0.0
        b_shunt = 0.0
        if ac_bus is not None and bus_id in ac_bus.index:
            bus_static = ac_bus.loc[bus_id]
            g_shunt = (_safe_float(bus_static.get("gs_pu")) or 0.0) * s_base
            b_shunt = (_safe_float(bus_static.get("bs_pu")) or 0.0) * s_base
        row["p_mw"] = p_gen + p_storage - p_load - g_shunt * vm**2
        row["q_mvar"] = q_gen + q_storage - q_load + b_shunt * vm**2
    return rows


def _acdcpf_generator_rows(net: Any) -> list[dict[str, Any]]:
    result_rows = _rows_from_table(
        getattr(net, "res_ac_gen", None),
        {"p_mw": "p_mw", "q_mvar": "q_mvar"},
    )
    if not result_rows:
        result_rows = _acdcpf_ppc_generator_rows(net)
    if not result_rows:
        result_rows = _acdcpf_static_generator_rows(net)
    return _merge_static_table_columns(
        result_rows,
        getattr(net, "ac_gen", None),
        {"name": "name", "bus": "bus", "v_pu": "v_pu"},
    )


def _acdcpf_storage_rows(net: Any) -> list[dict[str, Any]]:
    storage_table = getattr(net, "storage", None)
    if storage_table is None or getattr(storage_table, "empty", True):
        return []

    rows = []
    for idx, row in storage_table.iterrows():
        if not bool(row.get("in_service", True)):
            continue
        p_mw = _safe_float(row.get("p_mw")) or 0.0
        q_mvar = _safe_float(row.get("q_mvar")) or 0.0
        s_mva = _safe_float(
            row.get(
                "sn_mva",
                row.get("s_mva", row.get("s_nom_mva", row.get("nominal_mva"))),
            )
        )
        result_row = None
        res_storage = getattr(net, "res_storage", None)
        if res_storage is not None and idx in res_storage.index:
            result_row = res_storage.loc[idx]
            result_p = _safe_float(result_row.get("p_mw"))
            result_q = _safe_float(result_row.get("q_mvar"))
            p_mw = p_mw if result_p is None else result_p
            q_mvar = q_mvar if result_q is None else result_q
        soc = _safe_float(
            result_row.get("soc_percent") if result_row is not None else row.get("soc_percent")
        )
        energy_capacity_mwh = _safe_float(row.get("energy_mwh"))
        rows.append(
            {
                "id": int(idx),
                "name": str(row.get("name", f"Storage {int(idx)}")),
                "bus_type": str(row.get("bus_type", row.get("connection", "dc"))).lower(),
                "bus": _optional_int(row.get("bus")),
                "p_mw": p_mw,
                "p_charge_mw": max(-p_mw, 0.0),
                "p_discharge_mw": max(p_mw, 0.0),
                "q_mvar": q_mvar,
                "s_mva": s_mva,
                "energy_mwh": (
                    energy_capacity_mwh * soc / 100.0
                    if energy_capacity_mwh is not None and soc is not None
                    else None
                ),
                "energy_capacity_mwh": energy_capacity_mwh,
                "soc_percent": soc,
                "soc_min_percent": _safe_float(row.get("soc_min_percent")),
                "soc_max_percent": _safe_float(row.get("soc_max_percent")),
                "eta_charge": _safe_float(row.get("eta_charge")),
                "eta_discharge": _safe_float(row.get("eta_discharge")),
                "loading_percent": _safe_percent(math.hypot(p_mw, q_mvar), s_mva),
                "mode": _storage_mode(p_mw),
            }
        )
    return rows


def _acdcpf_static_generator_rows(net: Any) -> list[dict[str, Any]]:
    return _rows_from_table(
        getattr(net, "ac_gen", None),
        {"p_mw": "p_mw", "q_mvar": "q_mvar", "v_pu": "v_pu"},
        raw_columns={"bus": "bus"},
    )


def _acdcpf_ppc_generator_rows(net: Any) -> list[dict[str, Any]]:
    """Read solved generator PG/QG from ACDCPF's internal PyPOWER result matrices."""

    ac_gen = getattr(net, "ac_gen", None)
    if ac_gen is None or getattr(ac_gen, "empty", True):
        return []
    active_indices = [
        idx
        for idx, row in ac_gen.iterrows()
        if bool(row.get("in_service", True))
    ]
    ppc_results = getattr(net, "_ppc_results", None)
    if not ppc_results:
        return []

    rows: list[dict[str, Any]] = []
    static_pos = 0
    for ppc in ppc_results.values():
        gen_matrix = ppc.get("gen") if isinstance(ppc, dict) else None
        if gen_matrix is None:
            continue
        for result_pos in range(len(gen_matrix)):
            if static_pos >= len(active_indices):
                return rows
            static_idx = active_indices[static_pos]
            gen_row = gen_matrix[result_pos]
            rows.append(
                {
                    "id": int(static_idx) if _is_integer_like(static_idx) else str(static_idx),
                    "p_mw": _safe_float(gen_row[1]),
                    "q_mvar": _safe_float(gen_row[2]),
                    "v_pu": _safe_float(gen_row[5]) if len(gen_row) > 5 else None,
                }
            )
            static_pos += 1
    return rows


def _acdcpf_ac_branch_rows(net: Any) -> list[dict[str, Any]]:
    rows = _merge_static_table_columns(
        _rows_from_table(
            getattr(net, "res_ac_line", None),
            {
                "p_from_mw": "p_from_mw",
                "q_from_mvar": "q_from_mvar",
                "p_to_mw": "p_to_mw",
                "q_to_mvar": "q_to_mvar",
                "p_loss_mw": "p_loss_mw",
                "q_loss_mvar": "q_loss_mvar",
                "i_ka": "i_ka",
            },
        ),
        getattr(net, "ac_line", None),
        {
            "name": "name",
            "from_bus": "from_bus",
            "to_bus": "to_bus",
            "length_km": "length_km",
            "max_i_ka": "max_i_ka",
            "rate_mva": "rate_mva",
            "s_mva": "s_mva",
            "sn_mva": "sn_mva",
            "tap": "tap",
            "shift_deg": "shift_deg",
        },
    )
    static = getattr(net, "ac_line", None)
    for row in rows:
        element_id = row.get("id")
        static_row = _static_row(static, element_id)
        row["s_from_mva"] = _apparent_power_mva(row.get("p_from_mw"), row.get("q_from_mvar"))
        row["s_to_mva"] = _apparent_power_mva(row.get("p_to_mw"), row.get("q_to_mvar"))
        row["rate_mva"] = _acdcpf_ac_line_rate_mva(net, static_row)
        row["loading_percent"] = _ac_branch_loading_percent(row)
    return rows


def _acdcpf_dc_branch_rows(net: Any) -> list[dict[str, Any]]:
    rows = _merge_static_table_columns(
        _rows_from_table(
            getattr(net, "res_dc_line", None),
            {
                "p_from_mw": "p_from_mw",
                "p_to_mw": "p_to_mw",
                "p_loss_mw": "p_loss_mw",
                "i_ka": "i_ka",
            },
        ),
        getattr(net, "dc_line", None),
        {
            "name": "name",
            "from_bus": "from_bus",
            "to_bus": "to_bus",
            "length_km": "length_km",
            "max_i_ka": "max_i_ka",
            "rate_mw": "rate_mw",
        },
    )
    static = getattr(net, "dc_line", None)
    for row in rows:
        element_id = row.get("id")
        static_row = _static_row(static, element_id)
        row["rate_mw"] = _acdcpf_dc_line_rate_mw(net, static_row)
        row["loading_percent"] = _dc_branch_loading_percent(row)
    return rows


def _acdcpf_vsc_rows(net: Any) -> list[dict[str, Any]]:
    rows = _merge_static_table_columns(
        _rows_from_table(
            getattr(net, "res_vsc", None),
            {
                "p_ac_mw": "p_ac_mw",
                "q_ac_mvar": "q_ac_mvar",
                "p_dc_mw": "p_dc_mw",
                "p_loss_mw": "p_loss_mw",
                "v_ac_pu": "v_ac_pu",
                "v_dc_pu": "v_dc_pu",
                "v_converter_pu": "v_converter_pu",
                "i_ac_ka": "i_ac_ka",
                "i_dc_ka": "i_dc_ka",
            },
        ),
        getattr(net, "vsc", None),
        {
            "name": "name",
            "ac_bus": "ac_bus",
            "dc_bus": "dc_bus",
            "control_mode": "control_mode",
            "s_mva": "s_mva",
        },
    )
    for row in rows:
        electronic_loss_mw = row.get("p_loss_mw")
        row["p_elec_loss_mw"] = electronic_loss_mw
        row["p_loss_mw"] = _converter_station_loss_mw(
            row.get("p_ac_mw"),
            row.get("p_dc_mw"),
            electronic_loss_mw,
        )
        row["loading_percent"] = _safe_percent(
            _apparent_power_mva(row.get("p_ac_mw"), row.get("q_ac_mvar")),
            row.get("s_mva"),
        )
        if row.get("i_dc_ka") is None:
            row["i_dc_ka"] = _acdcpf_vsc_dc_current_ka(net, row)
    return rows


def _acdcpf_dcdc_rows(net: Any) -> list[dict[str, Any]]:
    rows = _merge_static_table_columns(
        _rows_from_table(
            getattr(net, "res_dcdc", None),
            {
                "p_from_mw": "p_from_mw",
                "p_to_mw": "p_to_mw",
                "p_loss_mw": "p_loss_mw",
            },
        ),
        getattr(net, "dcdc", None),
        {
            "name": "name",
            "from_bus": "from_bus",
            "to_bus": "to_bus",
            "d_ratio": "d_ratio",
            "r_ohm": "r_ohm",
            "rate_mw": "rate_mw",
        },
    )
    for row in rows:
        row["loading_percent"] = _safe_percent(
            max(abs(_safe_float(row.get("p_from_mw")) or 0.0), abs(_safe_float(row.get("p_to_mw")) or 0.0)),
            row.get("rate_mw"),
        )
    return rows


def _acdcpf_transformer_rows(net: Any) -> list[dict[str, Any]]:
    """Return transformer rows when the case defines transformers explicitly or through AC branch taps."""

    explicit = _explicit_transformer_rows(net)
    if explicit:
        return explicit
    return [_branch_transformer_row(row) for row in _acdcpf_ac_branch_rows(net) if _is_transformer_like_branch(row)]


def _explicit_transformer_rows(net: Any) -> list[dict[str, Any]]:
    result_table = _first_nonempty_table(
        getattr(net, "res_trafo", None),
        getattr(net, "res_transformer", None),
    )
    static_table = _first_nonempty_table(
        getattr(net, "trafo", None),
        getattr(net, "transformer", None),
    )
    if result_table is None:
        return []

    rows = _merge_static_table_columns(
        _rows_from_table(
            result_table,
            {
                "p_hv_mw": "p_from_mw",
                "q_hv_mvar": "q_from_mvar",
                "p_lv_mw": "p_to_mw",
                "q_lv_mvar": "q_to_mvar",
                "p_loss_mw": "p_loss_mw",
                "q_loss_mvar": "q_loss_mvar",
                "s_hv_mva": "s_from_mva",
                "s_lv_mva": "s_to_mva",
                "i_ka": "i_ka",
                "loading_percent": "loading_percent",
                "tap": "tap",
                "shift_deg": "shift_deg",
            },
        ),
        static_table,
        {
            "name": "name",
            "hv_bus": "from_bus",
            "lv_bus": "to_bus",
            "rate_mva": "sn_mva",
            "tap": "tap",
            "shift_deg": "shift_deg",
        },
    )
    for row in rows:
        row["from_bus"] = row.get("hv_bus")
        row["to_bus"] = row.get("lv_bus")
        row["p_from_mw"] = row.get("p_hv_mw")
        row["q_from_mvar"] = row.get("q_hv_mvar")
        row["p_to_mw"] = row.get("p_lv_mw")
        row["q_to_mvar"] = row.get("q_lv_mvar")
        if row.get("loading_percent") is None:
            row["loading_percent"] = _safe_percent(
                max(
                    _safe_float(row.get("s_hv_mva")) or 0.0,
                    _safe_float(row.get("s_lv_mva")) or 0.0,
                ),
                row.get("rate_mva"),
            )
    return rows


def _branch_transformer_row(branch: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": branch.get("id"),
        "name": branch.get("name"),
        "from_bus": branch.get("from_bus"),
        "to_bus": branch.get("to_bus"),
        "hv_bus": branch.get("from_bus"),
        "lv_bus": branch.get("to_bus"),
        "p_from_mw": branch.get("p_from_mw"),
        "q_from_mvar": branch.get("q_from_mvar"),
        "p_to_mw": branch.get("p_to_mw"),
        "q_to_mvar": branch.get("q_to_mvar"),
        "p_hv_mw": branch.get("p_from_mw"),
        "q_hv_mvar": branch.get("q_from_mvar"),
        "p_lv_mw": branch.get("p_to_mw"),
        "q_lv_mvar": branch.get("q_to_mvar"),
        "p_loss_mw": branch.get("p_loss_mw"),
        "q_loss_mvar": branch.get("q_loss_mvar"),
        "s_hv_mva": branch.get("s_from_mva"),
        "s_lv_mva": branch.get("s_to_mva"),
        "i_ka": branch.get("i_ka"),
        "rate_mva": branch.get("rate_mva"),
        "loading_percent": branch.get("loading_percent"),
        "tap": branch.get("tap"),
        "shift_deg": branch.get("shift_deg"),
    }


def _static_row(table: Any, element_id: Any) -> Mapping[str, Any] | None:
    if table is None or getattr(table, "empty", True) or element_id not in table.index:
        return None
    return table.loc[element_id]


def _first_nonempty_table(*tables: Any) -> Any | None:
    for table in tables:
        if table is not None and not getattr(table, "empty", True):
            return table
    return None


def _acdcpf_ac_line_rate_mva(net: Any, static_row: Mapping[str, Any] | None) -> float | None:
    if static_row is None:
        return None
    direct_rating = _first_safe_float(
        static_row,
        ("rate_mva", "rating_mva", "mva_rating", "MVA_rating", "s_mva", "sn_mva", "max_s_mva"),
    )
    if direct_rating is not None and direct_rating > 0.0:
        return direct_rating
    max_i_ka = _safe_float(static_row.get("max_i_ka"))
    if max_i_ka is None or max_i_ka <= 0.0:
        return None
    from_bus = int(static_row["from_bus"])
    ac_bus = getattr(net, "ac_bus", None)
    if ac_bus is None or from_bus not in ac_bus.index:
        return None
    vr_kv = _safe_float(ac_bus.loc[from_bus].get("vr_kv"))
    if vr_kv is None or vr_kv <= 0.0:
        return None
    return float(math.sqrt(3.0) * vr_kv * max_i_ka)


def _acdcpf_dc_line_rate_mw(net: Any, static_row: Mapping[str, Any] | None) -> float | None:
    if static_row is None:
        return None
    direct_rating = _first_safe_float(
        static_row,
        ("rate_mw", "rating_mw", "mw_rating", "MW_rating", "p_max_mw", "max_p_mw"),
    )
    if direct_rating is not None and direct_rating > 0.0:
        return direct_rating
    max_i_ka = _safe_float(static_row.get("max_i_ka"))
    if max_i_ka is None or max_i_ka <= 0.0:
        return None
    from_bus = int(static_row["from_bus"])
    dc_bus = getattr(net, "dc_bus", None)
    if dc_bus is None or from_bus not in dc_bus.index:
        return None
    v_base_kv = _safe_float(dc_bus.loc[from_bus].get("v_base"))
    if v_base_kv is None or v_base_kv <= 0.0:
        return None
    return float(getattr(net, "pol", 1.0)) * v_base_kv * max_i_ka


def _ac_branch_loading_percent(row: Mapping[str, Any]) -> float | None:
    return _safe_percent(
        max(_safe_float(row.get("s_from_mva")) or 0.0, _safe_float(row.get("s_to_mva")) or 0.0),
        row.get("rate_mva"),
    )


def _dc_branch_loading_percent(row: Mapping[str, Any]) -> float | None:
    rate_mw = _safe_float(row.get("rate_mw"))
    if rate_mw is not None and rate_mw > 0.0:
        return _safe_percent(
            max(abs(_safe_float(row.get("p_from_mw")) or 0.0), abs(_safe_float(row.get("p_to_mw")) or 0.0)),
            rate_mw,
        )

    current = _safe_float(row.get("i_ka"))
    if current is not None:
        static_limit = _safe_float(row.get("max_i_ka"))
        if static_limit is not None and static_limit > 0.0:
            return 100.0 * abs(current) / static_limit
    return None


def _acdcpf_vsc_dc_current_ka(net: Any, row: Mapping[str, Any]) -> float | None:
    p_dc_mw = _safe_float(row.get("p_dc_mw"))
    dc_bus = row.get("dc_bus")
    if p_dc_mw is None or dc_bus is None:
        return None
    try:
        dc_bus_idx = int(dc_bus)
    except (TypeError, ValueError):
        return None

    dc_table = getattr(net, "dc_bus", None)
    if dc_table is None or dc_bus_idx not in dc_table.index:
        return None

    v_base_kv = _safe_float(dc_table.loc[dc_bus_idx].get("v_base"))
    v_dc_pu = _safe_float(row.get("v_dc_pu"))
    if v_dc_pu is None:
        res_dc_bus = getattr(net, "res_dc_bus", None)
        if res_dc_bus is not None and dc_bus_idx in res_dc_bus.index:
            v_dc_pu = _safe_float(res_dc_bus.loc[dc_bus_idx].get("v_dc_pu"))
    if v_base_kv is None or v_dc_pu is None or v_base_kv <= 0.0 or v_dc_pu <= 0.0:
        return None

    # VSC-side DC current follows the OPF relation Pdc = Vdc * Idc.
    # DC line currents remain reported with ACDCPF's per-conductor convention.
    return abs(p_dc_mw) / (v_base_kv * v_dc_pu)


def _apparent_power_mva(p_mw: Any, q_mvar: Any) -> float | None:
    p_value = _safe_float(p_mw)
    q_value = _safe_float(q_mvar)
    if p_value is None or q_value is None:
        return None
    return math.hypot(p_value, q_value)


def _converter_station_loss_mw(
    p_ac_mw: Any,
    p_dc_mw: Any,
    fallback_loss_mw: Any = None,
) -> float | None:
    p_ac_value = _safe_float(p_ac_mw)
    p_dc_value = _safe_float(p_dc_mw)
    if p_ac_value is not None and p_dc_value is not None:
        return p_ac_value - p_dc_value
    return _safe_float(fallback_loss_mw)


def _safe_percent(value: Any, baseline: Any) -> float | None:
    value_float = _safe_float(value)
    baseline_float = _safe_float(baseline)
    if value_float is None or baseline_float is None or baseline_float <= 0.0:
        return None
    return 100.0 * value_float / baseline_float


def _storage_mode(p_mw: Any) -> str:
    value = _safe_float(p_mw) or 0.0
    if value > 1e-9:
        return "discharging"
    if value < -1e-9:
        return "charging"
    return "idle"


def _is_transformer_like_branch(row: Mapping[str, Any]) -> bool:
    name = str(row.get("name", "")).lower()
    if "trafo" in name or "transformer" in name:
        return True
    tap = _safe_float(row.get("tap"))
    shift = _safe_float(row.get("shift_deg"))
    return (
        tap is not None
        and abs(tap - 1.0) > 1e-12
    ) or (
        shift is not None
        and abs(shift) > 1e-12
    )


def _pyflow_converter_rating_mva(converter: Any, base_mva: float) -> float | None:
    for attr in ("MVA_max", "MVA_rating", "S_max", "S_rated", "rating"):
        value = _safe_float(getattr(converter, attr, None))
        if value is not None and value > 0.0:
            return value * base_mva if value <= 10.0 else value
    return None


def _pyflow_branch_loading_percent(line: Any, base_mva: float) -> float | None:
    rating_mva = None
    for attr in ("MVA_rating", "S_max", "rateA", "rate"):
        value = _safe_float(getattr(line, attr, None))
        if value is not None and value > 0.0:
            rating_mva = value * base_mva if value <= 10.0 else value
            break
    if rating_mva is None:
        return None
    return _safe_percent(
        max(abs(getattr(line, "fromS", 0.0)), abs(getattr(line, "toS", 0.0))) * base_mva,
        rating_mva,
    )


def _pyflow_dc_branch_loading_percent(line: Any, base_mva: float) -> float | None:
    rating_mw = None
    for attr in ("MW_rating", "P_max", "rateA", "rate"):
        value = _safe_float(getattr(line, attr, None))
        if value is not None and value > 0.0:
            rating_mw = value * base_mva if value <= 10.0 else value
            break
    if rating_mw is None:
        return None
    return _safe_percent(
        max(abs(float(getattr(line, "fromP", 0.0))), abs(float(getattr(line, "toP", 0.0)))) * base_mva,
        rating_mw,
    )


def _pyflow_load_rows(grid: Any, base_mva: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in getattr(grid, "nodes_AC", []):
        p_load = _safe_float(getattr(node, "PLi", None))
        q_load = _safe_float(getattr(node, "QLi", None))
        if p_load is None and q_load is None:
            continue
        if abs(p_load or 0.0) <= 1e-12 and abs(q_load or 0.0) <= 1e-12:
            continue
        rows.append(
            {
                "id": int(node.nodeNumber),
                "name": f"Load at {getattr(node, 'name', node.nodeNumber)}",
                "bus": int(node.nodeNumber),
                "p_mw": (p_load or 0.0) * base_mva,
                "q_mvar": (q_load or 0.0) * base_mva,
            }
        )
    return rows


def _pyflow_generator_rows(grid: Any, base_mva: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in grid.nodes_AC:
        connected_gens = list(getattr(node, "connected_gen", []))
        if not connected_gens:
            continue

        p_gen = _pyflow_node_generation_pu(grid, node, "P")
        q_gen = _pyflow_node_generation_pu(grid, node, "Q")
        if len(connected_gens) == 1:
            gen = connected_gens[0]
            gen_id = int(gen.genNumber)
            seen.add(gen_id)
            rows.append(
                {
                    "id": gen_id,
                    "name": str(gen.name),
                    "bus": int(node.nodeNumber),
                    "p_mw": p_gen * base_mva,
                    "q_mvar": q_gen * base_mva,
                }
            )
            continue

        for gen in connected_gens:
            gen_id = int(gen.genNumber)
            seen.add(gen_id)
            rows.append(
                {
                    "id": gen_id,
                    "name": str(gen.name),
                    "bus": int(node.nodeNumber),
                    "p_mw": float(gen.PGen) * base_mva,
                    "q_mvar": float(gen.QGen) * base_mva,
                    "v_pu": float(getattr(node, "V", 1.0)),
                }
            )

    for gen in grid.Generators:
        gen_id = int(gen.genNumber)
        if gen_id in seen:
            continue
        row = {
            "id": gen_id,
            "name": str(gen.name),
            "p_mw": float(gen.PGen) * base_mva,
            "q_mvar": float(gen.QGen) * base_mva,
        }
        node = getattr(gen, "Node_AC", None)
        if node is not None and hasattr(node, "nodeNumber"):
            row["bus"] = int(node.nodeNumber)
            row["v_pu"] = float(getattr(node, "V", 1.0))
        rows.append(row)
    return rows


def _pyflow_node_generation_pu(grid: Any, node: Any, quantity: str) -> float:
    if quantity == "P":
        injection = float(getattr(node, "P_INJ", 0.0))
        load = float(getattr(node, "PLi", 0.0))
        converter = sum(
            float(grid.Converters_ACDC[int(conv_idx)].P_AC)
            for conv_idx in getattr(node, "connected_conv", [])
        )
    else:
        injection = float(getattr(node, "Q_INJ", 0.0))
        load = float(getattr(node, "QLi", 0.0))
        converter = sum(
            float(grid.Converters_ACDC[int(conv_idx)].Q_AC)
            for conv_idx in getattr(node, "connected_conv", [])
        )
    return injection + load - converter


def _opf_ac_bus_rows(
    data: Mapping[str, Any],
    extracted: dict[str, Any],
    base_mva: float,
) -> list[dict[str, Any]]:
    buses = data.get("ac_buses", {}) if data else {}
    voltage = extracted.get("ac_bus_voltage_magnitude_pu", {})
    angle = extracted.get("ac_bus_voltage_angle_rad", {})
    rows: list[dict[str, Any]] = []
    for key in sorted(set(buses) | set(voltage), key=_natural_key):
        static = buses.get(key, {})
        if int(static.get("status", 1)) == 0:
            continue
        v_pu = _safe_float(voltage.get(key, static.get("v0", 1.0)))
        angle_rad = _safe_float(angle.get(key, static.get("theta0", 0.0))) or 0.0
        p_pu, q_pu = _opf_ac_bus_injection_pu(data, extracted, key, v_pu)
        rows.append(
            {
                "id": _opf_element_id(key),
                "name": static.get("name", f"AC bus {_opf_element_id(key)}"),
                "v_pu": v_pu,
                "angle_deg": math.degrees(angle_rad),
                "p_mw": p_pu * base_mva,
                "q_mvar": q_pu * base_mva,
            }
        )
    return rows


def _opf_dc_bus_rows(
    data: Mapping[str, Any],
    extracted: dict[str, Any],
    base_mva: float,
) -> list[dict[str, Any]]:
    buses = data.get("dc_buses", {}) if data else {}
    voltage = extracted.get("dc_bus_voltage_pu", {})
    rows: list[dict[str, Any]] = []
    for key in sorted(set(buses) | set(voltage), key=_natural_key):
        static = buses.get(key, {})
        if int(static.get("status", 1)) == 0:
            continue
        v_pu = _safe_float(voltage.get(key, static.get("v0", 1.0)))
        rows.append(
            {
                "id": _opf_element_id(key),
                "name": static.get("name", f"DC bus {_opf_element_id(key)}"),
                "v_pu": v_pu,
                "v_kv": _opf_dc_bus_voltage_kv(data, key, v_pu),
                "p_mw": _opf_dc_bus_injection_pu(data, extracted, key) * base_mva,
            }
        )
    return rows


def _opf_ac_bus_injection_pu(
    data: Mapping[str, Any],
    extracted: Mapping[str, Any],
    bus_key: str,
    v_pu: float | None,
) -> tuple[float, float]:
    generators = data.get("generators", {}) if data else {}
    loads = data.get("ac_loads", {}) if data else {}
    storage_units = data.get("storage_units", {}) if data else {}
    pg = extracted.get("generator_pg_pu", {})
    qg = extracted.get("generator_qg_pu", {})
    pst = extracted.get("storage_p_pu", {})
    qst = extracted.get("storage_q_pu", {})
    p_gen = sum(float(pg.get(key, 0.0)) for key, gen in generators.items() if gen.get("bus") == bus_key)
    q_gen = sum(float(qg.get(key, 0.0)) for key, gen in generators.items() if gen.get("bus") == bus_key)
    p_storage = sum(
        float(pst.get(key, 0.0))
        for key, storage in storage_units.items()
        if storage.get("bus_type") == "ac" and storage.get("bus") == bus_key
    )
    q_storage = sum(
        float(qst.get(key, 0.0))
        for key, storage in storage_units.items()
        if storage.get("bus_type") == "ac" and storage.get("bus") == bus_key
    )
    p_load = sum(
        float(load.get("p", 0.0))
        for load in loads.values()
        if int(load.get("status", 1)) != 0 and load.get("bus") == bus_key
    )
    q_load = sum(
        float(load.get("q", 0.0))
        for load in loads.values()
        if int(load.get("status", 1)) != 0 and load.get("bus") == bus_key
    )
    bus = data.get("ac_buses", {}).get(bus_key, {}) if data else {}
    voltage = v_pu if v_pu is not None else _safe_float(bus.get("v0")) or 1.0
    p_shunt = float(bus.get("g_shunt", 0.0)) * voltage**2
    q_shunt = float(bus.get("b_shunt", 0.0)) * voltage**2
    return p_gen + p_storage - p_load - p_shunt, q_gen + q_storage - q_load + q_shunt


def _opf_dc_bus_injection_pu(
    data: Mapping[str, Any],
    extracted: Mapping[str, Any],
    bus_key: str,
) -> float:
    converters = data.get("converters", {}) if data else {}
    generators = data.get("dc_generators", {}) if data else {}
    loads = data.get("dc_loads", {}) if data else {}
    storage_units = data.get("storage_units", {}) if data else {}
    p_dc_converter = extracted.get("converter_p_dc_pu", {})
    p_dc_gen = extracted.get("dc_generator_pg_pu", {})
    pst = extracted.get("storage_p_pu", {})
    converter_injection = sum(
        -float(p_dc_converter.get(key, 0.0))
        for key, converter in converters.items()
        if converter.get("dc_bus") == bus_key
    )
    generator_injection = sum(
        float(p_dc_gen.get(key, 0.0))
        for key, generator in generators.items()
        if generator.get("bus") == bus_key
    )
    load_consumption = sum(
        float(load.get("p", 0.0))
        for load in loads.values()
        if int(load.get("status", 1)) != 0 and load.get("bus") == bus_key
    )
    storage_injection = sum(
        float(pst.get(key, 0.0))
        for key, storage in storage_units.items()
        if storage.get("bus_type") == "dc" and storage.get("bus") == bus_key
    )
    return converter_injection + generator_injection + storage_injection - load_consumption


def _opf_generator_rows(
    data: Mapping[str, Any],
    extracted: dict[str, Any],
    base_mva: float,
) -> list[dict[str, Any]]:
    pg = extracted.get("generator_pg_pu", {})
    qg = extracted.get("generator_qg_pu", {})
    generators = data.get("generators", {}) if data else {}
    rows = []
    for key in pg:
        static = generators.get(key, {})
        bus_key = static.get("bus")
        rows.append(
            {
                "id": _opf_element_id(key),
                "name": static.get("name", f"Gen {_opf_element_id(key)}"),
                "bus": _opf_element_id(bus_key or ""),
                "p_mw": float(pg[key]) * base_mva,
                "q_mvar": float(qg.get(key, 0.0)) * base_mva,
                "v_pu": _opf_ac_bus_voltage_pu(data, extracted, bus_key),
            }
        )
    return rows


def _opf_ac_load_rows(data: Mapping[str, Any], base_mva: float) -> list[dict[str, Any]]:
    ac_loads = data.get("ac_loads", {}) if data else {}
    rows = []
    for key, load in ac_loads.items():
        if int(load.get("status", 1)) == 0:
            continue
        rows.append(
            {
                "id": _opf_element_id(key),
                "name": load.get("name", f"AC load {_opf_element_id(key)}"),
                "bus": _opf_element_id(load.get("bus", "")),
                "p_mw": float(load.get("p", 0.0)) * base_mva,
                "q_mvar": float(load.get("q", 0.0)) * base_mva,
            }
        )
    return rows


def _opf_dc_generator_rows(
    data: Mapping[str, Any],
    extracted: dict[str, Any],
    base_mva: float,
) -> list[dict[str, Any]]:
    pg = extracted.get("dc_generator_pg_pu", {})
    dc_generators = data.get("dc_generators", {}) if data else {}
    rows = []
    for key, value in pg.items():
        static = dc_generators.get(key, {})
        rows.append(
            {
                "id": _opf_element_id(key),
                "name": static.get("name", f"DC generator {_opf_element_id(key)}"),
                "bus": _opf_element_id(static.get("bus", "")),
                "p_mw": float(value) * base_mva,
            }
        )
    return rows


def _opf_dc_load_rows(data: Mapping[str, Any], base_mva: float) -> list[dict[str, Any]]:
    dc_loads = data.get("dc_loads", {}) if data else {}
    rows = []
    for key, load in dc_loads.items():
        if int(load.get("status", 1)) == 0:
            continue
        rows.append(
            {
                "id": _opf_element_id(key),
                "name": load.get("name", f"DC load {_opf_element_id(key)}"),
                "bus": _opf_element_id(load.get("bus", "")),
                "p_mw": float(load.get("p", 0.0)) * base_mva,
            }
        )
    return rows


def _opf_storage_rows(
    data: Mapping[str, Any],
    extracted: dict[str, Any],
    base_mva: float,
) -> list[dict[str, Any]]:
    storage_units = data.get("storage_units", {}) if data else {}
    p_storage = extracted.get("storage_p_pu", {})
    p_charge = extracted.get("storage_charge_p_pu", {})
    p_discharge = extracted.get("storage_discharge_p_pu", {})
    q_storage = extracted.get("storage_q_pu", {})
    soc_percent = extracted.get("storage_soc_percent", {})
    energy_mwh = extracted.get("storage_energy_mwh", {})
    rows = []
    for key in sorted(set(storage_units) | set(p_storage), key=_natural_key):
        static = storage_units.get(key, {})
        if int(static.get("status", 1)) == 0:
            continue
        p_pu = float(p_storage.get(key, static.get("p0", 0.0)))
        p_mw = p_pu * base_mva
        p_charge_mw = float(
            p_charge.get(key, max(-p_pu, 0.0))
        ) * base_mva
        p_discharge_mw = float(
            p_discharge.get(key, max(p_pu, 0.0))
        ) * base_mva
        q_mvar = float(q_storage.get(key, static.get("q0", 0.0))) * base_mva
        s_mva = _safe_float(static.get("s_rating"))
        s_mva = s_mva * base_mva if s_mva is not None else None
        energy_capacity_mwh = float(static.get("energy_mwh", 0.0))
        initial_energy_mwh = float(static.get("soc0", 0.0)) * energy_capacity_mwh
        rows.append(
            {
                "id": _opf_element_id(key),
                "name": static.get("name", f"Storage {_opf_element_id(key)}"),
                "bus_type": static.get("bus_type", "dc"),
                "bus": _opf_element_id(static.get("bus", "")),
                "p_mw": p_mw,
                "p_charge_mw": p_charge_mw,
                "p_discharge_mw": p_discharge_mw,
                "q_mvar": q_mvar,
                "s_mva": s_mva,
                "energy_mwh": float(energy_mwh.get(key, initial_energy_mwh)),
                "energy_capacity_mwh": energy_capacity_mwh,
                "soc_percent": float(soc_percent.get(key, 100.0 * float(static.get("soc0", 0.0)))),
                "soc_min_percent": 100.0 * float(static.get("soc_min", 0.0)),
                "soc_max_percent": 100.0 * float(static.get("soc_max", 1.0)),
                "eta_charge": float(static.get("eta_charge", 1.0)),
                "eta_discharge": float(static.get("eta_discharge", 1.0)),
                "loading_percent": _safe_percent(math.hypot(p_mw, q_mvar), s_mva),
                "mode": _storage_mode(p_mw),
            }
        )
    return rows


def _opf_converter_rows(
    data: Mapping[str, Any],
    extracted: dict[str, Any],
    base_mva: float,
) -> list[dict[str, Any]]:
    p_ac = extracted.get("converter_p_ac_terminal_absorbed_pu", {})
    q_ac = extracted.get("converter_q_ac_terminal_absorbed_pu", {})
    p_dc = extracted.get("converter_p_dc_pu", {})
    electronic = extracted.get("converter_loss_pu", {})
    transformer = extracted.get("converter_transformer_loss_pu", {})
    reactor = extracted.get("converter_phase_reactor_loss_pu", {})
    i_ac = extracted.get("converter_i_ac_pu", {})
    i_dc = extracted.get("converter_i_dc_pu", {})
    ac_voltage = extracted.get("ac_bus_voltage_magnitude_pu", {})
    dc_voltage = extracted.get("dc_bus_voltage_pu", {})
    converters = data.get("converters", {}) if data else {}
    rows = []
    for key in p_ac:
        static = converters.get(key, {})
        ac_bus_key = static.get("ac_bus")
        dc_bus_key = static.get("dc_bus")
        loss_pu = (
            float(electronic.get(key, 0.0))
            + float(transformer.get(key, 0.0))
            + float(reactor.get(key, 0.0))
        )
        p_ac_mw = float(p_ac[key]) * base_mva
        q_ac_mvar = float(q_ac.get(key, 0.0)) * base_mva
        p_dc_mw = -float(p_dc.get(key, 0.0)) * base_mva
        electronic_loss_mw = float(electronic.get(key, 0.0)) * base_mva
        s_mva = _safe_float(static.get("s_ac_rated"))
        s_mva = s_mva * base_mva if s_mva is not None else None
        rows.append(
            {
                "id": _opf_element_id(key),
                "name": static.get("name", f"VSC {_opf_element_id(key)}"),
                "ac_bus": _opf_element_id(ac_bus_key or ""),
                "dc_bus": _opf_element_id(dc_bus_key or ""),
                "control_mode": static.get("control_mode"),
                "p_ac_mw": p_ac_mw,
                "q_ac_mvar": q_ac_mvar,
                "p_dc_mw": p_dc_mw,
                "p_loss_mw": loss_pu * base_mva,
                "p_elec_loss_mw": electronic_loss_mw,
                "s_mva": s_mva,
                "loading_percent": _safe_percent(math.hypot(p_ac_mw, q_ac_mvar), s_mva),
                "i_ac_ka": _opf_converter_ac_current_ka(data, static, i_ac.get(key), base_mva),
                "i_dc_ka": _opf_converter_dc_current_ka(data, static, i_dc.get(key), base_mva),
                "v_ac_pu": ac_voltage.get(ac_bus_key),
                "v_dc_pu": dc_voltage.get(dc_bus_key),
            }
        )
    return rows


def _opf_dcdc_converter_rows(
    data: Mapping[str, Any],
    extracted: dict[str, Any],
    base_mva: float,
) -> list[dict[str, Any]]:
    p_from = extracted.get("dcdc_p_from_pu", {})
    p_to = extracted.get("dcdc_p_to_pu", {})
    loss = extracted.get("dcdc_loss_pu", {})
    ratio = extracted.get("dcdc_ratio_pu", {})
    dcdc_converters = data.get("dcdc_converters", {}) if data else {}
    rows = []
    for key in sorted(set(p_from) | set(p_to) | set(loss) | set(ratio), key=_natural_key):
        static = dcdc_converters.get(key, {})
        solved_d_pu = float(ratio.get(key, static.get("d_pu", 0.0)))
        static_d_pu = float(static.get("d_pu", 0.0) or 0.0)
        static_d_ratio = float(static.get("d_ratio", solved_d_pu) or solved_d_pu)
        solved_d_ratio = (
            solved_d_pu * static_d_ratio / static_d_pu
            if abs(static_d_pu) > 1e-12
            else solved_d_pu
        )
        row = {
                "id": _opf_element_id(key),
                "name": static.get("name", f"DCDC {_opf_element_id(key)}"),
                "from_bus": _opf_element_id(static.get("from", "")),
                "to_bus": _opf_element_id(static.get("to", "")),
                "p_from_mw": float(p_from.get(key, 0.0)) * base_mva,
                "p_to_mw": float(p_to.get(key, 0.0)) * base_mva,
                "p_loss_mw": float(loss.get(key, 0.0)) * base_mva,
                "d_ratio": solved_d_ratio,
                "rate_mw": _opf_rate_mw_or_mva(static.get("rate"), base_mva),
            }
        row["loading_percent"] = _safe_percent(
            max(abs(row["p_from_mw"]), abs(row["p_to_mw"])),
            row.get("rate_mw"),
        )
        rows.append(row)
    return rows


def _opf_ac_branch_rows(
    data: Mapping[str, Any],
    extracted: dict[str, Any],
    base_mva: float,
    *,
    include_transformers: bool = False,
) -> list[dict[str, Any]]:
    p_from = extracted.get("ac_branch_p_from_pu", {})
    q_from = extracted.get("ac_branch_q_from_pu", {})
    p_to = extracted.get("ac_branch_p_to_pu", {})
    q_to = extracted.get("ac_branch_q_to_pu", {})
    branches = data.get("ac_branches", {}) if data else {}
    rows = []
    for key in p_from:
        static = branches.get(key, {})
        is_transformer = str(static.get("kind", "line")).lower() == "transformer"
        if is_transformer != include_transformers:
            continue
        p_from_mw = float(p_from[key]) * base_mva
        q_from_mvar = float(q_from.get(key, 0.0)) * base_mva
        p_to_mw = float(p_to.get(key, 0.0)) * base_mva
        q_to_mvar = float(q_to.get(key, 0.0)) * base_mva
        rate_mva = _opf_rate_mw_or_mva(static.get("rate"), base_mva)
        row = {
            "id": _opf_element_id(key),
            "name": static.get("name", f"AC line {_opf_element_id(key)}"),
            "kind": static.get("kind", "line"),
            "from_bus": _opf_element_id(static.get("from", "")),
            "to_bus": _opf_element_id(static.get("to", "")),
            "p_from_mw": p_from_mw,
            "q_from_mvar": q_from_mvar,
            "p_to_mw": p_to_mw,
            "q_to_mvar": q_to_mvar,
            "p_loss_mw": (float(p_from[key]) + float(p_to.get(key, 0.0))) * base_mva,
            "q_loss_mvar": q_from_mvar + q_to_mvar,
            "s_from_mva": math.hypot(p_from_mw, q_from_mvar),
            "s_to_mva": math.hypot(p_to_mw, q_to_mvar),
            "rate_mva": rate_mva,
            "tap": static.get("tap"),
            "shift_deg": static.get("shift_degree"),
        }
        row["i_ka"] = _opf_ac_branch_current_ka(data, extracted, static, row)
        row["loading_percent"] = _ac_branch_loading_percent(row)
        rows.append(row)
    return rows


def _opf_dc_branch_rows(
    data: Mapping[str, Any],
    extracted: dict[str, Any],
    base_mva: float,
) -> list[dict[str, Any]]:
    p_from = extracted.get("dc_branch_p_from_pu", {})
    p_to = extracted.get("dc_branch_p_to_pu", {})
    loss = extracted.get("dc_branch_loss_pu", {})
    current = extracted.get("dc_branch_current_pu", {})
    branches = data.get("dc_branches", {}) if data else {}
    rows = []
    for key in p_from:
        static = branches.get(key, {})
        row = {
            "id": _opf_element_id(key),
            "name": static.get("name", f"DC line {_opf_element_id(key)}"),
            "from_bus": _opf_element_id(static.get("from", "")),
            "to_bus": _opf_element_id(static.get("to", "")),
            "p_from_mw": float(p_from[key]) * base_mva,
            "p_to_mw": float(p_to.get(key, 0.0)) * base_mva,
            "p_loss_mw": float(loss.get(key, 0.0)) * base_mva,
            "i_ka": _opf_dc_branch_current_ka(data, static, current.get(key), base_mva),
            "rate_mw": _opf_rate_mw_or_mva(static.get("rate"), base_mva),
        }
        row["loading_percent"] = _dc_branch_loading_percent(row)
        rows.append(row)
    return rows


def _opf_ac_branch_current_ka(
    data: Mapping[str, Any],
    extracted: Mapping[str, Any],
    branch: Mapping[str, Any],
    row: Mapping[str, Any],
) -> float | None:
    from_current = _opf_ac_terminal_current_ka(
        data,
        extracted,
        branch.get("from"),
        row.get("s_from_mva"),
    )
    to_current = _opf_ac_terminal_current_ka(
        data,
        extracted,
        branch.get("to"),
        row.get("s_to_mva"),
    )
    currents = [value for value in (from_current, to_current) if value is not None]
    return max(currents) if currents else None


def _opf_ac_terminal_current_ka(
    data: Mapping[str, Any],
    extracted: Mapping[str, Any],
    bus_key: Any,
    s_mva: Any,
) -> float | None:
    apparent_power = _safe_float(s_mva)
    voltage_kv = _opf_ac_bus_voltage_kv(data, extracted, bus_key)
    if apparent_power is None or voltage_kv is None or voltage_kv <= 0.0:
        return None
    return apparent_power / (math.sqrt(3.0) * voltage_kv)


def _opf_ac_bus_voltage_kv(
    data: Mapping[str, Any],
    extracted: Mapping[str, Any],
    bus_key: Any,
) -> float | None:
    if bus_key is None:
        return None
    key = str(bus_key)
    bus = data.get("ac_buses", {}).get(key, {}) if data else {}
    v_base = _safe_float(bus.get("v_base_kv"))
    v_pu = _safe_float(
        extracted.get("ac_bus_voltage_magnitude_pu", {}).get(key, bus.get("v0"))
    )
    if v_base is None or v_pu is None:
        return None
    return v_base * v_pu


def _opf_ac_bus_voltage_pu(
    data: Mapping[str, Any],
    extracted: Mapping[str, Any],
    bus_key: Any,
) -> float | None:
    if bus_key is None:
        return None
    key = str(bus_key)
    bus = data.get("ac_buses", {}).get(key, {}) if data else {}
    return _safe_float(
        extracted.get("ac_bus_voltage_magnitude_pu", {}).get(key, bus.get("v0"))
    )


def _opf_dc_branch_current_ka(
    data: Mapping[str, Any],
    branch: Mapping[str, Any],
    current_pu: Any,
    base_mva: float,
) -> float | None:
    current = _safe_float(current_pu)
    from_bus = branch.get("from")
    if current is None or from_bus is None:
        return None
    bus = data.get("dc_buses", {}).get(str(from_bus), {}) if data else {}
    v_base = _safe_float(bus.get("v_base_kv"))
    if v_base is None or v_base <= 0.0:
        return None
    return abs(current) * base_mva / v_base


def _opf_converter_ac_current_ka(
    data: Mapping[str, Any],
    converter: Mapping[str, Any],
    current_pu: Any,
    base_mva: float,
) -> float | None:
    current = _safe_float(current_pu)
    ac_bus = converter.get("ac_bus")
    if current is None or ac_bus is None:
        return None
    bus = data.get("ac_buses", {}).get(str(ac_bus), {}) if data else {}
    v_base = _safe_float(bus.get("v_base_kv"))
    if v_base is None or v_base <= 0.0:
        return None
    return abs(current) * base_mva / (math.sqrt(3.0) * v_base)


def _opf_converter_dc_current_ka(
    data: Mapping[str, Any],
    converter: Mapping[str, Any],
    current_pu: Any,
    base_mva: float,
) -> float | None:
    current = _safe_float(current_pu)
    dc_bus = converter.get("dc_bus")
    if current is None or dc_bus is None:
        return None
    bus = data.get("dc_buses", {}).get(str(dc_bus), {}) if data else {}
    v_base = _safe_float(bus.get("v_base_kv"))
    if v_base is None or v_base <= 0.0:
        return None
    return abs(current) * base_mva / v_base


def _opf_transformer_rows(
    data: Mapping[str, Any],
    extracted: dict[str, Any],
    base_mva: float,
) -> list[dict[str, Any]]:
    return [
        _branch_transformer_row(row)
        for row in _opf_ac_branch_rows(
            data,
            extracted,
            base_mva,
            include_transformers=True,
        )
    ]


def _opf_dc_bus_voltage_kv(data: Mapping[str, Any], bus_key: str, v_pu: Any) -> float | None:
    bus = data.get("dc_buses", {}).get(bus_key, {}) if data else {}
    v_base = _safe_float(bus.get("v_base_kv"))
    v_value = _safe_float(v_pu)
    if v_base is None or v_value is None:
        return None
    return v_base * v_value


def _opf_rate_mw_or_mva(rate_pu: Any, base_mva: float) -> float | None:
    value = _safe_float(rate_pu)
    if value is None or value <= 0.0:
        return None
    return value * base_mva


def _compare_acdcpf_pf_to_matacdc_reference(
    pf_result: PFResult,
    grid_case: Stagg5GridCase | str | None = None,
) -> dict[str, Any]:
    selected_case = get_stagg5_grid_case(grid_case)
    reference = _load_matacdc_reference(selected_case.case_name)
    if not reference:
        return {"available": False, "message": "MATACDC reference JSON was not found."}

    net = pf_result.raw_result
    checks = {
        "ac_bus_v_pu": _max_abs_diff(_column_values(net.res_ac_bus, "v_pu"), reference["ac_bus"]["vm_pu"]),
        "ac_bus_angle_deg": _max_abs_diff(
            _column_values(net.res_ac_bus, "v_angle_deg"), reference["ac_bus"]["va_deg"]
        ),
        "dc_bus_v_pu": _max_abs_diff(
            _column_values(net.res_dc_bus, "v_dc_pu"), reference["dc_bus"]["vdc_pu"]
        ),
        "ac_line_p_from_mw": _max_abs_diff(
            _column_values(net.res_ac_line, "p_from_mw"), reference["ac_branch"]["pf_mw"]
        ),
        "ac_line_q_from_mvar": _max_abs_diff(
            _column_values(net.res_ac_line, "q_from_mvar"), reference["ac_branch"]["qf_mvar"]
        ),
        "dc_line_p_from_mw": _max_abs_diff(
            _column_values(net.res_dc_line, "p_from_mw"), reference["dc_branch"]["pf_mw"]
        ),
        "vsc_p_ac_mw": _max_abs_diff(
            _column_values(net.res_vsc, "p_ac_mw"),
            [-float(value) for value in reference["converter"]["ps_mw"]],
        ),
        "vsc_q_ac_mvar": _max_abs_diff(
            _column_values(net.res_vsc, "q_ac_mvar"),
            [-float(value) for value in reference["converter"]["qs_mvar"]],
        ),
    }
    return {
        "available": True,
        "reference": str(reference.get("_path", "")),
        "checks": checks,
    }


def _load_matacdc_reference(case_name: str) -> dict[str, Any] | None:
    candidates = []
    spec = importlib.util.find_spec("acdcpf")
    if spec and spec.origin:
        candidates.append(Path(spec.origin).resolve().parents[1] / "tests" / "matacdc_reference_data")
    candidates.append(PROJECT_ROOT / "external" / "acdcpf_upstream" / "tests" / "matacdc_reference_data")
    candidates.append(PROJECT_ROOT.parent / "acdcpf" / "tests" / "matacdc_reference_data")

    for directory in candidates:
        path = directory / f"{case_name}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_path"] = str(path)
            return data
    return None


def _build_markdown_report(
    runs: list[BenchmarkRun],
    *,
    matacdc_validation: dict[str, Any],
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
) -> str:
    selected_case = get_stagg5_grid_case(grid_case)
    selected_vsc_setpoints = _coerce_vsc_setpoint_scenario(vsc_setpoint_scenario, selected_case)
    controls = opf_control_selection or _benchmark_opf_control_selection(selected_case)
    acdcpf_pf = _run_by_label(runs, "ACDCPF PF")
    tool1_objective = _run_by_label(runs, TOOL1_OBJECTIVE_LABEL)
    tool1_validated = _run_by_label(runs, TOOL1_VALIDATED_LABEL)
    tool1_report = _tool1_report_run(runs)
    pyflow_pf = _run_by_label(runs, "PyFlow PF")
    pyflow_opf = _run_by_label(runs, "PyFlow OPF VSC-only")
    report_runs = _visible_report_runs(runs)

    lines = [
        "# PF/OPF Benchmark Comparison",
        "",
        f"Generated: `{generated_at}`",
        f"Case: `{selected_case.case_name}`",
        f"Grid: {selected_case.display_name}",
        "",
        selected_case.description,
        "",
        "## Study Scope",
        "",
        "- Baseline PF is `acdcpf.run_pf`.",
        "- Tool1 (acdcopf) is the Pyomo/IPOPT loss-minimization model developed for this benchmark.",
        (
            "- PyFlow is run as a secondary reference with non-slack generator active power, "
            "case-defined generator reactive limits, and PV/slack voltages aligned where applicable."
            if selected_case.supports_pyflow
            else "- PyFlow reference is unavailable for this native ACDCPF hybrid DCDC topology."
        ),
        f"- Enabled OPF controls: {_opf_control_selection_description(controls)}.",
        f"- Control device scope: `{control_device_scope}`.",
        (
            "- Optimized control scope: "
            f"{_opf_control_scope_description(selected_case, controls, control_device_scope=control_device_scope)}."
        ),
        f"- Fixed OPF controls from the selectable list: {_opf_disabled_control_description(controls)}.",
        (
            "- Additional optimized controls: battery/storage active power."
            if selected_case.key == STAGG5_HYBRID_DCDC
            else "- No battery/storage controls are enabled for this grid case."
        ),
        "- AC generator voltage setpoints remain fixed; this selector changes only AC generator P/Q.",
        "- DC load powers remain fixed. DC generator curtailment, when enabled, can only reduce positive DC generation.",
        "- Storage convention: positive `p_mw` discharges into the connected bus; negative `p_mw` charges from it.",
        f"- Control margin: {_control_margin_description(control_margin_percent)}.",
        f"- VSC starting setpoint scenario: `{selected_vsc_setpoints.name}`.",
        "",
        "## VSC Starting Setpoints",
        "",
        selected_vsc_setpoints.description,
        "",
    ]
    lines.extend(_vsc_setpoint_scenario_table(selected_vsc_setpoints))
    lines.extend(
        [
            "",
            "## Solver Status",
            "",
        ]
    )
    lines.extend(
        _markdown_table(
            ["Run", "Success", "Message"],
            [[run.label, str(run.success), run.message] for run in report_runs],
        )
    )

    lines.extend(["", "## PF Baseline: ACDCPF vs PyFlow", ""])
    lines.extend(
        _loss_pair_table(
            acdcpf_pf,
            pyflow_pf,
            left_label="ACDCPF PF",
            right_label="PyFlow PF",
            difference_label="ACDCPF PF - PyFlow PF",
            percent_label="ACDCPF PF - PyFlow PF % of PyFlow",
        )
    )
    lines.extend(["", "### PF Bus Voltage Comparison", ""])
    lines.extend(_bus_voltage_pair_table(acdcpf_pf, pyflow_pf))
    lines.extend(["", "### PF Generator Comparison", ""])
    lines.extend(_comparison_table([run for run in (acdcpf_pf, pyflow_pf) if run], "generators", ["p_mw", "q_mvar"]))
    lines.extend(["", "### PF DC Generator Comparison", ""])
    lines.extend(_comparison_table([run for run in (acdcpf_pf, pyflow_pf) if run], "dc_generators", ["p_mw"]))
    lines.extend(["", "### PF DC Load Comparison", ""])
    lines.extend(_comparison_table([run for run in (acdcpf_pf, pyflow_pf) if run], "dc_loads", ["p_mw"]))
    lines.extend(["", "### PF Storage Comparison", ""])
    lines.extend(
        _comparison_table(
            [run for run in (acdcpf_pf, pyflow_pf) if run],
            "storage_units",
            ["p_mw", "q_mvar", "loading_percent"],
        )
    )
    lines.extend(["", "### PF Converter Comparison", ""])
    lines.extend(
        _comparison_table(
            [run for run in (acdcpf_pf, pyflow_pf) if run],
            "converters",
            ["p_ac_mw", "q_ac_mvar", "p_dc_mw", "p_loss_mw"],
        )
    )
    lines.extend(["", "### PF DCDC Converter Comparison", ""])
    lines.extend(
        _comparison_table(
            [run for run in (acdcpf_pf, pyflow_pf) if run],
            "dcdc_converters",
            ["p_from_mw", "p_to_mw", "p_loss_mw", "d_ratio"],
        )
    )
    lines.extend(["", "### PF AC Branch Comparison", ""])
    lines.extend(
        _comparison_table(
            [run for run in (acdcpf_pf, pyflow_pf) if run],
            "ac_branches",
            ["p_from_mw", "p_to_mw", "p_loss_mw"],
        )
    )
    lines.extend(["", "### PF DC Branch Comparison", ""])
    lines.extend(
        _comparison_table(
            [run for run in (acdcpf_pf, pyflow_pf) if run],
            "dc_branches",
            ["p_from_mw", "p_to_mw", "p_loss_mw"],
        )
    )

    lines.extend(["", "## OPF: Tool1 (acdcopf) vs PyFlow", ""])
    lines.extend(_opf_solver_check_table(acdcpf_pf, tool1_objective, tool1_validated))
    lines.extend(["", "### OPF Loss Change From Each PF Baseline", ""])
    lines.extend(_opf_loss_change_table(acdcpf_pf, tool1_report, pyflow_pf, pyflow_opf))
    lines.extend(["", "### Final OPF Result Comparison", ""])
    lines.extend(
        _loss_pair_table(
            tool1_report,
            pyflow_opf,
            left_label=tool1_report.label if tool1_report else TOOL1_OBJECTIVE_LABEL,
            right_label="PyFlow OPF",
            difference_label="Tool1 (acdcopf) - PyFlow OPF",
            percent_label="Tool1 (acdcopf) - PyFlow OPF % of PyFlow",
        )
    )
    lines.extend(["", "### Optimized VSC Controls", ""])
    lines.extend(_vsc_control_change_table(acdcpf_pf, tool1_report, pyflow_pf, pyflow_opf))
    lines.extend(["", "### Final OPF Generator Outputs", ""])
    lines.extend(
        _comparison_table(
            [run for run in (tool1_report, pyflow_opf) if run],
            "generators",
            ["p_mw", "q_mvar"],
        )
    )
    lines.extend(["", "### Final OPF DC Generator Outputs", ""])
    lines.extend(
        _comparison_table(
            [run for run in (tool1_report, pyflow_opf) if run],
            "dc_generators",
            ["p_mw"],
        )
    )
    lines.extend(["", "### Final OPF DC Load Outputs", ""])
    lines.extend(
        _comparison_table(
            [run for run in (tool1_report, pyflow_opf) if run],
            "dc_loads",
            ["p_mw"],
        )
    )
    lines.extend(["", "### Final OPF Storage Outputs", ""])
    lines.extend(
        _comparison_table(
            [run for run in (tool1_report, pyflow_opf) if run],
            "storage_units",
            ["p_mw", "q_mvar", "loading_percent"],
        )
    )
    lines.extend(["", "### Final OPF Converter Outputs", ""])
    lines.extend(
        _comparison_table(
            [run for run in (tool1_report, pyflow_opf) if run],
            "converters",
            ["p_ac_mw", "q_ac_mvar", "p_dc_mw", "p_loss_mw"],
        )
    )
    lines.extend(["", "### Final OPF DCDC Converter Outputs", ""])
    lines.extend(
        _comparison_table(
            [run for run in (tool1_report, pyflow_opf) if run],
            "dcdc_converters",
            ["p_from_mw", "p_to_mw", "p_loss_mw", "d_ratio"],
        )
    )
    lines.extend(["", "### OPF Voltage Movement From PF Baseline", ""])
    lines.extend(_voltage_movement_section(runs))
    lines.extend(["", "## Pandapower-Style Result Tables", ""])
    lines.extend(_pandapower_style_markdown_section(runs))

    lines.extend(["", "## MATACDC Reference Check For ACDCPF PF", ""])
    lines.extend(_matacdc_section(matacdc_validation))
    lines.extend(["", "## Diagnostics", ""])
    lines.extend(_diagnostics_section(report_runs))
    lines.extend(
        [
            "",
            "## Interpretation Notes",
            "",
            "- The PF block checks whether the two baseline power-flow models agree before optimization.",
            "- The OPF block compares each optimizer against its own PF baseline, then compares final optimized states.",
            f"- `{TOOL1_OBJECTIVE_LABEL}` is the internal Pyomo/IPOPT result reported as Tool1 (acdcopf).",
            "- A separate ACDCPF validation solve is kept internally to check the optimized setpoints, but it is not reported as a separate result run.",
            "- Cross-solver loss-difference percentages use PyFlow as the denominator: positive means the Tool1 (acdcopf) result is higher than PyFlow.",
            "- MATACDC reference checks are shown only for cases with a registered reference JSON.",
        ]
    )
    lines.extend(_custom_import_markdown(selected_case))
    return "\n".join(lines) + "\n"


def _custom_import_markdown(case):
    if not case.import_metadata:
        return []
    import json
    return ["", "## Custom Network Import", "", "Source IDs, interpreted units, and import warnings:", "", "```json", json.dumps(case.import_metadata, indent=2), "```", ""]


def _build_profiled_markdown_report(
    time_points: list[BenchmarkTimePoint],
    *,
    generated_at: str,
    vsc_setpoint_scenario: VSCSetpointScenario | str,
    grid_case: Stagg5GridCase | str | None = None,
    control_margin_percent: float | None = None,
    opf_control_selection: OPFControlSelection | None = None,
    control_device_scope: str = CONTROL_DEVICE_SCOPE_BENCHMARK,
    profile_input: Path | None = None,
) -> str:
    selected_case = get_stagg5_grid_case(grid_case)
    selected_vsc_setpoints = _coerce_vsc_setpoint_scenario(
        vsc_setpoint_scenario,
        selected_case,
    )
    controls = opf_control_selection or _benchmark_opf_control_selection(selected_case)

    lines = [
        "# Tool1 (acdcopf) Snapshot Time-Series Benchmark",
        "",
        f"Report generated: `{generated_at}`",
        f"Case: `{selected_case.case_name}`",
        f"Grid: {selected_case.display_name}",
        f"Profile input: `{profile_input}`" if profile_input is not None else "Profile input: n/a",
        f"Snapshots solved: {len(time_points)}",
        "",
        selected_case.description,
        "",
        "## Study Scope",
        "",
        "- Baseline PF is `acdcpf.run_pf` and remains an independent solve for each `time_index`.",
        (
            "- Tool1 (acdcopf) is the Pyomo/IPOPT loss-minimization model; storage SOC is linked "
            "across timestamps when native storage is present."
        ),
        "- PyFlow reference is reported as not comparable for profiled runs by default.",
        "- Native storage uses signed P, charge/discharge efficiencies, SOC limits, and a final-SOC return constraint.",
        f"- Enabled OPF controls: {_opf_control_selection_description(controls)}.",
        f"- Control device scope: `{control_device_scope}`.",
        (
            "- Optimized control scope: "
            f"{_opf_control_scope_description(selected_case, controls, control_device_scope=control_device_scope)}."
        ),
        f"- Control margin: {_control_margin_description(control_margin_percent)}.",
        f"- VSC starting setpoint scenario: `{selected_vsc_setpoints.name}`.",
        "",
        "## VSC Starting Setpoints",
        "",
        selected_vsc_setpoints.description,
        "",
    ]
    lines.extend(_vsc_setpoint_scenario_table(selected_vsc_setpoints))
    lines.extend(["", "## Snapshot Loss Summary", ""])
    lines.extend(_profiled_loss_summary_table(time_points))
    lines.extend(["", "## OPF Result Diagnostics", ""])
    lines.extend(_profiled_diagnostic_summary_table(time_points))
    lines.extend(_physics_markdown_section([run for point in time_points for run in point.runs]))
    lines.extend(["", "## PyFlow Reference Status", ""])
    lines.extend(_profiled_pyflow_status_table(time_points))
    lines.extend(
        [
            "",
            "## Export Notes",
            "",
            "- PF CSV/XLSX files contain PF results for all successful PF snapshots.",
            "- OPF CSV/XLSX files contain Tool1 (acdcopf) results for all OPF snapshots; failed OPF snapshots keep status rows but have empty physical result tables.",
            "- The `timestamp` column is the study timestamp from the profile. `report_generated_at` appears only in metadata.",
            "- The interactive HTML view includes a timestamp selector and defaults to the first profile timestamp.",
        ]
    )
    lines.extend(_custom_import_markdown(selected_case))
    return "\n".join(lines) + "\n"


def _profiled_loss_summary_table(time_points: list[BenchmarkTimePoint]) -> list[str]:
    rows = []
    for time_point in time_points:
        pf = _run_by_label(time_point.runs, "ACDCPF PF")
        opf = _tool1_report_run(time_point.runs)
        pf_total = _total_loss(pf)
        opf_total = _total_loss(opf)
        rows.append(
            [
                time_point.time_index,
                time_point.timestamp,
                str(pf.success) if pf is not None else "False",
                _format_number(pf_total),
                str(opf.success) if opf is not None else "False",
                _format_number(opf_total),
                _format_number(_difference(opf_total, pf_total)),
                _format_percent_change(opf_total, pf_total),
            ]
        )
    return _markdown_table(
        [
            "t",
            "timestamp",
            "PF success",
            "PF loss MW",
            "OPF success",
            "OPF loss MW",
            "OPF - PF MW",
            "OPF - PF %",
        ],
        rows,
    )


def _profiled_diagnostic_summary_table(time_points: list[BenchmarkTimePoint]) -> list[str]:
    rows = []
    for time_point in time_points:
        for run in _visible_report_runs(time_point.runs):
            rows.append(
                [
                    time_point.time_index,
                    time_point.timestamp,
                    run.label,
                    str(run.success),
                    _format_number(_total_loss(run)),
                    _format_number(run.diagnostics.get("max_pyomo_constraint_residual")),
                    _format_number(run.diagnostics.get("max_mismatch")),
                    run.message,
                ]
            )
    return _markdown_table(
        ["t", "timestamp", "run", "success", "loss MW", "max residual", "max mismatch", "message"],
        rows,
    )


def _profiled_pyflow_status_table(time_points: list[BenchmarkTimePoint]) -> list[str]:
    rows = []
    for time_point in time_points:
        for run in time_point.runs:
            if not _is_pyflow_run(run):
                continue
            rows.append([time_point.time_index, time_point.timestamp, run.label, run.message])
    if not rows:
        return ["PyFlow reference was skipped."]
    return _markdown_table(["t", "timestamp", "run", "status"], rows)


def _visible_report_runs(runs: list[BenchmarkRun]) -> list[BenchmarkRun]:
    """Return runs shown as first-class report results."""

    if _run_by_label(runs, TOOL1_OBJECTIVE_LABEL) is None:
        return list(runs)
    return [run for run in runs if run.label != TOOL1_VALIDATED_LABEL]


def _tool1_report_run(runs: list[BenchmarkRun]) -> BenchmarkRun | None:
    """Return the Tool1 (acdcopf) result preferred for user-facing result tables."""

    return _run_by_label(runs, TOOL1_OBJECTIVE_LABEL) or _run_by_label(
        runs,
        TOOL1_VALIDATED_LABEL,
    )


def _pandapower_style_markdown_section(runs: list[BenchmarkRun]) -> list[str]:
    run = _run_by_label(runs, TOOL1_OBJECTIVE_LABEL) or _run_by_label(runs, "ACDCPF PF")
    if run is None:
        return ["No pandapower-style result tables are available."]

    lines = [
        (
            "The CSV and Excel exports contain one `res_*` section/worksheet per element type, "
            "following pandapower-style naming. The compact tables below show the main "
            f"physical loading outputs for `{run.label}`."
        ),
        "",
        "### res_line",
        "",
    ]
    lines.extend(
        _result_table_for_markdown(
            run,
            "ac_branches",
            id_header="line",
            fields=[
                ("name", "name"),
                ("from_bus", "from"),
                ("to_bus", "to"),
                ("p_from_mw", "p_from_mw"),
                ("q_from_mvar", "q_from_mvar"),
                ("p_to_mw", "p_to_mw"),
                ("q_to_mvar", "q_to_mvar"),
                ("p_loss_mw", "pl_mw"),
                ("i_ka", "i_ka"),
                ("loading_percent", "loading_pct"),
            ],
        )
    )
    lines.extend(["", "### res_trafo", ""])
    transformer_rows = run.tables.get("transformers", [])
    if transformer_rows:
        lines.extend(
            _result_table_for_markdown(
                run,
                "transformers",
                id_header="trafo",
                fields=[
                    ("name", "name"),
                    ("hv_bus", "hv_bus"),
                    ("lv_bus", "lv_bus"),
                    ("p_hv_mw", "p_hv_mw"),
                    ("q_hv_mvar", "q_hv_mvar"),
                    ("p_loss_mw", "pl_mw"),
                    ("loading_percent", "loading_pct"),
                ],
            )
        )
    else:
        lines.append(
            "No standalone transformer elements are defined in the selected Stagg5 case."
        )

    lines.extend(["", "### res_vsc", ""])
    lines.extend(
        _result_table_for_markdown(
            run,
            "converters",
            id_header="vsc",
            fields=[
                ("name", "name"),
                ("ac_bus", "ac_bus"),
                ("dc_bus", "dc_bus"),
                ("p_ac_mw", "p_ac_mw"),
                ("q_ac_mvar", "q_ac_mvar"),
                ("p_loss_mw", "pl_mw"),
                ("s_mva", "sn_mva"),
                ("loading_percent", "loading_pct"),
            ],
        )
    )
    if run.tables.get("dcdc_converters"):
        lines.extend(["", "### res_dcdc", ""])
        lines.extend(
            _result_table_for_markdown(
                run,
                "dcdc_converters",
                id_header="dcdc",
                fields=[
                    ("name", "name"),
                    ("from_bus", "from_bus"),
                    ("to_bus", "to_bus"),
                    ("p_from_mw", "p_from_mw"),
                    ("p_to_mw", "p_to_mw"),
                    ("p_loss_mw", "pl_mw"),
                    ("d_ratio", "ratio"),
                ],
            )
        )
    return lines


def _result_table_for_markdown(
    run: BenchmarkRun,
    table_name: str,
    *,
    id_header: str,
    fields: list[tuple[str, str]],
) -> list[str]:
    rows = []
    for table_row in sorted(
        run.tables.get(table_name, []),
        key=lambda item: _natural_key(str(item.get("id", item.get("name", "")))),
    ):
        rows.append(
            [
                table_row.get("id"),
                *[_format_result_table_value(table_row.get(source)) for source, _ in fields],
            ]
        )
    if not rows:
        return ["No data available."]
    return _markdown_table([id_header, *[header for _, header in fields]], rows)


def _format_result_table_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return _format_number(value)


def _vsc_setpoint_scenario_table(scenario: VSCSetpointScenario | str) -> list[str]:
    rows = []
    for idx, values in sorted(_vsc_setpoints_for_scenario(scenario).items()):
        rows.append(
            [
                idx,
                _format_number(values["p_ac_mw"]),
                _format_number(values["q_ac_mvar"]),
            ]
        )
    return _markdown_table(["VSC", "Starting p_ac_mw", "Starting q_ac_mvar"], rows)


def _opf_solver_check_table(
    acdcpf_pf: BenchmarkRun | None,
    opf: BenchmarkRun | None,
    validated: BenchmarkRun | None,
) -> list[str]:
    pf_total = _total_loss(acdcpf_pf)
    opf_total = _total_loss(opf)
    validated_total = _total_loss(validated)
    rows = [
        ["ACDCPF PF total loss", _format_number(pf_total)],
        ["Tool1 (acdcopf) objective total loss", _format_number(opf_total)],
        [
            "Tool1 (acdcopf) objective change vs ACDCPF PF",
            _format_number(_difference(opf_total, pf_total)),
        ],
    ]
    validation_mismatch = _difference(opf_total, validated_total)
    if validation_mismatch is not None:
        rows.append(
            [
                "Internal ACDCPF re-solve mismatch",
                _format_number(validation_mismatch),
            ]
        )
    if opf is not None:
        rows.append(
            [
                "Max Pyomo constraint residual",
                _format_number(opf.diagnostics.get("max_pyomo_constraint_residual")),
            ]
        )
        rows.append(["IPOPT termination", opf.message])
    return _markdown_table(["Metric", "Value"], rows)


def _loss_pair_table(
    left: BenchmarkRun | None,
    right: BenchmarkRun | None,
    *,
    left_label: str,
    right_label: str,
    difference_label: str | None = None,
    percent_label: str | None = None,
) -> list[str]:
    difference_label = difference_label or f"{left_label} - {right_label}"
    percent_label = percent_label or f"{left_label} - {right_label} % of {right_label}"
    rows = []
    for label, key in (
        ("AC branch loss MW", "ac_branch_mw"),
        ("Transformer loss MW", "transformer_mw"),
        ("DC branch loss MW", "dc_branch_mw"),
        ("Converter loss MW", "converter_mw"),
        ("Total active loss MW", "total_active_losses_mw"),
    ):
        left_value = _loss_value(left, key)
        right_value = _loss_value(right, key)
        rows.append(
            [
                label,
                _format_number(left_value),
                _format_number(right_value),
                _format_number(_difference(left_value, right_value)),
                _format_percent_change(left_value, right_value),
            ]
        )
    return _markdown_table(
        ["Quantity", left_label, right_label, difference_label, percent_label],
        rows,
    )


def _opf_loss_change_table(
    acdcpf_pf: BenchmarkRun | None,
    tool1: BenchmarkRun | None,
    pyflow_pf: BenchmarkRun | None,
    pyflow_opf: BenchmarkRun | None,
) -> list[str]:
    rows = []
    for label, baseline, optimized in (
        ("Tool1 (acdcopf)", acdcpf_pf, tool1),
        ("PyFlow OPF", pyflow_pf, pyflow_opf),
    ):
        baseline_total = _total_loss(baseline)
        optimized_total = _total_loss(optimized)
        rows.append(
            [
                label,
                _format_number(baseline_total),
                _format_number(optimized_total),
                _format_number(_difference(optimized_total, baseline_total)),
                _format_percent_change(optimized_total, baseline_total),
            ]
        )
    return _markdown_table(
        ["Run", "PF baseline MW", "OPF result MW", "Change MW", "Change %"],
        rows,
    )


def _bus_voltage_pair_table(left: BenchmarkRun | None, right: BenchmarkRun | None) -> list[str]:
    if left is None or right is None:
        return ["No bus voltage comparison data available."]
    rows = [
        [
            "AC voltage magnitude pu",
            _format_number(_max_table_delta(left, right, "ac_buses", "v_pu")),
        ],
        [
            "AC voltage angle deg",
            _format_number(_max_table_delta(left, right, "ac_buses", "angle_deg")),
        ],
        [
            "DC voltage pu",
            _format_number(_max_table_delta(left, right, "dc_buses", "v_pu")),
        ],
    ]
    return _markdown_table(["Quantity", "Max abs difference"], rows)


def _vsc_control_change_table(
    acdcpf_pf: BenchmarkRun | None,
    tool1: BenchmarkRun | None,
    pyflow_pf: BenchmarkRun | None,
    pyflow_opf: BenchmarkRun | None,
) -> list[str]:
    rows = []
    for converter_index in _converter_indices_from_runs(acdcpf_pf, tool1, pyflow_pf, pyflow_opf):
        for field_name in ("p_ac_mw", "q_ac_mvar"):
            acdcpf_before = _table_value(acdcpf_pf, "converters", converter_index, field_name)
            acdcpf_after = _table_value(tool1, "converters", converter_index, field_name)
            pyflow_before = _table_value(pyflow_pf, "converters", converter_index, field_name)
            pyflow_after = _table_value(pyflow_opf, "converters", converter_index, field_name)
            rows.append(
                [
                    converter_index,
                    field_name,
                    _format_number(acdcpf_before),
                    _format_number(acdcpf_after),
                    _format_number(_difference(acdcpf_after, acdcpf_before)),
                    _format_number(pyflow_before),
                    _format_number(pyflow_after),
                    _format_number(_difference(pyflow_after, pyflow_before)),
                ]
            )
    return _markdown_table(
        [
            "VSC",
            "Control",
            "ACDCPF PF",
            "Tool1 (acdcopf)",
            "Tool1 (acdcopf) change",
            "PyFlow PF",
            "PyFlow OPF",
            "PyFlow change",
        ],
        rows,
    )


def _voltage_movement_section(runs: list[BenchmarkRun]) -> list[str]:
    pairs = [
        ("ACDCPF PF", TOOL1_OBJECTIVE_LABEL),
        ("PyFlow PF", "PyFlow OPF VSC-only"),
    ]
    rows = []
    for before_label, after_label in pairs:
        before = _run_by_label(runs, before_label)
        after = _run_by_label(runs, after_label)
        if before is None or after is None:
            continue
        rows.append(
            [
                f"{before_label} -> {after_label}",
                _format_number(_max_table_delta(before, after, "ac_buses", "v_pu")),
                _format_number(_max_table_delta(before, after, "ac_buses", "angle_deg")),
                _format_number(_max_table_delta(before, after, "dc_buses", "v_pu")),
            ]
        )
    if not rows:
        return ["No voltage comparison data available."]
    return _markdown_table(
        ["Comparison", "Max AC V diff pu", "Max AC angle diff deg", "Max DC V diff pu"],
        rows,
    )


def _comparison_table(
    runs: list[BenchmarkRun],
    table_name: str,
    fields: list[str],
) -> list[str]:
    ids = sorted(
        {
            str(row.get("id"))
            for run in runs
            for row in run.tables.get(table_name, [])
            if row.get("id") is not None
        },
        key=_natural_key,
    )
    if not ids:
        return ["No data available."]

    headers = ["Element", "Field", *[run.label for run in runs]]
    rows = []
    for element_id in ids:
        for field_name in fields:
            rows.append(
                [
                    element_id,
                    field_name,
                    *[
                        _format_number(_row_value(run.tables.get(table_name, []), element_id, field_name))
                        for run in runs
                    ],
                ]
            )
    return _markdown_table(headers, rows)


def _run_by_label(runs: list[BenchmarkRun], label: str) -> BenchmarkRun | None:
    for run in runs:
        if run.label == label:
            return run
    return None


def _total_loss(run: BenchmarkRun | None) -> float | None:
    if run is None or not run.success:
        return None
    return run.losses_mw.get("total_active_losses_mw")


def _loss_value(run: BenchmarkRun | None, key: str) -> float | None:
    if run is None or not run.success:
        return None
    return run.losses_mw.get(key)


def _table_value(
    run: BenchmarkRun | None,
    table_name: str,
    element_id: int | str,
    field_name: str,
) -> float | None:
    if run is None or not run.success:
        return None
    return _row_value(run.tables.get(table_name, []), str(element_id), field_name)


def _converter_indices_from_runs(*runs: BenchmarkRun | None) -> tuple[int, ...]:
    indices: set[int] = set()
    for run in runs:
        if run is None or not run.success:
            continue
        for row in run.tables.get("converters", []):
            element_id = _optional_int(row.get("id"))
            if element_id is not None:
                indices.add(element_id)
    return tuple(sorted(indices))


def _difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _percent_change(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None:
        return None
    baseline = float(baseline)
    if baseline == 0.0:
        return None
    return 100.0 * (float(value) - baseline) / abs(baseline)


def _max_table_delta(
    before: BenchmarkRun | None,
    after: BenchmarkRun | None,
    table_name: str,
    field_name: str,
) -> float | None:
    if before is None or after is None:
        return None
    before_rows = before.tables.get(table_name, [])
    after_rows = after.tables.get(table_name, [])
    deltas = []
    for row in before_rows:
        element_id = row.get("id")
        if element_id is None:
            continue
        before_value = _safe_float(row.get(field_name))
        after_value = _row_value(after_rows, str(element_id), field_name)
        if before_value is not None and after_value is not None:
            deltas.append(abs(after_value - before_value))
    if not deltas:
        return None
    return float(max(deltas))


def _matacdc_section(validation: dict[str, Any]) -> list[str]:
    if not validation or not validation.get("available"):
        return [str(validation.get("message", "MATACDC reference was not checked."))]
    lines = [f"Reference file: `{validation.get('reference')}`", ""]
    rows = [[name, _format_number(value)] for name, value in validation["checks"].items()]
    lines.extend(_markdown_table(["Quantity", "Max abs difference"], rows))
    return lines


def _diagnostics_section(runs: list[BenchmarkRun]) -> list[str]:
    lines: list[str] = _physics_markdown_section(runs)
    for run in runs:
        lines.append(f"### {run.label}")
        if not run.diagnostics:
            lines.append("")
            lines.append("No diagnostics reported.")
            lines.append("")
            continue
        rows = [[key, _diagnostic_value(value)] for key, value in sorted(run.diagnostics.items()) if key not in {"physics_validation", "equipment_limits"}]
        lines.extend(_markdown_table(["Key", "Value"], rows))
        lines.append("")
    return lines


def _physics_markdown_section(runs: list[BenchmarkRun]) -> list[str]:
    limit_lines = _equipment_limits_markdown_section(runs)
    checked = [(run, run.diagnostics["physics_validation"]) for run in runs if "physics_validation" in run.diagnostics]
    if not checked:
        return limit_lines
    lines = limit_lines + ["", "## Source-Network Physics Checks", "",
             "Independent checks of native input data and solved states. `partial` means no detected violation, but some source limits are unavailable.",
             "Baseline PF feasibility and global optimality are not certified by this report. Full element-level checks are in the `physics_checks` CSV/XLSX section.", ""]
    rows = [[run.diagnostics.get("profile_time_index", 0), physics["status"], physics["passed"], physics["failed"], physics["not_checked"]]
            for run, physics in checked]
    lines.extend(_markdown_table(["t", "status", "passed", "failed", "not checked"], rows))
    failures = [[run.diagnostics.get("profile_time_index", 0), check["element"], check["check"],
                 _format_number(check["value"]), _format_number(check["lower"]), _format_number(check["upper"]),
                 _format_number(check["violation"]), check["unit"], _format_number(check["tolerance"]), check["note"]]
                for run, physics in checked for check in physics["checks"] if check["status"] == "failed"]
    if failures:
        lines.extend(["", "### Physics Violations", ""])
        lines.extend(_markdown_table(["t", "element", "check", "value", "min", "max", "violation", "unit", "tolerance", "note"], failures))
    return lines + [""]


def _equipment_limits_markdown_section(runs: list[BenchmarkRun]) -> list[str]:
    limits = [limit for run in runs for limit in run.diagnostics.get("equipment_limits", [])]
    if not limits:
        return []
    missing = {}
    for limit in limits:
        if limit["status"] == "missing":
            key = (limit["element"].split("[")[0], limit["parameter"], limit["unit"])
            missing.setdefault(key, set()).add(limit["element"])
    lines = ["", "## Equipment Limit Coverage", "",
             "Control margins do not replace equipment ratings. Specified inputs are not proof of nameplate provenance or feasibility.",
             "The `equipment_limits` CSV/XLSX section lists values, source fields, OPF fallback behavior and corresponding physics checks.", ""]
    if missing:
        rows = [[kind, parameter, unit, len(elements)] for (kind, parameter, unit), elements in sorted(missing.items())]
        lines.extend(_markdown_table(["Element type", "Missing parameter", "Unit", "Elements"], rows))
    else:
        lines.append("All inventoried active-equipment limit fields are specified.")
    return lines + [""]


def _markdown_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    table_rows = [[str(cell) for cell in row] for row in rows]
    header_cells = [str(header) for header in headers]
    column_count = len(header_cells)
    widths = [
        max(
            3,
            len(header_cells[index]),
            *[
                len(row[index])
                for row in table_rows
                if index < len(row)
            ],
        )
        for index in range(column_count)
    ]

    def format_row(cells: list[str]) -> str:
        padded_cells = [
            (cells[index] if index < len(cells) else "").ljust(widths[index])
            for index in range(column_count)
        ]
        return "| " + " | ".join(padded_cells) + " |"

    header = format_row(header_cells)
    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    body = [format_row(row) for row in table_rows]
    return [header, separator, *body]


def _rows_from_table(
    table: Any,
    columns: dict[str, str],
    *,
    raw_columns: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if table is None or getattr(table, "empty", True):
        return []
    rows: list[dict[str, Any]] = []
    for idx, row in table.iterrows():
        item = {"id": int(idx) if _is_integer_like(idx) else str(idx)}
        if "name" in table.columns:
            item["name"] = str(row["name"])
        for output_name, column in columns.items():
            if column in table.columns:
                item[output_name] = _safe_float(row[column])
        for output_name, column in (raw_columns or {}).items():
            if column in table.columns:
                item[output_name] = _raw_table_value(row[column])
        rows.append(item)
    return rows


def _merge_static_table_columns(
    rows: list[dict[str, Any]],
    static_table: Any,
    raw_columns: dict[str, str],
) -> list[dict[str, Any]]:
    """Merge topology/configuration columns into solved result rows by element index."""

    if static_table is None or getattr(static_table, "empty", True):
        return rows
    for row in rows:
        element_id = row.get("id")
        if element_id not in static_table.index:
            continue
        static_row = static_table.loc[element_id]
        for output_name, column in raw_columns.items():
            if column in static_table.columns:
                row[output_name] = _raw_table_value(static_row[column])
    return rows


def _raw_table_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (int,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return int(number) if number.is_integer() else number
    number = _safe_float(value)
    if number is not None:
        return int(number) if number.is_integer() else number
    return str(value)


def _row_value(rows: list[dict[str, Any]], element_id: str, field_name: str) -> float | None:
    for row in rows:
        if str(row.get("id")) == str(element_id):
            return _safe_float(row.get(field_name))
    return None


def _column_values(table: Any, column: str) -> list[float]:
    if table is None or getattr(table, "empty", True) or column not in table.columns:
        return []
    return [float(value) for value in table[column].to_numpy()]


def _max_abs_diff(actual: Iterable[float], expected: Iterable[float]) -> float | None:
    actual_array = np.asarray(list(actual), dtype=float)
    expected_array = np.asarray(list(expected), dtype=float)
    if actual_array.size == 0 or expected_array.size == 0 or actual_array.size != expected_array.size:
        return None
    return float(np.max(np.abs(actual_array - expected_array)))


def _max_constraint_residual(model: pyo.ConcreteModel) -> float:
    max_residual = 0.0
    for constraint in model.component_data_objects(pyo.Constraint, active=True):
        body = pyo.value(constraint.body)
        lower = pyo.value(constraint.lower) if constraint.has_lb() else None
        upper = pyo.value(constraint.upper) if constraint.has_ub() else None
        if constraint.equality:
            residual = abs(body - float(lower))
        else:
            residual = 0.0
            if lower is not None:
                residual = max(residual, float(lower) - body)
            if upper is not None:
                residual = max(residual, body - float(upper))
        max_residual = max(max_residual, residual)
    return float(max_residual)


def _ensure_local_ipopt_on_path() -> None:
    solver_dir = _solver_dir()
    if solver_dir.exists():
        os.environ["PATH"] = f"{solver_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def _solver_dir() -> Path:
    return ipopt_executable_path().parent


def _ipopt_executable_path() -> Path:
    return ipopt_executable_path()


def _module_path(module_name: str) -> str:
    spec = importlib.util.find_spec(module_name)
    if not spec or not spec.origin:
        return "not found"
    return str(Path(spec.origin).resolve())


def _solver_attr(result: Any, name: str) -> str | None:
    if result is None or not hasattr(result, "solver"):
        return None
    return str(getattr(result.solver, name, None))


def _solver_iterations(result: Any) -> int | None:
    if result is None or not hasattr(result, "solver"):
        return None
    statistics = getattr(result.solver, "statistics", None)
    if statistics is None:
        return None
    branch_and_bound = getattr(statistics, "branch_and_bound", None)
    if branch_and_bound is not None:
        iterations = getattr(branch_and_bound, "number_of_created_subproblems", None)
        numeric_iterations = _safe_float(iterations)
        if numeric_iterations is not None:
            return int(numeric_iterations)
    return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        result = float(value)
        if not math.isfinite(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def _first_safe_float(row: Mapping[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        value = _safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _format_number(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    if abs(number) >= 1000 or (0 < abs(number) < 1e-4):
        return f"{number:.6e}"
    return f"{number:.6f}"


def _format_percent_change(value: float | None, baseline: float | None) -> str:
    percent = _percent_change(value, baseline)
    if percent is None:
        return "n/a"
    return f"{percent:.3f}%"


def _diagnostic_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return _format_number(value) if _safe_float(value) is not None else str(value)


def _natural_key(value: str) -> tuple[str, int]:
    prefix = "".join(ch for ch in value if not ch.isdigit())
    digits = "".join(ch for ch in value if ch.isdigit())
    return prefix, int(digits) if digits else -1


def _opf_element_id(key: Any) -> int | str:
    text = str(key)
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else text


def _is_integer_like(value: Any) -> bool:
    try:
        return int(value) == value
    except (TypeError, ValueError):
        return False


if __name__ == "__main__":
    main()
