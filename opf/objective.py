from __future__ import annotations

from dataclasses import dataclass, field
import logging

import numpy as np

from acdcpf_opf.powerflow.result import PFResult

from .config import OPFConfig

logger = logging.getLogger(__name__)


@dataclass
class ObjectiveEvaluation:
    """Objective value and diagnostics for one PF-backed OPF candidate."""

    value: float
    pf_result: PFResult
    loss_breakdown_mw: dict[str, float] = field(default_factory=dict)
    message: str = ""


def compute_loss_breakdown(pf_result: PFResult) -> dict[str, float]:
    """Compute active loss components explicitly from the PF result."""
    return pf_result.active_loss_breakdown()


def compute_total_active_losses(pf_result: PFResult) -> float:
    """Return total active losses in MW from explicit component sums.

    The total includes AC branch losses, DC branch losses, VSC converter
    losses, and any additional active loss arrays exposed by the PF adapter.
    Missing components are not invented.
    """
    return float(sum(compute_loss_breakdown(pf_result).values()))


def total_active_loss_objective(
    pf_result: PFResult,
    config: OPFConfig,
) -> ObjectiveEvaluation:
    """Evaluate total active power loss minimization for a solved PF result."""
    if not pf_result.converged:
        return ObjectiveEvaluation(
            value=float(config.penalty_value),
            pf_result=pf_result,
            loss_breakdown_mw=compute_loss_breakdown(pf_result),
            message=f"PF did not converge: {pf_result.message}",
        )

    total_losses = compute_total_active_losses(pf_result)
    if not np.isfinite(total_losses):
        return ObjectiveEvaluation(
            value=float(config.penalty_value),
            pf_result=pf_result,
            loss_breakdown_mw=compute_loss_breakdown(pf_result),
            message="PF converged but active loss total is not finite.",
        )

    logger.debug("Total active losses: %.9g MW", total_losses)
    return ObjectiveEvaluation(
        value=total_losses,
        pf_result=pf_result,
        loss_breakdown_mw=compute_loss_breakdown(pf_result),
        message="ok",
    )
