from __future__ import annotations

import logging
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PARENT = PACKAGE_ROOT.parent
if str(SOURCE_PARENT) not in sys.path:
    sys.path.insert(0, str(SOURCE_PARENT))

import pyflow_acdc as pyf

from acdcpf_opf.opf.constraints import compute_constraint_violations, max_constraint_violation
from acdcpf_opf.opf_acdc_loss_min import (
    LossMinimizationOptions,
    apply_opf_result_controls,
    build_loss_minimization_config,
    solve_stagg5_loss_minimization,
)
from acdcpf_opf.powerflow.acdcpf_adapter import ACDCPFAdapter


LOSS_TOLERANCE_MW = 1e-8


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")

    options = LossMinimizationOptions(max_iterations=30, logging_level=logging.WARNING)
    config = build_loss_minimization_config(options)
    run = solve_stagg5_loss_minimization(options)

    pyf.initialize_pyflowacdc()
    fresh_grid, _ = pyf.Stagg5MATACDC()
    apply_opf_result_controls(fresh_grid, run.opf_result, options=options)
    rerun_pf = ACDCPFAdapter().solve(fresh_grid, copy_case=True, write_back=False)
    rerun_violations = compute_constraint_violations(fresh_grid, rerun_pf, config)
    rerun_loss_error = abs(rerun_pf.total_active_losses - run.opf_result.total_active_losses)

    pyf.initialize_pyflowacdc()
    pyflow_grid, _ = pyf.Stagg5MATACDC()
    apply_opf_result_controls(pyflow_grid, run.opf_result, options=options)
    _, pyflow_tolerance, _ = pyf.ACDC_sequential(pyflow_grid)
    pyflow_losses = _pyflow_loss_components(pyflow_grid)

    print("\nStagg5 OPF validation")
    print("=" * 80)
    _print_check("Base ACDCPF PF converged", run.base_pf.converged)
    _print_check("OPF reported success", run.opf_result.success)
    _print_check("Independent ACDCPF rerun converged", rerun_pf.converged)
    _print_check("Independent ACDCPF loss matches OPF", rerun_loss_error <= LOSS_TOLERANCE_MW)
    _print_check("Independent ACDCPF constraints satisfied", max_constraint_violation(rerun_violations) <= config.tolerance)
    _print_check("Native pyflow optimized PF converged", pyflow_tolerance["final_sequential_tolerance"] < 1e-5)

    print("\nIndependent ACDCPF rerun")
    print("-" * 80)
    print(f"OPF loss:   {run.opf_result.total_active_losses:.9f} MW")
    print(f"Rerun loss: {rerun_pf.total_active_losses:.9f} MW")
    print(f"Difference: {rerun_loss_error:.3e} MW")
    print(f"Max constraint violation: {max_constraint_violation(rerun_violations):.3e}")

    print("\nNative pyflow check with optimized controls")
    print("-" * 80)
    print(f"Final pyflow tolerance: {pyflow_tolerance['final_sequential_tolerance']:.3e}")
    print(f"AC line losses:        {pyflow_losses['ac']:.6f} MW")
    print(f"DC line losses:        {pyflow_losses['dc']:.6f} MW")
    print(f"Converter losses:      {pyflow_losses['converter']:.6f} MW")
    print(f"Total component loss:  {pyflow_losses['total']:.6f} MW")
    print(f"ACDCPF optimized loss: {run.opf_result.total_active_losses:.6f} MW")
    print("Note: pyflow and ACDCPF differ mainly in converter loss reporting/modeling.")


def _pyflow_loss_components(grid) -> dict[str, float]:
    s_base = float(grid.S_base)
    ac_loss = sum(line.loss.real for line in grid.lines_AC) * s_base
    dc_loss = sum(line.loss for line in grid.lines_DC) * s_base
    converter_loss = sum(conv.P_loss for conv in grid.Converters_ACDC) * s_base
    return {
        "ac": float(ac_loss),
        "dc": float(dc_loss),
        "converter": float(converter_loss),
        "total": float(ac_loss + dc_loss + converter_loss),
    }


def _print_check(label: str, passed: bool) -> None:
    status = "PASS" if passed else "FAIL"
    print(f"{status:<5} {label}")


if __name__ == "__main__":
    main()
