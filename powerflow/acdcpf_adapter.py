from __future__ import annotations

import copy
import logging
import warnings
from typing import Any

import numpy as np

from .base import PowerFlowSolver
from .result import PFResult
from .validation import column_as_array, columns_as_array, converter_station_active_losses, sum_column

logger = logging.getLogger(__name__)


class ACDCPFAdapter(PowerFlowSolver):
    """Power-flow backend adapter for the local ``acdcpf`` solver.

    The adapter accepts a pyflow ACDC grid, delegates translation and solving to
    ``acdcpf_pyflow_backend``, then maps the solver result tables into
    :class:`PFResult`. By default the input case is deep-copied and is not
    modified in place.
    """

    name = "acdcpf"

    def __init__(
        self,
        *,
        max_iter_outer: int = 30,
        max_iter_inner: int = 30,
        tolerance: float = 1e-8,
        verbose: bool = False,
    ) -> None:
        self.max_iter_outer = max_iter_outer
        self.max_iter_inner = max_iter_inner
        self.tolerance = tolerance
        self.verbose = verbose

    def solve(
        self,
        case: Any,
        *,
        copy_case: bool = True,
        write_back: bool = False,
    ) -> PFResult:
        """Run ACDCPF on a pyflow-compatible case.

        Parameters
        ----------
        case:
            pyflow ACDC grid-like object.
        copy_case:
            If true, solve a deep copy so trial OPF variables do not mutate the
            caller's case.
        write_back:
            If true, write solved values into the grid passed to the backend.
            With ``copy_case=True`` the write-back is confined to the copy.
        """
        try:
            from acdcpf_pyflow_backend.runner import run_acdcpf_pf_on_pyflow

            grid = copy.deepcopy(case) if copy_case else case
            with warnings.catch_warnings():
                # ACDCPF currently triggers this pandas warning while building
                # small result tables. It is noisy during OPF iterations and
                # does not indicate a numerical problem.
                warnings.filterwarnings(
                    "ignore",
                    category=FutureWarning,
                    message="The behavior of DataFrame concatenation with empty or all-NA entries is deprecated.*",
                    module=r"acdcpf\.create\.ac",
                )
                backend_result = run_acdcpf_pf_on_pyflow(
                    grid,
                    write_back=write_back,
                    verbose=self.verbose,
                    max_iter_outer=self.max_iter_outer,
                    max_iter_inner=self.max_iter_inner,
                    tol=self.tolerance,
                )
        except Exception as exc:  # pragma: no cover - exercised through failure tests with fake solvers
            logger.exception("ACDCPF power flow failed before producing a result.")
            return PFResult(
                converged=False,
                message=f"ACDCPF solve failed: {exc}",
                raw_result=exc,
                diagnostics={"exception_type": type(exc).__name__},
            )

        pf_result = self._to_pf_result(backend_result)
        if not pf_result.converged:
            logger.warning("ACDCPF did not converge: %s", pf_result.message)
        return pf_result

    def _to_pf_result(self, backend_result: Any) -> PFResult:
        ac_bus = backend_result.ac_bus
        ac_line = backend_result.ac_line
        dc_bus = backend_result.dc_bus
        dc_line = backend_result.dc_line
        vsc = backend_result.vsc
        dcdc = backend_result.dcdc

        ac_losses = column_as_array(ac_line, "p_loss_mw")
        dc_losses = column_as_array(dc_line, "p_loss_mw")
        converter_losses = converter_station_active_losses(vsc)
        dcdc_losses = column_as_array(dcdc, "p_loss_mw")

        additional_losses: dict[str, np.ndarray] = {}
        if dcdc_losses.size:
            additional_losses["dcdc_converter_mw"] = dcdc_losses

        total_active_losses = (
            float(np.sum(ac_losses))
            + float(np.sum(dc_losses))
            + float(np.sum(converter_losses))
            + sum(float(np.sum(values)) for values in additional_losses.values())
        )

        total_reactive_losses = None
        if ac_line is not None and "q_loss_mvar" in ac_line.columns:
            total_reactive_losses = sum_column(ac_line, "q_loss_mvar")

        raw_net = getattr(backend_result, "acdcpf_net", None)
        diagnostics = {
            "power_unit": "MW",
            "reactive_power_unit": "MVAr",
            "voltage_unit": "p.u.",
            "angle_unit": "radian",
            "missing_outputs": [
                "ACDCPF does not expose a public solved generator/slack result table.",
                "ACDCPF does not expose final max power mismatch.",
                "ACDCPF does not expose public outer/inner iteration counts.",
            ],
        }

        message = "ACDCPF converged." if backend_result.converged else "ACDCPF did not converge."

        return PFResult(
            converged=bool(backend_result.converged),
            voltage_magnitude=column_as_array(ac_bus, "v_pu"),
            voltage_angle=np.radians(column_as_array(ac_bus, "v_angle_deg")),
            dc_voltage_magnitude=column_as_array(dc_bus, "v_dc_pu"),
            active_power_flow=columns_as_array(ac_line, ["p_from_mw", "p_to_mw"]),
            reactive_power_flow=columns_as_array(ac_line, ["q_from_mvar", "q_to_mvar"]),
            dc_active_power_flow=columns_as_array(dc_line, ["p_from_mw", "p_to_mw"]),
            generator_active_power=None,
            generator_reactive_power=None,
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
            iterations=getattr(raw_net, "iterations", None),
            max_mismatch=getattr(raw_net, "max_mismatch", None),
            message=message,
            raw_result=backend_result,
            diagnostics=diagnostics,
        )
