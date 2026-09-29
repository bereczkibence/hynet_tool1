from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from acdcpf_opf.powerflow.result import PFResult


@dataclass(frozen=True)
class PyflowToPyomoOptions:
    """Options for converting pyflow grid data into the Pyomo OPF schema."""

    optimize_non_slack_generator_active_power: bool = False
    ac_generator_active_power_indices: Sequence[int] | None = None
    optimize_non_slack_generator_reactive_power: bool = True
    ac_generator_reactive_power_indices: Sequence[int] | None = None
    optimize_generator_voltage_setpoints: bool = True
    ac_generator_voltage_node_indices: Sequence[int] | None = None
    optimize_converter_pq_setpoints: bool = True
    optimize_converter_active_power: bool = True
    optimize_converter_reactive_power: bool = True
    converter_active_power_indices: Sequence[int] | None = None
    converter_reactive_power_indices: Sequence[int] | None = None
    control_margin_percent: float | None = None
    fix_ac_slack_voltage: bool = True
    fix_converter_vdc_vac_controls: bool = True
    converter_loss_mode: str = "smooth_directional"
    converter_loss_switch_sharpness: float = 50.0
    unsupported_features: list[str] = field(default_factory=list)


def convert_pyflow_grid_to_opf_data(
    grid: Any,
    *,
    pf_result: PFResult | None = None,
    options: PyflowToPyomoOptions | None = None,
) -> dict[str, Any]:
    """Convert a pyflow ACDC grid into the Pyomo formulation data dictionary.

    The returned dictionary uses per-unit values internally. Converter AC-side
    powers are converted from pyflow's sign convention to the formulation's
    convention: positive terminal power absorbs power from the AC grid. PyFlow's
    ``P_AC``/``Q_AC`` setpoints are AC-grid-terminal quantities, so fixed
    controls are applied to transformer terminal flows, not to the internal
    electronic converter variables.
    """
    options = options or PyflowToPyomoOptions()
    _validate_supported_grid(grid)

    data: dict[str, Any] = {
        "base_mva": float(grid.S_base),
        "ac_buses": _build_ac_buses(grid, pf_result),
        "dc_buses": _build_dc_buses(grid, pf_result),
        "ac_branches": _build_ac_branches(grid),
        "dc_branches": _build_dc_branches(grid),
        "generators": _build_generators(grid),
        "ac_loads": _build_ac_loads(grid),
        "dc_loads": _build_dc_loads(grid),
        "dc_generators": {},
        "converters": _build_converters(grid, pf_result, options),
        "dcdc_converters": {},
        "fixed": {},
        "metadata": {
            "source": "pyflow_acdc",
            "converter_sign_convention": (
                "Ptf_if=-pyflow.P_AC, Qtf_if=-pyflow.Q_AC for terminal "
                "controls; Pcv_dc=-acdcpf.p_dc_mw/S_base."
            ),
            "parallel_converter_convention": (
                "PyFlow NumConvP is converted to one equivalent station: "
                "rating/filter scale by N, transformer/reactor impedance scales by 1/N, "
                "and converter losses use N*a + b*I + c*I^2/N."
            ),
            "control_margin_percent": options.control_margin_percent,
            "optimize_converter_active_power": options.optimize_converter_active_power,
            "optimize_converter_reactive_power": options.optimize_converter_reactive_power,
            "optimize_non_slack_generator_active_power": (
                options.optimize_non_slack_generator_active_power
            ),
            "optimize_non_slack_generator_reactive_power": (
                options.optimize_non_slack_generator_reactive_power
            ),
            "optimize_generator_voltage_setpoints": options.optimize_generator_voltage_setpoints,
        },
    }

    _apply_fixed_controls(data, grid, options)
    return data


def _validate_supported_grid(grid: Any) -> None:
    if getattr(grid, "Converters_DCDC", []):
        raise NotImplementedError("Pyomo OPF conversion does not support DCDC converters yet.")
    if not getattr(grid, "nodes_AC", []):
        raise ValueError("At least one AC bus is required.")
    if not getattr(grid, "nodes_DC", []):
        raise ValueError("At least one DC bus is required for this AC/DC formulation.")


def _build_ac_buses(grid: Any, pf_result: PFResult | None) -> dict[str, dict[str, float | int | bool]]:
    buses: dict[str, dict[str, float | int | bool]] = {}
    for idx, node in enumerate(grid.nodes_AC):
        v_min = float(node.Umin)
        v_max = float(node.Umax)
        v0 = _clip(_array_value(pf_result.voltage_magnitude if pf_result else None, idx, node.V), v_min, v_max)
        buses[_ac_bus_key(node)] = {
            "index": int(node.nodeNumber),
            "name": str(node.name),
            "status": 1,
            "v0": v0,
            "theta0": _array_value(pf_result.voltage_angle if pf_result else None, idx, node.theta),
            "v_min": v_min,
            "v_max": v_max,
            "v_base_kv": _ac_node_base_kv(node),
            "is_slack": str(node.type) == "Slack",
            "g_shunt": float(getattr(node.Reactor, "real", 0.0)),
            "b_shunt": float(getattr(node.Reactor, "imag", 0.0)),
        }
    return buses


def _build_dc_buses(grid: Any, pf_result: PFResult | None) -> dict[str, dict[str, float | int | str]]:
    buses: dict[str, dict[str, float | int | str]] = {}
    for idx, node in enumerate(grid.nodes_DC):
        buses[_dc_bus_key(node)] = {
            "index": int(node.nodeNumber),
            "name": str(node.name),
            "status": 1,
            "type": str(node.type),
            "v0": _array_value(pf_result.dc_voltage_magnitude if pf_result else None, idx, node.V),
            "v_min": float(node.Umin),
            "v_max": float(node.Umax),
        }
    return buses


def _build_ac_branches(grid: Any) -> dict[str, dict[str, float | int | str]]:
    branches: dict[str, dict[str, float | int | str]] = {}
    s_base = float(grid.S_base)
    for line in grid.lines_AC:
        rating = float(getattr(line, "MVA_rating", 0.0))
        branches[_ac_branch_key(line)] = {
            "index": int(line.lineNumber),
            "name": str(line.name),
            "status": 1,
            "from": _ac_bus_key(line.fromNode),
            "to": _ac_bus_key(line.toNode),
            "r": float(line.R),
            "x": float(line.X),
            "b": float(getattr(line, "B", 0.0)),
            "tap": float(getattr(line, "m", 1.0)),
            "shift_degree": float(np.degrees(float(getattr(line, "shift", 0.0)))),
            "rate": rating / s_base if 0.0 < rating < 9999.0 else None,
        }
    return branches


def _build_dc_branches(grid: Any) -> dict[str, dict[str, float | int | str]]:
    branches: dict[str, dict[str, float | int | str]] = {}
    s_base = float(grid.S_base)
    for line in grid.lines_DC:
        rating = float(getattr(line, "MW_rating", 0.0))
        branches[_dc_branch_key(line)] = {
            "index": int(line.lineNumber),
            "name": str(line.name),
            "status": 1,
            "from": _dc_bus_key(line.fromNode),
            "to": _dc_bus_key(line.toNode),
            "r": float(line.R),
            "poles": float(getattr(line, "pol", 1.0)),
            "rate": rating / s_base if 0.0 < rating < 9999.0 else None,
            "i_max": _dc_current_limit_pu(line),
        }
    return branches


def _build_generators(grid: Any) -> dict[str, dict[str, float | int | str]]:
    generators: dict[str, dict[str, float | int | str]] = {}
    seen: set[int] = set()
    for node in grid.nodes_AC:
        for gen in getattr(node, "connected_gen", []):
            gen_number = int(gen.genNumber)
            if gen_number in seen:
                continue
            seen.add(gen_number)
            pg_min = _finite_or_default(getattr(gen, "Min_pow_gen", None), 0.0)
            pg_max = _finite_or_default(getattr(gen, "Max_pow_gen", None), 1e3)
            qg_min, qg_max = _reactive_power_limits(gen)
            generators[f"G{gen_number}"] = {
                "index": gen_number,
                "name": str(gen.name),
                "status": 1,
                "bus": _ac_bus_key(node),
                "is_slack": str(node.type) == "Slack",
                "pg0": _clip(float(gen.PGen), pg_min, pg_max),
                "qg0": _clip(float(gen.QGen), qg_min, qg_max),
                "pg_min": pg_min,
                "pg_max": pg_max,
                "qg_min": qg_min,
                "qg_max": qg_max,
            }
    return generators


def _build_ac_loads(grid: Any) -> dict[str, dict[str, float | str]]:
    loads: dict[str, dict[str, float | str]] = {}
    for node in grid.nodes_AC:
        p_load = float(getattr(node, "PLi", 0.0))
        q_load = float(getattr(node, "QLi", 0.0))
        if p_load == 0.0 and q_load == 0.0:
            continue
        loads[f"AC_LOAD_{node.nodeNumber}"] = {
            "bus": _ac_bus_key(node),
            "status": 1,
            "p": p_load,
            "q": q_load,
        }
    return loads


def _build_dc_loads(grid: Any) -> dict[str, dict[str, float | str]]:
    loads: dict[str, dict[str, float | str]] = {}
    for node in grid.nodes_DC:
        p_load = float(getattr(node, "PLi", 0.0))
        if p_load == 0.0:
            continue
        loads[f"DC_LOAD_{node.nodeNumber}"] = {
            "bus": _dc_bus_key(node),
            "status": 1,
            "p": p_load,
        }
    return loads


def _build_converters(
    grid: Any,
    pf_result: PFResult | None,
    options: PyflowToPyomoOptions,
) -> dict[str, dict[str, Any]]:
    converters: dict[str, dict[str, Any]] = {}
    s_base = float(grid.S_base)
    for idx, conv in enumerate(grid.Converters_ACDC):
        key = _converter_key(conv)
        parallel_converters = _positive_float(getattr(conv, "NumConvP", 1.0), "NumConvP")
        s_rated = float(conv.MVA_max) * parallel_converters / s_base
        initial_p_ac = _array_value(
            pf_result.converter_active_power_ac / s_base if pf_result is not None else None,
            idx,
            -float(conv.P_AC),
        )
        initial_q_ac = _array_value(
            pf_result.converter_reactive_power_ac / s_base if pf_result is not None else None,
            idx,
            -float(conv.Q_AC),
        )
        initial_p_dc = _array_value(
            -pf_result.converter_active_power_dc / s_base if pf_result is not None else None,
            idx,
            -float(getattr(conv, "P_DC", 0.0)),
        )
        u_cv_min = float(conv.Ucmin)
        u_cv_max = float(conv.Ucmax)
        initial_u_cv = _clip(
            _array_value(
                pf_result.converter_internal_voltage if pf_result is not None else None,
                idx,
                getattr(conv, "U_c", 1.0),
            ),
            u_cv_min,
            u_cv_max,
        )
        u_f_min = float(conv.Node_AC.Umin)
        u_f_max = float(conv.Node_AC.Umax)
        initial_u_f = _clip(
            float(getattr(conv, "U_f", conv.Node_AC.V)),
            u_f_min,
            u_f_max,
        )
        initial_i_ac = _array_value(
            pf_result.converter_ac_current / _current_base_ka(conv, s_base) if pf_result is not None else None,
            idx,
            max(0.05, abs(initial_p_ac)),
        )
        loss_c_positive = float(conv.c_rect)
        loss_c_negative = float(conv.c_inver)
        loss_c = loss_c_positive if initial_p_ac >= 0.0 else loss_c_negative
        p_terminal_min, p_terminal_max = _control_margin_bounds(
            initial=initial_p_ac,
            lower=-s_rated,
            upper=s_rated,
            rating=s_rated,
            margin_percent=options.control_margin_percent,
        )
        q_terminal_min, q_terminal_max = _control_margin_bounds(
            initial=initial_q_ac,
            lower=-s_rated,
            upper=s_rated,
            rating=s_rated,
            margin_percent=options.control_margin_percent,
        )

        converters[key] = {
            "index": int(conv.ConvNumber),
            "name": str(conv.name),
            "status": 1,
            "type": "vsc",
            "control_mode": f"{conv.type}/{conv.AC_type}",
            "parallel_converters": parallel_converters,
            "ac_bus": _ac_bus_key(conv.Node_AC),
            "dc_bus": _dc_bus_key(conv.Node_DC),
            "s_ac_rated": s_rated,
            "p_ac_min": -s_rated,
            "p_ac_max": s_rated,
            "q_ac_min": -s_rated,
            "q_ac_max": s_rated,
            "p_dc_min": -s_rated,
            "p_dc_max": s_rated,
            "p_terminal_min": p_terminal_min,
            "p_terminal_max": p_terminal_max,
            "q_terminal_min": q_terminal_min,
            "q_terminal_max": q_terminal_max,
            "u_f_min": u_f_min,
            "u_f_max": u_f_max,
            "u_cv_min": u_cv_min,
            "u_cv_max": u_cv_max,
            "i_ac_max": s_rated / max(float(conv.Ucmin), 1e-6),
            "i_dc_min": -s_rated / max(float(conv.Node_DC.Umin), 1e-6),
            "i_dc_max": s_rated / max(float(conv.Node_DC.Umin), 1e-6),
            "transformer": {
                "enabled": True,
                "r": float(conv.R_t) / parallel_converters,
                "x": float(conv.X_t) / parallel_converters,
                "tap": 1.0,
            },
            "phase_reactor": {
                "enabled": True,
                "r": float(conv.PR_R) / parallel_converters,
                "x": float(conv.PR_X) / parallel_converters,
            },
            "filter": {"b": float(conv.Bf) * parallel_converters},
            "loss_a": float(conv.a_conv),
            "loss_b": float(conv.b_conv),
            "loss_c": loss_c,
            "loss_c_positive_p_ac": loss_c_positive,
            "loss_c_negative_p_ac": loss_c_negative,
            "loss_mode": options.converter_loss_mode,
            "loss_switch_sharpness": options.converter_loss_switch_sharpness,
            "droop": _converter_droop(conv),
            "initial": {
                "p_ac": initial_p_ac,
                "q_ac": initial_q_ac,
                "p_dc": initial_p_dc,
                "u_f": initial_u_f,
                "u_cv": initial_u_cv,
                "theta_f": float(conv.Node_AC.theta),
                "theta_cv": float(conv.Node_AC.theta),
                "i_ac": max(abs(initial_i_ac), 1e-4),
                "i_dc": initial_p_dc / max(float(conv.Node_DC.V), 1e-6),
            },
        }
    return converters


def _converter_droop(conv: Any) -> dict[str, float | bool]:
    """Convert PyFlow's DC droop convention to the Pyomo sign convention."""
    if str(getattr(conv, "type", "")) != "Droop":
        return {"enabled": False}

    return {
        "enabled": True,
        "pdc_set": -float(getattr(conv, "P_DC", 0.0)),
        "vdc_set": float(getattr(conv.Node_DC, "V_ini", conv.Node_DC.V)),
        "k": float(getattr(conv, "Droop_rate", 0.0)),
    }


def _apply_fixed_controls(data: dict[str, Any], grid: Any, options: PyflowToPyomoOptions) -> None:
    fixed = data["fixed"]
    selected_generator_pg = _normalize_selected_indices(options.ac_generator_active_power_indices)
    selected_generator_qg = _normalize_selected_indices(options.ac_generator_reactive_power_indices)
    selected_generator_voltage = _normalize_selected_indices(options.ac_generator_voltage_node_indices)
    selected_converter_p = _normalize_selected_indices(options.converter_active_power_indices)
    selected_converter_q = _normalize_selected_indices(options.converter_reactive_power_indices)

    if options.fix_ac_slack_voltage:
        for node in grid.nodes_AC:
            if str(node.type) == "Slack":
                fixed.setdefault("Vmag", {})[_ac_bus_key(node)] = _fixed_ac_voltage_value(data, node)

    for gen_key, gen in data["generators"].items():
        if bool(gen.get("is_slack", False)):
            continue
        optimize_pg = (
            options.optimize_non_slack_generator_active_power
            and _is_selected(int(gen["index"]), selected_generator_pg)
        )
        if not optimize_pg:
            fixed.setdefault("Pg", {})[gen_key] = float(gen["pg0"])
        optimize_qg = (
            options.optimize_non_slack_generator_reactive_power
            and _is_selected(int(gen["index"]), selected_generator_qg)
        )
        if not optimize_qg:
            fixed.setdefault("Qg", {})[gen_key] = float(gen["qg0"])

    for node in grid.nodes_AC:
        if str(node.type) == "Slack" or not getattr(node, "connected_gen", []):
            continue
        node_number = int(node.nodeNumber)
        optimize_voltage = (
            options.optimize_generator_voltage_setpoints
            and _is_selected(node_number, selected_generator_voltage)
        )
        if not optimize_voltage:
            fixed.setdefault("Vmag", {})[_ac_bus_key(node)] = _fixed_ac_voltage_value(data, node)

    for conv in grid.Converters_ACDC:
        conv_key = _converter_key(conv)
        conv_number = int(conv.ConvNumber)
        if options.fix_converter_vdc_vac_controls and str(conv.type) == "Slack":
            fixed.setdefault("Vdc", {})[_dc_bus_key(conv.Node_DC)] = float(conv.Node_DC.V)
        if options.fix_converter_vdc_vac_controls and str(conv.AC_type) in {"PV", "Slack"}:
            fixed.setdefault("Vmag", {})[_ac_bus_key(conv.Node_AC)] = _fixed_ac_voltage_value(
                data,
                conv.Node_AC,
            )

        optimize_converter_p = (
            options.optimize_converter_pq_setpoints
            and options.optimize_converter_active_power
            and _is_selected(conv_number, selected_converter_p)
        )
        optimize_converter_q = (
            options.optimize_converter_pq_setpoints
            and options.optimize_converter_reactive_power
            and _is_selected(conv_number, selected_converter_q)
        )

        if not optimize_converter_p and _is_active_power_controlled_converter(conv):
            fixed.setdefault("Ptf_if", {})[conv_key] = -float(conv.P_AC)
        if not optimize_converter_q and str(conv.AC_type) == "PQ":
            fixed.setdefault("Qtf_if", {})[conv_key] = -float(conv.Q_AC)


def _array_value(values: Any, idx: int, fallback: Any) -> float:
    if values is None:
        return float(fallback)
    if idx >= len(values):
        return float(fallback)
    return float(values[idx])


def _fixed_ac_voltage_value(data: dict[str, Any], node: Any) -> float:
    """Return a feasible fixed AC voltage for a PyFlow node.

    Some PyFlow examples ship with voltage setpoints slightly outside their own
    Umin/Umax limits. IPOPT rejects such fixed variables before solving, so the
    OPF fixes the setpoint to the feasible initialization used for the bus.
    """

    bus = data["ac_buses"][_ac_bus_key(node)]
    return float(bus["v0"])


def _finite_or_default(value: Any, default: float) -> float:
    if value is None:
        return default
    value = float(value)
    if not np.isfinite(value) or abs(value) >= 1e8:
        return default
    return value


def _positive_float(value: Any, name: str) -> float:
    value = float(value)
    if not np.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be a positive finite value.")
    return value


def _reactive_power_limits(gen: Any) -> tuple[float, float]:
    """Return generator reactive limits, treating pyflow defaults as missing.

    The pyflow Stagg generator objects expose ``Min_pow_genR=0`` and
    ``Max_pow_genR=999.99`` even though their PV/slack reactive outputs can be
    negative in the solved power flow. Interpreting those defaults as real OPF
    limits makes the benchmark infeasible, so use broad symmetric limits unless
    a case provides more specific bounds.
    """
    raw_min = getattr(gen, "Min_pow_genR", None)
    raw_max = getattr(gen, "Max_pow_genR", None)
    q_min = _finite_or_default(raw_min, -1e3)
    q_max = _finite_or_default(raw_max, 1e3)

    if abs(q_min) <= 1e-12 and q_max >= 999.0:
        return -1e3, 1e3
    return q_min, q_max


def _clip(value: float, lower: float, upper: float) -> float:
    return float(min(max(value, lower), upper))


def _normalize_selected_indices(indices: Sequence[int] | None) -> set[int] | None:
    if indices is None:
        return None
    return {int(index) for index in indices}


def _is_selected(index: int, selected_indices: set[int] | None) -> bool:
    return selected_indices is None or int(index) in selected_indices


def _is_active_power_controlled_converter(conv: Any) -> bool:
    return str(getattr(conv, "type", "")) in {"PAC", "P"}


def _control_margin_bounds(
    *,
    initial: float,
    lower: float | None,
    upper: float | None,
    rating: float,
    margin_percent: float | None,
) -> tuple[float | None, float | None]:
    if margin_percent is None:
        return lower, upper

    margin = _control_margin_fraction(margin_percent) * float(rating)
    bounded_lower = float(initial) - margin
    bounded_upper = float(initial) + margin
    if lower is not None:
        bounded_lower = max(float(lower), bounded_lower)
    if upper is not None:
        bounded_upper = min(float(upper), bounded_upper)
    if bounded_lower > bounded_upper:
        raise ValueError(
            "Control margin produced invalid bounds. Check the initial setpoint, "
            "apparent rating, and physical min/max limits."
        )
    return bounded_lower, bounded_upper


def _control_margin_fraction(margin_percent: float) -> float:
    value = float(margin_percent)
    if value < 0.0:
        raise ValueError("control_margin_percent must be nonnegative.")
    return value / 100.0


def _dc_current_limit_pu(line: Any) -> float:
    rating = float(getattr(line, "MW_rating", 0.0))
    if rating <= 0.0 or rating >= 9999.0:
        return 1e3
    return rating / float(getattr(line, "S_base", 100.0))


def _current_base_ka(conv: Any, s_base: float) -> float:
    return s_base / (np.sqrt(3.0) * float(conv.AC_kV_base))


def _ac_node_base_kv(node: Any) -> float | None:
    for attribute in ("AC_kV_base", "kV_base", "base_kv", "V_base", "Vbase"):
        value = getattr(node, attribute, None)
        try:
            if value is not None and float(value) > 0.0:
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _ac_bus_key(node: Any) -> str:
    return f"AC{int(node.nodeNumber)}"


def _dc_bus_key(node: Any) -> str:
    return f"DC{int(node.nodeNumber)}"


def _ac_branch_key(line: Any) -> str:
    return f"AC_LINE{int(line.lineNumber)}"


def _dc_branch_key(line: Any) -> str:
    return f"DC_LINE{int(line.lineNumber)}"


def _converter_key(conv: Any) -> str:
    return f"CONV{int(conv.ConvNumber)}"
