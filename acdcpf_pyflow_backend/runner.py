from __future__ import annotations

import numpy as np
import pandas as pd

from ._bootstrap import ensure_acdcpf_importable
from .datatypes import AcdcpfBackendResult
from .translator import build_acdcpf_network_from_pyflow


def run_acdcpf_pf_on_pyflow(
    grid,
    *,
    write_back: bool = True,
    verbose: bool = False,
    max_iter_outer: int = 30,
    max_iter_inner: int = 30,
    tol: float = 1e-8,
) -> AcdcpfBackendResult:
    """
    Run acdcpf as a separate PF backend for a pyflow_acdc grid.

    This function does not modify pyflow internals. It translates the grid,
    runs the separate acdcpf project, and optionally writes solved values
    back to the original pyflow grid object.
    """
    ensure_acdcpf_importable()
    import acdcpf as pf

    artifacts = build_acdcpf_network_from_pyflow(grid)
    net = artifacts.net
    converged = pf.run_pf(
        net,
        enforce_limits=False,
        enforce_slack_q_limits=False,
        max_iter_outer=max_iter_outer,
        max_iter_inner=max_iter_inner,
        tol=tol,
        verbose=verbose,
    )

    result = AcdcpfBackendResult(
        converged=bool(converged),
        ac_bus=net.res_ac_bus.copy(),
        ac_line=net.res_ac_line.copy(),
        dc_bus=net.res_dc_bus.copy(),
        dc_line=net.res_dc_line.copy(),
        vsc=net.res_vsc.copy(),
        dcdc=net.res_dcdc.copy(),
        mapping=artifacts.mapping,
        acdcpf_net=net,
    )

    if write_back:
        _write_results_back_to_pyflow(grid, result)

    return result


def _write_results_back_to_pyflow(grid, result: AcdcpfBackendResult) -> None:
    s_base = float(grid.S_base)

    _write_ac_bus_voltages(grid, result.ac_bus, result.mapping.ac_bus_by_pyflow_index)
    _write_dc_bus_voltages(grid, result.dc_bus, result.mapping.dc_bus_by_pyflow_index)
    _write_converter_results(grid, result.vsc, result.mapping.vsc_by_pyflow_index, s_base)
    _normalize_pyflow_scalar_state(grid)

    if getattr(grid, "nodes_AC", []):
        grid.Update_PQ_AC()
    if getattr(grid, "nodes_DC", []):
        grid.Update_P_DC()

    grid.V_AC = np.array([node.V for node in grid.nodes_AC], dtype=float) if grid.nodes_AC else np.array([])
    grid.Theta_V_AC = np.array([node.theta for node in grid.nodes_AC], dtype=float) if grid.nodes_AC else np.array([])
    grid.V_DC = np.array([node.V for node in grid.nodes_DC], dtype=float) if grid.nodes_DC else np.array([])

    _recompute_ac_injections(grid)
    _recompute_dc_injections(grid)

    if grid.nodes_AC:
        grid.Line_AC_calc()
        grid.Line_AC_calc_exp()
    if grid.nodes_DC:
        grid.Line_DC_calc()


def _write_ac_bus_voltages(grid, ac_bus: pd.DataFrame, bus_map: dict[int, int]) -> None:
    for py_idx, acdcpf_idx in bus_map.items():
        if acdcpf_idx not in ac_bus.index:
            continue
        row = ac_bus.loc[acdcpf_idx]
        node = grid.nodes_AC[py_idx]
        node.V = float(row["v_pu"])
        node.theta = np.radians(float(row["v_angle_deg"]))


def _write_dc_bus_voltages(grid, dc_bus: pd.DataFrame, bus_map: dict[int, int]) -> None:
    for py_idx, acdcpf_idx in bus_map.items():
        if acdcpf_idx not in dc_bus.index:
            continue
        row = dc_bus.loc[acdcpf_idx]
        node = grid.nodes_DC[py_idx]
        node.V = float(row["v_dc_pu"])


def _write_converter_results(grid, vsc: pd.DataFrame, vsc_map: dict[int, int], s_base: float) -> None:
    for conv in grid.Converters_ACDC:
        acdcpf_idx = vsc_map.get(conv.ConvNumber)
        if acdcpf_idx is None or acdcpf_idx not in vsc.index:
            continue
        row = vsc.loc[acdcpf_idx]
        conv.P_AC = -float(row["p_ac_mw"]) / s_base
        conv.Q_AC = -float(row["q_ac_mvar"]) / s_base
        conv.P_DC = float(row["p_dc_mw"]) / s_base
        conv.P_loss = float(row["p_loss_mw"]) / s_base
        conv.U_s = float(row["v_ac_pu"])
        conv.U_c = float(row["v_converter_pu"])
        conv.Node_AC.P_s = np.float64(conv.P_AC)
        conv.Node_DC.Pconv = conv.P_DC
        conv.Node_DC.conv_loading = max(
            np.sqrt(conv.P_AC ** 2 + conv.Q_AC ** 2),
            abs(conv.P_DC),
        )


def _normalize_pyflow_scalar_state(grid) -> None:
    for node in getattr(grid, "nodes_AC", []):
        node.P_s = np.float64(node.P_s)
        node.Q_s = np.float64(node.Q_s)
        node.Q_s_fx = np.float64(node.Q_s_fx)


def _recompute_ac_injections(grid) -> None:
    if not grid.nodes_AC:
        grid.P_AC_INJ = np.array([])
        grid.Q_INJ = np.array([])
        return

    V = np.array([node.V for node in grid.nodes_AC], dtype=float)
    angles = np.array([node.theta for node in grid.nodes_AC], dtype=float)
    G = np.real(grid.Ybus_AC_full)
    B = np.imag(grid.Ybus_AC_full)
    angle_diff = angles[:, None] - angles[None, :]

    P = V[:, None] * V[None, :] * (G * np.cos(angle_diff) + B * np.sin(angle_diff))
    Q = V[:, None] * V[None, :] * (G * np.sin(angle_diff) - B * np.cos(angle_diff))

    P = P.sum(axis=1)
    Q = Q.sum(axis=1)

    for idx, node in enumerate(grid.nodes_AC):
        node.P_INJ = float(P[idx])
        node.Q_INJ = float(Q[idx])

    grid.P_AC_INJ = np.vstack([node.P_INJ for node in grid.nodes_AC])
    grid.Q_INJ = np.vstack([node.Q_INJ for node in grid.nodes_AC])


def _recompute_dc_injections(grid) -> None:
    if not grid.nodes_DC:
        grid.P_DC_INJ = np.array([])
        return

    V = np.array([node.V for node in grid.nodes_DC], dtype=float)
    Pf = np.zeros((grid.nn_DC, 1))

    for node in grid.nodes_DC:
        i = node.nodeNumber
        for k in range(grid.nn_DC):
            Y = grid.Ybus_DC[i, k]
            if i == k or Y == 0:
                continue
            line = grid.get_lineDC_by_nodes(i, k)
            pol = line.pol
            G = 1.0 / line.R
            Pf[i] += pol * V[i] * (V[i] - V[k]) * G

    for idx, node in enumerate(grid.nodes_DC):
        node.P_INJ = float(Pf[idx].item())

    grid.P_DC_INJ = np.vstack([node.P_INJ for node in grid.nodes_DC])
