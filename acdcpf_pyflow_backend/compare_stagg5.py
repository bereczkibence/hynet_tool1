from __future__ import annotations

import sys
from pathlib import Path

import pyflow_acdc as pyf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from acdcpf_pyflow_backend.runner import run_acdcpf_pf_on_pyflow


def main() -> None:
    pyf.initialize_pyflowacdc()

    native_grid, _ = pyf.Stagg5MATACDC()
    pyf.ACDC_sequential(native_grid)

    adapted_grid, _ = pyf.Stagg5MATACDC()
    result = run_acdcpf_pf_on_pyflow(adapted_grid, write_back=True)

    print(f"acdcpf backend converged: {result.converged}")
    print("\nConverter comparison:")
    print("name | native P_AC | backend P_AC | native Q_AC | backend Q_AC | native P_DC | backend P_DC")
    print("-" * 95)
    for native_conv, backend_conv in zip(native_grid.Converters_ACDC, adapted_grid.Converters_ACDC):
        print(
            f"{native_conv.name:>4} | "
            f"{native_conv.P_AC:>11.6f} | {backend_conv.P_AC:>12.6f} | "
            f"{native_conv.Q_AC:>11.6f} | {backend_conv.Q_AC:>12.6f} | "
            f"{native_conv.P_DC:>11.6f} | {backend_conv.P_DC:>12.6f}"
        )

    print("\nAC bus voltage comparison:")
    print("bus | native V | backend V | native theta | backend theta")
    print("-" * 65)
    for native_node, backend_node in zip(native_grid.nodes_AC, adapted_grid.nodes_AC):
        print(
            f"{native_node.name:>3} | "
            f"{native_node.V:>8.6f} | {backend_node.V:>9.6f} | "
            f"{native_node.theta:>12.6f} | {backend_node.theta:>13.6f}"
        )


if __name__ == "__main__":
    main()
