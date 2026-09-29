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
    grid, res = pyf.Stagg5MATACDC()

    result = run_acdcpf_pf_on_pyflow(grid, write_back=True, verbose=False)

    print(f"Converged: {result.converged}")
    print("\nAC bus voltages:")
    print(result.ac_bus[["v_pu", "v_angle_deg"]])
    print("\nDC bus voltages:")
    print(result.dc_bus[["v_dc_pu", "v_dc_kv"]])
    print("\nVSC results:")
    print(result.vsc[["p_ac_mw", "q_ac_mvar", "p_dc_mw", "p_loss_mw"]])
    print("\nPyflow loss report after write-back:")
    res.Power_loss()


if __name__ == "__main__":
    main()
