from __future__ import annotations

from pathlib import Path
import threading
import unittest

import pandas as pd

from acdcpf_opf.benchmarks.stagg5 import benchmark_pf_opf_comparison as benchmark
from acdcpf_opf.benchmarks.stagg5.case_variants import (
    STAGG5_HYBRID_DCDC,
    create_acdcpf_network_for_stagg5_case,
)
from acdcpf_opf.benchmarks.stagg5.service import (
    BenchmarkRequest,
    BenchmarkResultBundle,
    apply_table_overrides,
    changed_table_overrides,
    editable_network_tables,
    opf_controls_from_tokens,
    run_benchmark_request,
)
from acdcpf_opf.dashboard.runtime import DashboardRunManager
from acdcpf_opf.data.time_profiles import load_time_profile
from acdcpf_opf import start_dashboard


class DashboardServiceTests(unittest.TestCase):
    def test_opf_controls_from_tokens_maps_dashboard_checkboxes(self) -> None:
        controls = opf_controls_from_tokens(("vsc_p", "vsc_q", "dcdc_ratio"))

        self.assertTrue(controls.vsc_p)
        self.assertTrue(controls.vsc_q)
        self.assertTrue(controls.dcdc_ratio)
        self.assertFalse(controls.ac_gen_p)

    def test_editable_network_tables_show_native_storage(self) -> None:
        tables = editable_network_tables(STAGG5_HYBRID_DCDC)

        self.assertIn("storage", tables)
        self.assertIn("dc_gen", tables)
        self.assertNotIn("dc_load", tables)
        self.assertEqual(len(tables["dc_gen"]), 1)
        self.assertEqual(len(tables["storage"]), 1)
        self.assertIn("energy_mwh", tables["storage"].columns)
        self.assertIn("soc_percent", tables["storage"].columns)

    def test_apply_table_overrides_updates_operational_values_only(self) -> None:
        net = create_acdcpf_network_for_stagg5_case(None)

        apply_table_overrides(
            net,
            {
                "ac_load": pd.DataFrame(
                    [{"element_id": 0, "bus": 999, "p_mw": 33.0, "q_mvar": 7.0}]
                )
            },
        )

        self.assertEqual(int(net.ac_load.at[0, "bus"]), 1)
        self.assertAlmostEqual(float(net.ac_load.at[0, "p_mw"]), 33.0)
        self.assertAlmostEqual(float(net.ac_load.at[0, "q_mvar"]), 7.0)

    def test_apply_table_overrides_updates_native_storage_values(self) -> None:
        net = create_acdcpf_network_for_stagg5_case(STAGG5_HYBRID_DCDC)

        apply_table_overrides(
            net,
            {
                "storage": pd.DataFrame(
                    [{"element_id": 0, "p_mw": 7.0, "soc_percent": 55.0}]
                )
            },
        )

        self.assertAlmostEqual(float(net.storage.at[0, "p_mw"]), 7.0)
        self.assertAlmostEqual(float(net.storage.at[0, "soc_percent"]), 55.0)

    def test_changed_table_overrides_returns_only_modified_rows(self) -> None:
        base_tables = editable_network_tables(None)
        edited_tables = {name: frame.copy() for name, frame in base_tables.items()}
        self.assertEqual(changed_table_overrides(base_tables, edited_tables), {})

        edited_tables["ac_load"].loc[0, "p_mw"] = 45.0
        changed = changed_table_overrides(base_tables, edited_tables)

        self.assertEqual(set(changed), {"ac_load"})
        self.assertEqual(changed["ac_load"].iloc[0]["element_id"], 0)

    def test_original_stagg5_service_run_matches_terminal_benchmark(self) -> None:
        if not benchmark._ipopt_executable_path().exists():
            self.skipTest("IPOPT executable is not available in the local solver bundle.")

        result = run_benchmark_request(
            BenchmarkRequest(
                include_pyflow_reference=False,
                write_exports=False,
            )
        )

        pf_run = next(run for run in result.runs if run.label == "ACDCPF PF")
        opf_run = next(run for run in result.runs if run.label == benchmark.TOOL1_OBJECTIVE_LABEL)
        self.assertTrue(pf_run.success)
        self.assertTrue(opf_run.success)
        self.assertAlmostEqual(
            pf_run.losses_mw["total_active_losses_mw"],
            8.626045,
            delta=0.05,
        )
        self.assertAlmostEqual(
            opf_run.losses_mw["total_active_losses_mw"],
            8.624743,
            delta=0.05,
        )

    def test_profiled_service_run_writes_time_points_without_opf(self) -> None:
        snapshots = load_time_profile(benchmark.ORIGINAL_STAGG5_LOAD_PROFILE)

        result = run_benchmark_request(
            BenchmarkRequest(
                profile_snapshots=snapshots,
                skip_opf=True,
                include_pyflow_reference=False,
                write_exports=False,
            )
        )

        self.assertTrue(result.is_profiled)
        self.assertEqual(len(result.time_points), 3)
        self.assertTrue(all(point.runs[0].success for point in result.time_points))


class DashboardRuntimeTests(unittest.TestCase):
    def test_background_run_manager_reports_success(self) -> None:
        manager: DashboardRunManager[int] = DashboardRunManager()
        try:
            future = manager.submit(lambda: 42)
            self.assertEqual(future.result(timeout=5), 42)
            self.assertEqual(manager.status().state, "succeeded")
            self.assertEqual(manager.result(), 42)
        finally:
            manager.shutdown()

    def test_background_run_manager_rejects_parallel_submission(self) -> None:
        manager: DashboardRunManager[int] = DashboardRunManager()
        gate = threading.Event()

        def hold() -> int:
            gate.wait(timeout=5)
            return 1

        try:
            manager.submit(hold)
            with self.assertRaisesRegex(RuntimeError, "already in progress"):
                manager.submit(lambda: 2)
            gate.set()
        finally:
            manager.shutdown()

    def test_legacy_streamlit_dashboard_app_imports_without_dependency(self) -> None:
        import acdcpf_opf.dashboard.app as app

        self.assertTrue(callable(app.main))

    def test_custom_dashboard_config_exposes_cases_controls_and_profiles(self) -> None:
        from acdcpf_opf.dashboard import web

        payload = web.dashboard_config_payload()
        cases = {case["key"]: case for case in payload["cases"]}

        self.assertIn(STAGG5_HYBRID_DCDC, cases)
        self.assertIn("control_tokens", payload)
        self.assertIn("vsc_p", cases[STAGG5_HYBRID_DCDC]["default_controls"])
        profile_keys = {profile["key"] for profile in cases[STAGG5_HYBRID_DCDC]["profiles"]}
        self.assertIn("hybrid_one_day", profile_keys)

    def test_custom_dashboard_payload_builds_benchmark_request(self) -> None:
        from acdcpf_opf.dashboard import web

        request = web.benchmark_request_from_payload(
            {
                "grid_case": STAGG5_HYBRID_DCDC,
                "vsc_setpoint_scenario": benchmark.VSC_SETPOINT_SCENARIO_ORIGINAL,
                "profile_key": "hybrid_one_day",
                "opf_controls": {"vsc_p": True, "vsc_q": True, "dcdc_ratio": True},
                "control_device_scope": benchmark.CONTROL_DEVICE_SCOPE_ALL,
                "control_margin_percent": 20,
                "table_overrides": {
                    "vsc": [{"element_id": 0, "p_mw": 55.0, "q_mvar": 20.0}]
                },
                "include_pyflow_reference": False,
            }
        )

        self.assertEqual(request.grid_case.key, STAGG5_HYBRID_DCDC)
        self.assertEqual(request.profile_input, benchmark.HYBRID_DCDC_ONE_DAY_LOAD_PV_PROFILE)
        self.assertTrue(request.opf_controls.vsc_p)
        self.assertTrue(request.opf_controls.dcdc_ratio)
        self.assertEqual(request.control_device_scope, benchmark.CONTROL_DEVICE_SCOPE_ALL)
        self.assertEqual(request.control_margin_percent, 20.0)
        self.assertIn("vsc", request.table_overrides)
        self.assertFalse(request.include_pyflow_reference)

    def test_custom_dashboard_hides_internal_validation_run(self) -> None:
        from acdcpf_opf.dashboard import web

        runs = (
            benchmark.BenchmarkRun(label="ACDCPF PF", success=True),
            benchmark.BenchmarkRun(label=benchmark.TOOL1_OBJECTIVE_LABEL, success=True),
            benchmark.BenchmarkRun(label=benchmark.TOOL1_VALIDATED_LABEL, success=True),
        )

        visible = web._visible_dashboard_runs(runs)

        self.assertEqual([run.label for run in visible], ["ACDCPF PF", benchmark.TOOL1_OBJECTIVE_LABEL])

    def test_custom_dashboard_result_payload_exposes_profile_curves(self) -> None:
        from acdcpf_opf.dashboard import web

        result = BenchmarkResultBundle(
            request=BenchmarkRequest(),
            generated_at="2026-08-28T12:00:00",
            output_directory=None,
            time_points=(
                benchmark.BenchmarkTimePoint(
                    time_index=0,
                    timestamp="2026-01-01T00:00:00",
                    runs=[
                        benchmark.BenchmarkRun(
                            label="ACDCPF PF",
                            success=True,
                            losses_mw={"total_active_losses_mw": 1.0},
                            tables={
                                "ac_buses": [
                                    {"id": 0, "name": "Bus 1", "v_pu": 1.0, "angle_deg": 0.0},
                                    {"id": 1, "name": "Bus 2", "v_pu": 0.99, "angle_deg": -1.0},
                                ],
                                "dc_buses": [
                                    {"id": 0, "name": "DC 1", "v_pu": 1.01, "v_kv": 348.45},
                                ],
                                "storage_units": [
                                    {
                                        "id": 0,
                                        "name": "Battery",
                                        "bus": 0,
                                        "p_mw": -2.0,
                                        "p_charge_mw": 2.0,
                                        "p_discharge_mw": 0.0,
                                        "q_mvar": 0.5,
                                        "soc_percent": 50.0,
                                    }
                                ],
                            },
                        ),
                        benchmark.BenchmarkRun(
                            label=benchmark.TOOL1_OBJECTIVE_LABEL,
                            success=True,
                            losses_mw={"total_active_losses_mw": 0.9},
                            tables={
                                "ac_buses": [
                                    {"id": 0, "name": "Bus 1", "v_pu": 1.0, "angle_deg": 0.0},
                                    {"id": 1, "name": "Bus 2", "v_pu": 0.995, "angle_deg": -0.8},
                                ],
                                "dc_buses": [
                                    {"id": 0, "name": "DC 1", "v_pu": 1.02, "v_kv": 351.9},
                                ],
                                "storage_units": [
                                    {
                                        "id": 0,
                                        "name": "Battery",
                                        "bus": 0,
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
            ),
        )

        payload = web.result_payload(result)
        time_series = payload["time_series"]

        self.assertTrue(time_series["available"])
        self.assertEqual(time_series["timeline"][0]["time_index"], 0)
        self.assertEqual(time_series["losses"][0]["pf_loss_mw"], 1.0)
        self.assertEqual(time_series["losses"][0]["opf_loss_mw"], 0.9)
        self.assertEqual(len(time_series["ac_bus_voltages"]), 4)
        self.assertIn(
            {"time_index": 0, "timestamp": "2026-01-01T00:00:00", "run": "ACDCPF PF", "id": 1, "name": "Bus 2", "bus": 1, "v_pu": 0.99, "angle_deg": -1.0},
            time_series["ac_bus_voltages"],
        )
        self.assertEqual(time_series["dc_bus_voltages"][1]["v_kv"], 351.9)
        self.assertEqual(time_series["storage_units"][0]["q_mvar"], 0.5)
        self.assertEqual(time_series["storage_units"][1]["q_mvar"], -0.2)
        self.assertEqual(time_series["storage_units"][0]["p_charge_mw"], 2.0)
        self.assertEqual(time_series["storage_units"][1]["p_discharge_mw"], 1.0)

    def test_dashboard_run_configuration_summary_describes_next_request(self) -> None:
        import acdcpf_opf.dashboard.app as app

        request = BenchmarkRequest(
            grid_case=STAGG5_HYBRID_DCDC,
            opf_controls=benchmark.OPFControlSelection(
                vsc_p=True,
                vsc_q=True,
                dcdc_ratio=True,
            ),
            control_device_scope=benchmark.CONTROL_DEVICE_SCOPE_ALL,
            control_margin_percent=15.0,
            include_pyflow_reference=False,
            include_csv=True,
            include_xlsx=False,
            include_html=True,
        )

        rows = app._run_configuration_rows(
            request,
            {"vsc": pd.DataFrame([{"element_id": 0, "p_mw": 51.0}])},
            profile_snapshots=(object(), object()),
        )
        summary = {row["setting"]: row["value"] for row in rows}

        self.assertEqual(summary["Grid case"], "Hybrid Stagg5 with DC PV and battery")
        self.assertEqual(summary["Run type"], "ACDCPF PF + TOOL1")
        self.assertIn("VSC active power", summary["Enabled OPF controls"])
        self.assertIn("DCDC voltage ratio", summary["Enabled OPF controls"])
        self.assertEqual(summary["Control device scope"], "All eligible devices")
        self.assertEqual(summary["Control margin"], "+/-15% of rating")
        self.assertEqual(summary["Time profile"], "2 independent profile snapshot(s)")
        self.assertEqual(summary["Operational table edits"], "1 row(s) across vsc")
        self.assertEqual(summary["PyFlow reference"], "Disabled")
        self.assertEqual(summary["Exports"], "Markdown, CSV, HTML")

    def test_start_dashboard_builds_uvicorn_command(self) -> None:
        project_root = Path("D:/project/acdcpf_opf")
        python = Path("D:/venv/Scripts/python.exe")

        command = start_dashboard._uvicorn_command(
            python,
            project_root,
            host="127.0.0.1",
            port=8502,
        )

        self.assertEqual(command[0], str(python))
        self.assertEqual(command[1:4], ["-m", "uvicorn", "acdcpf_opf.dashboard.web:app"])
        self.assertNotIn("--app-dir", command)
        self.assertEqual(command[-4:], ["--host", "127.0.0.1", "--port", "8502"])

    def test_start_dashboard_keeps_current_interpreter(self) -> None:
        import sys

        self.assertEqual(start_dashboard._selected_python(Path.cwd(), None), Path(sys.executable).resolve())


if __name__ == "__main__":
    unittest.main()
