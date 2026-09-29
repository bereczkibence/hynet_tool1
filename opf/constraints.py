from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

import numpy as np

from acdcpf_opf.powerflow.result import PFResult

from .config import OPFConfig


@dataclass
class ConstraintViolation:
    """One OPF constraint check.

    ``violation`` is a nonnegative magnitude. Unavailable checks are included
    with ``available=False`` so missing solver outputs are visible without
    inventing values.
    """

    name: str
    violation: float
    actual: float | None = None
    limit: float | None = None
    available: bool = True
    message: str = ""

    @property
    def is_violated(self) -> bool:
        return self.available and self.violation > 0.0


def compute_constraint_violations(
    case: Any,
    pf_result: PFResult,
    config: OPFConfig,
) -> list[ConstraintViolation]:
    """Check OPF feasibility conditions available from case and PF outputs."""
    violations: list[ConstraintViolation] = []

    if not pf_result.converged:
        violations.append(
            ConstraintViolation(
                name="pf_convergence",
                violation=1.0,
                actual=0.0,
                limit=1.0,
                message=pf_result.message or "Power flow did not converge.",
            )
        )

    _check_ac_voltage_limits(case, pf_result, config, violations)
    _check_dc_voltage_limits(case, pf_result, config, violations)
    _check_ac_branch_loading_limits(case, pf_result, config, violations)
    _check_dc_branch_loading_limits(case, pf_result, config, violations)
    _check_converter_limits(case, pf_result, config, violations)
    _check_generator_limits(case, config, violations)
    _check_mismatch_limit(pf_result, config, violations)

    return violations


def max_constraint_violation(violations: list[ConstraintViolation]) -> float:
    """Return the largest available violation magnitude."""
    available = [item.violation for item in violations if item.available]
    if not available:
        return 0.0
    return float(max(available))


def violated_constraints(violations: list[ConstraintViolation]) -> list[ConstraintViolation]:
    """Return only constraints with a positive violation."""
    return [item for item in violations if item.is_violated]


def _check_ac_voltage_limits(
    case: Any,
    pf_result: PFResult,
    config: OPFConfig,
    violations: list[ConstraintViolation],
) -> None:
    nodes = getattr(case, "nodes_AC", [])
    if len(nodes) and pf_result.voltage_magnitude.size == 0:
        violations.append(_unavailable("ac_voltage_limits", "AC bus voltages are not available."))
        return

    for idx, node in enumerate(nodes):
        if idx >= pf_result.voltage_magnitude.size:
            violations.append(_unavailable(f"ac_voltage[{idx}]", "Missing AC voltage result row."))
            continue
        _append_bound_violation(
            violations,
            name=f"ac_voltage[{getattr(node, 'name', idx)}]",
            actual=float(pf_result.voltage_magnitude[idx]),
            lower=float(getattr(node, "Umin")),
            upper=float(getattr(node, "Umax")),
            tolerance=config.voltage_tolerance,
        )


def _check_dc_voltage_limits(
    case: Any,
    pf_result: PFResult,
    config: OPFConfig,
    violations: list[ConstraintViolation],
) -> None:
    nodes = getattr(case, "nodes_DC", [])
    if len(nodes) and pf_result.dc_voltage_magnitude.size == 0:
        violations.append(_unavailable("dc_voltage_limits", "DC bus voltages are not available."))
        return

    for idx, node in enumerate(nodes):
        if idx >= pf_result.dc_voltage_magnitude.size:
            violations.append(_unavailable(f"dc_voltage[{idx}]", "Missing DC voltage result row."))
            continue
        _append_bound_violation(
            violations,
            name=f"dc_voltage[{getattr(node, 'name', idx)}]",
            actual=float(pf_result.dc_voltage_magnitude[idx]),
            lower=float(getattr(node, "Umin")),
            upper=float(getattr(node, "Umax")),
            tolerance=config.voltage_tolerance,
        )


def _check_ac_branch_loading_limits(
    case: Any,
    pf_result: PFResult,
    config: OPFConfig,
    violations: list[ConstraintViolation],
) -> None:
    lines = getattr(case, "lines_AC", [])
    if len(lines) and pf_result.ac_branch_loading_percent.size == 0:
        violations.append(
            _unavailable("ac_branch_loading_limits", "AC branch loading_percent is not available.")
        )
        return

    limit = 100.0 + config.branch_loading_tolerance
    for idx, loading in enumerate(pf_result.ac_branch_loading_percent):
        _append_upper_violation(
            violations,
            name=f"ac_branch_loading[{_line_name(lines, idx)}]",
            actual=float(loading),
            upper=limit,
        )


def _check_dc_branch_loading_limits(
    case: Any,
    pf_result: PFResult,
    config: OPFConfig,
    violations: list[ConstraintViolation],
) -> None:
    lines = getattr(case, "lines_DC", [])
    if not lines:
        return
    if pf_result.dc_active_power_flow.size == 0:
        violations.append(
            _unavailable(
                "dc_branch_loading_limits",
                "DC active branch flow results are not available.",
            )
        )
        return

    found_rating = False
    for idx, line in enumerate(lines):
        rating = _valid_rating(getattr(line, "MW_rating", None))
        if rating is None:
            continue
        found_rating = True
        if idx >= pf_result.dc_active_power_flow.shape[0]:
            violations.append(_unavailable(f"dc_branch_loading[{idx}]", "Missing DC line flow row."))
            continue
        flow = pf_result.dc_active_power_flow[idx]
        loading = max(abs(float(flow[0])), abs(float(flow[1]))) / rating * 100.0
        _append_upper_violation(
            violations,
            name=f"dc_branch_loading[{_line_name(lines, idx)}]",
            actual=loading,
            upper=100.0 + config.branch_loading_tolerance,
        )

    if not found_rating:
        violations.append(
            _unavailable(
                "dc_branch_loading_limits",
                "No finite DC branch MW_rating values are available for loading checks.",
            )
        )


def _check_converter_limits(
    case: Any,
    pf_result: PFResult,
    config: OPFConfig,
    violations: list[ConstraintViolation],
) -> None:
    converters = getattr(case, "Converters_ACDC", [])
    if not converters:
        return

    if pf_result.converter_active_power_ac.size == 0:
        violations.append(
            _unavailable("converter_limits", "Converter active/reactive power results are not available.")
        )
        return

    for idx, conv in enumerate(converters):
        s_max = _valid_rating(getattr(conv, "MVA_max", None))
        if s_max is None:
            continue
        if idx >= pf_result.converter_active_power_ac.size:
            violations.append(_unavailable(f"converter[{idx}]", "Missing converter result row."))
            continue

        p_ac = float(pf_result.converter_active_power_ac[idx])
        q_ac = (
            float(pf_result.converter_reactive_power_ac[idx])
            if idx < pf_result.converter_reactive_power_ac.size
            else 0.0
        )
        s_ac = sqrt(p_ac * p_ac + q_ac * q_ac)
        _append_upper_violation(
            violations,
            name=f"converter_ac_mva[{getattr(conv, 'name', idx)}]",
            actual=s_ac,
            upper=s_max,
        )

        if idx < pf_result.converter_active_power_dc.size:
            _append_upper_violation(
                violations,
                name=f"converter_dc_mw[{getattr(conv, 'name', idx)}]",
                actual=abs(float(pf_result.converter_active_power_dc[idx])),
                upper=s_max,
            )

        if idx < pf_result.converter_internal_voltage.size:
            _append_bound_violation(
                violations,
                name=f"converter_internal_voltage[{getattr(conv, 'name', idx)}]",
                actual=float(pf_result.converter_internal_voltage[idx]),
                lower=_valid_bound(getattr(conv, "Ucmin", None)),
                upper=_valid_bound(getattr(conv, "Ucmax", None)),
                tolerance=config.voltage_tolerance,
            )

        current_limit = _converter_current_limit_ka(conv)
        if current_limit is not None and idx < pf_result.converter_ac_current.size:
            _append_upper_violation(
                violations,
                name=f"converter_ac_current[{getattr(conv, 'name', idx)}]",
                actual=float(pf_result.converter_ac_current[idx]),
                upper=current_limit,
            )


def _check_generator_limits(
    case: Any,
    config: OPFConfig,
    violations: list[ConstraintViolation],
) -> None:
    # ACDCPF currently does not expose solved slack-generator dispatch, so this
    # checks the generator setpoints present on the pyflow case. Slack result
    # validation should be added when the PF backend exposes generator results.
    for node, gen in _iter_generators(getattr(case, "nodes_AC", [])):
        _check_one_generator(
            gen,
            config,
            violations,
            prefix="ac_generator",
            node_type=str(getattr(node, "type", "")),
        )
    for node, gen in _iter_generators(getattr(case, "nodes_DC", [])):
        _check_one_generator(
            gen,
            config,
            violations,
            prefix="dc_generator",
            node_type=str(getattr(node, "type", "")),
        )


def _check_one_generator(
    gen: Any,
    config: OPFConfig,
    violations: list[ConstraintViolation],
    *,
    prefix: str,
    node_type: str,
) -> None:
    name = getattr(gen, "name", getattr(gen, "genNumber", "?"))
    if hasattr(gen, "PGen"):
        if prefix == "ac_generator" and node_type == "Slack":
            violations.append(
                _unavailable(
                    f"{prefix}_p[{name}]",
                    "TODO: solved AC slack-generator active power is not exposed by ACDCPF.",
                )
            )
        else:
            _append_bound_violation(
                violations,
                name=f"{prefix}_p[{name}]",
                actual=float(getattr(gen, "PGen")),
                lower=_valid_bound(getattr(gen, "Min_pow_gen", None)),
                upper=_valid_bound(getattr(gen, "Max_pow_gen", None)),
                tolerance=0.0,
            )

    if prefix == "ac_generator" and hasattr(gen, "QGen"):
        if node_type in {"Slack", "PV"}:
            violations.append(
                _unavailable(
                    f"{prefix}_q[{name}]",
                    "TODO: solved AC generator reactive power is not exposed by ACDCPF.",
                )
            )
        else:
            _append_bound_violation(
                violations,
                name=f"{prefix}_q[{name}]",
                actual=float(getattr(gen, "QGen")),
                lower=_valid_bound(getattr(gen, "Min_pow_genR", None)),
                upper=_valid_bound(getattr(gen, "Max_pow_genR", None)),
                tolerance=0.0,
            )


def _check_mismatch_limit(
    pf_result: PFResult,
    config: OPFConfig,
    violations: list[ConstraintViolation],
) -> None:
    if pf_result.max_mismatch is None:
        violations.append(
            _unavailable(
                "power_balance_mismatch",
                "TODO: ACDCPF does not expose a final max mismatch value yet.",
            )
        )
        return
    _append_upper_violation(
        violations,
        name="power_balance_mismatch",
        actual=float(pf_result.max_mismatch),
        upper=config.mismatch_tolerance,
    )


def _append_bound_violation(
    violations: list[ConstraintViolation],
    *,
    name: str,
    actual: float,
    lower: float | None,
    upper: float | None,
    tolerance: float,
) -> None:
    if lower is not None:
        violation = max(0.0, lower - actual - tolerance)
        if violation > 0.0:
            violations.append(
                ConstraintViolation(name=name, violation=violation, actual=actual, limit=lower)
            )
    if upper is not None:
        violation = max(0.0, actual - upper - tolerance)
        if violation > 0.0:
            violations.append(
                ConstraintViolation(name=name, violation=violation, actual=actual, limit=upper)
            )


def _append_upper_violation(
    violations: list[ConstraintViolation],
    *,
    name: str,
    actual: float,
    upper: float,
) -> None:
    violation = max(0.0, actual - upper)
    if violation > 0.0:
        violations.append(ConstraintViolation(name=name, violation=violation, actual=actual, limit=upper))


def _unavailable(name: str, message: str) -> ConstraintViolation:
    return ConstraintViolation(
        name=name,
        violation=0.0,
        available=False,
        message=message,
    )


def _valid_rating(value: Any) -> float | None:
    rating = _valid_bound(value)
    if rating is None or rating <= 0.0 or rating >= 9999.0:
        return None
    return rating


def _converter_current_limit_ka(conv: Any) -> float | None:
    current_limit = _valid_rating(getattr(conv, "basekA", None))
    if current_limit is not None:
        return current_limit

    s_max = _valid_rating(getattr(conv, "MVA_max", None))
    ac_kv = _valid_rating(getattr(conv, "AC_kV_base", None))
    if s_max is None or ac_kv is None:
        return None
    return s_max / (np.sqrt(3.0) * ac_kv)


def _valid_bound(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    if not np.isfinite(value) or abs(value) >= 1e8:
        return None
    return value


def _iter_generators(nodes: list[Any]) -> list[tuple[Any, Any]]:
    generators: list[tuple[Any, Any]] = []
    seen: set[int] = set()
    for node in nodes:
        for gen in getattr(node, "connected_gen", []):
            gen_id = int(getattr(gen, "genNumber", id(gen)))
            if gen_id in seen:
                continue
            seen.add(gen_id)
            generators.append((node, gen))
    return generators


def _line_name(lines: list[Any], idx: int) -> str:
    if idx < len(lines):
        return str(getattr(lines[idx], "name", idx))
    return str(idx)
