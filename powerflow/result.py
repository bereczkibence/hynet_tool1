from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


def _empty_array() -> np.ndarray:
    return np.array([], dtype=float)


@dataclass
class PFResult:
    """Standardized power-flow result used by OPF code.

    Power quantities are in MW/MVAr unless explicitly noted. Voltage
    magnitudes are per-unit. AC voltage angles are stored in radians.
    """

    converged: bool
    voltage_magnitude: np.ndarray = field(default_factory=_empty_array)
    voltage_angle: np.ndarray = field(default_factory=_empty_array)
    dc_voltage_magnitude: np.ndarray = field(default_factory=_empty_array)
    active_power_flow: np.ndarray = field(default_factory=_empty_array)
    reactive_power_flow: np.ndarray = field(default_factory=_empty_array)
    dc_active_power_flow: np.ndarray = field(default_factory=_empty_array)
    generator_active_power: np.ndarray | None = None
    generator_reactive_power: np.ndarray | None = None
    converter_active_power_ac: np.ndarray = field(default_factory=_empty_array)
    converter_reactive_power_ac: np.ndarray = field(default_factory=_empty_array)
    converter_active_power_dc: np.ndarray = field(default_factory=_empty_array)
    converter_internal_voltage: np.ndarray = field(default_factory=_empty_array)
    converter_ac_current: np.ndarray = field(default_factory=_empty_array)
    ac_branch_active_losses: np.ndarray = field(default_factory=_empty_array)
    dc_branch_active_losses: np.ndarray = field(default_factory=_empty_array)
    converter_active_losses: np.ndarray = field(default_factory=_empty_array)
    additional_active_losses: dict[str, np.ndarray] = field(default_factory=dict)
    ac_branch_loading_percent: np.ndarray = field(default_factory=_empty_array)
    total_active_losses: float = 0.0
    total_reactive_losses: float | None = None
    iterations: int | None = None
    max_mismatch: float | None = None
    message: str = ""
    raw_result: Any = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def active_loss_breakdown(self) -> dict[str, float]:
        """Return the explicit active-loss components included in the total."""
        breakdown = {
            "ac_branch_mw": float(np.sum(self.ac_branch_active_losses)),
            "dc_branch_mw": float(np.sum(self.dc_branch_active_losses)),
            "converter_mw": float(np.sum(self.converter_active_losses)),
        }
        for name, values in self.additional_active_losses.items():
            breakdown[name] = float(np.sum(values))
        return breakdown
