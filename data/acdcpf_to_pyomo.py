from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from acdcpf.validation import validate_network

from acdcpf_opf.data.equipment_limits import DCDC_RATE_FIELDS, equipment_limit_inventory, validate_equipment_limits
from acdcpf_opf.powerflow.result import PFResult


@dataclass(frozen=True)
class ACDCPFToPyomoOptions:
    """Options for converting a native ``acdcpf.Network`` into OPF data.

    Defaults are intentionally benchmark-oriented: generator active dispatch is
    fixed, generator voltage setpoints are fixed, and VSC controls are the
    controllable variables unless a converter is excluded by index.
    """

    optimize_non_slack_generator_active_power: bool = False
    ac_generator_active_power_indices: Sequence[int] | None = None
    optimize_non_slack_generator_reactive_power: bool = True
    ac_generator_reactive_power_indices: Sequence[int] | None = None
    optimize_generator_voltage_setpoints: bool = False
    ac_generator_voltage_node_indices: Sequence[int] | None = None
    optimize_dc_generator_active_power: bool = False
    dc_generator_active_power_indices: Sequence[int] | None = None
    dc_generator_curtailment_only: bool = False
    optimize_converter_pq_setpoints: bool = True
    optimize_converter_active_power: bool = True
    optimize_converter_reactive_power: bool = True
    converter_active_power_indices: Sequence[int] | None = None
    converter_reactive_power_indices: Sequence[int] | None = None
    optimize_dcdc_voltage_ratio: bool = False
    dcdc_voltage_ratio_indices: Sequence[int] | None = None
    optimize_storage_active_power: bool = True
    storage_active_power_indices: Sequence[int] | None = None
    optimize_storage_reactive_power: bool = True
    storage_reactive_power_indices: Sequence[int] | None = None
    control_margin_percent: float | None = None
    fix_ac_slack_voltage: bool = True
    fix_converter_vdc_vac_controls: bool = True
    converter_loss_mode: str = "smooth_directional"
    converter_loss_switch_sharpness: float = 50.0
    snapshot_duration_hours: float = 1.0
    unsupported_features: list[str] = field(default_factory=list)


def convert_acdcpf_network_to_opf_data(
    net: Any,
    *,
    pf_result: PFResult | None = None,
    options: ACDCPFToPyomoOptions | None = None,
) -> dict[str, Any]:
    """Convert a native ``acdcpf.Network`` to the Pyomo OPF data schema.

    The ACDCPF package uses AC-terminal VSC powers with load convention:
    positive ``p_mw`` absorbs active power from the AC grid. That is the same
    terminal convention used by the OPF variable ``Ptf_if``. DC
    converter powers are converted to the OPF canonical sign where
    ``Pcv_dc < 0`` injects into the DC grid.
    """
    options = options or ACDCPFToPyomoOptions()
    validate_equipment_limits(net)
    _validate_supported_network(net)

    slack_buses, slack_gens = _infer_ac_slack_sets(net)
    unsupported = list(options.unsupported_features)
    if _has_nonzero_line_conductance(net):
        raise NotImplementedError(
            "AC line shunt conductance g_us_per_km is ignored by the current OPF branch model."
        )
    if _has_nonzero_transformer_conductance(net):
        raise NotImplementedError(
            "Transformer shunt conductance g_pu is ignored by the current OPF branch model."
        )

    data: dict[str, Any] = {
        "base_mva": float(net.s_base),
        "ac_buses": _build_ac_buses(net, pf_result, slack_buses),
        "dc_buses": _build_dc_buses(net, pf_result),
        "ac_branches": _build_ac_branches(net),
        "dc_branches": _build_dc_branches(net),
        "generators": _build_generators(net, pf_result, slack_gens),
        "ac_loads": _build_ac_loads(net),
        "dc_loads": _build_dc_loads(net),
        "dc_generators": _build_dc_generators(net, options),
        "storage_units": _build_storage_units(net, options),
        "converters": _build_converters(net, pf_result, options),
        "dcdc_converters": _build_dcdc_converters(net, pf_result, options),
        "fixed": {},
        "metadata": {
            "source": "acdcpf.Network",
            "custom_import": getattr(net, "import_metadata", None),
            "equipment_limits": equipment_limit_inventory(net),
            "converter_sign_convention": (
                "Ptf_if=acdcpf.vsc.p_mw/S_base at the AC terminal; "
                "Pcv_dc=-acdcpf.res_vsc.p_dc_mw/S_base for canonical OPF DC sign."
            ),
            "controls": {
                "generators": "Non-slack generator active power is fixed by default.",
                "generator_reactive_power": (
                    "Non-slack generator reactive power is optimized unless options fix it."
                ),
                "dc_generators": "DC generator active power is fixed unless options select it.",
                "storage": (
                    "Storage active/reactive power is optimized unless options fix it; "
                    "DC-connected storage has reactive power fixed to zero."
                ),
                "vsc": "VSC P/Q controls are optimized only when selected and supported by control_mode.",
                "dcdc": "DCDC voltage ratio is fixed unless options select it and ratio bounds are provided.",
                "vdc_vac": "Voltage-controlled VSC setpoints are fixed unless options override this.",
            },
            "control_margin_percent": options.control_margin_percent,
            "snapshot_duration_hours": options.snapshot_duration_hours,
            "optimize_converter_active_power": options.optimize_converter_active_power,
            "optimize_converter_reactive_power": options.optimize_converter_reactive_power,
            "optimize_dcdc_voltage_ratio": options.optimize_dcdc_voltage_ratio,
            "optimize_non_slack_generator_active_power": (
                options.optimize_non_slack_generator_active_power
            ),
            "optimize_non_slack_generator_reactive_power": (
                options.optimize_non_slack_generator_reactive_power
            ),
            "optimize_dc_generator_active_power": options.optimize_dc_generator_active_power,
            "dc_generator_curtailment_only": options.dc_generator_curtailment_only,
            "unsupported_features": unsupported,
        },
    }

    _apply_fixed_controls(data, net, options, slack_buses)
    return data


def is_acdcpf_network(case: Any) -> bool:
    """Return true when *case* looks like a native ``acdcpf.Network``."""
    required = ("s_base", "ac_bus", "dc_bus", "ac_line", "dc_line", "vsc")
    return all(hasattr(case, attr) for attr in required)


def _validate_supported_network(net: Any) -> None:
    validate_network(net)
    if _active_table(net.ac_bus).empty:
        raise ValueError("At least one AC bus is required.")


def _build_ac_buses(
    net: Any,
    pf_result: PFResult | None,
    slack_buses: set[int],
) -> dict[str, dict[str, float | int | bool | str]]:
    buses: dict[str, dict[str, float | int | bool | str]] = {}
    for pos, (idx, row) in enumerate(_active_table(net.ac_bus).iterrows()):
        idx = int(idx)
        buses[_ac_bus_key(idx)] = {
            "index": idx,
            "name": str(row.get("name", f"AC {idx}")),
            "status": 1,
            "v0": _result_table_value(
                pf_result,
                "res_ac_bus",
                "v_pu",
                idx,
                _pf_array_value(
                    pf_result.voltage_magnitude if pf_result else None,
                    pos,
                    _ac_initial_voltage(net, idx),
                ),
            ),
            "theta0": np.radians(
                _result_table_value(
                    pf_result,
                    "res_ac_bus",
                    "v_angle_deg",
                    idx,
                    np.degrees(
                        _pf_array_value(
                            pf_result.voltage_angle if pf_result else None,
                            pos,
                            0.0,
                        )
                    ),
                )
            ),
            "v_min": _float_value(row, "v_min_pu", 0.9),
            "v_max": _float_value(row, "v_max_pu", 1.1),
            "v_base_kv": _float_value(row, "vr_kv", 1.0),
            "is_slack": idx in slack_buses,
            "g_shunt": _float_value(row, "gs_pu", 0.0),
            "b_shunt": _float_value(row, "bs_pu", 0.0),
        }
    return buses


def _build_dc_buses(net: Any, pf_result: PFResult | None) -> dict[str, dict[str, float | int | str]]:
    buses: dict[str, dict[str, float | int | str]] = {}
    for pos, (idx, row) in enumerate(_active_table(net.dc_bus).iterrows()):
        idx = int(idx)
        buses[_dc_bus_key(idx)] = {
            "index": idx,
            "name": str(row.get("name", f"DC {idx}")),
            "status": 1,
            "type": str(row.get("bus_type", "p")),
            "dc_grid": int(_float_value(row, "dc_grid", 0.0)),
            "v_base_kv": _float_value(row, "v_base", 1.0),
            "v0": _result_table_value(
                pf_result,
                "res_dc_bus",
                "v_dc_pu",
                idx,
                _pf_array_value(
                    pf_result.dc_voltage_magnitude if pf_result else None,
                    pos,
                    _float_value(row, "v_dc_pu", 1.0),
                ),
            ),
            "v_min": _float_value(row, "v_min", 0.95),
            "v_max": _float_value(row, "v_max", 1.05),
        }
    return buses


def _build_ac_branches(net: Any) -> dict[str, dict[str, float | int | str | None]]:
    branches: dict[str, dict[str, float | int | str | None]] = {}
    s_base = float(net.s_base)
    for idx, row in _active_table(net.ac_line).iterrows():
        idx = int(idx)
        from_bus = int(row["from_bus"])
        to_bus = int(row["to_bus"])
        length = _float_value(row, "length_km", 1.0)
        vr_kv = _float_value(net.ac_bus.loc[from_bus], "vr_kv", 1.0)
        z_base = vr_kv**2 / s_base

        branches[_ac_branch_key(idx)] = {
            "index": idx,
            "name": str(row.get("name", f"AC line {idx}")),
            "kind": "line",
            "status": 1,
            "from": _ac_bus_key(from_bus),
            "to": _ac_bus_key(to_bus),
            "r": _float_value(row, "r_ohm_per_km", 0.0) * length / z_base,
            "x": _float_value(row, "x_ohm_per_km", 0.0) * length / z_base,
            "b": _float_value(row, "b_us_per_km", 0.0) * length * 1e-6 * z_base,
            "tap": _float_value(row, "tap", 1.0),
            "shift_degree": _float_value(row, "shift_deg", 0.0),
            "rate": _ac_branch_rate_pu(row, vr_kv=vr_kv, s_base=s_base),
            "i_from_max": _ac_current_limit(row, vr_kv, s_base),
            "i_to_max": _ac_current_limit(row, float(net.ac_bus.loc[to_bus, "vr_kv"]), s_base),
        }

    for idx, row in _active_table(getattr(net, "trafo", pd.DataFrame())).iterrows():
        idx = int(idx)
        from_bus = int(row["from_bus"])
        to_bus = int(row["to_bus"])
        r_pu, x_pu, b_pu = _transformer_series_pu_on_system_base(net, row)
        branches[_transformer_branch_key(idx)] = {
            "index": idx,
            "name": str(row.get("name", f"Transformer {idx}")),
            "kind": "transformer",
            "status": 1,
            "from": _ac_bus_key(from_bus),
            "to": _ac_bus_key(to_bus),
            "r": r_pu,
            "x": x_pu,
            "b": b_pu,
            "tap": _float_value(row, "tap", 1.0),
            "shift_degree": _float_value(row, "shift_deg", 0.0),
            "rate": _float_value(row, "sn_mva", 0.0) / s_base,
            "tap_min": _finite_or_none(row.get("tap_min")),
            "tap_max": _finite_or_none(row.get("tap_max")),
            "tap_step_percent": _finite_or_none(row.get("tap_step_percent")),
            "tap_controllable": bool(row.get("tap_controllable", False)),
        }
    return branches


def _build_dc_branches(net: Any) -> dict[str, dict[str, float | int | str | None]]:
    branches: dict[str, dict[str, float | int | str | None]] = {}
    s_base = float(net.s_base)
    poles = float(getattr(net, "pol", 1.0))
    for idx, row in _active_table(net.dc_line).iterrows():
        idx = int(idx)
        from_bus = int(row["from_bus"])
        to_bus = int(row["to_bus"])
        length = _float_value(row, "length_km", 1.0)
        v_base = _float_value(net.dc_bus.loc[from_bus], "v_base", 1.0)
        z_base = v_base**2 / s_base
        r_pu = _float_value(row, "r_ohm_per_km", 0.0) * length / z_base

        i_max, rate = _dc_branch_current_and_rate_pu(
            row,
            v_base_kv=v_base,
            s_base=s_base,
            poles=poles,
        )

        branches[_dc_branch_key(idx)] = {
            "index": idx,
            "name": str(row.get("name", f"DC line {idx}")),
            "status": 1,
            "from": _dc_bus_key(from_bus),
            "to": _dc_bus_key(to_bus),
            "r": r_pu,
            "poles": poles,
            "rate": rate,
            "i_max": i_max,
        }
    return branches


def _ac_current_limit(row, voltage_kv, base_mva):
    current = _finite_or_none(row.get("max_i_ka"))
    return current * np.sqrt(3.0) * voltage_kv / base_mva if current is not None and current > 0 else None


def _ac_branch_rate_pu(row: Mapping[str, Any], *, vr_kv: float, s_base: float) -> float | None:
    rate_mva = _first_finite(
        row,
        ("rate_mva", "rating_mva", "mva_rating", "MVA_rating", "s_mva", "sn_mva", "max_s_mva"),
    )
    if rate_mva is not None and rate_mva > 0.0:
        return rate_mva / s_base

    return None


def _dc_branch_current_and_rate_pu(
    row: Mapping[str, Any],
    *,
    v_base_kv: float,
    s_base: float,
    poles: float,
) -> tuple[float | None, float | None]:
    rate_mw = _first_finite(
        row,
        ("rate_mw", "rating_mw", "mw_rating", "MW_rating", "p_max_mw", "max_p_mw"),
    )
    rate_pu = rate_mw / s_base if rate_mw is not None and rate_mw > 0 else None
    max_i_ka = _finite_or_none(row.get("max_i_ka"))
    if max_i_ka is not None and max_i_ka > 0.0:
        i_base_ka = s_base / v_base_kv
        i_max = max_i_ka / i_base_ka
        return i_max, rate_pu

    return None, rate_pu


def _build_generators(
    net: Any,
    pf_result: PFResult | None,
    slack_gens: set[int],
) -> dict[str, dict[str, float | int | str | bool]]:
    generators: dict[str, dict[str, float | int | str | bool]] = {}
    s_base = float(net.s_base)
    for pos, (idx, row) in enumerate(_active_table(net.ac_gen).iterrows()):
        idx = int(idx)
        q_min = _finite_or_default(row.get("q_min_mvar"), -1e4) / s_base
        q_max = _finite_or_default(row.get("q_max_mvar"), 1e4) / s_base
        pg0 = _pf_array_value(
            pf_result.generator_active_power / s_base
            if pf_result is not None and pf_result.generator_active_power is not None
            else None,
            pos,
            _float_value(row, "p_mw", 0.0) / s_base,
        )
        pg0 = _result_table_value(
            pf_result,
            "res_ac_gen",
            "p_mw",
            idx,
            pg0 * s_base,
        ) / s_base
        qg0 = _pf_array_value(
            pf_result.generator_reactive_power / s_base
            if pf_result is not None and pf_result.generator_reactive_power is not None
            else None,
            pos,
            _float_value(row, "q_mvar", 0.0) / s_base,
        )
        qg0 = _result_table_value(
            pf_result,
            "res_ac_gen",
            "q_mvar",
            idx,
            qg0 * s_base,
        ) / s_base
        generators[_gen_key(idx)] = {
            "index": idx,
            "name": str(row.get("name", f"Gen {idx}")),
            "status": 1,
            "bus": _ac_bus_key(int(row["bus"])),
            "is_slack": idx in slack_gens,
            "pg0": pg0,
            "qg0": _clip(qg0, q_min, q_max),
            "pg_min": _finite_or_default(row.get("p_min_mw"), -1e4) / s_base,
            "pg_max": _finite_or_default(row.get("p_max_mw"), 1e4) / s_base,
            "qg_min": q_min,
            "qg_max": q_max,
        }
    return generators


def _build_ac_loads(net: Any) -> dict[str, dict[str, float | int | str]]:
    loads: dict[str, dict[str, float | int | str]] = {}
    s_base = float(net.s_base)
    for idx, row in _active_table(net.ac_load).iterrows():
        idx = int(idx)
        loads[f"AC_LOAD{idx}"] = {
            "index": idx,
            "name": str(row.get("name", f"AC load {idx}")),
            "bus": _ac_bus_key(int(row["bus"])),
            "status": 1,
            "p": _float_value(row, "p_mw", 0.0) / s_base,
            "q": _float_value(row, "q_mvar", 0.0) / s_base,
        }
    return loads


def _build_dc_loads(net: Any) -> dict[str, dict[str, float | int | str]]:
    loads: dict[str, dict[str, float | int | str]] = {}
    s_base = float(net.s_base)
    for idx, row in _active_table(net.dc_load).iterrows():
        idx = int(idx)
        load_type = str(row.get("load_type", "constant_power"))
        if load_type != "constant_power":
            raise NotImplementedError("Pyomo OPF conversion supports only constant-power DC loads.")
        loads[f"DC_LOAD{idx}"] = {
            "index": idx,
            "name": str(row.get("name", f"DC load {idx}")),
            "bus": _dc_bus_key(int(row["bus"])),
            "status": 1,
            "p": _float_value(row, "p_mw", 0.0) / s_base,
        }
    return loads


def _build_dc_generators(
    net: Any,
    options: ACDCPFToPyomoOptions,
) -> dict[str, dict[str, float | int | str]]:
    generators: dict[str, dict[str, float | int | str]] = {}
    s_base = float(net.s_base)
    for idx, row in _active_table(getattr(net, "dc_gen", pd.DataFrame())).iterrows():
        idx = int(idx)
        p0 = _float_value(row, "p_mw", 0.0) / s_base
        p_min = _first_finite(row, ("p_min_mw", "min_p_mw", "pmin_mw"))
        p_max = _first_finite(row, ("p_max_mw", "max_p_mw", "pmax_mw"))
        if p_min is None:
            p_min = min(0.0, p0 * s_base)
        if p_max is None:
            p_max = max(0.0, p0 * s_base)
        if options.dc_generator_curtailment_only and p0 > 0.0:
            p_min, p_max = _dc_generator_curtailment_bounds(
                p0_mw=p0 * s_base,
                lower=float(p_min),
                upper=float(p_max),
                margin_percent=options.control_margin_percent,
            )

        generators[_dc_gen_key(idx)] = {
            "index": idx,
            "name": str(row.get("name", f"DC gen {idx}")),
            "bus": _dc_bus_key(int(row["bus"])),
            "status": 1,
            "p0": p0,
            "p_min": float(p_min) / s_base,
            "p_max": float(p_max) / s_base,
        }
    return generators


def _build_storage_units(
    net: Any,
    options: ACDCPFToPyomoOptions,
) -> dict[str, dict[str, float | int | str]]:
    """Build signed one-timestamp storage injections for OPF_BME.

    Positive ``p`` means discharging into the connected bus. Negative ``p``
    means charging from the connected bus. A DC-connected storage unit cannot
    exchange reactive power, so its Q limits are forced to zero.
    """

    storage_table = getattr(net, "storage", pd.DataFrame())
    units: dict[str, dict[str, float | int | str]] = {}
    if storage_table is None or storage_table.empty:
        return units

    s_base = float(net.s_base)
    for idx, row in _active_table(storage_table).iterrows():
        idx = int(idx)
        bus_type = str(row.get("bus_type", row.get("connection", "dc"))).strip().lower()
        if bus_type not in {"ac", "dc"}:
            raise ValueError(f"Storage unit {idx} has unsupported bus_type {bus_type!r}.")

        s_nom_mva = _first_finite(row, ("sn_mva", "s_mva", "s_nom_mva", "nominal_mva"))
        if s_nom_mva is None or s_nom_mva <= 0.0:
            raise ValueError(f"Storage unit {idx} must define a positive apparent rating.")

        p0_mw = _float_value(row, "p_mw", 0.0)
        q0_mvar = _float_value(row, "q_mvar", 0.0) if bus_type == "ac" else 0.0
        p_min_mw = _first_finite(row, ("p_min_mw", "min_p_mw", "pmin_mw"))
        p_max_mw = _first_finite(row, ("p_max_mw", "max_p_mw", "pmax_mw"))
        if p_min_mw is None:
            p_min_mw = -s_nom_mva
        if p_max_mw is None:
            p_max_mw = s_nom_mva

        if bus_type == "ac":
            q_min_mvar = _first_finite(row, ("q_min_mvar", "min_q_mvar", "qmin_mvar"))
            q_max_mvar = _first_finite(row, ("q_max_mvar", "max_q_mvar", "qmax_mvar"))
            if q_min_mvar is None:
                q_min_mvar = -s_nom_mva
            if q_max_mvar is None:
                q_max_mvar = s_nom_mva
            bus_key = _ac_bus_key(int(row["bus"]))
        else:
            q_min_mvar = 0.0
            q_max_mvar = 0.0
            bus_key = _dc_bus_key(int(row["bus"]))

        if p_min_mw > p_max_mw:
            raise ValueError(f"Storage unit {idx} has p_min greater than p_max.")
        if q_min_mvar > q_max_mvar:
            raise ValueError(f"Storage unit {idx} has q_min greater than q_max.")
        if p0_mw**2 + q0_mvar**2 > (s_nom_mva + 1e-9) ** 2:
            raise ValueError(f"Storage unit {idx} initial P/Q exceeds its apparent rating.")
        energy_mwh = _first_finite(row, ("energy_mwh", "e_mwh", "max_e_mwh", "capacity_mwh"))
        if energy_mwh is None or energy_mwh <= 0.0:
            raise ValueError(f"Storage unit {idx} must define a positive energy_mwh.")
        soc0 = _float_value(row, "soc_percent", 50.0) / 100.0
        soc_min = _float_value(row, "soc_min_percent", 0.0) / 100.0
        soc_max = _float_value(row, "soc_max_percent", 100.0) / 100.0
        if not (0.0 <= soc_min <= soc0 <= soc_max <= 1.0):
            raise ValueError(
                f"Storage unit {idx} SOC must satisfy 0 <= min <= initial <= max <= 100 percent."
            )
        eta_charge = _float_value(row, "eta_charge", 0.95)
        eta_discharge = _float_value(row, "eta_discharge", 0.95)
        if not (0.0 < eta_charge <= 1.0 and 0.0 < eta_discharge <= 1.0):
            raise ValueError(f"Storage unit {idx} efficiencies must be in the interval (0, 1].")

        p_min_pu, p_max_pu = _control_margin_bounds(
            initial=p0_mw / s_base,
            lower=float(p_min_mw) / s_base,
            upper=float(p_max_mw) / s_base,
            rating=float(s_nom_mva) / s_base,
            margin_percent=options.control_margin_percent,
        )
        q_min_pu, q_max_pu = _control_margin_bounds(
            initial=q0_mvar / s_base,
            lower=float(q_min_mvar) / s_base,
            upper=float(q_max_mvar) / s_base,
            rating=float(s_nom_mva) / s_base,
            margin_percent=options.control_margin_percent,
        )

        units[_storage_key(idx)] = {
            "index": idx,
            "name": str(row.get("name", f"Storage {idx}")),
            "status": 1,
            "bus": bus_key,
            "bus_type": bus_type,
            "p0": p0_mw / s_base,
            "q0": q0_mvar / s_base,
            "p_min": p_min_pu,
            "p_max": p_max_pu,
            "q_min": q_min_pu,
            "q_max": q_max_pu,
            "s_rating": float(s_nom_mva) / s_base,
            "energy_mwh": float(energy_mwh),
            "soc0": float(soc0),
            "soc_min": float(soc_min),
            "soc_max": float(soc_max),
            "eta_charge": float(eta_charge),
            "eta_discharge": float(eta_discharge),
        }
    return units


def _build_converters(
    net: Any,
    pf_result: PFResult | None,
    options: ACDCPFToPyomoOptions,
) -> dict[str, dict[str, Any]]:
    converters: dict[str, dict[str, Any]] = {}
    s_base = float(net.s_base)
    for pos, (idx, row) in enumerate(_active_table(net.vsc).iterrows()):
        idx = int(idx)
        ac_bus = int(row["ac_bus"])
        dc_bus = int(row["dc_bus"])
        ac_bus_row = net.ac_bus.loc[ac_bus]
        dc_bus_row = net.dc_bus.loc[dc_bus]
        s_rated = _float_value(row, "s_mva", s_base) / s_base
        p_ac_terminal = _pf_array_value(
            pf_result.converter_active_power_ac / s_base if pf_result else None,
            pos,
            _float_value(row, "p_mw", 0.0) / s_base,
        )
        p_ac_terminal = _result_table_value(
            pf_result,
            "res_vsc",
            "p_ac_mw",
            idx,
            p_ac_terminal * s_base,
        ) / s_base
        q_ac_terminal = _pf_array_value(
            pf_result.converter_reactive_power_ac / s_base if pf_result else None,
            pos,
            _float_value(row, "q_mvar", 0.0) / s_base,
        )
        q_ac_terminal = _result_table_value(
            pf_result,
            "res_vsc",
            "q_ac_mvar",
            idx,
            q_ac_terminal * s_base,
        ) / s_base
        p_dc_canonical = _pf_array_value(
            -pf_result.converter_active_power_dc / s_base if pf_result else None,
            pos,
            0.0,
        )
        p_dc_canonical = -_result_table_value(
            pf_result,
            "res_vsc",
            "p_dc_mw",
            idx,
            -p_dc_canonical * s_base,
        ) / s_base
        base_kv_ac = _loss_base_kv(net, row, ac_bus)
        current_base_ka = s_base / (np.sqrt(3.0) * base_kv_ac)
        initial_i_ac = _pf_array_value(
            pf_result.converter_ac_current / current_base_ka if pf_result else None,
            pos,
            max(abs(p_ac_terminal), 1e-4),
        )
        initial_i_ac = _result_table_value(
            pf_result,
            "res_vsc",
            "i_ac_ka",
            idx,
            initial_i_ac * current_base_ka,
        ) / current_base_ka
        initial_ac_voltage = _result_table_value(
            pf_result,
            "res_ac_bus",
            "v_pu",
            ac_bus,
            _network_voltage_value(
                pf_result.voltage_magnitude if pf_result else None,
                ac_bus,
                _ac_initial_voltage(net, ac_bus),
            ),
        )
        initial_ac_angle = np.radians(
            _result_table_value(
                pf_result,
                "res_ac_bus",
                "v_angle_deg",
                ac_bus,
                0.0,
            )
        )
        if pf_result is not None and not _has_result_table_value(
            pf_result,
            "res_ac_bus",
            "v_angle_deg",
            ac_bus,
        ):
            initial_ac_angle = _network_voltage_value(pf_result.voltage_angle, ac_bus, 0.0)
        initial_u_cv = _pf_array_value(
            pf_result.converter_internal_voltage if pf_result else None,
            pos,
            1.0,
        )
        initial_u_cv = _result_table_value(
            pf_result,
            "res_vsc",
            "v_converter_pu",
            idx,
            initial_u_cv,
        )

        loss_c_ohm = _selected_loss_c_ohm(row, p_ac_terminal)
        loss_c_positive_ohm = _loss_c_positive_p_ac_ohm(row)
        loss_c_negative_ohm = _loss_c_negative_p_ac_ohm(row)
        p_terminal_min, p_terminal_max = _control_margin_bounds(
            initial=p_ac_terminal,
            lower=-s_rated,
            upper=s_rated,
            rating=s_rated,
            margin_percent=options.control_margin_percent,
        )
        q_terminal_min, q_terminal_max = _control_margin_bounds(
            initial=q_ac_terminal,
            lower=-s_rated,
            upper=s_rated,
            rating=s_rated,
            margin_percent=options.control_margin_percent,
        )
        filter_min = _float_value(row, "v_filter_min_pu", _float_value(ac_bus_row, "v_min_pu", 0.9))
        filter_max = _float_value(row, "v_filter_max_pu", _float_value(ac_bus_row, "v_max_pu", 1.1))
        bridge_min = _float_value(row, "v_converter_min_pu", _float_value(ac_bus_row, "v_min_pu", 0.9))
        bridge_max = _float_value(row, "v_converter_max_pu", _float_value(ac_bus_row, "v_max_pu", 1.1))
        if filter_min > filter_max or bridge_min > bridge_max:
            raise ValueError(f"vsc[{idx}]: internal voltage bounds conflict with explicit or inherited bounds.")
        explicit_ac_current = _finite_or_none(row.get("max_i_ac_ka"))
        explicit_dc_current = _finite_or_none(row.get("max_i_dc_ka"))
        # Icv_dc is aggregate per-unit current; the native DC rating is per pole.
        dc_current_base_ka = s_base / (float(net.pol) * float(dc_bus_row["v_base"]))
        ac_current_max = (
            explicit_ac_current / current_base_ka if explicit_ac_current is not None and explicit_ac_current > 0
            else s_rated / max(_float_value(ac_bus_row, "v_min_pu", 0.9), 1e-6)
        )
        dc_current_max = (
            explicit_dc_current / dc_current_base_ka if explicit_dc_current is not None and explicit_dc_current > 0
            else s_rated / max(_float_value(dc_bus_row, "v_min", 0.95), 1e-6)
        )
        converters[_converter_key(idx)] = {
            "index": idx,
            "name": str(row.get("name", f"VSC {idx}")),
            "status": 1,
            "type": "vsc",
            "control_mode": str(row.get("control_mode", "p_q")),
            "parallel_converters": 1.0,
            "ac_bus": _ac_bus_key(ac_bus),
            "dc_bus": _dc_bus_key(dc_bus),
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
            "u_f_min": filter_min,
            "u_f_max": filter_max,
            "u_cv_min": bridge_min,
            "u_cv_max": bridge_max,
            "i_ac_max": ac_current_max,
            "i_dc_min": -dc_current_max,
            "i_dc_max": dc_current_max,
            "transformer": {
                "enabled": True,
                "r": _float_value(row, "r_tf_pu", 0.0),
                "x": _float_value(row, "x_tf_pu", 0.0),
                "tap": 1.0,
            },
            "phase_reactor": {
                "enabled": True,
                "r": _float_value(row, "r_c_pu", 0.0),
                "x": _float_value(row, "x_c_pu", 0.0),
            },
            "filter": {"b": _float_value(row, "b_filter_pu", 0.0)},
            "loss_a": _float_value(row, "loss_a", 0.0) / s_base,
            "loss_b": _float_value(row, "loss_b", 0.0) * current_base_ka / s_base,
            "loss_c": loss_c_ohm * current_base_ka**2 / s_base,
            "loss_c_positive_p_ac": loss_c_positive_ohm * current_base_ka**2 / s_base,
            "loss_c_negative_p_ac": loss_c_negative_ohm * current_base_ka**2 / s_base,
            "loss_mode": options.converter_loss_mode,
            "loss_switch_sharpness": options.converter_loss_switch_sharpness,
            "droop": _converter_droop(net, row),
            "initial": {
                "p_ac": p_ac_terminal,
                "q_ac": q_ac_terminal,
                "p_dc": p_dc_canonical,
                "u_f": initial_ac_voltage,
                "u_cv": initial_u_cv,
                "theta_f": initial_ac_angle,
                "theta_cv": initial_ac_angle,
                "i_ac": max(abs(initial_i_ac), 1e-4),
                "i_dc": p_dc_canonical / max(_float_value(dc_bus_row, "v_dc_pu", 1.0), 1e-6),
            },
        }
    return converters


def _build_dcdc_converters(
    net: Any,
    pf_result: PFResult | None,
    options: ACDCPFToPyomoOptions,
) -> dict[str, dict[str, Any]]:
    converters: dict[str, dict[str, Any]] = {}
    s_base = float(net.s_base)
    poles = float(getattr(net, "pol", 1.0))
    selected_ratio = _normalize_selected_indices(options.dcdc_voltage_ratio_indices)

    for idx, row in _active_table(getattr(net, "dcdc", pd.DataFrame())).iterrows():
        idx = int(idx)
        from_bus = int(row["from_bus"])
        to_bus = int(row["to_bus"])
        v_from_base = _float_value(net.dc_bus.loc[from_bus], "v_base", 1.0)
        v_to_base = _float_value(net.dc_bus.loc[to_bus], "v_base", 1.0)
        d_ratio = _float_value(row, "d_ratio", 1.0)
        d_pu = _dcdc_ratio_pu(d_ratio, v_from_base, v_to_base)

        r_ohm = _float_value(row, "r_ohm", 0.0)
        z_base_to = v_to_base**2 / s_base
        g_series = z_base_to / r_ohm if r_ohm > 0.0 else 0.0
        g_shunt = _float_value(row, "g_us", 0.0) * 1e-6 * v_from_base**2 / s_base

        optimize_ratio = (
            options.optimize_dcdc_voltage_ratio
            and _is_selected(idx, selected_ratio)
        )
        d_pu_min, d_pu_max = _dcdc_ratio_bounds_pu(
            row,
            v_from_base=v_from_base,
            v_to_base=v_to_base,
        )
        if optimize_ratio and (d_pu_min is None or d_pu_max is None):
            raise ValueError(
                f"DCDC converter {idx} needs d_ratio_min/d_ratio_max or "
                "d_pu_min/d_pu_max before its voltage ratio can be optimized."
            )
        if not optimize_ratio:
            d_pu_min = d_pu
            d_pu_max = d_pu

        v_from0 = _result_table_value(
            pf_result,
            "res_dc_bus",
            "v_dc_pu",
            from_bus,
            _float_value(net.dc_bus.loc[from_bus], "v_dc_pu", 1.0),
        )
        v_to0 = _result_table_value(
            pf_result,
            "res_dc_bus",
            "v_dc_pu",
            to_bus,
            _float_value(net.dc_bus.loc[to_bus], "v_dc_pu", 1.0),
        )
        p_from0, p_to0 = _dcdc_terminal_power_pu(
            v_from=v_from0,
            v_to=v_to0,
            d_pu=d_pu,
            g_series=g_series,
            g_shunt=g_shunt,
            poles=poles,
        )
        p_from0 = _result_table_value(
            pf_result,
            "res_dcdc",
            "p_from_mw",
            idx,
            p_from0 * s_base,
        ) / s_base
        p_to0 = _result_table_value(
            pf_result,
            "res_dcdc",
            "p_to_mw",
            idx,
            p_to0 * s_base,
        ) / s_base

        rate_mw = _first_finite(row, DCDC_RATE_FIELDS)
        converters[_dcdc_key(idx)] = {
            "index": idx,
            "name": str(row.get("name", f"DCDC {idx}")),
            "status": 1,
            "from": _dc_bus_key(from_bus),
            "to": _dc_bus_key(to_bus),
            "d_ratio": d_ratio,
            "d_pu": d_pu,
            "d_pu_min": float(d_pu_min),
            "d_pu_max": float(d_pu_max),
            "optimize_ratio": optimize_ratio,
            "r_ohm": r_ohm,
            "g_series": g_series,
            "g_shunt": g_shunt,
            "poles": poles,
            "rate": rate_mw / s_base if rate_mw is not None else None,
            "initial": {
                "p_from": p_from0,
                "p_to": p_to0,
                "loss": p_from0 + p_to0,
            },
        }
    return converters


def _apply_fixed_controls(
    data: dict[str, Any],
    net: Any,
    options: ACDCPFToPyomoOptions,
    slack_buses: set[int],
) -> None:
    fixed = data["fixed"]
    selected_generator_pg = _normalize_selected_indices(options.ac_generator_active_power_indices)
    selected_generator_qg = _normalize_selected_indices(options.ac_generator_reactive_power_indices)
    selected_generator_voltage = _normalize_selected_indices(options.ac_generator_voltage_node_indices)
    selected_dc_generator_pg = _normalize_selected_indices(options.dc_generator_active_power_indices)
    selected_storage_p = _normalize_selected_indices(options.storage_active_power_indices)
    selected_storage_q = _normalize_selected_indices(options.storage_reactive_power_indices)
    selected_converter_p = _normalize_selected_indices(options.converter_active_power_indices)
    selected_converter_q = _normalize_selected_indices(options.converter_reactive_power_indices)
    generator_voltage_buses = {
        int(row["bus"]) for _, row in _active_table(net.ac_gen).iterrows()
        if _is_finite(row.get("v_pu"))
    }

    if options.fix_ac_slack_voltage:
        for bus_idx in slack_buses:
            fixed.setdefault("Vmag", {})[_ac_bus_key(bus_idx)] = _ac_initial_voltage(net, bus_idx)

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

    for gen_key, gen in data.get("dc_generators", {}).items():
        optimize_pg = (
            options.optimize_dc_generator_active_power
            and _is_selected(int(gen["index"]), selected_dc_generator_pg)
            and (
                not options.dc_generator_curtailment_only
                or float(gen.get("p0", 0.0)) > 0.0
            )
        )
        if not optimize_pg:
            fixed.setdefault("Pdcg", {})[gen_key] = float(gen["p0"])

    for storage_key, storage in data.get("storage_units", {}).items():
        optimize_p = (
            options.optimize_storage_active_power
            and _is_selected(int(storage["index"]), selected_storage_p)
        )
        if not optimize_p:
            fixed.setdefault("Pst", {})[storage_key] = float(storage["p0"])

        optimize_q = (
            options.optimize_storage_reactive_power
            and str(storage.get("bus_type", "")).lower() == "ac"
            and _is_selected(int(storage["index"]), selected_storage_q)
        )
        if not optimize_q:
            fixed.setdefault("Qst", {})[storage_key] = float(storage.get("q0", 0.0))

    for _, row in _active_table(net.ac_gen).iterrows():
        bus_idx = int(row["bus"])
        if bus_idx in slack_buses:
            continue
        optimize_voltage = (
            options.optimize_generator_voltage_setpoints
            and _is_selected(bus_idx, selected_generator_voltage)
        )
        if not optimize_voltage and _is_finite(row.get("v_pu")):
            fixed.setdefault("Vmag", {})[_ac_bus_key(bus_idx)] = float(row["v_pu"])

    for idx, row in _active_table(net.vsc).iterrows():
        idx = int(idx)
        conv_key = _converter_key(idx)
        ac_bus_key = _ac_bus_key(int(row["ac_bus"]))
        dc_bus_key = _dc_bus_key(int(row["dc_bus"]))
        control_mode = str(row.get("control_mode", "p_q")).lower()

        if options.fix_converter_vdc_vac_controls and "vdc" in control_mode:
            fixed.setdefault("Vdc", {})[dc_bus_key] = _float_value(row, "v_dc_pu", 1.0)
        if "vac" in control_mode and int(row["ac_bus"]) in generator_voltage_buses:
            # Match ACDCPF's generator-priority policy at a shared PV bus.
            fixed.setdefault("Qtf_if", {})[conv_key] = 0.0
        elif options.fix_converter_vdc_vac_controls and "vac" in control_mode:
            fixed.setdefault("Vmag", {})[ac_bus_key] = _float_value(row, "v_ac_pu", 1.0)

        optimize_p = (
            options.optimize_converter_pq_setpoints
            and options.optimize_converter_active_power
            and _is_selected(idx, selected_converter_p)
        )
        optimize_q = (
            options.optimize_converter_pq_setpoints
            and options.optimize_converter_reactive_power
            and _is_selected(idx, selected_converter_q)
        )

        if _has_fixed_p_setpoint(control_mode) and not optimize_p:
            fixed.setdefault("Ptf_if", {})[conv_key] = _float_value(row, "p_mw", 0.0) / float(net.s_base)
        if control_mode in {"pdc_q", "pdc_vac"} and not optimize_p:
            fixed.setdefault("Pcv_dc", {})[conv_key] = -_float_value(row, "p_dc_set_mw", 0.0) / float(net.s_base)
        if _has_fixed_q_setpoint(control_mode) and not optimize_q:
            fixed.setdefault("Qtf_if", {})[conv_key] = _float_value(row, "q_mvar", 0.0) / float(net.s_base)


def _infer_ac_slack_sets(net: Any) -> tuple[set[int], set[int]]:
    islands = _ac_islands(net)
    active_gens = _active_table(net.ac_gen).sort_index()
    explicit_slack_buses = {
        int(idx)
        for idx, row in _active_table(net.ac_bus).iterrows()
        if bool(row.get("is_slack", False))
    }
    slack_buses: set[int] = set()
    slack_gens: set[int] = set()

    for island in islands:
        island_set = set(island)
        island_gens = [
            (int(idx), int(row["bus"]))
            for idx, row in active_gens.iterrows()
            if int(row["bus"]) in island_set
        ]
        explicit_in_island = sorted(explicit_slack_buses & island_set)
        if explicit_in_island:
            bus_idx = explicit_in_island[0]
            slack_buses.add(bus_idx)
            for gen_idx, gen_bus_idx in island_gens:
                if gen_bus_idx == bus_idx:
                    slack_gens.add(gen_idx)
                    break
        elif island_gens:
            gen_idx, bus_idx = island_gens[0]
            slack_gens.add(gen_idx)
            slack_buses.add(bus_idx)
        elif island:
            slack_buses.add(int(island[0]))
    return slack_buses, slack_gens


def _ac_islands(net: Any) -> list[list[int]]:
    buses = [int(idx) for idx in _active_table(net.ac_bus).index]
    adjacency = {bus: set() for bus in buses}
    for _, line in _active_table(net.ac_line).iterrows():
        from_bus = int(line["from_bus"])
        to_bus = int(line["to_bus"])
        if from_bus in adjacency and to_bus in adjacency:
            adjacency[from_bus].add(to_bus)
            adjacency[to_bus].add(from_bus)
    for _, transformer in _active_table(getattr(net, "trafo", pd.DataFrame())).iterrows():
        from_bus = int(transformer["from_bus"])
        to_bus = int(transformer["to_bus"])
        if from_bus in adjacency and to_bus in adjacency:
            adjacency[from_bus].add(to_bus)
            adjacency[to_bus].add(from_bus)

    islands: list[list[int]] = []
    visited: set[int] = set()
    for start in buses:
        if start in visited:
            continue
        queue = [start]
        island: list[int] = []
        while queue:
            bus = queue.pop(0)
            if bus in visited:
                continue
            visited.add(bus)
            island.append(bus)
            queue.extend(sorted(adjacency[bus] - visited))
        islands.append(sorted(island))
    return islands


def _converter_droop(net: Any, row: Mapping[str, Any]) -> dict[str, float | bool]:
    control_mode = str(row.get("control_mode", "")).lower()
    if "droop" not in control_mode:
        return {"enabled": False}

    dc_bus = int(row["dc_bus"])
    v_base = _float_value(net.dc_bus.loc[dc_bus], "v_base", 1.0)
    droop_kv_per_mw = _float_value(row, "droop_kv_per_mw", 0.0)
    if abs(droop_kv_per_mw) <= 1e-12:
        return {"enabled": False}

    return {
        "enabled": True,
        "pdc_set": -_float_value(row, "p_dc_set_mw", 0.0) / float(net.s_base),
        "vdc_set": _float_value(row, "v_dc_set_pu", 1.0),
        "k": v_base / (droop_kv_per_mw * float(net.s_base)),
    }


def _active_table(table: pd.DataFrame) -> pd.DataFrame:
    if table is None or table.empty:
        return pd.DataFrame()
    if "in_service" not in table.columns:
        return table
    return table[table["in_service"] == True]


def _ac_initial_voltage(net: Any, bus_idx: int) -> float:
    gens = _active_table(net.ac_gen)
    if not gens.empty:
        bus_gens = gens[gens["bus"].astype(int) == int(bus_idx)]
        for _, gen in bus_gens.iterrows():
            if _is_finite(gen.get("v_pu")):
                return float(gen["v_pu"])
    return 1.0


def _pf_array_value(values: Any, pos: int, fallback: Any) -> float:
    if values is None or pos >= len(values):
        return float(fallback)
    return float(values[pos])


def _result_table_value(
    pf_result: PFResult | None,
    table_name: str,
    column: str,
    index: int,
    fallback: Any,
) -> float:
    if _has_result_table_value(pf_result, table_name, column, index):
        table = getattr(pf_result.raw_result, table_name)
        return float(table.at[index, column])
    return float(fallback)


def _has_result_table_value(
    pf_result: PFResult | None,
    table_name: str,
    column: str,
    index: int,
) -> bool:
    if pf_result is None or getattr(pf_result, "raw_result", None) is None:
        return False
    table = getattr(pf_result.raw_result, table_name, None)
    return (
        table is not None
        and not table.empty
        and column in table.columns
        and index in table.index
        and _is_finite(table.at[index, column])
    )


def _network_voltage_value(values: Any, index: int, fallback: Any) -> float:
    if values is None or index >= len(values):
        return float(fallback)
    return float(values[index])


def _float_value(row: Mapping[str, Any], key: str, default: float) -> float:
    value = row.get(key, default)
    return float(value) if _is_finite(value) else float(default)


def _finite_or_default(value: Any, default: float) -> float:
    return float(value) if _is_finite(value) else float(default)


def _finite_or_none(value: Any) -> float | None:
    return float(value) if _is_finite(value) else None


def _optional_int(value: Any) -> int | None:
    return int(value) if _is_finite(value) else None


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


def _dc_generator_curtailment_bounds(
    *,
    p0_mw: float,
    lower: float,
    upper: float,
    margin_percent: float | None,
) -> tuple[float, float]:
    """Return downward-only DC generator bounds, optionally margin-limited.

    ``control_margin_percent`` means "maximum allowed curtailment from the
    profiled/starting generation". For example, a 30 MW PV source with an 80%
    margin may dispatch down to 6 MW, not all the way to zero.
    """

    p0 = float(p0_mw)
    bounded_lower = max(float(lower), 0.0)
    bounded_upper = min(float(upper), p0)
    if margin_percent is not None:
        max_curtailment = _control_margin_fraction(margin_percent) * p0
        bounded_lower = max(bounded_lower, p0 - max_curtailment)
    if bounded_lower > bounded_upper:
        raise ValueError(
            "DC generator curtailment bounds are invalid. Check the initial generation, "
            "control margin, and physical min/max limits."
        )
    return bounded_lower, bounded_upper


def _control_margin_fraction(margin_percent: float) -> float:
    value = float(margin_percent)
    if value < 0.0:
        raise ValueError("control_margin_percent must be nonnegative.")
    return value / 100.0


def _first_finite(row: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = row.get(key)
        if _is_finite(value):
            return float(value)
    return None


def _is_finite(value: Any) -> bool:
    if value is None:
        return False
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _clip(value: float, lower: float, upper: float) -> float:
    return float(min(max(value, lower), upper))


def _normalize_selected_indices(indices: Sequence[int] | None) -> set[int] | None:
    if indices is None:
        return None
    return {int(index) for index in indices}


def _is_selected(index: int, selected_indices: set[int] | None) -> bool:
    return selected_indices is None or int(index) in selected_indices


def _has_fixed_p_setpoint(control_mode: str) -> bool:
    return control_mode in {"p_q", "p_vac"}


def _has_fixed_q_setpoint(control_mode: str) -> bool:
    return control_mode in {"p_q", "pdc_q", "vdc_q", "droop_q"}


def _selected_loss_c_ohm(row: Mapping[str, Any], p_ac_terminal: float) -> float:
    if p_ac_terminal >= 0.0:
        return _loss_c_positive_p_ac_ohm(row)
    return _loss_c_negative_p_ac_ohm(row)


def _loss_c_positive_p_ac_ohm(row: Mapping[str, Any]) -> float:
    return _float_value(row, "loss_c_inv", _float_value(row, "loss_c", 0.0))


def _loss_c_negative_p_ac_ohm(row: Mapping[str, Any]) -> float:
    return _float_value(row, "loss_c", 0.0)


def _loss_base_kv(net: Any, row: Mapping[str, Any], ac_bus: int) -> float:
    loss_base = row.get("loss_base_kv")
    if _is_finite(loss_base) and float(loss_base) > 0.0:
        return float(loss_base)
    return _float_value(net.ac_bus.loc[ac_bus], "vr_kv", 1.0)


def _has_nonzero_line_conductance(net: Any) -> bool:
    lines = _active_table(getattr(net, "ac_line", pd.DataFrame()))
    if lines.empty or "g_us_per_km" not in lines.columns:
        return False
    return bool(np.any(np.abs(lines["g_us_per_km"].astype(float).to_numpy()) > 1e-12))


def _has_nonzero_transformer_conductance(net: Any) -> bool:
    transformers = _active_table(getattr(net, "trafo", pd.DataFrame()))
    if transformers.empty or "g_pu" not in transformers.columns:
        return False
    return bool(np.any(np.abs(transformers["g_pu"].astype(float).to_numpy()) > 1e-12))


def _transformer_series_pu_on_system_base(
    net: Any,
    row: Mapping[str, Any],
) -> tuple[float, float, float]:
    from_bus = int(row["from_bus"])
    bus_base_kv = _float_value(net.ac_bus.loc[from_bus], "vr_kv", 1.0)
    transformer_base_kv = _float_value(row, "vn_from_kv", bus_base_kv)
    sn_mva = _float_value(row, "sn_mva", float(net.s_base))
    voltage_factor = (transformer_base_kv / bus_base_kv) ** 2
    impedance_factor = float(net.s_base) / sn_mva * voltage_factor
    admittance_factor = sn_mva / float(net.s_base) / voltage_factor
    return (
        _float_value(row, "r_pu", 0.0) * impedance_factor,
        _float_value(row, "x_pu", 0.0) * impedance_factor,
        _float_value(row, "b_pu", 0.0) * admittance_factor,
    )


def _dcdc_ratio_pu(d_ratio: float, v_from_base: float, v_to_base: float) -> float:
    return float(d_ratio) * float(v_from_base) / float(v_to_base)


def _dcdc_ratio_bounds_pu(
    row: Mapping[str, Any],
    *,
    v_from_base: float,
    v_to_base: float,
) -> tuple[float | None, float | None]:
    d_pu_min = _first_finite(row, ("d_pu_min", "d_min_pu"))
    d_pu_max = _first_finite(row, ("d_pu_max", "d_max_pu"))
    if d_pu_min is not None or d_pu_max is not None:
        return d_pu_min, d_pu_max

    d_ratio_min = _first_finite(row, ("d_ratio_min", "d_min", "ratio_min"))
    d_ratio_max = _first_finite(row, ("d_ratio_max", "d_max", "ratio_max"))
    if d_ratio_min is None or d_ratio_max is None:
        return None, None
    return (
        _dcdc_ratio_pu(d_ratio_min, v_from_base, v_to_base),
        _dcdc_ratio_pu(d_ratio_max, v_from_base, v_to_base),
    )


def _dcdc_terminal_power_pu(
    *,
    v_from: float,
    v_to: float,
    d_pu: float,
    g_series: float,
    g_shunt: float,
    poles: float,
) -> tuple[float, float]:
    p_from = float(v_from) * (
        float(d_pu) ** 2 * float(g_series) * float(v_from)
        - float(d_pu) * float(g_series) * float(v_to)
        + float(g_shunt) * float(v_from)
    )
    p_to = float(v_to) * (
        -float(d_pu) * float(g_series) * float(v_from)
        + float(g_series) * float(v_to)
    )
    return float(poles) * p_from, float(poles) * p_to


def _ac_bus_key(index: int) -> str:
    return f"AC{int(index)}"


def _dc_bus_key(index: int) -> str:
    return f"DC{int(index)}"


def _ac_branch_key(index: int) -> str:
    return f"AC_LINE{int(index)}"


def _transformer_branch_key(index: int) -> str:
    return f"TRAFO{int(index)}"


def _dc_branch_key(index: int) -> str:
    return f"DC_LINE{int(index)}"


def _gen_key(index: int) -> str:
    return f"G{int(index)}"


def _dc_gen_key(index: int) -> str:
    return f"DCG{int(index)}"


def _storage_key(index: int) -> str:
    return f"STORAGE{int(index)}"


def _converter_key(index: int) -> str:
    return f"CONV{int(index)}"


def _dcdc_key(index: int) -> str:
    return f"DCDC{int(index)}"
