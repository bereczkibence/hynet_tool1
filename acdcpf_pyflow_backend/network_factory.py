"""Tool1 network construction extensions. No Tool5 modules are patched.

Transformer/storage table creators adapted from ArtemMedvedevDev/acdcpf,
MIT, Copyright (c) 2025 ACDCPF Contributors; see THIRD_PARTY_NOTICES.md.
"""
from __future__ import annotations
from typing import Any, Optional
import pandas as pd
from ._bootstrap import ensure_acdcpf_importable

Network = Any


def _append_row(table, row):
    idx = 0 if table.empty else int(table.index.max()) + 1
    result = table.copy()
    for key in row:
        if key not in result:
            result[key] = pd.Series(index=result.index, dtype=object)
    result.loc[idx] = row
    return result, idx


def normalize_network(net):
    for name, columns in {
        "trafo": ["from_bus", "to_bus", "in_service"],
        "storage": ["bus", "bus_type", "in_service"],
        "res_trafo": [], "res_storage": [],
    }.items():
        if not hasattr(net, name):
            setattr(net, name, pd.DataFrame(columns=columns))
    if "is_slack" not in net.ac_bus:
        net.ac_bus["is_slack"] = False
    return net


class NetworkFactory:
    def __init__(self):
        ensure_acdcpf_importable()
        import acdcpf
        self.backend = acdcpf

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def create_empty_network(self, *args, **kwargs):
        return normalize_network(self.backend.create_empty_network(*args, **kwargs))

    def create_ac_bus(self, net, *args, is_slack=False, **kwargs):
        import inspect
        if "is_slack" in inspect.signature(self.backend.create_ac_bus).parameters:
            kwargs["is_slack"] = is_slack
        idx = self.backend.create_ac_bus(net, *args, **kwargs)
        net.ac_bus.at[idx, "is_slack"] = bool(is_slack)
        return idx

    def create_ac_gen(self, net, *args, p_min_mw=float("-inf"), p_max_mw=float("inf"), **kwargs):
        idx = self.backend.create_ac_gen(net, *args, **kwargs)
        net.ac_gen.at[idx, "p_min_mw"] = p_min_mw
        net.ac_gen.at[idx, "p_max_mw"] = p_max_mw
        return idx

    def create_transformer(self, net, *args, **kwargs):
        normalize_network(net)
        return create_transformer(net, *args, **kwargs)

    def create_storage(self, net, *args, **kwargs):
        normalize_network(net)
        return create_storage(net, *args, **kwargs)

def create_transformer(
    net: Network,
    from_bus: int,
    to_bus: int,
    sn_mva: float,
    r_pu: float,
    x_pu: float,
    vn_from_kv: Optional[float] = None,
    vn_to_kv: Optional[float] = None,
    g_pu: float = 0.0,
    b_pu: float = 0.0,
    tap: float = 1.0,
    shift_deg: float = 0.0,
    tap_min: Optional[float] = None,
    tap_max: Optional[float] = None,
    tap_step_percent: Optional[float] = None,
    tap_controllable: bool = False,
    name: str = "",
    in_service: bool = True,
) -> int:
    """Create a two-winding AC transformer.

    The transformer is stored as a first-class network element. Power-flow and
    OPF routines convert it to the same MATPOWER-style tapped branch model used
    for transformer-like AC branches.
    """
    normalize_network(net)
    if sn_mva <= 0.0:
        raise ValueError("Transformer sn_mva must be positive.")
    if x_pu == 0.0 and r_pu == 0.0:
        raise ValueError("Transformer impedance cannot be zero.")

    net.trafo, idx = _append_row(net.trafo, {
        "name": name,
        "from_bus": from_bus,
        "to_bus": to_bus,
        "vn_from_kv": vn_from_kv,
        "vn_to_kv": vn_to_kv,
        "sn_mva": sn_mva,
        "r_pu": r_pu,
        "x_pu": x_pu,
        "g_pu": g_pu,
        "b_pu": b_pu,
        "tap": tap,
        "shift_deg": shift_deg,
        "tap_min": tap_min,
        "tap_max": tap_max,
        "tap_step_percent": tap_step_percent,
        "tap_controllable": tap_controllable,
        "in_service": in_service,
    })
    return idx


def create_storage(
    net: Network,
    bus: int,
    bus_type: str = "dc",
    p_mw: float = 0.0,
    q_mvar: float = 0.0,
    sn_mva: float = 0.0,
    energy_mwh: float = 0.0,
    soc_percent: float = 50.0,
    soc_min_percent: float = 0.0,
    soc_max_percent: float = 100.0,
    eta_charge: float = 0.95,
    eta_discharge: float = 0.95,
    p_min_mw: float | None = None,
    p_max_mw: float | None = None,
    q_min_mvar: float | None = None,
    q_max_mvar: float | None = None,
    name: str = "",
    in_service: bool = True,
) -> int:
    """Create an AC- or DC-connected storage unit.

    Positive ``p_mw`` means discharging into the connected bus. Negative
    ``p_mw`` means charging from the connected bus.
    """

    normalized_bus_type = str(bus_type).strip().lower()
    if normalized_bus_type not in {"ac", "dc"}:
        raise ValueError("Storage bus_type must be 'ac' or 'dc'.")
    normalize_network(net)
    if sn_mva <= 0.0:
        raise ValueError("Storage sn_mva must be positive.")
    if energy_mwh <= 0.0:
        raise ValueError("Storage energy_mwh must be positive.")
    if not (0.0 <= soc_min_percent <= soc_percent <= soc_max_percent <= 100.0):
        raise ValueError("Storage SOC must satisfy 0 <= min <= initial <= max <= 100.")
    if not (0.0 < eta_charge <= 1.0 and 0.0 < eta_discharge <= 1.0):
        raise ValueError("Storage efficiencies must be in the interval (0, 1].")

    if p_min_mw is None:
        p_min_mw = -float(sn_mva)
    if p_max_mw is None:
        p_max_mw = float(sn_mva)
    if q_min_mvar is None:
        q_min_mvar = -float(sn_mva) if normalized_bus_type == "ac" else 0.0
    if q_max_mvar is None:
        q_max_mvar = float(sn_mva) if normalized_bus_type == "ac" else 0.0

    if normalized_bus_type == "dc":
        q_mvar = 0.0
        q_min_mvar = 0.0
        q_max_mvar = 0.0

    net.storage, idx = _append_row(
        net.storage,
        {
            "name": name,
            "bus": bus,
            "bus_type": normalized_bus_type,
            "p_mw": p_mw,
            "q_mvar": q_mvar,
            "sn_mva": sn_mva,
            "energy_mwh": energy_mwh,
            "soc_percent": soc_percent,
            "soc_min_percent": soc_min_percent,
            "soc_max_percent": soc_max_percent,
            "eta_charge": eta_charge,
            "eta_discharge": eta_discharge,
            "p_min_mw": p_min_mw,
            "p_max_mw": p_max_mw,
            "q_min_mvar": q_min_mvar,
            "q_max_mvar": q_max_mvar,
            "in_service": in_service,
        },
    )
    return idx
