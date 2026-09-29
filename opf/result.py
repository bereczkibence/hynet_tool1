from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from acdcpf_opf.powerflow.result import PFResult

from .constraints import ConstraintViolation


@dataclass(frozen=True)
class DecisionVariableChange:
    """Before/after values for one OPF decision variable."""

    name: str
    before_value: float
    after_value: float
    delta_value: float
    unit: str = "p.u."
    before_physical: float | None = None
    after_physical: float | None = None
    delta_physical: float | None = None
    physical_unit: str | None = None

    @property
    def display_unit(self) -> str:
        return self.physical_unit or self.unit

    @property
    def display_before(self) -> float:
        return self.before_physical if self.before_physical is not None else self.before_value

    @property
    def display_after(self) -> float:
        return self.after_physical if self.after_physical is not None else self.after_value

    @property
    def display_delta(self) -> float:
        return self.delta_physical if self.delta_physical is not None else self.delta_value


@dataclass
class OPFResult:
    """Structured result returned by the OPF solver."""

    success: bool
    objective_value: float
    decision_variables: dict[str, float]
    pf_result: PFResult | None
    decision_variable_changes: list[DecisionVariableChange] = field(default_factory=list)
    constraint_violations: list[ConstraintViolation] = field(default_factory=list)
    total_active_losses: float | None = None
    ac_branch_active_losses: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    dc_branch_active_losses: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    converter_active_losses: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    iterations: int | None = None
    optimizer_status: Any = None
    message: str = ""
    raw_optimizer_result: Any = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def decision_variable_change_table(self) -> str:
        """Format before/after decision-variable values for console output."""
        if not self.decision_variable_changes:
            return "No decision variables were enabled."

        header = f"{'Variable':<28} {'Before':>14} {'After':>14} {'Change':>14}"
        lines = [header, "-" * len(header)]
        for change in self.decision_variable_changes:
            unit = change.display_unit
            lines.append(
                f"{change.name:<28} "
                f"{change.display_before:>10.3f} {unit:<4} "
                f"{change.display_after:>10.3f} {unit:<4} "
                f"{change.display_delta:>+10.3f} {unit:<4}"
            )
        return "\n".join(lines)
