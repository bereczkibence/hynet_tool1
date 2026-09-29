from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Mapping

import pyomo.environ as pyo
from acdcpf_opf.runtime_paths import ipopt_executable_path

from acdcpf_opf.data.acdcpf_to_pyomo import (
    ACDCPFToPyomoOptions,
    convert_acdcpf_network_to_opf_data,
    is_acdcpf_network,
)
from acdcpf_opf.data.pyflow_to_pyomo import (
    PyflowToPyomoOptions,
    convert_pyflow_grid_to_opf_data,
)
from acdcpf_opf.opf.formulations.acdc_opf_pyomo_loss_min import (
    OPFBuildOptions,
    OPFSolveOptions,
    build_acdc_opf_model,
    extract_opf_results,
    extract_multiperiod_opf_results,
    solve_acdc_opf,
    solve_multiperiod_acdc_opf,
    validate_required_data,
)
from acdcpf_opf.powerflow.acdcpf_adapter import ACDCPFAdapter
from acdcpf_opf.powerflow.acdcpf_network_adapter import ACDCPFNetworkAdapter
from acdcpf_opf.powerflow.result import PFResult
from acdcpf_opf.opf.validation import SolutionValidation, compare_pf_replay, validate_solution
from acdcpf_opf.opf.physics_report import PhysicsReport, PhysicsTolerances
from acdcpf_opf.opf.physics_validation import validate_network_physics, validate_time_series_physics

logger = logging.getLogger(__name__)

ConversionOptions = PyflowToPyomoOptions | ACDCPFToPyomoOptions


@dataclass(frozen=True)
class PyomoACDCOPFConfig:
    """Configuration for the explicit Pyomo/IPOPT AC/DC OPF workflow."""

    ipopt_executable: str | None = None
    tee: bool = False
    max_iter: int = 500
    tolerance: float = 1e-6
    print_level: int = 5
    use_acdcpf_initialization: bool = True
    run_final_acdcpf_validation: bool = True
    logging_level: int | str = logging.INFO
    validation_tolerance: float = 1e-6
    physics_tolerances: PhysicsTolerances = PhysicsTolerances()

    @staticmethod
    def default_ipopt_path() -> str:
        return str(ipopt_executable_path())


@dataclass
class PyomoACDCOPFResult:
    """Structured result for the Pyomo/IPOPT AC/DC OPF workflow."""

    success: bool
    message: str
    data: dict[str, Any]
    model: pyo.ConcreteModel | None = None
    solver_result: Any = None
    extracted_results: dict[str, Any] | None = None
    base_pf_result: PFResult | None = None
    validation_pf_result: PFResult | None = None
    exception: Exception | None = None
    validation: SolutionValidation | None = None
    replay_comparison: dict[str, Any] | None = None
    physics_validation: PhysicsReport | None = None

    @property
    def objective_total_active_losses_mw(self) -> float | None:
        if not self.extracted_results:
            return None
        value = self.extracted_results.get("objective_total_active_losses_mw")
        return None if value is None else float(value)

    @property
    def active_loss_breakdown_mw(self) -> dict[str, float]:
        if not self.extracted_results:
            return {}
        breakdown = self.extracted_results.get("active_loss_breakdown_mw", {})
        return {key: float(value) for key, value in breakdown.items()}


def build_pyomo_acdc_loss_min_model(
    grid: Any,
    *,
    config: PyomoACDCOPFConfig | None = None,
    conversion_options: ConversionOptions | None = None,
) -> tuple[pyo.ConcreteModel, dict[str, Any], PFResult | None]:
    """Build the explicit Pyomo AC/DC loss-minimization model.

    Native ``acdcpf.Network`` cases use the ACDCPF package directly for
    initialization. PyFlow grids keep the legacy adapter path for backward
    compatibility with existing benchmark scripts.
    """
    config = config or PyomoACDCOPFConfig()
    logging.getLogger(__package__).setLevel(config.logging_level)

    base_pf = None
    if config.use_acdcpf_initialization:
        base_pf = _power_flow_adapter_for(grid).solve(grid, copy_case=True, write_back=False)
        if not base_pf.converged:
            raise RuntimeError(f"ACDCPF base PF did not converge: {base_pf.message}")

    data = _convert_case_to_opf_data(grid, pf_result=base_pf, options=conversion_options)
    validate_required_data(data)
    model = build_acdc_opf_model(data, OPFBuildOptions())
    return model, data, base_pf


def solve_pyomo_acdc_loss_min_opf(
    grid: Any,
    *,
    config: PyomoACDCOPFConfig | None = None,
    conversion_options: ConversionOptions | None = None,
) -> PyomoACDCOPFResult:
    """Build and solve the explicit AC/DC OPF with Pyomo + IPOPT.

    ACDCPF is used only for initialization and optional final validation. IPOPT
    solves the explicit nonlinear model; ACDCPF is never called inside the
    objective function.
    """
    config = config or PyomoACDCOPFConfig()
    data = {}
    base_pf = None
    try:
        model, data, base_pf = build_pyomo_acdc_loss_min_model(
            grid,
            config=config,
            conversion_options=conversion_options,
        )
        model, solver_result = solve_acdc_opf(
            data,
            model=model,
            build_options=OPFBuildOptions(),
            solve_options=OPFSolveOptions(
                ipopt_executable=config.ipopt_executable or config.default_ipopt_path(),
                tee=config.tee,
                max_iter=config.max_iter,
                tol=config.tolerance,
                print_level=config.print_level,
            ),
        )
        extracted = extract_opf_results(model, base_mva=data["base_mva"])
        validation = validate_solution(model, tolerance=config.validation_tolerance)
        success = _solver_succeeded(solver_result) and validation.valid
        message = str(solver_result.solver.termination_condition)
        if not validation.valid:
            message += "; post-solve validation failed: " + "; ".join((validation.nonfinite + validation.violations)[:3])
        physics = None
        if is_acdcpf_network(grid):
            native_options = conversion_options or ACDCPFToPyomoOptions()
            physics = validate_network_physics(
                grid, extracted, options=native_options,
                baseline=base_pf.raw_result if base_pf else None,
                duration_hours=native_options.snapshot_duration_hours,
                tolerances=config.physics_tolerances,
            )
            if not physics.valid:
                success = False
                message += "; source-network physics failed: " + physics.failure_message()
        validation_pf = None
        replay_comparison = None
        if success and config.run_final_acdcpf_validation:
            validation_grid = _copy_case(grid)
            apply_pyomo_results_to_case(validation_grid, extracted)
            validation_pf = _power_flow_adapter_for(validation_grid).solve(
                validation_grid,
                copy_case=True,
                write_back=False,
            )
            if not validation_pf.converged:
                success = False
                message += "; ACDCPF replay did not converge"
            else:
                replay_comparison = compare_pf_replay(extracted, validation_pf, data)
                if replay_comparison.get("matches") is False:
                    success = False
                    message += "; ACDCPF replay differs from OPF beyond declared tolerances"

        return PyomoACDCOPFResult(
            success=success,
            message=message,
            data=data,
            model=model,
            solver_result=solver_result,
            extracted_results=extracted,
            base_pf_result=base_pf,
            validation_pf_result=validation_pf,
            validation=validation,
            replay_comparison=replay_comparison,
            physics_validation=physics,
        )
    except Exception as exc:
        logger.error("Pyomo/IPOPT AC/DC OPF failed: %s", exc)
        logger.debug("Pyomo/IPOPT failure details", exc_info=True)
        return PyomoACDCOPFResult(
            success=False,
            message=str(exc),
            data=data,
            base_pf_result=base_pf,
            exception=exc,
        )


def solve_pyomo_acdc_loss_min_time_series(
    cases_by_period: Mapping[Any, Any],
    *,
    config: PyomoACDCOPFConfig | None = None,
    conversion_options: ConversionOptions | None = None,
    period_metadata: Mapping[Any, Mapping[str, Any]] | None = None,
) -> dict[Any, PyomoACDCOPFResult]:
    """Solve linked snapshot OPF with inter-temporal storage SOC constraints."""

    config = config or PyomoACDCOPFConfig()
    period_metadata = period_metadata or {}
    try:
        if not cases_by_period:
            raise ValueError("At least one time period is required.")

        period_data: dict[Any, dict[str, Any]] = {}
        base_pf_by_period: dict[Any, PFResult] = {}
        for key, case in cases_by_period.items():
            base_pf = _power_flow_adapter_for(case).solve(
                case,
                copy_case=True,
                write_back=False,
            )
            if not base_pf.converged:
                raise RuntimeError(
                    f"ACDCPF base PF did not converge for period {key!r}: {base_pf.message}"
                )
            data = _convert_case_to_opf_data(
                case,
                pf_result=base_pf,
                options=conversion_options,
            )
            data.setdefault("metadata", {}).update(dict(period_metadata.get(key, {})))
            validate_required_data(data)
            period_data[key] = data
            base_pf_by_period[key] = base_pf

        model, solver_result = solve_multiperiod_acdc_opf(
            period_data,
            build_options=OPFBuildOptions(),
            solve_options=OPFSolveOptions(
                ipopt_executable=config.ipopt_executable or config.default_ipopt_path(),
                tee=config.tee,
                max_iter=config.max_iter,
                tol=config.tolerance,
                print_level=config.print_level,
            ),
        )
        extracted_by_period = extract_multiperiod_opf_results(
            model,
            {key: data["base_mva"] for key, data in period_data.items()},
        )
        validation = validate_solution(model, tolerance=config.validation_tolerance)
        solver_success = _solver_succeeded(solver_result) and validation.valid
        message = str(solver_result.solver.termination_condition)
        if not validation.valid:
            message += "; post-solve validation failed: " + "; ".join((validation.nonfinite + validation.violations)[:3])
        physics_by_period = {}
        if all(is_acdcpf_network(case) for case in cases_by_period.values()):
            native_options = conversion_options or ACDCPFToPyomoOptions()
            physics_by_period = validate_time_series_physics(
                cases_by_period, extracted_by_period, options=native_options,
                baselines={key: pf.raw_result for key, pf in base_pf_by_period.items()},
                durations={key: period_metadata.get(key, {}).get("snapshot_duration_hours", native_options.snapshot_duration_hours)
                           for key in cases_by_period},
                tolerances=config.physics_tolerances,
            )
            failed = [key for key, report in physics_by_period.items() if not report.valid]
            if failed:
                solver_success = False
                message += f"; source-network physics failed in periods {failed[:5]}: " + physics_by_period[failed[0]].failure_message()
        results: dict[Any, PyomoACDCOPFResult] = {}
        for key, case in cases_by_period.items():
            validation_pf = None
            replay_comparison = None
            period_success = solver_success
            period_message = message
            if solver_success and config.run_final_acdcpf_validation:
                validation_grid = _copy_case(case)
                apply_pyomo_results_to_case(validation_grid, extracted_by_period[key])
                validation_pf = _power_flow_adapter_for(validation_grid).solve(
                    validation_grid,
                    copy_case=True,
                    write_back=False,
                )
                if not validation_pf.converged:
                    period_success = False
                    period_message += "; ACDCPF replay did not converge"
                else:
                    replay_comparison = compare_pf_replay(extracted_by_period[key], validation_pf, period_data[key])
                    if replay_comparison.get("matches") is False:
                        period_success = False
                        period_message += "; ACDCPF replay differs from OPF beyond declared tolerances"

            results[key] = PyomoACDCOPFResult(
                success=period_success,
                message=period_message,
                data=period_data[key],
                model=model,
                solver_result=solver_result,
                extracted_results=extracted_by_period[key],
                base_pf_result=base_pf_by_period[key],
                validation_pf_result=validation_pf,
                validation=validation,
                replay_comparison=replay_comparison,
                physics_validation=physics_by_period.get(key),
            )
        return results
    except Exception as exc:
        logger.error("Pyomo/IPOPT multi-period AC/DC OPF failed: %s", exc)
        logger.debug("Pyomo/IPOPT multi-period failure details", exc_info=True)
        return {
            key: PyomoACDCOPFResult(
                success=False,
                message=str(exc),
                data={},
                exception=exc,
            )
            for key in cases_by_period
        }


def apply_pyomo_results_to_pyflow_grid(grid: Any, results: dict[str, Any]) -> None:
    """Apply solved Pyomo setpoints to a pyflow grid for PF validation."""
    generator_pg = results.get("generator_pg_pu", {})
    generator_qg = results.get("generator_qg_pu", {})
    ac_voltage = results.get("ac_bus_voltage_magnitude_pu", {})
    dc_voltage = results.get("dc_bus_voltage_pu", {})
    converter_p_ac_terminal = results.get("converter_p_ac_terminal_absorbed_pu", {})
    converter_q_ac_terminal = results.get("converter_q_ac_terminal_absorbed_pu", {})

    for node in getattr(grid, "nodes_AC", []):
        key = f"AC{int(node.nodeNumber)}"
        if key in ac_voltage:
            node.V = float(ac_voltage[key])
        for gen in getattr(node, "connected_gen", []):
            gen_key = f"G{int(gen.genNumber)}"
            if gen_key in generator_pg:
                gen.PGen = float(generator_pg[gen_key])
            if gen_key in generator_qg:
                gen.QGen = float(generator_qg[gen_key])

    for node in getattr(grid, "nodes_DC", []):
        key = f"DC{int(node.nodeNumber)}"
        if key in dc_voltage:
            node.V = float(dc_voltage[key])

    for conv in getattr(grid, "Converters_ACDC", []):
        key = f"CONV{int(conv.ConvNumber)}"
        if key in converter_p_ac_terminal and str(getattr(conv, "type", "")) in {"PAC", "P"}:
            conv.P_AC = -float(converter_p_ac_terminal[key])
        if key in converter_q_ac_terminal and str(getattr(conv, "AC_type", "")) == "PQ":
            conv.Q_AC = -float(converter_q_ac_terminal[key])


def apply_pyomo_results_to_acdcpf_network(net: Any, results: dict[str, Any]) -> None:
    """Apply solved Pyomo setpoints to a native ``acdcpf.Network``."""
    base_mva = float(net.s_base)
    generator_pg = results.get("generator_pg_pu", {})
    generator_qg = results.get("generator_qg_pu", {})
    dc_generator_pg = results.get("dc_generator_pg_pu", {})
    storage_p = results.get("storage_p_pu", {})
    storage_q = results.get("storage_q_pu", {})
    storage_soc = results.get("storage_soc_percent", {})
    ac_voltage = results.get("ac_bus_voltage_magnitude_pu", {})
    dc_voltage = results.get("dc_bus_voltage_pu", {})
    dcdc_ratio = results.get("dcdc_ratio_pu", {})
    converter_p_ac_terminal = results.get("converter_p_ac_terminal_absorbed_pu", {})
    converter_q_ac_terminal = results.get("converter_q_ac_terminal_absorbed_pu", {})

    if hasattr(net, "ac_gen") and not net.ac_gen.empty:
        for idx, row in net.ac_gen.iterrows():
            gen_key = f"G{int(idx)}"
            bus_key = f"AC{int(row['bus'])}"
            if gen_key in generator_pg:
                net.ac_gen.at[idx, "p_mw"] = float(generator_pg[gen_key]) * base_mva
            if gen_key in generator_qg:
                net.ac_gen.at[idx, "q_mvar"] = float(generator_qg[gen_key]) * base_mva
            if "v_pu" in net.ac_gen.columns and bus_key in ac_voltage and _is_finite(row.get("v_pu")):
                net.ac_gen.at[idx, "v_pu"] = float(ac_voltage[bus_key])

    if hasattr(net, "dc_gen") and not net.dc_gen.empty:
        for idx, _ in net.dc_gen.iterrows():
            gen_key = f"DCG{int(idx)}"
            if gen_key in dc_generator_pg:
                net.dc_gen.at[idx, "p_mw"] = float(dc_generator_pg[gen_key]) * base_mva

    _apply_storage_results_to_acdcpf_network(
        net,
        storage_p=storage_p,
        storage_q=storage_q,
        storage_soc=storage_soc,
        base_mva=base_mva,
    )

    if hasattr(net, "dc_bus") and not net.dc_bus.empty:
        for idx, _ in net.dc_bus.iterrows():
            bus_key = f"DC{int(idx)}"
            if bus_key in dc_voltage:
                net.dc_bus.at[idx, "v_dc_pu"] = float(dc_voltage[bus_key])

    if hasattr(net, "dcdc") and not net.dcdc.empty:
        for idx, row in net.dcdc.iterrows():
            converter_key = f"DCDC{int(idx)}"
            if converter_key not in dcdc_ratio:
                continue
            from_bus = int(row["from_bus"])
            to_bus = int(row["to_bus"])
            v_from_base = float(net.dc_bus.loc[from_bus, "v_base"])
            v_to_base = float(net.dc_bus.loc[to_bus, "v_base"])
            net.dcdc.at[idx, "d_ratio"] = float(dcdc_ratio[converter_key]) * v_to_base / v_from_base

    if not hasattr(net, "vsc") or net.vsc.empty:
        return

    for idx, row in net.vsc.iterrows():
        conv_key = f"CONV{int(idx)}"
        ac_bus_key = f"AC{int(row['ac_bus'])}"
        dc_bus_key = f"DC{int(row['dc_bus'])}"
        control_mode = str(row.get("control_mode", "p_q")).lower()

        if control_mode in {"p_q", "p_vac"} and conv_key in converter_p_ac_terminal:
            net.vsc.at[idx, "p_mw"] = float(converter_p_ac_terminal[conv_key]) * base_mva
        if control_mode in {"pdc_q", "pdc_vac"} and conv_key in results.get("converter_p_dc_pu", {}):
            net.vsc.at[idx, "p_dc_set_mw"] = -float(results["converter_p_dc_pu"][conv_key]) * base_mva
        if control_mode in {"p_q", "pdc_q", "vdc_q", "droop_q"} and conv_key in converter_q_ac_terminal:
            net.vsc.at[idx, "q_mvar"] = float(converter_q_ac_terminal[conv_key]) * base_mva
        if "vac" in control_mode and ac_bus_key in ac_voltage:
            net.vsc.at[idx, "v_ac_pu"] = float(ac_voltage[ac_bus_key])
        if "vdc" in control_mode and dc_bus_key in dc_voltage:
            net.vsc.at[idx, "v_dc_pu"] = float(dc_voltage[dc_bus_key])


def _apply_storage_results_to_acdcpf_network(
    net: Any,
    *,
    storage_p: dict[str, float],
    storage_q: dict[str, float],
    storage_soc: dict[str, float],
    base_mva: float,
) -> None:
    """Map OPF storage decisions onto native ACDCPF storage rows."""

    storage_table = getattr(net, "storage", None)
    if storage_table is None or getattr(storage_table, "empty", True):
        return

    for idx, row in storage_table.iterrows():
        storage_key = f"STORAGE{int(idx)}"
        if storage_key not in storage_p:
            continue

        p_mw = float(storage_p[storage_key]) * base_mva
        q_mvar = float(storage_q.get(storage_key, 0.0)) * base_mva
        if "p_mw" in storage_table.columns:
            net.storage.at[idx, "p_mw"] = p_mw
        if "q_mvar" in storage_table.columns:
            net.storage.at[idx, "q_mvar"] = q_mvar
        if "soc_percent" in storage_table.columns and storage_key in storage_soc:
            net.storage.at[idx, "soc_percent"] = float(storage_soc[storage_key])


def apply_pyomo_results_to_case(case: Any, results: dict[str, Any]) -> None:
    """Apply OPF setpoints to the case type used for validation."""
    if is_acdcpf_network(case):
        apply_pyomo_results_to_acdcpf_network(case, results)
    else:
        apply_pyomo_results_to_pyflow_grid(case, results)


def _solver_succeeded(result: Any) -> bool:
    termination = result.solver.termination_condition
    return result.solver.status == pyo.SolverStatus.ok and termination in {
        pyo.TerminationCondition.optimal,
        pyo.TerminationCondition.locallyOptimal,
    }


def _power_flow_adapter_for(case: Any) -> ACDCPFNetworkAdapter | ACDCPFAdapter:
    if is_acdcpf_network(case):
        return ACDCPFNetworkAdapter()
    return ACDCPFAdapter()


def _convert_case_to_opf_data(
    case: Any,
    *,
    pf_result: PFResult | None,
    options: ConversionOptions | None,
) -> dict[str, Any]:
    if is_acdcpf_network(case):
        return convert_acdcpf_network_to_opf_data(
            case,
            pf_result=pf_result,
            options=options,
        )
    return convert_pyflow_grid_to_opf_data(
        case,
        pf_result=pf_result,
        options=options,
    )


def _copy_case(grid: Any) -> Any:
    import copy

    return copy.deepcopy(grid)


def _is_finite(value: Any) -> bool:
    try:
        return value is not None and bool(pyo.value(value) == pyo.value(value))
    except Exception:
        return False


def _optional_int(value: Any) -> int | None:
    return int(value) if _is_finite(value) else None
