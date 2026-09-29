from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .result import PFResult


class PowerFlowSolver(ABC):
    """Common interface for power-flow backends used by the OPF layer."""

    name: str = "powerflow"

    @abstractmethod
    def solve(
        self,
        case: Any,
        *,
        copy_case: bool = True,
        write_back: bool = False,
    ) -> PFResult:
        """Run a power flow for *case* and return a standardized result."""
