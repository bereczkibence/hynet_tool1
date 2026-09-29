from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
import uuid
import os
import logging
from importlib import metadata
from acdcopf.presentation import brand_text
from acdcopf.status import DEVELOPMENT_STATUS, DEVELOPMENT_NOTICE
from typing import Any, Mapping

import pandas as pd

from acdcpf_opf.benchmarks.stagg5 import benchmark_pf_opf_comparison as benchmark
from acdcpf_opf.benchmarks.stagg5.case_variants import (
    STAGG5_HYBRID_DCDC,
    STAGG5_ORIGINAL,
    Stagg5GridCase,
    get_stagg5_grid_case,
    custom_grid_case,
)
from acdcpf_opf.benchmarks.stagg5.service import (
    EDITABLE_TABLE_SPECS,
    BenchmarkRequest,
    BenchmarkResultBundle,
    control_selection_description,
    default_control_selection,
    editable_network_tables,
    network_limit_inventory,
    run_benchmark_request,
)
from acdcpf_opf.dashboard.runtime import DashboardRunManager
from acdcpf_opf.runtime_paths import reports_directory


try:  # pragma: no cover - exercised when optional dashboard dependency is installed
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from .schemas import ImportRequest, RunRequest, LimitsRequest
    from fastapi.staticfiles import StaticFiles
except ImportError:  # pragma: no cover - lets non-dashboard tests import helper functions
    FastAPI = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]
    HTMLResponse = None  # type: ignore[assignment]
    StaticFiles = None  # type: ignore[assignment]


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_RUN_ROOT = (
    reports_directory() / "dashboard_runs"
)


@dataclass
class WebDashboardState:
    """Server-side state for one local dashboard process."""

    run_manager: DashboardRunManager[BenchmarkResultBundle] = field(
        default_factory=DashboardRunManager
    )
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_result: BenchmarkResultBundle | None = None
    last_error: str | None = None
    last_submitted_request: BenchmarkRequest | None = None
    last_submitted_summary: list[dict[str, str]] | None = None
    custom_cases: dict[str, Stagg5GridCase] = field(default_factory=dict)


STATE = WebDashboardState()
SESSION_ID = uuid.uuid4().hex
SUBMISSION_LOCK = threading.Lock()


def resolve_dashboard_case(key):
    if isinstance(key, Stagg5GridCase):
        return key
    with STATE.lock:
        if key in STATE.custom_cases:
            return STATE.custom_cases[key]
    if str(key).startswith("custom_"):
        raise ValueError("This imported network is no longer available. Import the files again after restarting the dashboard.")
    return get_stagg5_grid_case(key)


def import_network_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    from acdcpf_opf.data.case_parser import read_case_text
    from acdcpf_opf.data.custom_network import ImportOptions, convert_custom_network

    options = ImportOptions(format=payload.get("format") or None, loss_units=payload.get("loss_units") or None, dc_voltage_convention=payload.get("dc_voltage_convention", "legacy"))
    ac_text = payload.get("ac_text")
    if not isinstance(ac_text, str) or not ac_text.strip():
        raise ValueError("Select an AC Python case file.")
    ac_name = Path(str(payload.get("ac_name") or "ac_case.py")).name
    dc_text = payload.get("dc_text")
    dc_name = Path(str(payload.get("dc_name") or "dc_case.py")).name if dc_text is not None else None
    ac = read_case_text(ac_text, filename=ac_name)
    if dc_text is not None and not isinstance(dc_text, str):
        raise ValueError("DC case content must be text.")
    dc = read_case_text(dc_text, filename=dc_name) if dc_text is not None else None
    imported = convert_custom_network(ac, dc, options=options, source_names={"ac": ac_name, "dc": dc_name})
    key = "custom_" + uuid.uuid4().hex
    case = custom_grid_case(imported, key=key)
    with STATE.lock:
        STATE.custom_cases[key] = case
    return {"import_id": key, "report": imported.report}


def create_app(*, serve_frontend: bool = True, cors_origins: list[str] | None = None) -> Any:
    """Create the local FastAPI dashboard application."""

    if FastAPI is None or StaticFiles is None:
        raise RuntimeError(
            "The Tool1 dashboard requires FastAPI and Uvicorn. "
            'Install them with `pip install -e ".[dashboard]"`.'
        )

    app = FastAPI(title="Tool1 (acdcopf) API", version="0.3.0",
        description=DEVELOPMENT_STATUS + ". " + DEVELOPMENT_NOTICE + " Single active run per process. Imports expire on restart. PF convergence is not a limit-feasibility certificate.")
    origins = cors_origins if cors_origins is not None else [x for x in os.environ.get("TOOL1_CORS_ORIGINS", "").split(",") if x]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        message = brand_text(str(exc.detail))
        return JSONResponse(status_code=exc.status_code, content={"detail": message, "error": {"code": f"http_{exc.status_code}", "message": message}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [{"location": list(item["loc"]), "message": item["msg"]} for item in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": "Invalid request fields.", "error": {"code": "validation_error", "message": "Invalid request fields.", "fields": details}})

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logging.getLogger(__name__).exception("Tool1 API request failed")
        return JSONResponse(status_code=500, content={"detail": "Tool1 backend error; inspect the server log.", "error": {"code": "internal_error", "message": "Tool1 backend error; inspect the server log."}})

    @app.get("/api/health")
    def health():
        from acdcpf_opf.runtime_paths import ipopt_executable_path
        return {"status": "ok", "tool": "Tool1 (acdcopf)", "version": "0.3.0", "development_status": DEVELOPMENT_STATUS, "power_flow": {"tool": "Tool5 (acdcpf)", **__import__("acdcpf").capabilities()}, "ipopt_available": ipopt_executable_path().is_file(), "session_id": SESSION_ID, "max_active_runs": 1}

    if serve_frontend:
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        @app.get("/", response_class=HTMLResponse)
        def index():
            return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8").replace('href="styles.css"', 'href="static/styles.css"').replace('src="config.js"', 'src="static/config.js"').replace('src="dashboard.js', 'src="static/dashboard.js'), headers={"Cache-Control": "no-store"})

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return dashboard_config_payload()

    @app.post("/api/networks/import")
    def import_network(payload: ImportRequest) -> dict[str, Any]:
        try:
            return import_network_payload(payload.model_dump(exclude_none=True))
        except (ValueError, TypeError, KeyError, NotImplementedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/network-inputs/{grid_case}/{scenario}")
    def network_inputs(grid_case: str, scenario: str) -> dict[str, Any]:
        try:
            return network_inputs_payload(grid_case, scenario)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runs")
    def submit_run(payload: RunRequest) -> dict[str, Any]:
        with SUBMISSION_LOCK:
            _collect_finished_run()
            if STATE.run_manager.status().state in {"queued", "running"}:
                raise HTTPException(status_code=409, detail="A Tool1 run is already active.")
            try:
                request = benchmark_request_from_payload(payload.model_dump(exclude_none=True))
                # Validate pending edits before submitting an expensive solve.
                network_limit_inventory(request.grid_case, request.vsc_setpoint_scenario, request.table_overrides)
                if not request.skip_opf:
                    from acdcpf_opf.runtime_paths import ipopt_executable_path
                    if not ipopt_executable_path().is_file():
                        raise HTTPException(status_code=503, detail="IPOPT is unavailable. Run tool1-doctor or configure TOOL1_IPOPT.")
            except HTTPException:
                raise
            except (ValueError, TypeError, KeyError, NotImplementedError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            summary = request_summary_rows(request)
            with STATE.lock:
                STATE.last_result = None
                STATE.last_error = None
                STATE.last_submitted_request = request
                STATE.last_submitted_summary = summary
            STATE.run_manager.submit(_run_request_and_capture, request)
            return {"state": "submitted", "summary": summary}

    @app.post("/api/network-limits/{grid_case}/{scenario}")
    def network_limits(grid_case: str, scenario: str, payload: LimitsRequest) -> dict[str, Any]:
        try:
            payload = payload.model_dump()
            rows = network_limit_inventory(resolve_dashboard_case(grid_case), scenario, payload.get("table_overrides", {}))
            return {"rows": rows}
        except (ValueError, TypeError, KeyError, NotImplementedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs/current")
    def run_status() -> dict[str, Any]:
        return status_payload()

    @app.get("/api/runs/current/grid-html", response_class=HTMLResponse)
    def current_grid_html() -> str:
        _collect_finished_run()
        result = STATE.last_result
        if result is None or result.html_path is None or not result.html_path.exists():
            raise HTTPException(status_code=404, detail="No grid HTML is available yet.")
        return result.html_path.read_text(encoding="utf-8")

    @app.get("/api/runs/current/exports/{filename}")
    def download(filename: str):
        _collect_finished_run()
        result = STATE.last_result
        if result is None:
            raise HTTPException(status_code=404, detail="No result is available.")
        paths = [result.markdown_path, result.html_path, *result.csv_paths, *result.xlsx_paths]
        path = next((path for path in paths if path and path.name == filename and path.is_file()), None)
        if path is None:
            raise HTTPException(status_code=404, detail="Export not found in the current result.")
        return FileResponse(path, filename=path.name)

    return app


def dashboard_config_payload() -> dict[str, Any]:
    """Return static choices needed by the browser dashboard."""

    cases = []
    with STATE.lock:
        available = {**benchmark.STAGG5_GRID_CASES, **STATE.custom_cases}
    for key, case in available.items():
        default_controls = default_control_selection(case)
        cases.append(
            {
                "key": key,
                "display_name": case.display_name,
                "description": case.description,
                "source": case.source,
                "supports_pyflow": case.supports_pyflow,
                "default_controls": _control_selection_to_dict(default_controls),
                "profiles": _profile_options_for_case(case),
                "custom": case.network_factory is not None,
                "default_pf_only": bool(case.import_metadata and case.import_metadata["counts"]["dc_bus"] == 0),
                "import_report": case.import_metadata,
            }
        )

    return {
        "cases": cases,
        "vsc_scenarios": [
            {
                "key": key,
                "description": benchmark.VSC_SETPOINT_SCENARIO_DESCRIPTIONS[key],
            }
            for key in benchmark.VSC_SETPOINT_SCENARIOS
        ],
        "control_tokens": [
            {"token": token, "label": benchmark.OPF_CONTROL_DISPLAY_NAMES[token]}
            for token in benchmark.OPF_CONTROL_TOKENS
        ],
        "control_scopes": [
            {
                "key": benchmark.CONTROL_DEVICE_SCOPE_BENCHMARK,
                "label": "Case-defined controllable devices",
            },
            {"key": benchmark.CONTROL_DEVICE_SCOPE_ALL, "label": "All eligible devices"},
        ],
        "session_id": SESSION_ID,
        "defaults": {
            "grid_case": STAGG5_ORIGINAL,
            "vsc_scenario": benchmark.VSC_SETPOINT_SCENARIO_ORIGINAL,
            "control_scope": benchmark.CONTROL_DEVICE_SCOPE_BENCHMARK,
            "include_pyflow_reference": False,
            "write_exports": True,
            "include_markdown": True,
            "include_csv": True,
            "include_xlsx": True,
            "include_html": True,
        },
    }


def network_inputs_payload(grid_case: str, scenario: str) -> dict[str, Any]:
    """Return editable operational network tables for a selected case."""

    selected_case = resolve_dashboard_case(grid_case)
    if selected_case.source != "acdcpf":
        return {
            "editable": False,
            "message": "Operational table editing is available only for native ACDCPF cases.",
            "tables": {},
        }
    tables = editable_network_tables(selected_case, scenario)
    return {
        "editable": True,
        "message": "",
        "tables": {
            table_name: {
                "label": EDITABLE_TABLE_SPECS[table_name].label,
                "read_only_columns": list(EDITABLE_TABLE_SPECS[table_name].read_only_columns) + [column for column in frame.columns if column.startswith("source_")],
                "editable_columns": list(EDITABLE_TABLE_SPECS[table_name].editable_columns),
                "rows": _frame_records(frame),
            }
            for table_name, frame in tables.items()
        },
    }


def benchmark_request_from_payload(payload: Mapping[str, Any]) -> BenchmarkRequest:
    """Convert a browser JSON payload to the service-layer request object."""

    selected_case = resolve_dashboard_case(payload.get("grid_case", STAGG5_ORIGINAL))
    if selected_case.network_factory and payload.get("vsc_setpoint_scenario", "original") != "original":
        raise ValueError("Custom networks use imported setpoints; Stagg perturbations are unavailable.")
    if payload.get("pf_policy") == "converter_limited":
        if not payload.get("skip_opf", False):
            raise ValueError("Converter-limited PF requires PF only; OPF initialization preserves requested controls.")
        if selected_case.source != "acdcpf":
            raise ValueError("Converter-limited PF requires a native Tool5 network.")
    controls = _opf_controls_from_payload(payload.get("opf_controls"), selected_case)
    profile_key = str(payload.get("profile_key") or "none")
    profile_path = _profile_path_from_payload(selected_case, profile_key, payload)
    table_overrides = _table_overrides_from_payload(payload.get("table_overrides", {}))
    return BenchmarkRequest(
        grid_case=selected_case,
        vsc_setpoint_scenario=str(
            payload.get("vsc_setpoint_scenario")
            or benchmark.VSC_SETPOINT_SCENARIO_ORIGINAL
        ),
        opf_controls=controls,
        control_device_scope=str(
            payload.get("control_device_scope") or benchmark.CONTROL_DEVICE_SCOPE_BENCHMARK
        ),
        control_margin_percent=_optional_float(payload.get("control_margin_percent")),
        profile_input=profile_path,
        table_overrides=table_overrides,
        pf_policy=payload.get("pf_policy", "unconstrained"),
        skip_opf=bool(payload.get("skip_opf", bool(selected_case.import_metadata and selected_case.import_metadata["counts"]["dc_bus"] == 0))),
        include_pyflow_reference=bool(payload.get("include_pyflow_reference", True)) and selected_case.supports_pyflow,
        write_exports=bool(payload.get("write_exports", True)),
        include_markdown=bool(payload.get("include_markdown", True)),
        include_csv=bool(payload.get("include_csv", True)),
        include_xlsx=bool(payload.get("include_xlsx", True)),
        include_html=bool(payload.get("include_html", True)),
        output_directory=DASHBOARD_RUN_ROOT,
    )


def request_summary_rows(request: BenchmarkRequest) -> list[dict[str, str]]:
    """Return a compact summary of the submitted browser request."""

    selected_case = get_stagg5_grid_case(request.grid_case)
    controls = (
        request.opf_controls
        if isinstance(request.opf_controls, benchmark.OPFControlSelection)
        else benchmark._parse_opf_controls(request.opf_controls, selected_case)
    )
    edited_rows = sum(
        len(frame)
        for frame in request.table_overrides.values()
        if isinstance(frame, pd.DataFrame)
    )
    edited_tables = ", ".join(sorted(request.table_overrides)) if request.table_overrides else "none"
    profile_label = "Single steady-state snapshot"
    if request.profile_input is not None:
        profile_label = str(request.profile_input.name)

    return [
        {"setting": "Grid case", "value": selected_case.display_name},
        {
            "setting": "Run type",
            "value": "Tool5 (acdcpf) PF only" if request.skip_opf else "Tool5 (acdcpf) PF + Tool1 (acdcopf) OPF",
        },
        {
            "setting": "VSC starting setpoints",
            "value": str(request.vsc_setpoint_scenario),
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
        {"setting": "Time profile", "value": profile_label},
        {"setting": "Operational edits", "value": f"{edited_rows} row(s) across {edited_tables}"},
        {
            "setting": "PyFlow reference",
            "value": "Enabled when comparable" if request.include_pyflow_reference else "Disabled",
        },
        {"setting": "Exports", "value": _selected_export_formats(request)},
    ]


def status_payload() -> dict[str, Any]:
    """Return current background-run status and latest available result."""

    _collect_finished_run()
    status = STATE.run_manager.status()
    result_failed = status.state == "succeeded" and STATE.last_result is not None and not STATE.last_result.success
    return {
        "state": "failed" if result_failed else status.state,
        "message": "Calculation completed with failed PF/OPF timestamps. Inspect solver diagnostics." if result_failed else status.message,
        "last_error": STATE.last_error,
        "last_submitted_summary": STATE.last_submitted_summary,
        "result": result_payload(STATE.last_result) if STATE.last_result is not None else None,
    }


def result_payload(result: BenchmarkResultBundle) -> dict[str, Any]:
    """Serialize the latest benchmark result for the browser dashboard."""

    runs = _representative_runs(result)
    pf = _run_by_label(runs, "ACDCPF PF")
    opf = _run_by_label(runs, benchmark.TOOL1_OBJECTIVE_LABEL)
    return {
        "generated_at": result.generated_at,
        "is_profiled": result.is_profiled,
        "snapshot_count": len(result.time_points) if result.is_profiled else 1,
        "output_directory": str(result.output_directory) if result.output_directory else None,
        "losses": {
            "pf_mw": _total_loss(pf),
            "opf_mw": _total_loss(opf),
            "delta_mw": _loss_delta(opf, pf),
        },
        "runs": [
            {"label": run.label, "display_label": brand_text(run.label), "success": run.success, "message": brand_text(run.message),
             "interpretation": ("Converged; equipment limits are not fully checked by PF." if run.label == "ACDCPF PF" and run.success else "See solver diagnostics and physics checks."),
             "physics": run.diagnostics.get("physics_validation", {}).get("status", "not_checked"),
             "tool5": run.diagnostics.get("tool5", {})}
            for run in runs
        ],
        "time_series": _time_series_payload(result),
        "tables": _result_tables(runs),
        "exports": {
            "markdown": str(result.markdown_path) if result.markdown_path else None,
            "csv": [str(path) for path in result.csv_paths],
            "xlsx": [str(path) for path in result.xlsx_paths],
            "html": str(result.html_path) if result.html_path else None,
        },
        "downloads": [{"name": path.name, "url": "/api/runs/current/exports/" + path.name} for path in [result.markdown_path, result.html_path, *result.csv_paths, *result.xlsx_paths] if path and path.is_file()],
        "grid_html_available": bool(result.html_path and result.html_path.exists()),
    }


def _run_request_and_capture(request: BenchmarkRequest) -> BenchmarkResultBundle:
    result = run_benchmark_request(request)
    with STATE.lock:
        STATE.last_result = result
        STATE.last_error = None
    return result


def _collect_finished_run() -> None:
    status = STATE.run_manager.status()
    if status.state == "failed":
        with STATE.lock:
            STATE.last_error = status.message
        return
    if status.state != "succeeded":
        return
    result = STATE.run_manager.result()
    if result is not None:
        with STATE.lock:
            STATE.last_result = result
            STATE.last_error = None


def _opf_controls_from_payload(
    raw_controls: Any,
    selected_case: Stagg5GridCase,
) -> benchmark.OPFControlSelection:
    if raw_controls is None:
        return default_control_selection(selected_case)
    if isinstance(raw_controls, Mapping):
        unknown = set(raw_controls) - set(benchmark.OPF_CONTROL_TOKENS)
        if unknown:
            raise ValueError("Unknown OPF controls: " + ", ".join(sorted(unknown)))
        values = {
            token: bool(raw_controls.get(token, False))
            for token in benchmark.OPF_CONTROL_TOKENS
        }
        return benchmark.OPFControlSelection(**values)
    if isinstance(raw_controls, (list, tuple, set)):
        return benchmark._parse_opf_controls(",".join(str(token) for token in raw_controls), selected_case)
    return benchmark._parse_opf_controls(str(raw_controls), selected_case)


def _profile_options_for_case(case: Stagg5GridCase) -> list[dict[str, str]]:
    options = [{"key": "none", "label": "No profile", "path": ""}]
    if case.key == STAGG5_ORIGINAL:
        options.extend(
            [
                {
                    "key": "original_3_step",
                    "label": "Original Stagg5 3-step AC load profile",
                    "path": str(benchmark.ORIGINAL_STAGG5_LOAD_PROFILE),
                },
                {
                    "key": "original_one_day",
                    "label": "Original Stagg5 24-hour AC load profile",
                    "path": str(benchmark.STAGG5_ONE_DAY_AC_LOAD_PROFILE),
                },
            ]
        )
    if case.key == STAGG5_HYBRID_DCDC:
        options.extend(
            [
                {
                    "key": "hybrid_3_step",
                    "label": "Hybrid DCDC 3-step load/PV profile",
                    "path": str(benchmark.HYBRID_DCDC_LOAD_PV_PROFILE),
                },
                {
                    "key": "hybrid_one_day",
                    "label": "Hybrid DCDC 24-hour load/PV profile",
                    "path": str(benchmark.HYBRID_DCDC_ONE_DAY_LOAD_PV_PROFILE),
                },
            ]
        )
    options.append({"key": "custom_path", "label": "Custom CSV/XLSX path", "path": ""})
    return options


def _profile_path_from_payload(
    selected_case: Stagg5GridCase,
    profile_key: str,
    payload: Mapping[str, Any],
) -> Path | None:
    if profile_key == "none":
        return None
    if profile_key == "custom_path":
        raw_path = str(payload.get("profile_path") or "").strip().strip('"')
        if not raw_path:
            raise ValueError("Custom profile path is empty.")
        return Path(raw_path)
    for option in _profile_options_for_case(selected_case):
        if option["key"] == profile_key and option["path"]:
            return Path(option["path"])
    raise ValueError(f"Profile `{profile_key}` is not available for {selected_case.display_name}.")


def _table_overrides_from_payload(raw_overrides: Any) -> dict[str, pd.DataFrame]:
    if not raw_overrides:
        return {}
    if not isinstance(raw_overrides, Mapping):
        raise ValueError("table_overrides must be an object keyed by table name.")
    overrides: dict[str, pd.DataFrame] = {}
    for table_name, rows in raw_overrides.items():
        if table_name not in EDITABLE_TABLE_SPECS:
            raise ValueError(f"`{table_name}` is not an editable table.")
        frame = pd.DataFrame(list(rows or []))
        if not frame.empty:
            overrides[str(table_name)] = frame
    return overrides


def _frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [_json_safe(record) for record in frame.to_dict(orient="records")]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value


def _control_selection_to_dict(selection: benchmark.OPFControlSelection) -> dict[str, bool]:
    return {token: bool(getattr(selection, token)) for token in benchmark.OPF_CONTROL_TOKENS}


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


def _representative_runs(result: BenchmarkResultBundle) -> tuple[benchmark.BenchmarkRun, ...]:
    if not result.is_profiled:
        return _visible_dashboard_runs(result.runs)
    if not result.time_points:
        return ()
    return _visible_dashboard_runs(tuple(result.time_points[0].runs))


def _visible_dashboard_runs(
    runs: tuple[benchmark.BenchmarkRun, ...],
) -> tuple[benchmark.BenchmarkRun, ...]:
    """Hide internal validation re-solves from user-facing dashboard tables."""

    return tuple(run for run in runs if run.label != benchmark.TOOL1_VALIDATED_LABEL)


def _result_tables(runs: tuple[benchmark.BenchmarkRun, ...]) -> dict[str, list[dict[str, Any]]]:
    table_names = (
        "ac_buses",
        "dc_buses",
        "generators",
        "dc_generators",
        "storage_units",
        "converters",
        "dcdc_converters",
        "transformers",
        "ac_branches",
        "dc_branches",
    )
    tables: dict[str, list[dict[str, Any]]] = {}
    for table_name in table_names:
        rows = []
        for run in runs:
            for row in run.tables.get(table_name, []):
                rows.append({"run": run.label, **_json_safe(row)})
        if rows:
            tables[table_name] = rows
    physics_rows = [
        {"run": run.label, **check}
        for run in runs
        for check in run.diagnostics.get("physics_validation", {}).get("checks", [])
    ]
    if physics_rows:
        tables["physics_checks"] = physics_rows
    limit_rows = [{"run": run.label, **row} for run in runs
                  for row in run.diagnostics.get("equipment_limits", [])]
    if limit_rows:
        tables["equipment_limits"] = limit_rows
    return tables


def _time_series_payload(result: BenchmarkResultBundle) -> dict[str, Any]:
    """Return plot-ready time-series data for the browser dashboard."""

    points = list(_iter_time_series_points(result))
    return {
        "available": bool(points),
        "timeline": [
            {"time_index": time_index, "timestamp": timestamp}
            for time_index, timestamp, _ in points
        ],
        "losses": _time_series_loss_rows(points),
        "ac_bus_voltages": _time_series_table_rows(
            points,
            "ac_buses",
            ("v_pu", "angle_deg"),
        ),
        "dc_bus_voltages": _time_series_table_rows(
            points,
            "dc_buses",
            ("v_pu", "v_kv"),
        ),
        "storage_units": _time_series_table_rows(
            points,
            "storage_units",
            (
                "p_mw",
                "p_charge_mw",
                "p_discharge_mw",
                "q_mvar",
                "soc_percent",
                "energy_mwh",
                "loading_percent",
            ),
        ),
    }


def _iter_time_series_points(
    result: BenchmarkResultBundle,
) -> tuple[tuple[int, str, tuple[benchmark.BenchmarkRun, ...]], ...]:
    if not result.is_profiled:
        return ((0, result.generated_at, _visible_dashboard_runs(result.runs)),)
    return tuple(
        (
            point.time_index,
            point.timestamp,
            _visible_dashboard_runs(tuple(point.runs)),
        )
        for point in result.time_points
    )


def _time_series_loss_rows(
    points: list[tuple[int, str, tuple[benchmark.BenchmarkRun, ...]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for time_index, timestamp, runs in points:
        pf = _run_by_label(runs, "ACDCPF PF")
        opf = _run_by_label(runs, benchmark.TOOL1_OBJECTIVE_LABEL)
        rows.append(
            {
                "time_index": time_index,
                "timestamp": timestamp,
                "pf_loss_mw": _total_loss(pf),
                "opf_loss_mw": _total_loss(opf),
                "delta_mw": _loss_delta(opf, pf),
            }
        )
    return rows


def _time_series_table_rows(
    points: list[tuple[int, str, tuple[benchmark.BenchmarkRun, ...]]],
    table_name: str,
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for time_index, timestamp, runs in points:
        for run in _curve_run_subset(runs):
            for table_row in run.tables.get(table_name, []):
                row = {
                    "time_index": time_index,
                    "timestamp": timestamp,
                    "run": run.label,
                    "id": table_row.get("id"),
                    "name": table_row.get("name"),
                    "bus": table_row.get("bus", table_row.get("id")),
                }
                for field_name in fields:
                    row[field_name] = table_row.get(field_name)
                rows.append(_json_safe(row))
    return rows


def _curve_run_subset(
    runs: tuple[benchmark.BenchmarkRun, ...],
) -> tuple[benchmark.BenchmarkRun, ...]:
    """Keep dashboard curves focused on the native PF/OPF path."""

    labels = {"ACDCPF PF", benchmark.TOOL1_OBJECTIVE_LABEL}
    return tuple(run for run in runs if run.label in labels)


def _run_by_label(
    runs: tuple[benchmark.BenchmarkRun, ...],
    label: str,
) -> benchmark.BenchmarkRun | None:
    for run in runs:
        if run.label == label:
            return run
    return None


def _total_loss(run: benchmark.BenchmarkRun | None) -> float | None:
    if run is None or not run.success:
        return None
    value = run.losses_mw.get("total_active_losses_mw")
    return None if value is None else float(value)


def _loss_delta(
    left: benchmark.BenchmarkRun | None,
    right: benchmark.BenchmarkRun | None,
) -> float | None:
    left_value = _total_loss(left)
    right_value = _total_loss(right)
    if left_value is None or right_value is None:
        return None
    return left_value - right_value


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


app = create_app() if FastAPI is not None else None
