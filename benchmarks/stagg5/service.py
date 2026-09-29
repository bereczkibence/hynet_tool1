from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import logging
import math
from typing import Any, Mapping

import pandas as pd

from acdcpf_opf.benchmarks.stagg5 import benchmark_pf_opf_comparison as benchmark
from acdcpf_opf.benchmarks.stagg5.case_variants import (
    STAGG5_GRID_CASES,
    STAGG5_HYBRID_DCDC,
    STAGG5_ORIGINAL,
    Stagg5GridCase,
    create_acdcpf_network_for_stagg5_case,
    get_stagg5_grid_case,
)
from acdcpf_opf.data.time_profiles import ProfileSnapshot, load_time_profile
from acdcpf_opf.runtime_paths import reports_directory
from acdcpf_opf.data.equipment_limits import (
    DCDC_PU_LIMIT_SPECS, LIMIT_SPECS, equipment_limit_inventory, source_limit_value, validate_equipment_limits,
)


CONTROL_DEVICE_SCOPES = (
    benchmark.CONTROL_DEVICE_SCOPE_BENCHMARK,
    benchmark.CONTROL_DEVICE_SCOPE_ALL,
)


@dataclass(frozen=True)
class EditableTableSpec:
    """Dashboard-safe editable columns for one ACDCPF network table."""

    name: str
    label: str
    read_only_columns: tuple[str, ...]
    editable_columns: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkRequest:
    """Complete dashboard/service request for one Tool1 (acdcopf) benchmark run."""

    grid_case: str | Stagg5GridCase = STAGG5_ORIGINAL
    vsc_setpoint_scenario: str | benchmark.VSCSetpointScenario = (
        benchmark.VSC_SETPOINT_SCENARIO_ORIGINAL
    )
    custom_vsc_setpoints: Mapping[int, Mapping[str, float]] = field(default_factory=dict)
    opf_controls: str | benchmark.OPFControlSelection = benchmark.OPF_CONTROL_PRESET_BENCHMARK
    control_device_scope: str = benchmark.CONTROL_DEVICE_SCOPE_BENCHMARK
    control_margin_percent: float | None = None
    profile_snapshots: tuple[ProfileSnapshot, ...] | None = None
    profile_input: Path | None = None
    profile_sheet: str | int | None = None
    profile_format: str = "auto"
    table_overrides: Mapping[str, Any] = field(default_factory=dict)
    skip_opf: bool = False
    pf_policy: str = "unconstrained"
    include_pyflow_reference: bool = True
    write_exports: bool = True
    include_markdown: bool = True
    include_csv: bool = True
    include_xlsx: bool = True
    include_html: bool = True
    output_directory: Path | None = None


@dataclass(frozen=True)
class BenchmarkResultBundle:
    """PF/OPF results and export paths produced from a ``BenchmarkRequest``."""

    request: BenchmarkRequest
    generated_at: str
    output_directory: Path | None
    runs: tuple[benchmark.BenchmarkRun, ...] = ()
    time_points: tuple[benchmark.BenchmarkTimePoint, ...] = ()
    matacdc_validation: Mapping[str, Any] = field(default_factory=dict)
    markdown: str = ""
    markdown_path: Path | None = None
    csv_paths: tuple[Path, ...] = ()
    xlsx_paths: tuple[Path, ...] = ()
    html_path: Path | None = None

    @property
    def is_profiled(self) -> bool:
        return bool(self.time_points)

    @property
    def success(self) -> bool:
        """Require native PF and requested OPF success at every timestamp."""
        required = {"ACDCPF PF"}
        if not self.request.skip_opf:
            required.add(benchmark.TOOL1_OBJECTIVE_LABEL)
        groups = [point.runs for point in self.time_points] if self.is_profiled else [self.runs]
        return all(
            all(any(run.label == label and run.success for run in runs) for label in required)
            for runs in groups
        )

    @property
    def visible_runs(self) -> tuple[benchmark.BenchmarkRun, ...]:
        if not self.is_profiled:
            return self.runs
        runs: list[benchmark.BenchmarkRun] = []
        for time_point in self.time_points:
            runs.extend(time_point.runs)
        return tuple(runs)


EDITABLE_TABLE_SPECS: dict[str, EditableTableSpec] = {
    "ac_bus": EditableTableSpec(
        name="ac_bus", label="AC Bus Limits", read_only_columns=("name", "vr_kv"),
        editable_columns=("v_min_pu", "v_max_pu"),
    ),
    "dc_bus": EditableTableSpec(
        name="dc_bus", label="DC Bus Limits", read_only_columns=("name", "v_base"),
        editable_columns=("v_min", "v_max"),
    ),
    "ac_line": EditableTableSpec(
        name="ac_line", label="AC Line Limits", read_only_columns=("name", "from_bus", "to_bus"),
        editable_columns=("rate_mva", "max_i_ka"),
    ),
    "dc_line": EditableTableSpec(
        name="dc_line", label="DC Line Limits", read_only_columns=("name", "from_bus", "to_bus"),
        editable_columns=("rate_mw", "max_i_ka"),
    ),
    "ac_load": EditableTableSpec(
        name="ac_load",
        label="AC Loads",
        read_only_columns=("name", "bus"),
        editable_columns=("p_mw", "q_mvar", "in_service"),
    ),
    "dc_load": EditableTableSpec(
        name="dc_load",
        label="DC Loads",
        read_only_columns=("name", "bus", "load_type"),
        editable_columns=("p_mw", "in_service"),
    ),
    "dc_gen": EditableTableSpec(
        name="dc_gen",
        label="DC Generators",
        read_only_columns=("name", "bus"),
        editable_columns=("p_mw", "p_min_mw", "p_max_mw", "in_service"),
    ),
    "ac_gen": EditableTableSpec(
        name="ac_gen",
        label="AC Generators",
        read_only_columns=("name", "bus"),
        editable_columns=(
            "p_mw",
            "p_min_mw",
            "p_max_mw",
            "q_mvar",
            "v_pu",
            "q_min_mvar",
            "q_max_mvar",
            "in_service",
        ),
    ),
    "storage": EditableTableSpec(
        name="storage",
        label="Storage",
        read_only_columns=("name", "bus", "bus_type"),
        editable_columns=(
            "p_mw",
            "q_mvar",
            "sn_mva",
            "energy_mwh",
            "soc_percent",
            "soc_min_percent",
            "soc_max_percent",
            "eta_charge",
            "eta_discharge",
            "p_min_mw",
            "p_max_mw",
            "q_min_mvar",
            "q_max_mvar",
            "in_service",
        ),
    ),
    "vsc": EditableTableSpec(
        name="vsc",
        label="VSC Converters",
        read_only_columns=("name", "ac_bus", "dc_bus", "control_mode"),
        editable_columns=(
            "s_mva",
            "max_i_ac_ka",
            "max_i_dc_ka",
            "v_filter_min_pu",
            "v_filter_max_pu",
            "v_converter_min_pu",
            "v_converter_max_pu",
            "p_mw",
            "q_mvar",
            "v_ac_pu",
            "v_dc_pu",
            "p_dc_set_mw",
            "v_dc_set_pu",
            "droop_kv_per_mw",
            "loss_a",
            "loss_b",
            "loss_c",
            "loss_c_inv",
            "in_service",
        ),
    ),
    "dcdc": EditableTableSpec(
        name="dcdc",
        label="DCDC Converters",
        read_only_columns=("name", "from_bus", "to_bus"),
        editable_columns=("d_ratio", "d_ratio_min", "d_ratio_max", "rate_mw", "in_service"),
    ),
    "trafo": EditableTableSpec(
        name="trafo",
        label="Transformers",
        read_only_columns=("name", "from_bus", "to_bus", "vn_from_kv", "vn_to_kv"),
        editable_columns=(
            "sn_mva",
            "max_i_ka",
            "tap",
            "tap_min",
            "tap_max",
            "tap_step_percent",
            "tap_controllable",
            "shift_deg",
            "in_service",
        ),
    ),
}


def run_benchmark_request(request: BenchmarkRequest) -> BenchmarkResultBundle:
    """Run the unchanged numerical workflow and apply Tool1 report branding."""
    from acdcopf.presentation import present_bundle
    return present_bundle(_run_benchmark_request(request))


def _run_benchmark_request(request: BenchmarkRequest) -> BenchmarkResultBundle:
    """Run ACDCPF PF and Tool1 (acdcopf) from a dashboard/service request."""

    if request.pf_policy not in {"unconstrained", "converter_limited"}:
        raise ValueError("Unknown Tool5 PF policy.")
    if request.pf_policy == "converter_limited" and not request.skip_opf:
        raise ValueError("Converter-limited PF requires PF only. OPF uses unconstrained initialization to preserve requested controls.")
    selected_case = get_stagg5_grid_case(request.grid_case)
    if request.pf_policy == "converter_limited" and selected_case.source != "acdcpf":
        raise ValueError("Converter-limited PF requires a native Tool5 network.")
    controls = _control_selection_from_request(request, selected_case)
    _validate_request(request, selected_case, controls)

    logging.basicConfig(level=logging.CRITICAL, format="%(levelname)s:%(name)s:%(message)s")
    logging.getLogger("pyomo.opt").setLevel(logging.CRITICAL)
    benchmark._configure_warning_filters()
    benchmark._ensure_local_ipopt_on_path()

    generated_at = datetime.now().isoformat(timespec="seconds")
    run_dir = _output_directory_for_request(request, selected_case, generated_at)
    vsc_scenario = _vsc_scenario_from_request(request, selected_case)
    profile_snapshots = _profile_snapshots_from_request(request)
    network_overrides = _network_override_callable(request)

    if profile_snapshots is not None:
        return _run_profiled_request(
            request,
            selected_case=selected_case,
            generated_at=generated_at,
            run_dir=run_dir,
            vsc_scenario=vsc_scenario,
            controls=controls,
            profile_snapshots=profile_snapshots,
            network_overrides=network_overrides,
        )
    return _run_single_snapshot_request(
        request,
        selected_case=selected_case,
        generated_at=generated_at,
        run_dir=run_dir,
        vsc_scenario=vsc_scenario,
        controls=controls,
        network_overrides=network_overrides,
    )


def editable_network_tables(
    grid_case: str | Stagg5GridCase,
    vsc_setpoint_scenario: str | benchmark.VSCSetpointScenario = (
        benchmark.VSC_SETPOINT_SCENARIO_ORIGINAL
    ),
) -> dict[str, pd.DataFrame]:
    """Return dashboard-ready editable views for a native ACDCPF benchmark case."""

    selected_case = get_stagg5_grid_case(grid_case)
    if selected_case.source != "acdcpf":
        return {}
    scenario = _predefined_or_existing_vsc_scenario(vsc_setpoint_scenario, selected_case)
    net = create_acdcpf_network_for_stagg5_case(selected_case)
    benchmark._apply_vsc_setpoints_to_acdcpf_network(net, scenario, selected_case)
    return editable_tables_from_network(net)


def editable_tables_from_network(net: Any) -> dict[str, pd.DataFrame]:
    """Return editable operational table views from an ACDCPF network."""

    tables: dict[str, pd.DataFrame] = {}
    for table_name, spec in EDITABLE_TABLE_SPECS.items():
        table = getattr(net, table_name, None)
        if table is None or getattr(table, "empty", True):
            continue
        tables[table_name] = _editable_table_view(table, spec)
        if table_name == "dcdc":
            for position, (_, row) in enumerate(table.iterrows()):
                pu_values = [source_limit_value(row, limit.fields)[1] for limit in DCDC_PU_LIMIT_SPECS]
                if any(value is not None for value in pu_values):
                    factor = float(net.dc_bus.at[int(row.to_bus), "v_base"]) / float(net.dc_bus.at[int(row.from_bus), "v_base"])
                    for column, value in zip(("d_ratio_min", "d_ratio_max"), pu_values):
                        tables[table_name].at[position, column] = value * factor if value is not None else float("nan")
    return tables


def apply_table_overrides(net: Any, table_overrides: Mapping[str, Any]) -> None:
    """Apply dashboard-edited operational values to a fresh ACDCPF network."""

    originals = {}
    try:
        for table_name, raw_frame in table_overrides.items():
            if raw_frame is None:
                continue
            if table_name not in EDITABLE_TABLE_SPECS:
                raise ValueError(f"Table `{table_name}` is not editable from the dashboard.")
            frame = _coerce_override_frame(raw_frame)
            if frame.empty:
                continue
            originals[table_name] = getattr(net, table_name).copy(deep=True)
            _apply_table_override_frame(net, table_name, frame)
        validate_equipment_limits(net)
    except (ValueError, TypeError, KeyError, AttributeError):
        for table_name, table in originals.items():
            setattr(net, table_name, table)
        raise


def network_limit_inventory(grid_case, scenario="original", table_overrides=None):
    """Inspect pending native inputs without starting PF or OPF."""
    selected_case = get_stagg5_grid_case(grid_case)
    net = create_acdcpf_network_for_stagg5_case(selected_case)
    scenario = _predefined_or_existing_vsc_scenario(scenario, selected_case)
    benchmark._apply_vsc_setpoints_to_acdcpf_network(net, scenario, selected_case)
    apply_table_overrides(net, table_overrides or {})
    return equipment_limit_inventory(net)


def changed_table_overrides(
    base_tables: Mapping[str, pd.DataFrame],
    edited_tables: Mapping[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Return only edited operational rows from dashboard table views."""

    changed: dict[str, pd.DataFrame] = {}
    for table_name, edited in edited_tables.items():
        if table_name not in base_tables or table_name not in EDITABLE_TABLE_SPECS:
            continue
        spec = EDITABLE_TABLE_SPECS[table_name]
        base = base_tables[table_name]
        edited_frame = _coerce_override_frame(edited)
        if edited_frame.empty or "element_id" not in edited_frame.columns:
            continue
        rows = []
        for _, row in edited_frame.iterrows():
            if _is_blank(row.get("element_id")):
                continue
            element_id = int(row["element_id"])
            base_row = _row_by_element_id(base, element_id)
            if base_row is None:
                rows.append(row.to_dict())
                continue
            if _row_has_editable_changes(base_row, row, spec.editable_columns):
                rows.append(row.to_dict())
        if rows:
            changed[table_name] = pd.DataFrame(rows)
    return changed


def opf_controls_from_tokens(tokens: tuple[str, ...]) -> benchmark.OPFControlSelection:
    """Create an ``OPFControlSelection`` from dashboard checkbox tokens."""

    raw = ",".join(tokens) if tokens else benchmark.OPF_CONTROL_PRESET_NONE
    return benchmark._parse_opf_controls(raw)


def control_selection_description(selection: benchmark.OPFControlSelection) -> str:
    return benchmark._opf_control_selection_description(selection)


def default_control_selection(grid_case: str | Stagg5GridCase) -> benchmark.OPFControlSelection:
    return benchmark._benchmark_opf_control_selection(grid_case)


def available_grid_cases() -> dict[str, Stagg5GridCase]:
    return dict(STAGG5_GRID_CASES)


def _run_single_snapshot_request(
    request: BenchmarkRequest,
    *,
    selected_case: Stagg5GridCase,
    generated_at: str,
    run_dir: Path | None,
    vsc_scenario: benchmark.VSCSetpointScenario,
    controls: benchmark.OPFControlSelection,
    network_overrides: Any | None,
) -> BenchmarkResultBundle:
    runs: list[benchmark.BenchmarkRun] = []
    matacdc_validation: dict[str, Any] = {}

    pf_run, pf_result = benchmark._run_acdcpf_pf(
        vsc_scenario,
        selected_case,
        network_overrides=network_overrides,
    )
    runs.append(pf_run)
    if _matacdc_reference_is_meaningful(request, selected_case, vsc_scenario, pf_result):
        matacdc_validation = benchmark._compare_acdcpf_pf_to_matacdc_reference(
            pf_result,
            selected_case,
        )
    elif pf_result is not None:
        matacdc_validation = {
            "available": False,
            "message": "MATACDC reference check skipped because this dashboard run changes the base case.",
        }

    if not request.skip_opf:
        opf_run, validation_run = benchmark._run_tool1_pyomo_ipopt(
            vsc_scenario,
            selected_case,
            opf_control_selection=controls,
            control_device_scope=request.control_device_scope,
            control_margin_percent=request.control_margin_percent,
            network_overrides=network_overrides,
        )
        runs.append(opf_run)
        if validation_run is not None:
            runs.append(validation_run)

    if request.include_pyflow_reference:
        runs.extend(_pyflow_runs_for_request(request, selected_case, vsc_scenario, controls))

    markdown = benchmark._build_markdown_report(
        runs,
        matacdc_validation=matacdc_validation,
        generated_at=generated_at,
        vsc_setpoint_scenario=vsc_scenario,
        grid_case=selected_case,
        control_margin_percent=request.control_margin_percent,
        opf_control_selection=controls,
        control_device_scope=request.control_device_scope,
    )
    export_paths = _write_single_snapshot_exports(
        request,
        runs,
        markdown,
        generated_at=generated_at,
        run_dir=run_dir,
        selected_case=selected_case,
        vsc_scenario=vsc_scenario,
        controls=controls,
    )
    return BenchmarkResultBundle(
        request=request,
        generated_at=generated_at,
        output_directory=run_dir,
        runs=tuple(runs),
        matacdc_validation=matacdc_validation,
        markdown=markdown,
        **export_paths,
    )


def _run_profiled_request(
    request: BenchmarkRequest,
    *,
    selected_case: Stagg5GridCase,
    generated_at: str,
    run_dir: Path | None,
    vsc_scenario: benchmark.VSCSetpointScenario,
    controls: benchmark.OPFControlSelection,
    profile_snapshots: tuple[ProfileSnapshot, ...],
    network_overrides: Any | None,
) -> BenchmarkResultBundle:
    time_points: list[benchmark.BenchmarkTimePoint] = []
    for snapshot in profile_snapshots:
        runs: list[benchmark.BenchmarkRun] = []
        pf_run, _ = benchmark._run_acdcpf_pf(
            vsc_scenario,
            selected_case,
            profile_snapshot=snapshot,
            network_overrides=network_overrides,
        )
        runs.append(pf_run)
        time_points.append(
            benchmark.BenchmarkTimePoint(
                time_index=snapshot.time_index,
                timestamp=snapshot.timestamp,
                runs=runs,
                matacdc_validation={
                    "available": False,
                    "message": "MATACDC reference checks are skipped for dashboard profile runs.",
                },
            )
        )

    if not request.skip_opf:
        opf_runs_by_time = benchmark._run_profiled_tool1_pyomo_ipopt(
            vsc_scenario,
            selected_case,
            profile_snapshots=profile_snapshots,
            opf_control_selection=controls,
            control_device_scope=request.control_device_scope,
            control_margin_percent=request.control_margin_percent,
            network_overrides=network_overrides,
        )
        for time_point in time_points:
            opf_run, validation_run = opf_runs_by_time.get(
                time_point.time_index,
                (
                    benchmark.BenchmarkRun(
                        label=benchmark.TOOL1_OBJECTIVE_LABEL,
                        success=False,
                        message="Tool1 (acdcopf) profile result is missing for this timestamp.",
                    ),
                    None,
                ),
            )
            time_point.runs.append(opf_run)
            if validation_run is not None:
                time_point.runs.append(validation_run)

    if request.include_pyflow_reference:
        for time_point in time_points:
            time_point.runs.extend(benchmark._pyflow_profile_unavailable_runs(selected_case))

    markdown = benchmark._build_profiled_markdown_report(
        time_points,
        generated_at=generated_at,
        vsc_setpoint_scenario=vsc_scenario,
        grid_case=selected_case,
        control_margin_percent=request.control_margin_percent,
        opf_control_selection=controls,
        control_device_scope=request.control_device_scope,
        profile_input=request.profile_input,
    )
    export_paths = _write_profiled_exports(
        request,
        time_points,
        markdown,
        generated_at=generated_at,
        run_dir=run_dir,
        selected_case=selected_case,
        vsc_scenario=vsc_scenario,
        controls=controls,
    )
    return BenchmarkResultBundle(
        request=request,
        generated_at=generated_at,
        output_directory=run_dir,
        time_points=tuple(time_points),
        markdown=markdown,
        **export_paths,
    )


def _write_single_snapshot_exports(
    request: BenchmarkRequest,
    runs: list[benchmark.BenchmarkRun],
    markdown: str,
    *,
    generated_at: str,
    run_dir: Path | None,
    selected_case: Stagg5GridCase,
    vsc_scenario: benchmark.VSCSetpointScenario,
    controls: benchmark.OPFControlSelection,
) -> dict[str, Any]:
    if not request.write_exports or run_dir is None:
        return {"markdown_path": None, "csv_paths": (), "xlsx_paths": (), "html_path": None}
    run_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = (
        benchmark._write_markdown_report(markdown, run_dir / "tool1_dashboard_report.md")
        if request.include_markdown
        else None
    )
    csv_paths: tuple[Path, ...] = ()
    if request.include_csv:
        csv_paths = tuple(
            benchmark._export_split_result_csv_reports(
                runs,
                (run_dir / "tool1_pf_results.csv", run_dir / "tool1_opf_results.csv"),
                generated_at=generated_at,
                vsc_setpoint_scenario=vsc_scenario,
                grid_case=selected_case,
                control_margin_percent=request.control_margin_percent,
                opf_control_selection=controls,
                control_device_scope=request.control_device_scope,
            )
        )
    xlsx_paths: tuple[Path, ...] = ()
    if request.include_xlsx:
        xlsx_paths = tuple(
            benchmark._export_split_result_xlsx_reports(
                runs,
                (run_dir / "tool1_pf_results.xlsx", run_dir / "tool1_opf_results.xlsx"),
                generated_at=generated_at,
                vsc_setpoint_scenario=vsc_scenario,
                grid_case=selected_case,
                control_margin_percent=request.control_margin_percent,
                opf_control_selection=controls,
                control_device_scope=request.control_device_scope,
            )
        )
    html_path = None
    if request.include_html:
        html_path = benchmark._export_html_grid_view(
            runs,
            run_dir / "tool1_grid_view.html",
            generated_at=generated_at,
            vsc_setpoint_scenario=vsc_scenario,
            grid_case=selected_case,
            control_margin_percent=request.control_margin_percent,
            opf_control_selection=controls,
            control_device_scope=request.control_device_scope,
        )
    return {
        "markdown_path": markdown_path,
        "csv_paths": csv_paths,
        "xlsx_paths": xlsx_paths,
        "html_path": html_path,
    }


def _write_profiled_exports(
    request: BenchmarkRequest,
    time_points: list[benchmark.BenchmarkTimePoint],
    markdown: str,
    *,
    generated_at: str,
    run_dir: Path | None,
    selected_case: Stagg5GridCase,
    vsc_scenario: benchmark.VSCSetpointScenario,
    controls: benchmark.OPFControlSelection,
) -> dict[str, Any]:
    if not request.write_exports or run_dir is None:
        return {"markdown_path": None, "csv_paths": (), "xlsx_paths": (), "html_path": None}
    run_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = (
        benchmark._write_markdown_report(markdown, run_dir / "tool1_dashboard_report.md")
        if request.include_markdown
        else None
    )
    csv_paths: tuple[Path, ...] = ()
    if request.include_csv:
        csv_paths = tuple(
            benchmark._export_profiled_split_result_csv_reports(
                time_points,
                (run_dir / "tool1_pf_results.csv", run_dir / "tool1_opf_results.csv"),
                generated_at=generated_at,
                vsc_setpoint_scenario=vsc_scenario,
                grid_case=selected_case,
                control_margin_percent=request.control_margin_percent,
                opf_control_selection=controls,
                control_device_scope=request.control_device_scope,
            )
        )
    xlsx_paths: tuple[Path, ...] = ()
    if request.include_xlsx:
        xlsx_paths = tuple(
            benchmark._export_profiled_split_result_xlsx_reports(
                time_points,
                (run_dir / "tool1_pf_results.xlsx", run_dir / "tool1_opf_results.xlsx"),
                generated_at=generated_at,
                vsc_setpoint_scenario=vsc_scenario,
                grid_case=selected_case,
                control_margin_percent=request.control_margin_percent,
                opf_control_selection=controls,
                control_device_scope=request.control_device_scope,
            )
        )
    html_path = None
    if request.include_html:
        html_path = benchmark._export_profiled_html_grid_view(
            time_points,
            run_dir / "tool1_grid_view.html",
            generated_at=generated_at,
            vsc_setpoint_scenario=vsc_scenario,
            grid_case=selected_case,
            control_margin_percent=request.control_margin_percent,
            opf_control_selection=controls,
            control_device_scope=request.control_device_scope,
        )
    return {
        "markdown_path": markdown_path,
        "csv_paths": csv_paths,
        "xlsx_paths": xlsx_paths,
        "html_path": html_path,
    }


def _pyflow_runs_for_request(
    request: BenchmarkRequest,
    selected_case: Stagg5GridCase,
    vsc_scenario: benchmark.VSCSetpointScenario,
    controls: benchmark.OPFControlSelection,
) -> list[benchmark.BenchmarkRun]:
    if _has_table_overrides(request):
        return [
            _not_comparable_pyflow_run("PyFlow PF", "Dashboard table overrides are native ACDCPF edits."),
            _not_comparable_pyflow_run(
                "PyFlow OPF VSC-only",
                "Dashboard table overrides are native ACDCPF edits.",
            ),
        ]
    if not selected_case.supports_pyflow:
        return benchmark._pyflow_unavailable_runs(selected_case)
    return [
        benchmark._run_pyflow_pf(vsc_scenario, selected_case),
        benchmark._run_pyflow_opf(vsc_scenario, selected_case, opf_control_selection=controls),
    ]


def _not_comparable_pyflow_run(label: str, reason: str) -> benchmark.BenchmarkRun:
    return benchmark.BenchmarkRun(
        label=label,
        success=False,
        message=f"not comparable: {reason}",
        diagnostics={"pyflow_reference_available": False, "reason": reason},
    )


def _matacdc_reference_is_meaningful(
    request: BenchmarkRequest,
    selected_case: Stagg5GridCase,
    vsc_scenario: benchmark.VSCSetpointScenario,
    pf_result: Any | None,
) -> bool:
    return (
        pf_result is not None
        and selected_case.supports_matacdc_reference
        and vsc_scenario.name == benchmark.VSC_SETPOINT_SCENARIO_ORIGINAL
        and not _has_table_overrides(request)
    )


def _profile_snapshots_from_request(request: BenchmarkRequest) -> tuple[ProfileSnapshot, ...] | None:
    if request.profile_snapshots is not None:
        return tuple(request.profile_snapshots)
    if request.profile_input is None:
        return None
    snapshots = load_time_profile(
        request.profile_input,
        sheet_name=request.profile_sheet,
        profile_format=request.profile_format,
    )
    if not snapshots:
        raise ValueError(f"Time profile `{request.profile_input}` did not contain snapshots.")
    return snapshots


def _network_override_callable(request: BenchmarkRequest) -> Any | None:
    def apply_overrides(net: Any) -> None:
        apply_table_overrides(net, request.table_overrides)
        net.tool5_policy = request.pf_policy

    return apply_overrides


def _control_selection_from_request(
    request: BenchmarkRequest,
    selected_case: Stagg5GridCase,
) -> benchmark.OPFControlSelection:
    if isinstance(request.opf_controls, benchmark.OPFControlSelection):
        return request.opf_controls
    return benchmark._parse_opf_controls(request.opf_controls, selected_case)


def _vsc_scenario_from_request(
    request: BenchmarkRequest,
    selected_case: Stagg5GridCase,
) -> benchmark.VSCSetpointScenario:
    if request.custom_vsc_setpoints:
        return benchmark.VSCSetpointScenario(
            name=benchmark.VSC_SETPOINT_SCENARIO_CUSTOM,
            description="Dashboard-edited VSC starting setpoints.",
            setpoints={
                int(idx): {
                    "p_ac_mw": float(values["p_ac_mw"]),
                    "q_ac_mvar": float(values["q_ac_mvar"]),
                }
                for idx, values in request.custom_vsc_setpoints.items()
            },
        )
    return _predefined_or_existing_vsc_scenario(request.vsc_setpoint_scenario, selected_case)


def _predefined_or_existing_vsc_scenario(
    scenario: str | benchmark.VSCSetpointScenario,
    selected_case: Stagg5GridCase,
) -> benchmark.VSCSetpointScenario:
    if isinstance(scenario, benchmark.VSCSetpointScenario):
        return scenario
    return benchmark._predefined_vsc_setpoint_scenario(str(scenario), selected_case)


def _validate_request(
    request: BenchmarkRequest,
    selected_case: Stagg5GridCase,
    controls: benchmark.OPFControlSelection,
) -> None:
    if request.control_device_scope not in CONTROL_DEVICE_SCOPES:
        raise ValueError(
            "control_device_scope must be one of: "
            f"{', '.join(CONTROL_DEVICE_SCOPES)}."
        )
    if request.control_margin_percent is not None:
        if not math.isfinite(float(request.control_margin_percent)) or request.control_margin_percent < 0:
            raise ValueError("control_margin_percent must be a finite nonnegative number.")
    if selected_case.source != "acdcpf" and _has_table_overrides(request):
        raise ValueError("Dashboard table overrides are supported only for native ACDCPF cases.")
    if selected_case.source != "acdcpf" and (
        request.profile_input is not None or request.profile_snapshots is not None
    ):
        raise ValueError("Time-profile runs are supported only for native ACDCPF cases.")
    if not isinstance(controls, benchmark.OPFControlSelection):
        raise TypeError("opf_controls must resolve to OPFControlSelection.")


def _output_directory_for_request(
    request: BenchmarkRequest,
    selected_case: Stagg5GridCase,
    generated_at: str,
) -> Path | None:
    if not request.write_exports:
        return None
    base_dir = request.output_directory or (
        reports_directory() / "dashboard_runs"
    )
    safe_time = generated_at.replace(":", "").replace("-", "").replace("T", "_")
    return base_dir / f"{safe_time}_{selected_case.key}"


def _has_table_overrides(request: BenchmarkRequest) -> bool:
    return any(_raw_override_has_rows(value) for value in request.table_overrides.values())


def _raw_override_has_rows(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, pd.DataFrame):
        return not value.empty
    if isinstance(value, Mapping):
        return bool(value)
    try:
        return len(value) > 0
    except TypeError:
        return True


def _editable_table_view(table: pd.DataFrame, spec: EditableTableSpec) -> pd.DataFrame:
    columns = [
        column
        for column in ("element_id", *spec.read_only_columns, *spec.editable_columns)
        if column == "element_id" or column in table.columns or column in spec.editable_columns
    ]
    columns.extend(column for column in table.columns if column.startswith("source_") and column not in columns)
    frame = table.copy()
    for column in spec.editable_columns:
        if column not in frame:
            frame[column] = float("nan")
    for limit in LIMIT_SPECS.get(spec.name, ()):
        column = limit.fields[0]
        if column in spec.editable_columns and len(limit.fields) > 1:
            frame[column] = [source_limit_value(row, limit.fields)[1] for _, row in table.iterrows()]
    frame.insert(0, "element_id", frame.index.astype(int))
    return frame.loc[:, columns].reset_index(drop=True)


def _coerce_override_frame(raw_frame: Any) -> pd.DataFrame:
    if isinstance(raw_frame, pd.DataFrame):
        return raw_frame.copy()
    if isinstance(raw_frame, Mapping):
        return pd.DataFrame.from_dict(raw_frame, orient="index").reset_index(names="element_id")
    return pd.DataFrame(raw_frame)


def _row_by_element_id(frame: pd.DataFrame, element_id: int) -> pd.Series | None:
    if "element_id" not in frame.columns:
        return None
    matches = frame[frame["element_id"].astype(int) == int(element_id)]
    if matches.empty:
        return None
    return matches.iloc[0]


def _row_has_editable_changes(
    base_row: pd.Series,
    edited_row: pd.Series,
    editable_columns: tuple[str, ...],
) -> bool:
    for column in editable_columns:
        if column not in base_row.index or column not in edited_row.index:
            continue
        if not _values_equivalent(base_row[column], edited_row[column]):
            return True
    return False


def _values_equivalent(left: Any, right: Any) -> bool:
    if _is_blank(left) and _is_blank(right):
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        try:
            return _coerce_bool(left) == _coerce_bool(right)
        except ValueError:
            return False
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _apply_table_override_frame(net: Any, table_name: str, frame: pd.DataFrame) -> None:
    spec = EDITABLE_TABLE_SPECS[table_name]
    table = getattr(net, table_name, None)
    if table is None:
        raise ValueError(f"Network has no `{table_name}` table.")
    if "element_id" not in frame.columns:
        raise ValueError(f"Override table `{table_name}` must contain an `element_id` column.")

    allowed_columns = {"element_id", *spec.read_only_columns, *spec.editable_columns}
    # Imported provenance accompanies dashboard rows but is never editable.
    allowed_columns.update(column for column in table.columns if column.startswith("source_"))
    unknown_columns = sorted(set(frame.columns) - allowed_columns)
    if unknown_columns:
        raise ValueError(
            f"Override table `{table_name}` contains unsupported columns: "
            f"{', '.join(unknown_columns)}."
        )

    for _, row in frame.iterrows():
        if _is_blank(row.get("element_id")):
            continue
        element_id = int(row["element_id"])
        if element_id not in table.index:
            raise ValueError(f"Network `{table_name}` has no element_id {element_id}.")
        if table_name == "dcdc" and {"d_ratio_min", "d_ratio_max"}.intersection(frame.columns):
            source = table.loc[element_id]
            pu_values = [source_limit_value(source, limit.fields)[1] for limit in DCDC_PU_LIMIT_SPECS]
            if any(value is not None for value in pu_values):
                factor = float(net.dc_bus.at[int(source.to_bus), "v_base"]) / float(net.dc_bus.at[int(source.from_bus), "v_base"])
                for column, value in zip(("d_ratio_min", "d_ratio_max"), pu_values):
                    table.at[element_id, column] = value * factor if value is not None else float("nan")
                for limit in DCDC_PU_LIMIT_SPECS:
                    for alias in limit.fields:
                        if alias in table:
                            table.at[element_id, alias] = float("nan")
        for column in spec.editable_columns:
            if column not in frame.columns:
                continue
            if column not in table.columns:
                table[column] = float("nan")
            # Canonical edits, including clearing a limit, supersede legacy
            # aliases so a hidden old column cannot silently restore the limit.
            for limit in LIMIT_SPECS.get(table_name, ()):
                if column == limit.fields[0]:
                    for alias in limit.fields[1:]:
                        if alias in table:
                            table.at[element_id, alias] = float("nan")
            value = row.get(column)
            if _is_blank(value):
                # Native PF reads generator Q bounds directly; preserve its
                # unbounded convention when an optional dispatch bound is cleared.
                if table_name == "ac_gen" and column in {"p_min_mw", "q_min_mvar"}:
                    table.at[element_id, column] = -math.inf
                elif table_name == "ac_gen" and column in {"p_max_mw", "q_max_mvar"}:
                    table.at[element_id, column] = math.inf
                else:
                    table.at[element_id, column] = float("nan")
            elif column in {"in_service", "tap_controllable"}:
                table.at[element_id, column] = _coerce_bool(value)
            else:
                table.at[element_id, column] = float(value)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"Cannot interpret {value!r} as a boolean value.")
