from __future__ import annotations

import logging
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PARENT = PACKAGE_ROOT.parent
if str(SOURCE_PARENT) not in sys.path:
    sys.path.insert(0, str(SOURCE_PARENT))

from acdcpf_opf.opf_acdc_loss_min import (
    LossMinimizationOptions,
    build_loss_minimization_config,
    run_acdcpf_base_pf,
    solve_acdc_loss_min_opf,
)
from acdcpf_opf.opf.variables import build_decision_variables

# Each tuple position is the pyflow object index. Set it to 1 to allow that
# setpoint to move during OPF, or 0 to keep the base-case value fixed.
#
# Stagg 5 synchronous generators: [generator 0, generator 1]
SYNCHRONOUS_GENERATOR_P_OPTIMIZATION = (0, 0)
SYNCHRONOUS_GENERATOR_V_OPTIMIZATION = (0, 0)

# Stagg 5 converters: [VSC 0 (P-Q), VSC 1 (DC slack/PV), VSC 2 (P-Q)]
VSC_P_OPTIMIZATION = (1, 1, 1)
VSC_Q_OPTIMIZATION = (1, 1, 1)


def build_stagg5_grid():
    """Load the pyflow Stagg 5-bus AC/DC case used by this example."""
    import pyflow_acdc as pyf

    pyf.initialize_pyflowacdc()
    grid, _ = pyf.Stagg5MATACDC()
    return grid


def build_stagg5_loss_minimization_options(grid) -> LossMinimizationOptions:
    """Build deterministic OPF options from the binary participation vectors."""
    generator_count = len(getattr(grid, "Generators", []))
    converter_count = len(getattr(grid, "Converters_ACDC", []))

    generator_p_indices = _enabled_indices(
        SYNCHRONOUS_GENERATOR_P_OPTIMIZATION,
        expected_length=generator_count,
        label="SYNCHRONOUS_GENERATOR_P_OPTIMIZATION",
    )
    generator_v_indices = _enabled_generator_voltage_node_indices(
        grid,
        SYNCHRONOUS_GENERATOR_V_OPTIMIZATION,
    )
    converter_p_indices = _enabled_indices(
        VSC_P_OPTIMIZATION,
        expected_length=converter_count,
        label="VSC_P_OPTIMIZATION",
    )
    converter_q_indices = _enabled_indices(
        VSC_Q_OPTIMIZATION,
        expected_length=converter_count,
        label="VSC_Q_OPTIMIZATION",
    )

    return LossMinimizationOptions(
        max_iterations=30,
        logging_level=logging.WARNING,
        optimize_non_slack_generator_active_power=bool(generator_p_indices),
        optimize_generator_voltage_setpoints=bool(generator_v_indices),
        optimize_converter_active_power=bool(converter_p_indices),
        optimize_converter_reactive_power=bool(converter_q_indices),
        ac_generator_active_power_indices=generator_p_indices,
        ac_generator_voltage_node_indices=generator_v_indices,
        converter_active_power_indices=converter_p_indices,
        converter_reactive_power_indices=converter_q_indices,
    )


def print_selected_controls(grid, options: LossMinimizationOptions) -> None:
    """Print the final OPF decision variables before solving."""
    config = build_loss_minimization_config(options)
    variable_set = build_decision_variables(grid, config)

    print("\nSelected OPF controls")
    print("-" * 80)
    if not variable_set.variables:
        print("No controls selected. The script will only evaluate the base PF point.")
        return
    for variable in variable_set.variables:
        print(f"- {variable.name}")


def print_loss_table(base_pf, opf_result) -> None:
    """Print active loss components before and after OPF."""
    base_breakdown = base_pf.active_loss_breakdown()
    optimized_breakdown = opf_result.diagnostics["loss_breakdown_mw"]
    names = sorted(set(base_breakdown) | set(optimized_breakdown))

    print("\nActive power losses")
    print("-" * 80)
    header = f"{'Component':<24} {'Before MW':>12} {'After MW':>12} {'Delta MW':>12}"
    print(header)
    print("-" * len(header))

    for name in names:
        before = float(base_breakdown.get(name, 0.0))
        after = float(optimized_breakdown.get(name, 0.0))
        print(f"{name:<24} {before:>12.6f} {after:>12.6f} {after - before:>12.6f}")

    before_total = float(base_pf.total_active_losses)
    after_total = float(opf_result.total_active_losses)
    print("-" * len(header))
    print(f"{'total_active_losses':<24} {before_total:>12.6f} {after_total:>12.6f} {after_total - before_total:>12.6f}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")

    grid = build_stagg5_grid()
    options = build_stagg5_loss_minimization_options(grid)
    print_selected_controls(grid, options)

    base_pf = run_acdcpf_base_pf(grid, options=options)
    opf_result = solve_acdc_loss_min_opf(grid, options=options)

    print("\nStagg 5-bus ACDCPF-backed loss-minimization OPF")
    print("=" * 80)
    print(f"Success: {opf_result.success}")
    print(f"Message: {opf_result.message}")
    print(f"Max constraint violation: {opf_result.diagnostics['max_constraint_violation']:.6g}")

    print("\nOptimized control setpoints")
    print("-" * 80)
    print(opf_result.decision_variable_change_table())

    print_loss_table(base_pf, opf_result)


def _enabled_indices(
    flags: tuple[int, ...],
    *,
    expected_length: int,
    label: str,
) -> tuple[int, ...]:
    if len(flags) != expected_length:
        raise ValueError(
            f"{label} must contain {expected_length} entries, one per case object; "
            f"received {len(flags)}."
        )
    if any(flag not in (0, 1) for flag in flags):
        raise ValueError(f"{label} may contain only 0 (fixed) or 1 (optimized).")
    return tuple(index for index, flag in enumerate(flags) if flag == 1)


def _enabled_generator_voltage_node_indices(
    grid,
    flags: tuple[int, ...],
) -> tuple[int, ...]:
    enabled_generators = set(
        _enabled_indices(
            flags,
            expected_length=len(getattr(grid, "Generators", [])),
            label="SYNCHRONOUS_GENERATOR_V_OPTIMIZATION",
        )
    )
    node_indices: list[int] = []
    for node in getattr(grid, "nodes_AC", []):
        connected = getattr(node, "connected_gen", [])
        if any(int(getattr(gen, "genNumber")) in enabled_generators for gen in connected):
            node_indices.append(int(getattr(node, "nodeNumber")))
    return tuple(node_indices)


if __name__ == "__main__":
    main()
