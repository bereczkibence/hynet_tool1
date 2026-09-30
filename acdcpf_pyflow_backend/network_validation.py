"""Tool1 structural validation, adapted from ArtemMedvedevDev/acdcpf.

MIT, Copyright (c) 2025 ACDCPF Contributors; see THIRD_PARTY_NOTICES.md.
"""

import math


ELEMENT_REFERENCES = {
    "ac_line": {"from_bus": "ac_bus", "to_bus": "ac_bus"},
    "trafo": {"from_bus": "ac_bus", "to_bus": "ac_bus"},
    "ac_load": {"bus": "ac_bus"},
    "ac_gen": {"bus": "ac_bus"},
    "dc_line": {"from_bus": "dc_bus", "to_bus": "dc_bus"},
    "dc_load": {"bus": "dc_bus"},
    "dc_gen": {"bus": "dc_bus"},
    "vsc": {"ac_bus": "ac_bus", "dc_bus": "dc_bus"},
    "dcdc": {"from_bus": "dc_bus", "to_bus": "dc_bus"},
    "storage": {},
}


def validate_network(net) -> None:
    """Reject invalid references and active devices on disconnected buses.

    A disabled bus must have its incident devices disabled explicitly. PF does
    not silently reconnect it or discard live loads. External IDs are integers
    and need not be contiguous or start at zero.
    """
    if not math.isfinite(float(net.s_base)) or net.s_base <= 0:
        raise ValueError("s_base must be finite and positive (MVA).")
    for name in ("ac_bus", "dc_bus", *ELEMENT_REFERENCES):
        table = getattr(net, name, None)
        if table is None or table.empty:
            continue
        if not table.index.is_unique:
            raise ValueError(f"{name}: duplicate element IDs.")
        if any(not isinstance(i, (int,)) and not hasattr(i, "__index__") for i in table.index):
            raise ValueError(f"{name}: element IDs must be integers.")
        for idx, row in table.iterrows():
            if bool(row.get("in_service", True)):
                unsupported_shunt = {"ac_line": "g_us_per_km", "trafo": "g_pu"}.get(name)
                if unsupported_shunt and float(row.get(unsupported_shunt, 0.0) or 0.0) != 0:
                    raise NotImplementedError(f"{name}[{idx}].{unsupported_shunt}: nonzero branch shunt conductance is not supported.")
                if name == "vsc" and str(row.control_mode).lower() not in {
                    "p_q", "p_vac", "pdc_q", "pdc_vac", "vdc_q", "vdc_vac", "droop_q", "droop_vac",
                }:
                    raise ValueError(f"vsc[{idx}]: unsupported control_mode {row.control_mode!r}.")
            references = ELEMENT_REFERENCES.get(name, {})
            if name == "storage":
                kind = str(row["bus_type"]).lower()
                if kind not in {"ac", "dc"}:
                    raise ValueError(f"storage[{idx}].bus_type must be ac or dc.")
                references = {"bus": f"{kind}_bus"}
            for field, bus_table_name in references.items():
                bus_table = getattr(net, bus_table_name)
                bus = row[field]
                if bus not in bus_table.index:
                    raise ValueError(f"{name}[{idx}].{field}: unknown {bus_table_name} ID {bus}.")
                if bool(row.get("in_service", True)) and not bool(bus_table.loc[bus].get("in_service", True)):
                    raise ValueError(f"{name}[{idx}].{field}: active device on out-of-service {bus_table_name} {bus}.")
            for field in ("p_mw", "q_mvar"):
                if field in row and not math.isfinite(float(row[field])):
                    raise ValueError(f"{name}[{idx}].{field} must be finite.")
            for low, high in (("p_min_mw", "p_max_mw"), ("q_min_mvar", "q_max_mvar")):
                if low in row and high in row and float(row[low]) > float(row[high]):
                    raise ValueError(f"{name}[{idx}]: {low} exceeds {high}.")
