from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Sequence


@dataclass
class OPFConfig:
    """Configuration for the outer-loop OPF solver."""

    objective: str = "total_active_loss_minimization"
    pf_backend: str = "acdcpf"
    optimizer_method: str = "SLSQP"
    max_iterations: int = 100
    tolerance: float = 1e-6
    mismatch_tolerance: float = 1e-6
    penalty_value: float = 1e9
    constraint_penalty_weight: float = 1e6
    voltage_tolerance: float = 1e-6
    branch_loading_tolerance: float = 1e-6
    debug: bool = False
    logging_level: int | str = logging.INFO

    pf_max_iter_outer: int = 30
    pf_max_iter_inner: int = 30
    pf_tolerance: float = 1e-8

    enforce_constraints: bool = True
    optimize_ac_generator_active_power: bool = False
    optimize_ac_generator_reactive_power: bool = False
    optimize_ac_generator_voltage: bool = False
    optimize_dc_generator_active_power: bool = False
    optimize_converter_active_power: bool = False
    optimize_converter_reactive_power: bool = False

    ac_generator_active_power_indices: Sequence[int] | None = None
    ac_generator_reactive_power_indices: Sequence[int] | None = None
    ac_generator_voltage_node_indices: Sequence[int] | None = None
    dc_generator_active_power_indices: Sequence[int] | None = None
    converter_active_power_indices: Sequence[int] | None = None
    converter_reactive_power_indices: Sequence[int] | None = None
