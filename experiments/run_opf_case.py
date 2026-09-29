from __future__ import annotations

import logging
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PARENT = PACKAGE_ROOT.parent
if str(SOURCE_PARENT) not in sys.path:
    sys.path.insert(0, str(SOURCE_PARENT))

import pyflow_acdc as pyf

from acdcpf_opf import ACDCPFAdapter, OPFConfig, OPFSolver


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

    pyf.initialize_pyflowacdc()
    grid, _ = pyf.Stagg5MATACDC()

    pf_solver = ACDCPFAdapter()
    base_pf = pf_solver.solve(grid)

    print("Base PF through ACDCPF adapter")
    print(f"  converged: {base_pf.converged}")
    print(f"  total active losses: {base_pf.total_active_losses:.6f} MW")
    print(f"  loss breakdown: {base_pf.active_loss_breakdown()}")

    config = OPFConfig(
        debug=False,
        # Start with zero control freedom. This verifies the OPF wrapper
        # reproduces the base PF objective before variables are enabled.
        optimize_ac_generator_active_power=False,
        optimize_converter_active_power=False,
    )
    solver = OPFSolver(config=config, pf_solver=pf_solver)
    opf_result = solver.solve(grid)

    print("\nOPF wrapper result")
    print(f"  success: {opf_result.success}")
    print(f"  objective value: {opf_result.objective_value:.6f} MW")
    print(f"  total active losses: {opf_result.total_active_losses:.6f} MW")
    print(f"  optimizer status: {opf_result.optimizer_status}")
    print(f"  message: {opf_result.message}")

    tuned_config = OPFConfig(
        max_iterations=20,
        optimize_converter_reactive_power=True,
    )
    tuned_result = OPFSolver(config=tuned_config, pf_solver=pf_solver).solve(grid)

    print("\nOPF with converter reactive-power controls")
    print(f"  success: {tuned_result.success}")
    print(f"  objective value: {tuned_result.objective_value:.6f} MW")
    print(f"  total active losses: {tuned_result.total_active_losses:.6f} MW")
    print(f"  max constraint violation: {tuned_result.diagnostics['max_constraint_violation']:.6g}")
    print(f"  optimizer status: {tuned_result.optimizer_status}")
    print(f"  decision variables: {tuned_result.decision_variables}")


if __name__ == "__main__":
    main()
