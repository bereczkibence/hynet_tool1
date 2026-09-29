from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

import pandas as pd

from acdcpf_opf.benchmarks.stagg5 import benchmark_pf_opf_comparison as benchmark
from acdcpf_opf.benchmarks.stagg5.case_variants import (
    STAGG5_HYBRID_DCDC,
    STAGG5_ORIGINAL,
    Stagg5GridCase,
    get_stagg5_grid_case,
)
from acdcpf_opf.benchmarks.stagg5.service import (
    EDITABLE_TABLE_SPECS,
    BenchmarkRequest,
    BenchmarkResultBundle,
    changed_table_overrides,
    control_selection_description,
    default_control_selection,
    editable_network_tables,
    run_benchmark_request,
)
from acdcpf_opf.dashboard.runtime import DashboardRunManager
from acdcpf_opf.data.time_profiles import load_time_profile
from acdcpf_opf.runtime_paths import reports_directory


DASHBOARD_RUN_ROOT = reports_directory() / "dashboard_runs"


def main() -> None:
    """Streamlit entrypoint for the local Tool1 (acdcopf) dashboard."""

    st = _streamlit()
    st.set_page_config(page_title="Tool1 (acdcopf) Dashboard", layout="wide")
    _ensure_session_state(st)
    _collect_finished_run(st)

    st.title("Tool1 (acdcopf) Local Dashboard")
    st.caption(
        "Configure ACDCPF PF and Tool1 (acdcopf) loss minimization runs without using terminal prompts."
    )

    settings = _sidebar_settings(st)
    tabs = st.tabs(["Network Inputs", "Profiles", "Run Status", "Results", "Grid View", "Exports"])

    with tabs[0]:
        edited_tables = _network_inputs_tab(st, settings)
    with tabs[1]:
        profile_snapshots = _profiles_tab(st, settings)
    with tabs[2]:
        _run_status_tab(st, settings, edited_tables, profile_snapshots)
    with tabs[3]:
        _results_tab(st)
    with tabs[4]:
        _grid_view_tab(st)
    with tabs[5]:
        _exports_tab(st)


def _streamlit():
    try:
        import streamlit as st
    except ImportError as exc:  # pragma: no cover - depends on optional dashboard dependency
        raise SystemExit(
            "The Tool1 (acdcopf) dashboard requires Streamlit. Install it with "
            "`pip install .[dashboard]` or `pip install .[all]`."
        ) from exc
    return st


def _ensure_session_state(st: Any) -> None:
    if "run_manager" not in st.session_state:
        st.session_state.run_manager = DashboardRunManager[BenchmarkResultBundle]()
    if "last_result" not in st.session_state:
        st.session_state.last_result = None
    if "last_error" not in st.session_state:
        st.session_state.last_error = None
    if "last_submitted_configuration" not in st.session_state:
        st.session_state.last_submitted_configuration = None


def _collect_finished_run(st: Any) -> None:
    manager: DashboardRunManager[BenchmarkResultBundle] = st.session_state.run_manager
    status = manager.status()
    if status.state not in {"succeeded", "failed"}:
        return
    if status.state == "failed":
        st.session_state.last_error = status.message
        return
    try:
        st.session_state.last_result = manager.result()
        st.session_state.last_error = None
    except Exception as exc:  # pragma: no cover - defensive UI path
        st.session_state.last_error = f"{type(exc).__name__}: {exc}"


def _sidebar_settings(st: Any) -> dict[str, Any]:
    cases = benchmark.STAGG5_GRID_CASES
    with st.sidebar:
        st.header("Run Setup")
        case_key = st.selectbox(
            "Grid case",
            options=list(cases.keys()),
            format_func=lambda key: cases[key].display_name,
        )
        selected_case = get_stagg5_grid_case(case_key)

        scenario_options = [benchmark.VSC_SETPOINT_SCENARIO_ORIGINAL]
        if selected_case.key in {STAGG5_ORIGINAL, STAGG5_HYBRID_DCDC}:
            scenario_options.append(benchmark.VSC_SETPOINT_SCENARIO_PERTURBED)
        scenario = st.selectbox(
            "VSC starting setpoints",
            options=scenario_options,
            format_func=lambda value: benchmark.VSC_SETPOINT_SCENARIO_DESCRIPTIONS[value],
        )

        st.subheader("OPF Controls")
        default_controls = default_control_selection(selected_case)
        control_values = {
            token: st.checkbox(
                benchmark.OPF_CONTROL_DISPLAY_NAMES[token],
                value=bool(getattr(default_controls, token)),
            )
            for token in benchmark.OPF_CONTROL_TOKENS
        }
        controls = benchmark.OPFControlSelection(**control_values)
        st.caption(control_selection_description(controls))

        control_scope = st.selectbox(
            "Control device scope",
            options=[benchmark.CONTROL_DEVICE_SCOPE_BENCHMARK, benchmark.CONTROL_DEVICE_SCOPE_ALL],
            format_func=lambda value: "Case-defined controllable devices" if value == "benchmark" else "All eligible devices",
        )
        use_margin = st.checkbox("Limit control movement with a margin", value=False)
        control_margin = None
        if use_margin:
            control_margin = st.number_input(
                "Control margin (% of apparent rating)",
                min_value=0.0,
                value=10.0,
                step=1.0,
            )

        st.subheader("References and Exports")
        include_pyflow = st.checkbox(
            "Include PyFlow reference when comparable",
            value=bool(selected_case.supports_pyflow),
        )
        skip_opf = st.checkbox("Run PF only, skip TOOL1", value=False)
        write_exports = st.checkbox("Write persistent report files", value=True)
        include_markdown = st.checkbox("Markdown", value=True, disabled=not write_exports)
        include_csv = st.checkbox("CSV", value=True, disabled=not write_exports)
        include_xlsx = st.checkbox("Excel", value=True, disabled=not write_exports)
        include_html = st.checkbox("HTML grid view", value=True, disabled=not write_exports)

    return {
        "selected_case": selected_case,
        "scenario": scenario,
        "controls": controls,
        "control_scope": control_scope,
        "control_margin": control_margin,
        "include_pyflow": include_pyflow,
        "skip_opf": skip_opf,
        "write_exports": write_exports,
        "include_markdown": include_markdown,
        "include_csv": include_csv,
        "include_xlsx": include_xlsx,
        "include_html": include_html,
    }


def _network_inputs_tab(st: Any, settings: dict[str, Any]) -> dict[str, pd.DataFrame]:
    selected_case: Stagg5GridCase = settings["selected_case"]
    st.subheader("Editable Operational Inputs")
    if selected_case.source != "acdcpf":
        st.info("This PyFlow-sourced case is available for runs, but editable ACDCPF tables are disabled in v1.")
        return {}

    base_tables = editable_network_tables(selected_case, settings["scenario"])
    st.session_state["base_editable_tables"] = base_tables
    edited_tables: dict[str, pd.DataFrame] = {}
    for table_name, frame in base_tables.items():
        spec = EDITABLE_TABLE_SPECS[table_name]
        with st.expander(spec.label, expanded=table_name in {"ac_load", "vsc"}):
            st.caption("Topology columns are shown for context and locked in this dashboard version.")
            edited = st.data_editor(
                frame,
                hide_index=True,
                use_container_width=True,
                disabled=["element_id", *spec.read_only_columns],
                key=f"editor_{selected_case.key}_{settings['scenario']}_{table_name}",
            )
            edited_tables[table_name] = edited
    return changed_table_overrides(base_tables, edited_tables)


def _profiles_tab(st: Any, settings: dict[str, Any]):
    selected_case: Stagg5GridCase = settings["selected_case"]
    st.subheader("Snapshot Time Profile")
    if selected_case.source != "acdcpf":
        st.session_state.profile_input_ready = True
        st.info("Time profiles are currently supported only for native ACDCPF cases.")
        return None

    options = ["No profile"]
    built_in_profiles = _built_in_profiles_for_case(selected_case)
    built_in_options = {
        f"Built-in: {label}": path
        for label, path in built_in_profiles
    }
    options.extend(built_in_options.keys())
    options.append("Upload CSV/XLSX")
    choice = st.radio("Profile source", options=options, horizontal=True)

    if choice == "No profile":
        st.session_state.profile_input_ready = True
        return None
    selected_built_in = built_in_options.get(choice)
    if selected_built_in is not None:
        st.code(str(selected_built_in))
        snapshots = load_time_profile(selected_built_in)
        st.session_state.profile_input_ready = True
        st.success(f"Loaded {len(snapshots)} profile snapshots.")
        _show_profile_preview(st, snapshots)
        return snapshots

    uploaded = st.file_uploader("Upload long-format profile", type=["csv", "xlsx", "xlsm", "xls"])
    if uploaded is None:
        st.session_state.profile_input_ready = False
        st.warning("Upload a profile file before running a profiled study.")
        return None
    profile_path = None
    try:
        suffix = Path(uploaded.name).suffix or ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(uploaded.getvalue())
            profile_path = Path(handle.name)
        snapshots = load_time_profile(profile_path)
    except Exception as exc:
        st.session_state.profile_input_ready = False
        st.error(f"Could not parse profile: {type(exc).__name__}: {exc}")
        return None
    finally:
        if profile_path is not None:
            profile_path.unlink(missing_ok=True)
    st.session_state.profile_input_ready = True
    st.success(f"Loaded {len(snapshots)} profile snapshots.")
    _show_profile_preview(st, snapshots)
    return snapshots


def _run_status_tab(
    st: Any,
    settings: dict[str, Any],
    table_overrides: dict[str, pd.DataFrame],
    profile_snapshots: tuple | None,
) -> None:
    st.subheader("Run Status")
    manager: DashboardRunManager[BenchmarkResultBundle] = st.session_state.run_manager
    status = manager.status()
    st.metric("Background worker", status.state)
    if status.message:
        st.caption(status.message)
    if st.session_state.last_error:
        st.error(st.session_state.last_error)

    request = BenchmarkRequest(
        grid_case=settings["selected_case"],
        vsc_setpoint_scenario=settings["scenario"],
        opf_controls=settings["controls"],
        control_device_scope=settings["control_scope"],
        control_margin_percent=settings["control_margin"],
        profile_snapshots=profile_snapshots,
        table_overrides=table_overrides,
        skip_opf=settings["skip_opf"],
        include_pyflow_reference=settings["include_pyflow"],
        write_exports=settings["write_exports"],
        include_markdown=settings["include_markdown"],
        include_csv=settings["include_csv"],
        include_xlsx=settings["include_xlsx"],
        include_html=settings["include_html"],
        output_directory=DASHBOARD_RUN_ROOT,
    )

    st.markdown("### Pending Run Configuration")
    st.dataframe(
        pd.DataFrame(_run_configuration_rows(request, table_overrides, profile_snapshots)),
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "Change the sidebar controls or editable tables, then click Run again. "
        "The next run uses the settings shown above."
    )

    col_run, col_refresh = st.columns([1, 1])
    profile_ready = bool(getattr(st.session_state, "profile_input_ready", True))
    with col_run:
        if st.button(
            "Run ACDCPF PF + TOOL1",
            type="primary",
            disabled=status.state in {"queued", "running"} or not profile_ready,
        ):
            try:
                st.session_state.last_error = None
                st.session_state.last_submitted_configuration = _run_configuration_rows(
                    request,
                    table_overrides,
                    profile_snapshots,
                )
                manager.submit(run_benchmark_request, request)
                st.success("Run submitted. The dashboard will use the settings shown above.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not submit run: {type(exc).__name__}: {exc}")
    with col_refresh:
        if st.button("Refresh status"):
            st.rerun()

    if st.session_state.last_submitted_configuration is not None:
        with st.expander("Last Submitted Configuration", expanded=False):
            st.dataframe(
                pd.DataFrame(st.session_state.last_submitted_configuration),
                hide_index=True,
                use_container_width=True,
            )

    if table_overrides:
        st.info(f"Applying edited operational rows in {len(table_overrides)} table(s).")
    else:
        st.caption("No operational table edits detected.")
    if not profile_ready:
        st.warning("Select or upload a valid profile before running this profiled study.")


def _results_tab(st: Any) -> None:
    result: BenchmarkResultBundle | None = st.session_state.last_result
    st.subheader("Results")
    if result is None:
        st.info("Run a study to see PF/OPF results.")
        return
    runs = _representative_runs(st, result)
    pf = _run_by_label(runs, "ACDCPF PF")
    opf = _run_by_label(runs, benchmark.TOOL1_OBJECTIVE_LABEL)
    cols = st.columns(4)
    cols[0].metric("PF loss MW", _format_loss(pf))
    cols[1].metric("Tool1 (acdcopf) loss MW", _format_loss(opf))
    cols[2].metric("OPF - PF MW", _format_delta(opf, pf))
    cols[3].metric("Snapshots", len(result.time_points) if result.is_profiled else 1)

    st.markdown("### Solver Status")
    st.dataframe(
        pd.DataFrame(
            [
                {"run": run.label, "success": run.success, "message": run.message}
                for run in runs
            ]
        ),
        use_container_width=True,
    )

    for table_name in ("ac_buses", "dc_buses", "generators", "dc_generators", "storage_units", "converters", "dcdc_converters", "transformers", "ac_branches", "dc_branches"):
        frames = _result_table_frames(runs, table_name)
        if frames:
            with st.expander(table_name.replace("_", " ").title(), expanded=table_name in {"ac_buses", "converters"}):
                st.dataframe(pd.concat(frames, ignore_index=True), use_container_width=True)


def _grid_view_tab(st: Any) -> None:
    result: BenchmarkResultBundle | None = st.session_state.last_result
    st.subheader("Interactive Grid View")
    if result is None:
        st.info("Run a study to generate the grid view.")
        return
    if result.html_path is None or not result.html_path.exists():
        st.warning("No HTML grid view was written for this run.")
        return
    import streamlit.components.v1 as components

    components.html(result.html_path.read_text(encoding="utf-8"), height=850, scrolling=True)


def _exports_tab(st: Any) -> None:
    result: BenchmarkResultBundle | None = st.session_state.last_result
    st.subheader("Exports")
    if result is None:
        st.info("Run a study to generate exports.")
        return
    if result.output_directory is not None:
        st.code(str(result.output_directory))
    for path in _export_paths(result):
        if path is None or not path.exists():
            continue
        st.download_button(
            label=f"Download {path.name}",
            data=path.read_bytes(),
            file_name=path.name,
        )


def _built_in_profiles_for_case(selected_case: Stagg5GridCase) -> list[tuple[str, Path]]:
    if selected_case.key == STAGG5_ORIGINAL:
        return [
            ("Original Stagg5 3-step AC load profile", benchmark.ORIGINAL_STAGG5_LOAD_PROFILE),
            ("Original Stagg5 24-hour AC load profile", benchmark.STAGG5_ONE_DAY_AC_LOAD_PROFILE),
        ]
    if selected_case.key == STAGG5_HYBRID_DCDC:
        return [
            ("Hybrid DCDC 3-step load/PV profile", benchmark.HYBRID_DCDC_LOAD_PV_PROFILE),
            (
                "Hybrid DCDC 24-hour load/PV profile",
                benchmark.HYBRID_DCDC_ONE_DAY_LOAD_PV_PROFILE,
            ),
        ]
    return []


def _show_profile_preview(st: Any, snapshots: tuple) -> None:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "time_index": snapshot.time_index,
                    "timestamp": snapshot.timestamp,
                    "changes": len(snapshot.changes),
                }
                for snapshot in snapshots
            ]
        ),
        use_container_width=True,
    )


def _run_configuration_rows(
    request: BenchmarkRequest,
    table_overrides: dict[str, pd.DataFrame],
    profile_snapshots: tuple | None,
) -> list[dict[str, str]]:
    """Return a compact, human-readable summary of the next dashboard run."""

    selected_case = get_stagg5_grid_case(request.grid_case)
    controls = (
        request.opf_controls
        if isinstance(request.opf_controls, benchmark.OPFControlSelection)
        else benchmark._parse_opf_controls(request.opf_controls, selected_case)
    )
    profile_description = "Single steady-state snapshot"
    if profile_snapshots is not None:
        profile_description = f"{len(profile_snapshots)} independent profile snapshot(s)"
    edited_rows = sum(len(frame) for frame in table_overrides.values())
    edited_tables = ", ".join(sorted(table_overrides)) if table_overrides else "none"
    export_formats = _selected_export_formats(request)
    export_root = request.output_directory or DASHBOARD_RUN_ROOT

    return [
        {"setting": "Grid case", "value": selected_case.display_name},
        {
            "setting": "Run type",
            "value": "ACDCPF PF only" if request.skip_opf else "ACDCPF PF + TOOL1",
        },
        {
            "setting": "VSC starting setpoints",
            "value": _vsc_scenario_label(request.vsc_setpoint_scenario),
        },
        {"setting": "Enabled OPF controls", "value": control_selection_description(controls)},
        {
            "setting": "Control device scope",
            "value": (
                "Case-defined controllable devices"
                if request.control_device_scope == benchmark.CONTROL_DEVICE_SCOPE_BENCHMARK
                else "All eligible devices"
            ),
        },
        {
            "setting": "Control margin",
            "value": (
                "Full technical bounds"
                if request.control_margin_percent is None
                else f"+/-{float(request.control_margin_percent):g}% of rating"
            ),
        },
        {"setting": "Time profile", "value": profile_description},
        {
            "setting": "Operational table edits",
            "value": f"{edited_rows} row(s) across {edited_tables}",
        },
        {
            "setting": "PyFlow reference",
            "value": "Enabled when comparable" if request.include_pyflow_reference else "Disabled",
        },
        {
            "setting": "Exports",
            "value": export_formats,
        },
        {
            "setting": "Export location",
            "value": (
                "not written"
                if not request.write_exports
                else f"timestamped folder under {export_root}"
            ),
        },
    ]


def _vsc_scenario_label(scenario: object) -> str:
    if hasattr(scenario, "description") and hasattr(scenario, "name"):
        return f"{scenario.name}: {scenario.description}"
    text = str(scenario)
    return benchmark.VSC_SETPOINT_SCENARIO_DESCRIPTIONS.get(text, text)


def _selected_export_formats(request: BenchmarkRequest) -> str:
    if not request.write_exports:
        return "not written"
    formats = []
    if request.include_markdown:
        formats.append("Markdown")
    if request.include_csv:
        formats.append("CSV")
    if request.include_xlsx:
        formats.append("Excel")
    if request.include_html:
        formats.append("HTML")
    return ", ".join(formats) if formats else "none selected"


def _representative_runs(st: Any, result: BenchmarkResultBundle) -> tuple:
    if not result.is_profiled:
        return result.runs
    if not result.time_points:
        return ()
    selected_point = st.selectbox(
        "Displayed snapshot",
        options=list(result.time_points),
        format_func=lambda point: f"t={point.time_index} | {point.timestamp}",
    )
    return tuple(selected_point.runs)


def _run_by_label(runs: tuple, label: str):
    for run in runs:
        if run.label == label:
            return run
    return None


def _format_loss(run: Any | None) -> str:
    if run is None:
        return "n/a"
    value = run.losses_mw.get("total_active_losses_mw")
    return "n/a" if value is None else f"{float(value):.4f}"


def _format_delta(left: Any | None, right: Any | None) -> str:
    if left is None or right is None:
        return "n/a"
    left_value = left.losses_mw.get("total_active_losses_mw")
    right_value = right.losses_mw.get("total_active_losses_mw")
    if left_value is None or right_value is None:
        return "n/a"
    return f"{float(left_value) - float(right_value):.4f}"


def _result_table_frames(runs: tuple, table_name: str) -> list[pd.DataFrame]:
    frames = []
    for run in runs:
        rows = run.tables.get(table_name, [])
        if not rows:
            continue
        frame = pd.DataFrame(rows)
        frame.insert(0, "run", run.label)
        frames.append(frame)
    return frames


def _export_paths(result: BenchmarkResultBundle) -> list[Path | None]:
    return [
        result.markdown_path,
        *result.csv_paths,
        *result.xlsx_paths,
        result.html_path,
    ]


if __name__ == "__main__":
    main()
