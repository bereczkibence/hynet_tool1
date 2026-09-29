from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def missing_columns(table: pd.DataFrame, columns: Iterable[str]) -> list[str]:
    """Return required columns that are absent from a result table."""
    if table is None:
        return list(columns)
    return [column for column in columns if column not in table.columns]


def column_as_array(table: pd.DataFrame, column: str) -> np.ndarray:
    """Return a DataFrame column as a float array, or an empty array."""
    if table is None or table.empty or column not in table.columns:
        return np.array([], dtype=float)
    return table[column].astype(float).to_numpy(copy=True)


def columns_as_array(table: pd.DataFrame, columns: list[str]) -> np.ndarray:
    """Return multiple DataFrame columns as a float array, or an empty matrix."""
    if table is None or table.empty or missing_columns(table, columns):
        return np.empty((0, len(columns)), dtype=float)
    return table[columns].astype(float).to_numpy(copy=True)


def converter_station_active_losses(table: pd.DataFrame) -> np.ndarray:
    """Return full VSC station active losses when terminal powers are available.

    ACDCPF stores ``res_vsc.p_loss_mw`` as the electronic converter loss after
    the detailed converter solve. For OPF benchmarking, the comparable station
    loss is the AC terminal absorption minus the DC terminal injection, which
    also includes the converter transformer and phase-reactor losses.
    """
    terminal_powers = columns_as_array(table, ["p_ac_mw", "p_dc_mw"])
    if terminal_powers.size:
        losses = terminal_powers[:, 0] - terminal_powers[:, 1]
        if np.all(np.isfinite(losses)):
            return losses
    return column_as_array(table, "p_loss_mw")


def sum_column(table: pd.DataFrame, column: str) -> float:
    """Return the finite sum of a result column, treating missing data as zero."""
    values = column_as_array(table, column)
    if values.size == 0:
        return 0.0
    return float(np.sum(values))
