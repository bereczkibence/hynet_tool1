from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

from acdcpf_opf.opf.config import OPFConfig
from acdcpf_opf.opf.result import OPFResult
from acdcpf_opf.opf.solver import OPFSolver
from acdcpf_opf.opf.variables import build_decision_variables
from acdcpf_opf.powerflow.acdcpf_adapter import ACDCPFAdapter
from acdcpf_opf.powerflow.base import PowerFlowSolver
from acdcpf_opf.powerflow.result import PFResult

LOGGER = logging.getLogger(__name__)


@dataclass
class LossMinimizationOptions:
    """Options for the black-box PF-based AC/DC loss-minimization OPF."""

    optimizer_method: str = "SLSQP"
    max_iterations: int = 100
    tolerance: float = 1e-6
    mismatch_tolerance: float = 1e-6
    penalty_value: float = 1e9
    constraint_penalty_weight: float = 1e6
    logging_level: int | str = logging.INFO
    debug: bool = False

    optimize_non_slack_generator_active_power: bool = False
    optimize_generator_voltage_setpoints: bool = False
    optimize_converter_active_power: bool = True
    optimize_converter_reactive_power: bool = True

    ac_generator_active_power_indices: Sequence[int] | None = None
    ac_generator_voltage_node_indices: Sequence[int] | None = None
    converter_active_power_indices: Sequence[int] | None = None
    converter_reactive_power_indices: Sequence[int] | None = None

    pf_max_iter_outer: int = 30
    pf_max_iter_inner: int = 30
    pf_tolerance: float = 1e-8


@dataclass
class Stagg5LossMinimizationRun:
    """Convenience package returned by the Stagg 5-bus example."""

    grid: Any
    base_pf: PFResult
    opf_result: OPFResult


def build_loss_minimization_config(options: LossMinimizationOptions | None = None) -> OPFConfig:
    """Build the OPFConfig used by the PF-backed loss-minimization solver."""
    opts = options or LossMinimizationOptions()
    return OPFConfig(
        objective="total_active_loss_minimization",
        pf_backend="acdcpf",
        optimizer_method=opts.optimizer_method,
        max_iterations=opts.max_iterations,
        tolerance=opts.tolerance,
        mismatch_tolerance=opts.mismatch_tolerance,
        penalty_value=opts.penalty_value,
        constraint_penalty_weight=opts.constraint_penalty_weight,
        debug=opts.debug,
        logging_level=opts.logging_level,
        pf_max_iter_outer=opts.pf_max_iter_outer,
        pf_max_iter_inner=opts.pf_max_iter_inner,
        pf_tolerance=opts.pf_tolerance,
        enforce_constraints=True,
        optimize_ac_generator_active_power=opts.optimize_non_slack_generator_active_power,
        optimize_ac_generator_voltage=opts.optimize_generator_voltage_setpoints,
        optimize_converter_active_power=opts.optimize_converter_active_power,
        optimize_converter_reactive_power=opts.optimize_converter_reactive_power,
        ac_generator_active_power_indices=opts.ac_generator_active_power_indices,
        ac_generator_voltage_node_indices=opts.ac_generator_voltage_node_indices,
        converter_active_power_indices=opts.converter_active_power_indices,
        converter_reactive_power_indices=opts.converter_reactive_power_indices,
    )


def solve_acdc_loss_min_opf(
    grid: Any,
    *,
    options: LossMinimizationOptions | None = None,
    pf_solver: PowerFlowSolver | None = None,
) -> OPFResult:
    """Solve a single-period AC/DC OPF by wrapping ACDCPF power flow.

    The optimizer changes control setpoints, ACDCPF enforces the nonlinear
    AC/DC power-flow equations for each candidate, and the objective minimizes
    total active losses plus MVP constraint penalties for violated limits.
    """
    validate_pyflow_grid(grid)
    config = build_loss_minimization_config(options)
    solver = OPFSolver(
        config=config,
        pf_solver=pf_solver or ACDCPFAdapter(
            max_iter_outer=config.pf_max_iter_outer,
            max_iter_inner=config.pf_max_iter_inner,
            tolerance=config.pf_tolerance,
            verbose=config.debug,
        ),
    )
    return solver.solve(grid)


def run_acdcpf_base_pf(
    grid: Any,
    *,
    options: LossMinimizationOptions | None = None,
) -> PFResult:
    """Run the ACDCPF PF backend once on a copy of the input grid."""
    opts = options or LossMinimizationOptions()
    validate_pyflow_grid(grid)
    return ACDCPFAdapter(
        max_iter_outer=opts.pf_max_iter_outer,
        max_iter_inner=opts.pf_max_iter_inner,
        tolerance=opts.pf_tolerance,
        verbose=opts.debug,
    ).solve(grid, copy_case=True, write_back=False)


def apply_opf_result_controls(
    grid: Any,
    opf_result: OPFResult,
    *,
    options: LossMinimizationOptions | None = None,
) -> None:
    """Apply an OPFResult's optimized controls to a pyflow grid in place."""
    config = build_loss_minimization_config(options)
    variable_set = build_decision_variables(grid, config)
    values = []
    for variable in variable_set.variables:
        if variable.name not in opf_result.decision_variables:
            raise KeyError(f"OPF result does not contain decision variable {variable.name!r}.")
        values.append(opf_result.decision_variables[variable.name])
    variable_set.apply(grid, values)


def solve_stagg5_loss_minimization(
    options: LossMinimizationOptions | None = None,
) -> Stagg5LossMinimizationRun:
    """Run the loss-minimization OPF on pyflow's Stagg 5-bus AC/DC case."""
    import pyflow_acdc as pyf

    pyf.initialize_pyflowacdc()
    grid, _ = pyf.Stagg5MATACDC()
    base_pf = run_acdcpf_base_pf(grid, options=options)
    opf_result = solve_acdc_loss_min_opf(grid, options=options)
    return Stagg5LossMinimizationRun(grid=grid, base_pf=base_pf, opf_result=opf_result)


def validate_pyflow_grid(grid: Any) -> None:
    """Validate the minimum pyflow grid attributes required by this solver."""
    missing = [
        name
        for name in ("S_base", "nodes_AC", "lines_AC", "Converters_ACDC")
        if not hasattr(grid, name)
    ]
    if missing:
        raise ValueError(f"Grid is missing required pyflow attributes: {', '.join(missing)}")


def run_stagg5_example() -> OPFResult:
    """Small example entry point for interactive use."""
    run = solve_stagg5_loss_minimization()
    return run.opf_result
