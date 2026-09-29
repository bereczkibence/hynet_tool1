from __future__ import annotations

import copy
import logging
from typing import Any

import numpy as np

from .base import PowerFlowSolver
from .result import PFResult
from .validation import column_as_array, columns_as_array, converter_station_active_losses, sum_column
from acdcpf_pyflow_backend._bootstrap import ensure_acdcpf_importable

logger = logging.getLogger(__name__)


class ACDCPFNetworkAdapter(PowerFlowSolver):
    """Power-flow adapter for a native ``acdcpf.Network`` instance.

    This adapter is the direct integration point for the ACDCPF package. It
    does not translate through PyFlow; the input case is expected
    to expose the native DataFrame-based ``acdcpf.Network`` schema.
    """

    name = "acdcpf-network"

    def __init__(
        self,
        *,
        max_iter_outer: int = 30,
        max_iter_inner: int = 30,
        tolerance: float = 1e-8,
        verbose: bool = False,
        policy: str = "unconstrained",
        enforce_slack_q_limits: bool = False,
    ) -> None:
        self.max_iter_outer = max_iter_outer
        self.max_iter_inner = max_iter_inner
        self.tolerance = tolerance
        self.verbose = verbose
        self.policy = policy
        self.enforce_slack_q_limits = enforce_slack_q_limits

    def solve(
        self,
        case: Any,
        *,
        copy_case: bool = True,
        write_back: bool = False,
    ) -> PFResult:
        """Run power flow on a native ``acdcpf.Network``.

        With ``copy_case=True`` the network is deep-copied before solving. If
        ``write_back`` is also true, solved result tables and internal PF state
        are copied back to the original network after the run.
        """
        try:
            ensure_acdcpf_importable()
            import acdcpf as pf

            solved_case = copy.deepcopy(case) if copy_case else case
            from .tool5_contract import prepare_converter_limits
            prepare_converter_limits(solved_case)
            result = pf.solve(
                solved_case,
                copy_network=False,
                options=pf.PFOptions(policy=getattr(solved_case, "tool5_policy", self.policy),
                    enforce_slack_q_limits=self.enforce_slack_q_limits,
                    max_iter_outer=self.max_iter_outer, max_iter_inner=self.max_iter_inner,
                    tolerance=self.tolerance, verbose=self.verbose),
            )
            solved_case.converged = result.converged

            if copy_case and write_back:
                _copy_solution_state(source=solved_case, target=case)
        except Exception as exc:  # pragma: no cover - exercised through integration failures
            logger.exception("Native ACDCPF power flow failed before producing a result.")
            return PFResult(
                converged=False,
                message=f"Native ACDCPF solve failed: {exc}",
                raw_result=exc,
                diagnostics={
                    "backend": self.name,
                    "exception_type": type(exc).__name__,
                },
            )

        pf_result = self._to_pf_result(solved_case)
        if not pf_result.converged:
            logger.warning("Native ACDCPF did not converge: %s", pf_result.message)
        return pf_result

    def _to_pf_result(self, net: Any) -> PFResult:
        ac_bus = getattr(net, "res_ac_bus", None)
        ac_line = getattr(net, "res_ac_line", None)
        trafo = getattr(net, "res_trafo", None)
        dc_bus = getattr(net, "res_dc_bus", None)
        dc_line = getattr(net, "res_dc_line", None)
        dc_gen = getattr(net, "res_dc_gen", None)
        vsc = getattr(net, "res_vsc", None)
        dcdc = getattr(net, "res_dcdc", None)
        generator_active_power, generator_reactive_power = _generator_power_arrays(net)

        ac_losses = column_as_array(ac_line, "p_loss_mw")
        transformer_losses = column_as_array(trafo, "p_loss_mw")
        dc_losses = column_as_array(dc_line, "p_loss_mw")
        converter_losses = converter_station_active_losses(vsc)
        dcdc_losses = column_as_array(dcdc, "p_loss_mw")

        additional_losses: dict[str, np.ndarray] = {}
        if transformer_losses.size:
            additional_losses["transformer_mw"] = transformer_losses
        if dcdc_losses.size:
            additional_losses["dcdc_converter_mw"] = dcdc_losses

        total_active_losses = (
            float(np.sum(ac_losses))
            + float(np.sum(dc_losses))
            + float(np.sum(converter_losses))
            + sum(float(np.sum(values)) for values in additional_losses.values())
        )

        total_reactive_losses = None
        reactive_parts = []
        if ac_line is not None and "q_loss_mvar" in ac_line.columns:
            reactive_parts.append(sum_column(ac_line, "q_loss_mvar"))
        if trafo is not None and "q_loss_mvar" in trafo.columns:
            reactive_parts.append(sum_column(trafo, "q_loss_mvar"))
        if reactive_parts:
            total_reactive_losses = float(sum(reactive_parts))

        converged = bool(getattr(net, "converged", False))
        message = "Native ACDCPF converged." if converged else "Native ACDCPF did not converge."

        warnings = getattr(net, "_tool5_report", {}).get("warnings", [])
        if warnings:
            message += " Warnings: " + " ".join(warnings)

        return PFResult(
            converged=converged,
            voltage_magnitude=column_as_array(ac_bus, "v_pu"),
            voltage_angle=np.radians(column_as_array(ac_bus, "v_angle_deg")),
            dc_voltage_magnitude=column_as_array(dc_bus, "v_dc_pu"),
            active_power_flow=columns_as_array(ac_line, ["p_from_mw", "p_to_mw"]),
            reactive_power_flow=columns_as_array(ac_line, ["q_from_mvar", "q_to_mvar"]),
            dc_active_power_flow=columns_as_array(dc_line, ["p_from_mw", "p_to_mw"]),
            generator_active_power=generator_active_power,
            generator_reactive_power=generator_reactive_power,
            converter_active_power_ac=column_as_array(vsc, "p_ac_mw"),
            converter_reactive_power_ac=column_as_array(vsc, "q_ac_mvar"),
            converter_active_power_dc=column_as_array(vsc, "p_dc_mw"),
            converter_internal_voltage=column_as_array(vsc, "v_converter_pu"),
            converter_ac_current=column_as_array(vsc, "i_ac_ka"),
            ac_branch_active_losses=ac_losses,
            dc_branch_active_losses=dc_losses,
            converter_active_losses=converter_losses,
            additional_active_losses=additional_losses,
            ac_branch_loading_percent=column_as_array(ac_line, "loading_percent"),
            total_active_losses=total_active_losses,
            total_reactive_losses=total_reactive_losses,
            iterations=getattr(net, "iterations", None),
            max_mismatch=getattr(net, "max_mismatch", None),
            message=message,
            raw_result=net,
            diagnostics={
                "tool5": getattr(net, "_tool5_report", {}),
                "backend": self.name,
                "source": "acdcpf.Network",
                "power_unit": "MW",
                "reactive_power_unit": "MVAr",
                "voltage_unit": "p.u.",
                "angle_unit": "radian",
                "dc_power_sign": "res_vsc.p_dc_mw is positive when injected into the DC grid.",
                "dc_gen_result_available": dc_gen is not None and not dc_gen.empty,
            },
        )


def _generator_power_arrays(net: Any) -> tuple[np.ndarray, np.ndarray]:
    """Return solved AC generator P/Q values in native generator order.

    Some ACDCPF versions do not populate ``res_ac_gen``. In that case the
    solved PyPOWER matrices are still available in ``net._ppc_results``; use
    those so OPF initialization and fixed-generator controls see the actual PF
    dispatch rather than the static input setpoints.
    """

    res_ac_gen = getattr(net, "res_ac_gen", None)
    p_values = column_as_array(res_ac_gen, "p_mw")
    q_values = column_as_array(res_ac_gen, "q_mvar")
    if p_values.size or q_values.size:
        return p_values, q_values

    ac_gen = getattr(net, "ac_gen", None)
    ppc_results = getattr(net, "_ppc_results", None)
    if ac_gen is None or getattr(ac_gen, "empty", True) or not ppc_results:
        return p_values, q_values

    active_indices = [
        idx
        for idx, row in ac_gen.iterrows()
        if bool(row.get("in_service", True))
    ]
    solved_p: list[float] = []
    solved_q: list[float] = []
    static_pos = 0
    for ppc_result in ppc_results.values():
        gen_matrix = ppc_result.get("gen") if isinstance(ppc_result, dict) else None
        if gen_matrix is None:
            continue
        for result_pos in range(len(gen_matrix)):
            if static_pos >= len(active_indices):
                return np.asarray(solved_p, dtype=float), np.asarray(solved_q, dtype=float)
            gen_row = gen_matrix[result_pos]
            solved_p.append(float(gen_row[1]))
            solved_q.append(float(gen_row[2]))
            static_pos += 1
    return np.asarray(solved_p, dtype=float), np.asarray(solved_q, dtype=float)


def _copy_solution_state(*, source: Any, target: Any) -> None:
    """Copy solved result tables and solver state into another network."""
    for attr in (
        "res_ac_bus",
        "res_ac_line",
        "res_trafo",
        "res_ac_gen",
        "res_dc_bus",
        "res_dc_line",
        "res_dc_gen",
        "res_vsc",
        "res_dcdc",
        "res_storage",
        "converged",
        "iterations",
        "_pf_index_maps",
        "_conv_data",
        "_p_s",
        "_q_s",
        "_p_dc_vsc",
        "_v_mag",
        "_v_ang",
        "_v_dc",
        "_vsc_internal",
        "_ppc_results",
        "_tool5_report",
    ):
        if hasattr(source, attr):
            setattr(target, attr, copy.deepcopy(getattr(source, attr)))
