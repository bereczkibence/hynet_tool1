from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class BackendMapping:
    """Index alignment between pyflow objects and acdcpf result tables."""

    ac_bus_by_pyflow_index: dict[int, int]
    dc_bus_by_pyflow_index: dict[int, int]
    ac_line_by_pyflow_index: dict[int, int]
    dc_line_by_pyflow_index: dict[int, int]
    vsc_by_pyflow_index: dict[int, int]


@dataclass
class AcdcpfBackendResult:
    """Normalized result container returned by the adapter backend."""

    converged: bool
    ac_bus: pd.DataFrame
    ac_line: pd.DataFrame
    dc_bus: pd.DataFrame
    dc_line: pd.DataFrame
    vsc: pd.DataFrame
    dcdc: pd.DataFrame
    mapping: BackendMapping
    acdcpf_net: Any
