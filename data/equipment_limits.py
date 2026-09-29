"""Inventory and input validation for native-network equipment limits.

An entered number is not a verified nameplate value. Missing ratings stay
missing; control margins and numerical fallback bounds are not ratings.
"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class LimitSpec:
    fields: tuple[str, ...]
    unit: str
    check: str
    missing_behavior: str
    positive: bool = False
    zero_unrated: bool = False


AC_RATE_FIELDS = ("rate_mva", "rating_mva", "mva_rating", "MVA_rating", "s_mva", "sn_mva", "max_s_mva")
DC_RATE_FIELDS = ("rate_mw", "rating_mw", "mw_rating", "MW_rating", "p_max_mw", "max_p_mw")
DCDC_RATE_FIELDS = DC_RATE_FIELDS
DCDC_PU_LIMIT_SPECS = (
    LimitSpec(("d_pu_min", "d_min_pu"), "pu", "voltage_ratio", "Ratio fixed without both bounds", True),
    LimitSpec(("d_pu_max", "d_max_pu"), "pu", "voltage_ratio", "Ratio fixed without both bounds", True),
)

# Paired bounds are kept adjacent for table readability, not inferred from names.
LIMIT_SPECS = {
    "ac_bus": (
        LimitSpec(("v_min_pu",), "pu", "voltage", "OPF fallback: 0.9 pu", True),
        LimitSpec(("v_max_pu",), "pu", "voltage", "OPF fallback: 1.1 pu", True),
    ),
    "dc_bus": (
        LimitSpec(("v_min",), "pu", "voltage", "OPF fallback: 0.95 pu", True),
        LimitSpec(("v_max",), "pu", "voltage", "OPF fallback: 1.05 pu", True),
    ),
    "ac_gen": tuple(
        LimitSpec((field,), unit, check, "OPF numerical fallback: +/-10000; not a physical limit")
        for field, unit, check in (
            ("p_min_mw", "MW", "p_dispatch"), ("p_max_mw", "MW", "p_dispatch"),
            ("q_min_mvar", "MVAr", "q_dispatch"), ("q_max_mvar", "MVAr", "q_dispatch"),
        )
    ),
    "dc_gen": (
        LimitSpec(("p_min_mw", "min_p_mw", "pmin_mw"), "MW", "p_dispatch", "OPF derives from starting P; curtailment also enforces available generation"),
        LimitSpec(("p_max_mw", "max_p_mw", "pmax_mw"), "MW", "p_dispatch", "OPF derives from starting P; curtailment also enforces available generation"),
    ),
    "ac_line": (
        LimitSpec(AC_RATE_FIELDS, "MVA", "s_from_rating / s_to_rating", "No apparent-power limit", True, True),
        LimitSpec(("max_i_ka",), "kA", "i_from_rating / i_to_rating", "No current limit", True, True),
    ),
    "dc_line": (
        LimitSpec(DC_RATE_FIELDS, "MW", "p_from_rating / p_to_rating", "No terminal-power limit", True, True),
        LimitSpec(("max_i_ka",), "kA/pole", "current_rating", "No current limit", True, True),
    ),
    "trafo": (
        LimitSpec(("sn_mva",), "MVA", "s_from_rating / s_to_rating", "Required: also defines impedance base", True),
        LimitSpec(("max_i_ka",), "kA", "i_from_rating / i_to_rating", "No separate current limit", True, True),
    ),
    "vsc": (
        LimitSpec(("s_mva",), "MVA", "bridge_apparent_rating", "Required bridge rating", True),
        LimitSpec(("max_i_ac_ka",), "kA", "ac_current_rating", "Derived OPF bound from S and AC voltage; no explicit bridge-current rating", True, True),
        LimitSpec(("max_i_dc_ka",), "kA/pole", "dc_current_rating", "Derived OPF bound from S and DC voltage; no explicit current rating", True, True),
        LimitSpec(("v_filter_min_pu",), "pu", "filter_voltage", "OPF inherits AC bus lower voltage bound", True),
        LimitSpec(("v_filter_max_pu",), "pu", "filter_voltage", "OPF inherits AC bus upper voltage bound", True),
        LimitSpec(("v_converter_min_pu",), "pu", "bridge_voltage", "OPF inherits AC bus lower voltage bound", True),
        LimitSpec(("v_converter_max_pu",), "pu", "bridge_voltage", "OPF inherits AC bus upper voltage bound", True),
    ),
    "dcdc": (
        LimitSpec(DCDC_RATE_FIELDS, "MW", "p_from_rating / p_to_rating", "No terminal-power limit", True, True),
        LimitSpec(("d_ratio_min", "d_min", "ratio_min"), "V_to/V_from", "voltage_ratio", "Ratio fixed unless explicit bounds permit optimization", True),
        LimitSpec(("d_ratio_max", "d_max", "ratio_max"), "V_to/V_from", "voltage_ratio", "Ratio fixed unless explicit bounds permit optimization", True),
    ),
    "storage": (
        LimitSpec(("sn_mva",), "MVA", "apparent_rating", "Required storage rating", True),
        LimitSpec(("energy_mwh",), "MWh", "energy_transition / soc_energy_consistency", "Required energy capacity", True),
        LimitSpec(("p_min_mw",), "MW", "p_dispatch", "Derived from -S"),
        LimitSpec(("p_max_mw",), "MW", "p_dispatch", "Derived from S"),
        LimitSpec(("q_min_mvar",), "MVAr", "q_dispatch", "Derived from -S; DC Q=0"),
        LimitSpec(("q_max_mvar",), "MVAr", "q_dispatch", "Derived from S; DC Q=0"),
        LimitSpec(("soc_min_percent",), "%", "soc_bounds", "Required SOC lower limit"),
        LimitSpec(("soc_max_percent",), "%", "soc_bounds", "Required SOC upper limit"),
    ),
}

BOUND_PAIRS = {
    "ac_bus": (("v_min_pu", "v_max_pu"),),
    "dc_bus": (("v_min", "v_max"),),
    "ac_gen": (("p_min_mw", "p_max_mw"), ("q_min_mvar", "q_max_mvar")),
    "dc_gen": (("p_min_mw", "p_max_mw"),),
    "vsc": (("v_filter_min_pu", "v_filter_max_pu"), ("v_converter_min_pu", "v_converter_max_pu")),
    "dcdc": (("d_ratio_min", "d_ratio_max"),),
    "storage": (("p_min_mw", "p_max_mw"), ("q_min_mvar", "q_max_mvar"), ("soc_min_percent", "soc_max_percent")),
}


def source_limit_value(row, fields):
    """Read the first finite canonical/legacy field, returning its name and value."""
    for field in fields:
        raw = row.get(field)
        if raw is None:
            continue
        value = float(raw)
        if math.isfinite(value):
            return field, value
    return fields[0], None


def _row_specs(table_name, row):
    specs = LIMIT_SPECS[table_name]
    if table_name == "dcdc" and any(source_limit_value(row, spec.fields)[1] is not None for spec in DCDC_PU_LIMIT_SPECS):
        return (specs[0], *DCDC_PU_LIMIT_SPECS)
    return specs


def validate_equipment_limits(net):
    """Reject malformed explicit limits, while allowing absent/unbounded limits."""
    for table_name, specs in LIMIT_SPECS.items():
        table = getattr(net, table_name, None)
        if table is None:
            continue
        for idx, row in table.iterrows():
            specs = _row_specs(table_name, row)
            for spec in specs:
                for field in spec.fields:
                    raw = row.get(field)
                    if raw is None:
                        continue
                    try:
                        value = float(raw)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"{table_name}[{idx}].{field}: expected a numeric limit or blank.") from exc
                    if math.isinf(value):
                        sign = -1 if "min" in field else 1
                        if spec.positive or value * sign < 0:
                            raise ValueError(f"{table_name}[{idx}].{field}: invalid infinite limit; leave unknown ratings blank.")
                field, value = source_limit_value(row, spec.fields)
                if value is not None and spec.positive and (value < 0 or (value == 0 and not spec.zero_unrated)):
                    raise ValueError(f"{table_name}[{idx}].{field}: limit must be positive ({spec.unit}); leave unknown ratings blank.")
            by_field = {spec.fields[0]: source_limit_value(row, spec.fields)[1] for spec in specs}
            pairs = (("d_pu_min", "d_pu_max"),) if "d_pu_min" in by_field else BOUND_PAIRS.get(table_name, ())
            for low, high in pairs:
                if by_field[low] is not None and by_field[high] is not None and by_field[low] > by_field[high]:
                    raise ValueError(f"{table_name}[{idx}]: {low} exceeds {high}.")
            if table_name == "storage":
                for field in ("soc_min_percent", "soc_max_percent"):
                    value = by_field[field]
                    if value is not None and not 0 <= value <= 100:
                        raise ValueError(f"storage[{idx}].{field}: SOC limit must be within 0..100 percent.")


def equipment_limit_inventory(net):
    """Return JSON-safe input coverage rows, not a solved-state feasibility test.

    The source identifies the field, not the provenance of its engineering
    value. Users must document nameplate data or synthetic design assumptions.
    """
    validate_equipment_limits(net)
    rows = []
    for table_name, specs in LIMIT_SPECS.items():
        table = getattr(net, table_name, None)
        if table is None:
            continue
        for idx, row in table.iterrows():
            specs = _row_specs(table_name, row)
            for spec in specs:
                field, value = source_limit_value(row, spec.fields)
                specified = value is not None and not (spec.zero_unrated and value == 0)
                active = bool(row.get("in_service", True))
                rows.append({
                    "element": f"{table_name}[{idx}]", "name": str(row.get("name", "")),
                    "parameter": spec.fields[0], "value": value, "unit": spec.unit,
                    "status": "out_of_service" if not active else "specified" if specified else "missing",
                    "source": f"net.{table_name}[{idx}].{field}",
                    "opf_use": "Explicit source limit" if specified else spec.missing_behavior,
                    "physics_check": spec.check,
                    "note": "Input coverage only; provenance and feasibility are not certified.",
                })
    return rows
