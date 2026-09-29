from __future__ import annotations

import logging
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PARENT = PACKAGE_ROOT.parent
if str(SOURCE_PARENT) not in sys.path:
    sys.path.insert(0, str(SOURCE_PARENT))

import pyflow_acdc as pyf

from acdcpf_opf.data.pyflow_to_pyomo import PyflowToPyomoOptions
from acdcpf_opf.opf.pyomo_acdc_loss_min import (
    PyomoACDCOPFConfig,
    build_pyomo_acdc_loss_min_model,
    solve_pyomo_acdc_loss_min_opf,
)

# Same study choice as run_stagg5_opf.py: keep non-slack generator dispatch
# fixed and optimize selected converter P/Q controls.
OPTIMIZE_AC_GENERATOR_ACTIVE_POWER = False
AC_GENERATOR_ACTIVE_POWER_INDICES: tuple[int, ...] = ()

OPTIMIZE_AC_GENERATOR_VOLTAGE = False
AC_GENERATOR_VOLTAGE_NODE_INDICES: tuple[int, ...] = ()

OPTIMIZE_CONVERTER_ACTIVE_POWER = True
CONVERTER_ACTIVE_POWER_INDICES: tuple[int, ...] = (0, 2)

OPTIMIZE_CONVERTER_REACTIVE_POWER = True
CONVERTER_REACTIVE_POWER_INDICES: tuple[int, ...] = (0, 2)


LOSS_BREAKDOWN_ORDER = (
    "ac_branch_mw",
    "dc_branch_mw",
    "converter_transformer_mw",
    "converter_phase_reactor_mw",
    "converter_electronic_mw",
    "converter_mw",
    "total_active_losses_mw",
)


def _print_loss_breakdown(title: str, breakdown: dict[str, float]) -> None:
    print(f"\n{title}")
    print("-" * 80)
    printed = set()
    for key in LOSS_BREAKDOWN_ORDER:
        if key in breakdown:
            print(f"{key:<36} {breakdown[key]:>12.6f} MW")
            printed.add(key)
    for key in sorted(set(breakdown) - printed):
        print(f"{key:<36} {breakdown[key]:>12.6f} MW")


def main() -> None:
    logging.basicConfig(level=logging.CRITICAL, format="%(levelname)s:%(name)s:%(message)s")
    logging.getLogger("pyomo.opt").setLevel(logging.CRITICAL)

    pyf.initialize_pyflowacdc()
    grid, _ = pyf.Stagg5MATACDC()
    config = PyomoACDCOPFConfig(
        ipopt_executable=PyomoACDCOPFConfig.default_ipopt_path(),
        tee=False,
        max_iter=300,
        tolerance=1e-6,
        print_level=5,
        logging_level=logging.CRITICAL,
    )
    conversion_options = PyflowToPyomoOptions(
        optimize_non_slack_generator_active_power=OPTIMIZE_AC_GENERATOR_ACTIVE_POWER,
        ac_generator_active_power_indices=AC_GENERATOR_ACTIVE_POWER_INDICES,
        optimize_generator_voltage_setpoints=OPTIMIZE_AC_GENERATOR_VOLTAGE,
        ac_generator_voltage_node_indices=AC_GENERATOR_VOLTAGE_NODE_INDICES,
        optimize_converter_active_power=OPTIMIZE_CONVERTER_ACTIVE_POWER,
        converter_active_power_indices=CONVERTER_ACTIVE_POWER_INDICES,
        optimize_converter_reactive_power=OPTIMIZE_CONVERTER_REACTIVE_POWER,
        converter_reactive_power_indices=CONVERTER_REACTIVE_POWER_INDICES,
    )

    model, data, base_pf = build_pyomo_acdc_loss_min_model(
        grid,
        config=config,
        conversion_options=conversion_options,
    )
    result = solve_pyomo_acdc_loss_min_opf(
        grid,
        config=config,
        conversion_options=conversion_options,
    )

    print("\nExplicit Pyomo/IPOPT AC/DC loss-minimization OPF")
    print("=" * 80)
    print(f"AC buses: {len(model.AC_BUS)}")
    print(f"DC buses: {len(model.DC_BUS)}")
    print(f"Converters: {len(model.CONV)}")
    print(f"Base ACDCPF converged: {base_pf.converged if base_pf else 'not run'}")
    if base_pf is not None:
        print(f"Base ACDCPF active losses: {base_pf.total_active_losses:.6f} MW")
    print(f"IPOPT executable: {config.ipopt_executable}")
    if base_pf is not None:
        _print_loss_breakdown(
            "Base ACDCPF active-loss breakdown",
            {
                **base_pf.active_loss_breakdown(),
                "total_active_losses_mw": base_pf.total_active_losses,
            },
        )

    print("\nSolve result")
    print("-" * 80)
    print(f"Success: {result.success}")
    print(f"Message: {result.message}")
    if result.objective_total_active_losses_mw is not None:
        print(f"Objective total active losses: {result.objective_total_active_losses_mw:.6f} MW")
        _print_loss_breakdown("Pyomo objective active-loss breakdown", result.active_loss_breakdown_mw)
    if result.validation_pf_result is not None:
        print(f"Final ACDCPF validation converged: {result.validation_pf_result.converged}")
        print(f"Final ACDCPF losses: {result.validation_pf_result.total_active_losses:.6f} MW")
        _print_loss_breakdown(
            "Final ACDCPF validation active-loss breakdown",
            {
                **result.validation_pf_result.active_loss_breakdown(),
                "total_active_losses_mw": result.validation_pf_result.total_active_losses,
            },
        )
    if result.exception is not None:
        print("\nIPOPT did not solve the model.")
        print("The model was built successfully, but the solver failed to run or converge.")
        print("Check that ipopt.exe and its required DLLs are available on this machine.")

    print("\nData summary")
    print("-" * 80)
    print(f"Base MVA: {data['base_mva']}")
    print(f"Fixed variable groups: {sorted(data.get('fixed', {}).keys())}")


if __name__ == "__main__":
    main()
