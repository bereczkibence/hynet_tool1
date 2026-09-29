from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
from typing import Any, Iterable

import pandas as pd


SUPPORTED_ELEMENT_TYPES = ("ac_load", "dc_load", "dc_gen")
PROFILE_FORMATS = ("auto", "csv", "xlsx")


@dataclass(frozen=True)
class ProfileChange:
    """One profile modification for one grid element at one timestamp."""

    element_type: str
    element_id: int | None = None
    element_name: str | None = None
    scale: float | None = None
    p_scale: float | None = None
    q_scale: float | None = None
    p_mw: float | None = None
    q_mvar: float | None = None


@dataclass(frozen=True)
class ProfileSnapshot:
    """All profile changes applied to one steady-state timestamp."""

    time_index: int
    timestamp: str
    changes: tuple[ProfileChange, ...]


def load_time_profile(
    path: str | Path,
    *,
    sheet_name: str | int | None = None,
    profile_format: str = "auto",
) -> tuple[ProfileSnapshot, ...]:
    """Load a long-format CSV/XLSX profile into ordered steady-state snapshots."""

    profile_path = Path(path)
    resolved_format = _resolve_profile_format(profile_path, profile_format)
    if resolved_format == "csv":
        frame = pd.read_csv(profile_path, sep=None, engine="python")
    else:
        try:
            frame = pd.read_excel(profile_path, sheet_name=0 if sheet_name is None else sheet_name)
        except ImportError as exc:  # pragma: no cover - depends on local optional dependency
            raise ValueError(
                "Reading XLSX profile files requires openpyxl. Install it with "
                "`pip install openpyxl` or use CSV profiles."
            ) from exc

    return profile_snapshots_from_frame(frame)


def profile_snapshots_from_frame(frame: pd.DataFrame) -> tuple[ProfileSnapshot, ...]:
    """Validate and convert a profile dataframe into profile snapshots."""

    if frame.empty:
        raise ValueError("Profile input is empty.")

    normalized = frame.rename(columns={column: _normalize_column_name(column) for column in frame.columns})
    _require_columns(normalized, ("time_index", "element_type"))
    if "element_id" not in normalized.columns and "element_name" not in normalized.columns:
        raise ValueError("Profile input must contain either `element_id` or `element_name`.")

    snapshots: dict[int, list[ProfileChange]] = {}
    timestamps: dict[int, str] = {}
    seen_targets: set[tuple[int, str, str]] = set()

    for row_number, row in normalized.iterrows():
        excel_row_number = int(row_number) + 2
        time_index = _required_int(row.get("time_index"), "time_index", excel_row_number)
        element_type = _normalize_element_type(row.get("element_type"), excel_row_number)
        element_id = _optional_int(row.get("element_id"), "element_id", excel_row_number)
        element_name = _optional_string(row.get("element_name"))
        if element_id is None and element_name is None:
            raise ValueError(
                f"Profile row {excel_row_number} must provide either `element_id` or `element_name`."
            )
        if element_id is not None and element_name is not None:
            raise ValueError(
                f"Profile row {excel_row_number} is ambiguous: use either `element_id` "
                "or `element_name`, not both."
            )

        change = ProfileChange(
            element_type=element_type,
            element_id=element_id,
            element_name=element_name,
            scale=_optional_float(row.get("scale"), "scale", excel_row_number),
            p_scale=_optional_float(row.get("p_scale"), "p_scale", excel_row_number),
            q_scale=_optional_float(row.get("q_scale"), "q_scale", excel_row_number),
            p_mw=_optional_float(row.get("p_mw"), "p_mw", excel_row_number),
            q_mvar=_optional_float(row.get("q_mvar"), "q_mvar", excel_row_number),
        )
        _validate_profile_change(change, excel_row_number)

        target_key = str(element_id) if element_id is not None else f"name:{element_name}"
        duplicate_key = (time_index, element_type, target_key)
        if duplicate_key in seen_targets:
            raise ValueError(
                f"Profile row {excel_row_number} duplicates a previous row for "
                f"{element_type} {target_key} at time_index {time_index}."
            )
        seen_targets.add(duplicate_key)

        timestamp = _profile_timestamp(row.get("timestamp"), time_index)
        existing_timestamp = timestamps.setdefault(time_index, timestamp)
        if existing_timestamp != timestamp:
            raise ValueError(
                f"Profile rows for time_index {time_index} use conflicting timestamps: "
                f"{existing_timestamp!r} and {timestamp!r}."
            )
        snapshots.setdefault(time_index, []).append(change)

    return tuple(
        ProfileSnapshot(
            time_index=time_index,
            timestamp=timestamps[time_index],
            changes=tuple(changes),
        )
        for time_index, changes in sorted(snapshots.items())
    )


def apply_profile_snapshot(net: Any, snapshot: ProfileSnapshot) -> None:
    """Apply one snapshot profile to an ACDCPF network in-place."""

    for change in snapshot.changes:
        table_name = _table_name_for_element_type(change.element_type)
        table = getattr(net, table_name, None)
        if table is None or getattr(table, "empty", True):
            raise ValueError(f"Network has no active `{table_name}` table for profile changes.")

        element_index = _match_element_index(table, change)
        row = table.loc[element_index]
        if not bool(row.get("in_service", True)):
            raise ValueError(
                f"Profile target {change.element_type} {element_index} is out of service."
            )

        if change.element_type == "ac_load":
            _apply_active_power_change(table, element_index, change, column="p_mw")
            _apply_reactive_power_change(table, element_index, change, column="q_mvar")
        elif change.element_type in {"dc_load", "dc_gen"}:
            _apply_active_power_change(table, element_index, change, column="p_mw")
        else:  # pragma: no cover - guarded by parser validation
            raise ValueError(f"Unsupported profile element_type `{change.element_type}`.")


def _resolve_profile_format(path: Path, profile_format: str) -> str:
    normalized = str(profile_format).lower()
    if normalized not in PROFILE_FORMATS:
        raise ValueError(
            f"Unsupported profile format `{profile_format}`. Use one of: {', '.join(PROFILE_FORMATS)}."
        )
    if normalized != "auto":
        return normalized
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return "xlsx"
    raise ValueError(
        f"Could not infer profile format from `{path}`. Use --profile-format csv or xlsx."
    )


def _normalize_column_name(column: Any) -> str:
    return str(column).strip().lower().replace(" ", "_").replace("-", "_")


def _require_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"Profile input is missing required columns: {', '.join(missing)}.")


def _normalize_element_type(value: Any, row_number: int) -> str:
    raw = _optional_string(value)
    if raw is None:
        raise ValueError(f"Profile row {row_number} has empty `element_type`.")
    normalized = raw.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "load": "ac_load",
        "acload": "ac_load",
        "res_load": "ac_load",
        "dcload": "dc_load",
        "res_dc_load": "dc_load",
        "dc_generator": "dc_gen",
        "dcgen": "dc_gen",
        "res_dc_gen": "dc_gen",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in SUPPORTED_ELEMENT_TYPES:
        raise ValueError(
            f"Profile row {row_number} has unsupported element_type `{raw}`. "
            f"Supported values: {', '.join(SUPPORTED_ELEMENT_TYPES)}."
        )
    return normalized


def _validate_profile_change(change: ProfileChange, row_number: int) -> None:
    has_any_modifier = any(
        value is not None
        for value in (change.scale, change.p_scale, change.q_scale, change.p_mw, change.q_mvar)
    )
    if not has_any_modifier:
        raise ValueError(f"Profile row {row_number} does not define any scale or absolute value.")

    if change.scale is not None and any(
        value is not None for value in (change.p_scale, change.q_scale, change.p_mw, change.q_mvar)
    ):
        raise ValueError(
            f"Profile row {row_number} is ambiguous: `scale` cannot be combined with "
            "`p_scale`, `q_scale`, `p_mw`, or `q_mvar`."
        )
    if change.p_scale is not None and change.p_mw is not None:
        raise ValueError(
            f"Profile row {row_number} is ambiguous: use either `p_scale` or `p_mw`."
        )
    if change.q_scale is not None and change.q_mvar is not None:
        raise ValueError(
            f"Profile row {row_number} is ambiguous: use either `q_scale` or `q_mvar`."
        )
    if change.element_type in {"dc_load", "dc_gen"} and (
        change.q_scale is not None or change.q_mvar is not None
    ):
        raise ValueError(
            f"Profile row {row_number} targets `{change.element_type}`, which has no reactive-power profile in v1."
        )


def _match_element_index(table: pd.DataFrame, change: ProfileChange) -> Any:
    if change.element_id is not None:
        if change.element_id not in table.index:
            raise ValueError(
                f"Unknown {change.element_type} element_id {change.element_id} in profile."
            )
        return change.element_id

    if "name" not in table.columns:
        raise ValueError(
            f"Cannot match {change.element_type} by name because the network table has no `name` column."
        )
    matches = [idx for idx, name in table["name"].items() if str(name) == str(change.element_name)]
    if not matches:
        raise ValueError(
            f"Unknown {change.element_type} element_name {change.element_name!r} in profile."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Profile element_name {change.element_name!r} matches multiple {change.element_type} rows."
        )
    return matches[0]


def _table_name_for_element_type(element_type: str) -> str:
    return {
        "ac_load": "ac_load",
        "dc_load": "dc_load",
        "dc_gen": "dc_gen",
    }[element_type]


def _apply_active_power_change(
    table: pd.DataFrame,
    element_index: Any,
    change: ProfileChange,
    *,
    column: str,
) -> None:
    base_value = _finite_table_value(table, element_index, column)
    if change.scale is not None:
        table.at[element_index, column] = base_value * change.scale
        return
    if change.p_mw is not None:
        table.at[element_index, column] = change.p_mw
        return
    if change.p_scale is not None:
        table.at[element_index, column] = base_value * change.p_scale


def _apply_reactive_power_change(
    table: pd.DataFrame,
    element_index: Any,
    change: ProfileChange,
    *,
    column: str,
) -> None:
    base_value = _finite_table_value(table, element_index, column)
    if change.scale is not None:
        table.at[element_index, column] = base_value * change.scale
        return
    if change.q_mvar is not None:
        table.at[element_index, column] = change.q_mvar
        return
    if change.q_scale is not None:
        table.at[element_index, column] = base_value * change.q_scale


def _finite_table_value(table: pd.DataFrame, element_index: Any, column: str) -> float:
    if column not in table.columns:
        raise ValueError(f"Profile target table has no `{column}` column.")
    value = _optional_float(table.at[element_index, column], column, row_number=0)
    return 0.0 if value is None else value


def _profile_timestamp(value: Any, time_index: int) -> str:
    if _is_missing(value):
        return str(time_index)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value).strip()


def _required_int(value: Any, column: str, row_number: int) -> int:
    number = _optional_int(value, column, row_number)
    if number is None:
        raise ValueError(f"Profile row {row_number} has empty `{column}`.")
    return number


def _optional_int(value: Any, column: str, row_number: int) -> int | None:
    number = _optional_float(value, column, row_number)
    if number is None:
        return None
    if abs(number - round(number)) > 1e-9:
        raise ValueError(f"Profile row {row_number} column `{column}` must be an integer.")
    return int(round(number))


def _optional_float(value: Any, column: str, row_number: int) -> float | None:
    if _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        location = f"row {row_number} " if row_number else ""
        raise ValueError(f"Profile {location}column `{column}` must be numeric.") from exc
    if not math.isfinite(number):
        location = f"row {row_number} " if row_number else ""
        raise ValueError(f"Profile {location}column `{column}` must be finite.")
    return number


def _optional_string(value: Any) -> str | None:
    if _is_missing(value):
        return None
    text = str(value).strip()
    return text or None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False

