"""Convert data-only PyPOWER/MatACDC cases to the pinned native ACDCPF API.

Source generator powers are injections; native VSC powers are consumption.
Source DC Pdc is demand (negative values become positive DC generation).
Input matrices are retained, never modified. No Python case code is executed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import copy
import math

import numpy as np

from . import case_indices as c
from .case_parser import read_case_text


@dataclass(frozen=True)
class ImportOptions:
    format: str | None = None
    loss_units: str | None = None
    ac_function: str | None = None
    dc_function: str | None = None
    dc_voltage_convention: str = "legacy"


@dataclass
class ImportedNetwork:
    network: Any
    ac_case: dict
    dc_case: dict | None
    id_maps: dict
    report: dict

    def fresh_network(self):
        """Each PF/OPF/profile run gets independent tables and solver state."""
        return copy.deepcopy(self.network)


def load_custom_network(ac_path, dc_path=None, *, options: ImportOptions | None = None) -> ImportedNetwork:
    options = options or ImportOptions()
    ac_path = Path(ac_path)
    ac = read_case_text(ac_path.read_text(encoding="utf-8-sig"), filename=ac_path.name, function=options.ac_function)
    dc = None
    if dc_path is not None:
        dc_path = Path(dc_path)
        dc = read_case_text(dc_path.read_text(encoding="utf-8-sig"), filename=dc_path.name, function=options.dc_function)
    return convert_custom_network(ac, dc, options=options, source_names={"ac": ac_path.name, "dc": dc_path.name if dc_path else None})


def _matrix(case, key, widths, *, empty=False):
    if key not in case:
        raise ValueError(f"Missing required matrix {key!r}.")
    matrix = np.asarray(case[key], dtype=float).copy()
    if matrix.size == 0 and empty:
        return np.empty((0, min(widths)))
    if matrix.ndim != 2 or matrix.shape[1] not in widths or (not empty and not len(matrix)):
        raise ValueError(f"{key}: expected a nonempty matrix with {sorted(widths)} columns, got {matrix.shape}.")
    if not np.isfinite(matrix).all():
        raise ValueError(f"{key}: all entries must be finite.")
    return matrix


def _positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive.")
    return value


def _integer(value, name, allowed=None):
    if not np.isfinite(value) or int(value) != value or (allowed is not None and value not in allowed):
        raise ValueError(f"{name}: invalid integer/code {value}.")
    return int(value)


def _bounds(low, high, name):
    if low <= 0 or low > high:
        raise ValueError(f"{name}: voltage bounds must be positive and ordered.")


def _bus_map(matrix, column, name):
    ids = [_integer(row[column], name) for row in matrix]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{name}: duplicate bus IDs.")
    return {external: internal for internal, external in enumerate(ids)}


def _reference(mapping, value, name):
    external = _integer(value, name)
    if external not in mapping:
        raise ValueError(f"{name}: unknown bus ID {external}.")
    return mapping[external]


def _tag(table, idx, row, **values):
    table.at[idx, "source_row"] = row
    for key, val in values.items():
        table.at[idx, key] = val


def _check_islands(net):
    for kind, edge_tables, reference_buses in (
        ("ac", (net.ac_line, net.trafo), set(net.ac_bus.index[net.ac_bus.is_slack.astype(bool)])),
        ("dc", (net.dc_line,), set(int(row.dc_bus) for _, row in net.vsc.iterrows() if row.in_service and str(row.control_mode).startswith("vdc"))),
    ):
        buses = getattr(net, f"{kind}_bus")
        active = set(int(i) for i, row in buses.iterrows() if row.in_service)
        adjacency = {i: set() for i in active}
        for edges in edge_tables:
            for _, row in edges.iterrows():
                if row.in_service:
                    start, end = int(row.from_bus), int(row.to_bus)
                    if start in active and end in active:
                        adjacency[start].add(end)
                        adjacency[end].add(start)
        remaining = set(active)
        while remaining:
            component, pending = set(), [next(iter(remaining))]
            while pending:
                bus = pending.pop()
                if bus not in component:
                    component.add(bus)
                    pending.extend(adjacency[bus] - component)
            remaining -= component
            if len(component & reference_buses) != 1:
                ids = [int(buses.at[i, "source_bus_id"]) for i in sorted(component)]
                raise ValueError(f"{kind.upper()} island {ids} requires exactly one voltage reference; found {len(component & reference_buses)}.")


def convert_custom_network(ac_case: Mapping, dc_case: Mapping | None = None, *, options: ImportOptions | None = None, source_names: dict | None = None) -> ImportedNetwork:
    """Convert dictionaries; ``format`` is mandatory whenever DC data is present.

Standard MatACDC Imax is pu on the AC system base; Tool7 Imax is kA.
Physical loss coefficients are MW/kV/ohm. PU losses use Sbase and
Ibase=Sbase/(sqrt(3)*basekVac). DC R is converted by Vbase²/baseMVAdc;
the native backend retains pol in P=pol*V*(V-Vother)/Rpu.
"""
    import acdcpf as pf
    from acdcpf.validation import validate_network
    from .equipment_limits import validate_equipment_limits

    options = options or ImportOptions()
    if options.format not in {None, "standard", "tool7"}:
        raise ValueError("format must be standard or tool7.")
    if options.dc_voltage_convention not in {"legacy", "per_pole", "pole_to_pole"}:
        raise ValueError("dc_voltage_convention must be legacy, per_pole or pole_to_pole.")
    if dc_case is not None and options.format is None:
        raise ValueError("Hybrid imports require an explicit format: standard or tool7.")
    if dc_case is not None and options.format == "tool7" and options.loss_units not in {"physical", "per_unit"}:
        raise ValueError("Tool #7 requires explicit loss_units: physical or per_unit.")
    if options.loss_units not in {None, "physical", "per_unit"} or (options.format == "standard" and options.loss_units == "per_unit"):
        raise ValueError("Standard MatACDC uses physical loss coefficients; invalid loss_units selection.")
    ac = copy.deepcopy(dict(ac_case))
    dc = copy.deepcopy(dict(dc_case)) if dc_case is not None else None
    if str(ac.get("version", "2")) != "2":
        raise ValueError("Only PyPOWER version 2 cases are supported.")
    unknown = set(ac) - {"version", "baseMVA", "bus", "gen", "branch", "gencost"}
    if unknown:
        raise ValueError(f"Unsupported AC case fields: {sorted(unknown)}.")
    base = _positive(ac.get("baseMVA", 0), "baseMVA")
    bus = _matrix(ac, "bus", {13})
    gen = _matrix(ac, "gen", {10, 21}, empty=True)
    branch = _matrix(ac, "branch", {11, 13}, empty=True)
    if gen.shape[1] > 10 and np.any(gen[:, 10:] != 0):
        raise ValueError("Nonzero generator capability/ramp extension columns are not supported.")
    amap = _bus_map(bus, c.BUS_I, "AC bus")
    pol, dcbase = 1, base
    if dc is not None:
        unknown = set(dc) - {"baseMVAac", "baseMVAdc", "pol", "busdc", "branchdc", "convdc"}
        if unknown:
            raise ValueError(f"Unsupported DC case fields: {sorted(unknown)}.")
        if not np.isclose(_positive(dc.get("baseMVAac", 0), "baseMVAac"), base):
            raise ValueError("baseMVAac must match AC baseMVA.")
        dcbase = _positive(dc.get("baseMVAdc", 0), "baseMVAdc")
        pol = _integer(float(dc.get("pol", 0)), "pol", {1, 2})
    net = pf.create_empty_network(name=(source_names or {}).get("ac", "Custom network"), s_base=base, pol=pol)
    maps = {"ac_bus": amap, "dc_bus": {}, "branch": {}, "gen": {}, "convdc": {}, "branchdc": {}}
    warnings = ["PF does not enforce all equipment/dispatch limits; inspect OPF physics diagnostics separately."]
    if dc is not None and options.dc_voltage_convention == "legacy":
        warnings.append("DC base convention is unconfirmed: retaining source values as per-pole for compatibility. Confirm per-pole versus pole-to-pole before interpreting physical voltage/current/resistance.")
    if "gencost" in ac:
        warnings.append("gencost retained as source metadata; the objective remains active power loss minimization.")
    _create_ac(pf, net, bus, gen, branch, maps, warnings)
    if dc is not None:
        _create_dc(pf, net, dc, dcbase, maps, options, warnings)
    validate_network(net)
    validate_equipment_limits(net)
    _check_islands(net)
    report = {
        "source_files": source_names or {"ac": "dictionary", "dc": "dictionary" if dc is not None else None},
        "format": options.format if dc is not None else "pypower",
        "loss_units": (options.loss_units or "physical") if dc is not None else None,
        "base_mva": base, "base_mva_dc": dcbase, "pol": pol,
        "dc_voltage_convention": options.dc_voltage_convention,
        "native_dc_voltage_convention": "per_pole",
        "native_dc_resistance_convention": "per_conductor",
        "counts": {name: len(getattr(net, name)) for name in ("ac_bus", "ac_line", "trafo", "ac_gen", "dc_bus", "dc_line", "vsc")},
        "id_maps": maps,
        "converters": [{"source_row": int(row.source_row), "dc_bus_id": int(row.source_dc_bus_id), "rating_mva": float(row.s_mva), "max_i_ac_ka": float(row.max_i_ac_ka), "loss_a_mw": float(row.loss_a), "loss_b_kv": float(row.loss_b), "loss_c_ohm": float(row.loss_c), "loss_c_inv_ohm": float(row.loss_c_inv)} for _, row in net.vsc.iterrows()],
        "warnings": warnings,
    }
    net.import_metadata = copy.deepcopy(report)
    return ImportedNetwork(net, ac, dc, maps, report)


def _create_ac(pf, net, bus, gen, branch, maps, warnings):
    amap, base = maps["ac_bus"], net.s_base
    for n, row in enumerate(bus):
        kind = _integer(row[c.BUS_TYPE], f"bus[{n}].type", {1, 2, 3, 4})
        _bounds(row[c.VMIN], row[c.VMAX], f"bus[{n}]")
        _positive(row[c.BASE_KV], f"bus[{n}].baseKV")
        _positive(row[c.VM], f"bus[{n}].Vm")
        idx = pf.create_ac_bus(net, row[c.BASE_KV], name=f"AC bus {int(row[c.BUS_I])}", v_min_pu=row[c.VMIN], v_max_pu=row[c.VMAX], gs_pu=row[c.GS]/base, bs_pu=row[c.BS]/base, is_slack=kind == 3, in_service=kind != 4)
        _tag(net.ac_bus, idx, n, source_bus_id=int(row[c.BUS_I]), initial_vm_pu=row[c.VM], initial_va_deg=row[c.VA])
        if row[c.PD] or row[c.QD]:
            li = pf.create_ac_load(net, idx, row[c.PD], row[c.QD], name=f"Load at AC {int(row[c.BUS_I])}", in_service=kind != 4)
            _tag(net.ac_load, li, n, source_bus_id=int(row[c.BUS_I]))
    for n, row in enumerate(gen):
        idx_bus = _reference(amap, row[c.GEN_BUS], f"gen[{n}]")
        status = _integer(row[c.GEN_STATUS], f"gen[{n}].status", {0, 1})
        if row[c.PMIN] > row[c.PMAX] or row[c.QMIN] > row[c.QMAX]:
            raise ValueError(f"gen[{n}]: inverted dispatch limits.")
        _positive(row[c.VG], f"gen[{n}].Vg")
        idx = pf.create_ac_gen(net, idx_bus, p_mw=row[c.PG], q_mvar=row[c.QG], v_pu=row[c.VG] if bus[idx_bus, c.BUS_TYPE] in {2, 3} else None, q_min_mvar=row[c.QMIN], q_max_mvar=row[c.QMAX], p_min_mw=row[c.PMIN], p_max_mw=row[c.PMAX], name=f"Generator {n} at AC {int(row[c.GEN_BUS])}", in_service=bool(status))
        _tag(net.ac_gen, idx, n, source_bus_id=int(row[c.GEN_BUS]))
        maps["gen"][n] = int(idx)
    for n, row in enumerate(bus):
        if row[c.BUS_TYPE] in {2, 3}:
            generators = net.ac_gen[(net.ac_gen.bus == n) & net.ac_gen.in_service.astype(bool)]
            if generators.empty:
                raise ValueError(f"AC bus {int(row[c.BUS_I])}: PV/slack bus has no active generator.")
            if generators.v_pu.nunique() != 1:
                raise ValueError(f"AC bus {int(row[c.BUS_I])}: conflicting generator voltage targets.")
    for n, row in enumerate(branch):
        start = _reference(amap, row[c.F_BUS], f"branch[{n}].from")
        end = _reference(amap, row[c.T_BUS], f"branch[{n}].to")
        status = _integer(row[c.BR_STATUS], f"branch[{n}].status", {0, 1})
        if start == end or row[c.BR_R] < 0 or (row[c.BR_R] == 0 and row[c.BR_X] == 0):
            raise ValueError(f"branch[{n}]: invalid impedance or self-loop.")
        if row[c.TAP] < 0 or np.any(row[c.RATE_A:c.RATE_C+1] < 0):
            raise ValueError(f"branch[{n}]: negative tap/rating.")
        if len(row) == 13 and ((row[c.ANGMIN] not in {0, -360}) or (row[c.ANGMAX] not in {0, 360})):
            raise ValueError(f"branch[{n}]: finite angle-difference limits are not supported.")
        rated = float(row[c.RATE_A])
        if rated == 0:
            warnings.append(f"branch[{n}]: rateA=0 means no apparent-power limit.")
        kwargs = dict(from_bus=start, to_bus=end, name=f"AC branch {n}: {int(row[c.F_BUS])}-{int(row[c.T_BUS])}", in_service=bool(status))
        if row[c.TAP] != 0 or row[c.SHIFT] != 0 or bus[start, c.BASE_KV] != bus[end, c.BASE_KV]:
            if rated <= 0:
                raise ValueError(f"branch[{n}]: native transformers require a positive rateA; unrated transformer import is unsupported.")
            # Native transformer impedance is on its own MVA rating.
            idx = pf.create_transformer(net, **kwargs, sn_mva=rated, r_pu=row[c.BR_R]*rated/base, x_pu=row[c.BR_X]*rated/base, b_pu=row[c.BR_B]*base/rated, vn_from_kv=bus[start,c.BASE_KV], vn_to_kv=bus[end,c.BASE_KV], tap=row[c.TAP] or 1.0, shift_deg=row[c.SHIFT])
            table_name = "trafo"
        else:
            zbase = bus[start, c.BASE_KV]**2/base
            idx = pf.create_ac_line(net, **kwargs, length_km=1.0, r_ohm_per_km=row[c.BR_R]*zbase, x_ohm_per_km=row[c.BR_X]*zbase, b_us_per_km=row[c.BR_B]/zbase*1e6)
            table_name = "ac_line"
            net.ac_line.at[idx, "rate_mva"] = rated
        _tag(getattr(net, table_name), idx, n, source_from_bus_id=int(row[c.F_BUS]), source_to_bus_id=int(row[c.T_BUS]))
        maps["branch"][n] = {"table": table_name, "index": int(idx)}


def _create_dc(pf, net, dc, dcbase, maps, options, warnings):
    buses = _matrix(dc, "busdc", {9})
    if options.dc_voltage_convention == "pole_to_pole":
        if int(net.pol) != 2:
            raise ValueError("pole_to_pole conversion requires a symmetric bipolar network (pol=2).")
        # Local matrix copy only. R conversion below uses this same per-pole base;
        # source per-unit R, DC power equations and original matrices are preserved.
        buses[:, c.DC_KV] /= 2.0
        warnings.append("Symmetric bipolar base converted from pole-to-pole to per-pole; physical R uses the converted base squared. Confirm source per-unit impedance convention.")
    branches = _matrix(dc, "branchdc", {9, 10}, empty=True)
    converters = _matrix(dc, "convdc", {20}, empty=True)
    dmap = maps["dc_bus"] = _bus_map(buses, c.DC_BUS, "DC bus")
    for n, row in enumerate(buses):
        _bounds(row[c.DC_VMIN], row[c.DC_VMAX], f"busdc[{n}]")
        _positive(row[c.DC_KV], f"busdc[{n}].basekVdc")
        _positive(row[c.DC_VM], f"busdc[{n}].Vdc")
        _integer(row[c.DC_GRID], f"busdc[{n}].grid")
        if row[c.DC_CAP] != 0:
            raise ValueError(f"busdc[{n}]: nonzero capacitance is unsupported in this steady-state importer.")
        if row[c.DC_AC_BUS] != 0:
            _reference(maps["ac_bus"], row[c.DC_AC_BUS], f"busdc[{n}].busac")
        idx = pf.create_dc_bus(net, row[c.DC_KV], name=f"DC bus {int(row[c.DC_BUS])}", dc_grid=int(row[c.DC_GRID]), v_dc_pu=row[c.DC_VM], v_min=row[c.DC_VMIN], v_max=row[c.DC_VMAX])
        _tag(net.dc_bus, idx, n, source_bus_id=int(row[c.DC_BUS]))
        if row[c.DC_PD] > 0:
            li = pf.create_dc_load(net, idx, p_mw=row[c.DC_PD], name=f"Load at DC {int(row[c.DC_BUS])}")
            _tag(net.dc_load, li, n, source_bus_id=int(row[c.DC_BUS]))
        elif row[c.DC_PD] < 0:
            gi = pf.create_dc_gen(net, idx, p_mw=-row[c.DC_PD], name=f"Generation at DC {int(row[c.DC_BUS])}")
            _tag(net.dc_gen, gi, n, source_bus_id=int(row[c.DC_BUS]))
    for n, row in enumerate(branches):
        start = _reference(dmap, row[c.DC_FROM], f"branchdc[{n}].from")
        end = _reference(dmap, row[c.DC_TO], f"branchdc[{n}].to")
        status = _integer(row[c.DC_STATUS], f"branchdc[{n}].status", {0, 1})
        _positive(row[c.DC_R], f"branchdc[{n}].r")
        if start == end or not np.isclose(buses[start,c.DC_KV], buses[end,c.DC_KV]):
            raise ValueError(f"branchdc[{n}]: ordinary DC cable must connect distinct buses of equal base voltage.")
        if row[c.DC_L] or row[c.DC_C] or (len(row) == 10 and row[c.DC_RATIO] != 1):
            raise ValueError(f"branchdc[{n}]: nonzero L/C or DC/DC ratio is unsupported; only ordinary DC cables are supported.")
        if np.any(row[c.DC_RATE_A:c.DC_RATE_C+1] < 0):
            raise ValueError(f"branchdc[{n}]: negative rating.")
        idx = pf.create_dc_line(net, start, end, length_km=1.0, r_ohm_per_km=row[c.DC_R]*buses[start,c.DC_KV]**2/dcbase, name=f"DC branch {n}: {int(row[c.DC_FROM])}-{int(row[c.DC_TO])}", in_service=bool(status))
        net.dc_line.at[idx, "rate_mw"] = row[c.DC_RATE_A]
        if row[c.DC_RATE_A] == 0:
            warnings.append(f"branchdc[{n}]: rateA=0 means no terminal-power limit.")
        _tag(net.dc_line, idx, n, source_from_bus_id=int(row[c.DC_FROM]), source_to_bus_id=int(row[c.DC_TO]))
        maps["branchdc"][n] = int(idx)
    seen = set()
    for n, row in enumerate(converters):
        db = _reference(dmap, row[c.CV_BUS], f"convdc[{n}]")
        if db in seen:
            raise ValueError(f"convdc[{n}]: duplicate converter DC terminal.")
        seen.add(db)
        ab = _reference(maps["ac_bus"], buses[db,c.DC_AC_BUS], f"convdc[{n}].AC connection")
        dtype = _integer(row[c.CV_DC_TYPE], f"convdc[{n}].type_dc", {1, 2})
        atype = _integer(row[c.CV_AC_TYPE], f"convdc[{n}].type_ac", {1, 2})
        status = _integer(row[c.CV_STATUS], f"convdc[{n}].status", {0, 1})
        _bounds(row[c.CV_VMIN], row[c.CV_VMAX], f"convdc[{n}]")
        kv = _positive(row[c.CV_KV], f"convdc[{n}].basekVac")
        if not np.isclose(kv, net.ac_bus.at[ab,"vr_kv"]):
            raise ValueError(f"convdc[{n}]: converter and connected AC bus voltage bases must match.")
        imax = _positive(row[c.CV_IMAX], f"convdc[{n}].Imax")
        ibase = net.s_base/(np.sqrt(3)*kv)
        ika = imax*ibase if options.format == "standard" else imax
        coeff = row[c.LOSS_A:c.LOSS_CI+1].copy()
        if np.any(coeff < 0) or row[c.CV_RTF] < 0 or row[c.CV_RC] < 0:
            raise ValueError(f"convdc[{n}]: negative resistance/loss coefficient.")
        if options.loss_units == "per_unit":
            coeff *= np.array([net.s_base, net.s_base/ibase, net.s_base/ibase**2, net.s_base/ibase**2])
        if atype == 2:
            _positive(row[c.CV_VAC], f"convdc[{n}].Vtar")
        mode = ("vdc" if dtype == 2 else "p") + ("_vac" if atype == 2 else "_q")
        idx = pf.create_vsc(net, ab, db, s_mva=np.sqrt(3)*kv*ika, control_mode=mode, p_mw=-row[c.CV_P], q_mvar=-row[c.CV_Q], v_ac_pu=row[c.CV_VAC] if atype == 2 else None, v_dc_pu=buses[db,c.DC_VM] if dtype == 2 else None, loss_a=coeff[0], loss_b=coeff[1], loss_c=coeff[2], loss_c_inv=coeff[3], r_tf_pu=row[c.CV_RTF], x_tf_pu=row[c.CV_XTF], r_c_pu=row[c.CV_RC], x_c_pu=row[c.CV_XC], b_filter_pu=row[c.CV_BF], loss_base_kv=kv, name=f"VSC {n} at DC {int(row[c.CV_BUS])}", in_service=bool(status))
        net.vsc.at[idx,"max_i_ac_ka"] = ika
        net.vsc.at[idx,"v_converter_min_pu"] = row[c.CV_VMIN]
        net.vsc.at[idx,"v_converter_max_pu"] = row[c.CV_VMAX]
        if dtype == 2 and status:
            net.dc_bus.at[db,"bus_type"] = "vdc"
        _tag(net.vsc, idx, n, source_dc_bus_id=int(row[c.CV_BUS]), source_ac_bus_id=int(buses[db,c.DC_AC_BUS]))
        maps["convdc"][n] = int(idx)
    warnings.append("DC rateA is interpreted as MW; rateB/rateC remain source metadata. Converter Vmmin/Vmmax constrain bridge voltage in OPF; PF does not enforce these limits.")
    warnings.append("Converter apparent-power ratings are derived from Imax at nominal AC voltage and enforced alongside explicit current limits.")
