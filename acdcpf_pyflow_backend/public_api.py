"""Adapt Tool1 tables to the unmodified public Tool5 run_pf interface.

Only the private solve view is translated. Source tables, limits, IDs, and the
Tool1 OPF equations are preserved. Transformer parameters are converted from
equipment base to the public solver's tapped AC-line representation. Signed
storage injections become negative loads in this temporary PF view only.
"""
from __future__ import annotations
import copy
import warnings
import numpy as np
import pandas as pd
from ._bootstrap import backend_info, ensure_acdcpf_importable
from .network_factory import normalize_network
from .network_validation import ELEMENT_REFERENCES, validate_network


def solve_network(net, *, policy="unconstrained", enforce_slack_q_limits=False,
                  max_iter_outer=30, max_iter_inner=30, tolerance=1e-8, verbose=False):
    ensure_acdcpf_importable()
    import acdcpf as pf
    if policy not in {"unconstrained", "converter_limited"}:
        raise ValueError("Unknown PF policy: " + policy)
    if enforce_slack_q_limits and policy != "converter_limited":
        raise ValueError("Slack reactive limiting requires converter_limited policy.")
    if callable(getattr(pf, "solve", None)):
        return pf.solve(net, copy_network=False, options=pf.PFOptions(
            policy=policy, enforce_slack_q_limits=enforce_slack_q_limits,
            max_iter_outer=max_iter_outer, max_iter_inner=max_iter_inner,
            tolerance=tolerance, verbose=verbose)).converged
    normalize_network(net)
    validate_network(net)
    work, maps, line_sources = _prepare(net, pf)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        converged = pf.run_pf(work, max_iter_outer=max_iter_outer, max_iter_inner=max_iter_inner,
                            tol=tolerance, verbose=verbose, enforce_limits=policy == "converter_limited",
                            enforce_slack_q_limits=enforce_slack_q_limits)
    _use_solved_branch_flows(work)
    messages = list(dict.fromkeys(str(w.message) for w in caught if not issubclass(w.category, FutureWarning)))
    changes = []
    for internal, external in maps["vsc"].items():
        source = net.vsc.loc[external]
        p, q = float(work._p_s[internal]), float(work._q_s[internal])
        disabled_v = internal in work._vsc_vcontrol_disabled
        disabled_d = internal in work._vsc_droop_disabled
        clamped = str(source.control_mode) == "p_q" and (
            abs(p-float(source.p_mw)) > tolerance or abs(q-float(source.q_mvar)) > tolerance)
        if policy == "converter_limited" and (disabled_v or disabled_d or clamped):
            changes.append(dict(vsc_id=int(external), requested_mode=str(source.control_mode),
                                ac_voltage_control_dropped=disabled_v, droop_control_dropped=disabled_d,
                                requested_p_mw=float(source.p_mw), requested_q_mvar=float(source.q_mvar),
                                solved_p_ac_mw=p, solved_q_ac_mvar=q))
    for table_name in ("ac_bus", "dc_bus", "ac_gen", "dc_gen", "dc_line", "vsc", "dcdc"):
        result = getattr(work, "res_"+table_name).copy()
        result.index = [maps[table_name][int(i)] for i in result.index]
        if "bus" in result:
            kind = "ac_bus" if table_name == "ac_gen" else "dc_bus"
            result["bus"] = result.bus.map(maps[kind])
        # Keep application order, including networks whose slack generator was reordered.
        result = result.reindex([i for i in getattr(net, table_name).index if i in result.index])
        setattr(net, "res_"+table_name, result)
    for kind in ("ac_line", "trafo"):
        selected = {i: external for i, (table, external) in line_sources.items() if table == kind}
        result = work.res_ac_line.loc[list(selected)].copy() if selected else work.res_ac_line.iloc[:0].copy()
        result.index = list(selected.values())
        if kind == "trafo" and not result.empty:
            apparent = np.maximum(np.hypot(result.p_from_mw, result.q_from_mvar),
                                  np.hypot(result.p_to_mw, result.q_to_mvar))
            result["loading_percent"] = apparent / net.trafo.loc[result.index, "sn_mva"].astype(float) * 100
        setattr(net, "res_"+kind, result)
    storage = net.storage[net.storage.in_service.astype(bool)].copy()
    net.res_storage = storage[[c for c in ("name", "bus", "bus_type", "p_mw", "q_mvar", "soc_percent") if c in storage]]
    if len(net.vsc) and np.any(net.vsc.b_filter_pu.astype(float) != 0):
        messages.append("Tool1 uses the selected public Tool5 converter-filter convention. Results can differ from the previous fork; review independent physics residuals.")
    messages.append("PF convergence does not establish equipment-limit feasibility; inspect Tool1 physics diagnostics.")
    violations = []
    for idx, row in net.res_vsc.iterrows():
        for field in ("loading_percent", "i_c_loading_percent"):
            value = float(row.get(field, np.nan))
            if np.isfinite(value) and value > 100.001:
                violations.append(dict(equipment="vsc", id=int(idx), quantity=field, value=value))
        for field, sign in (("vc_min_pu", -1), ("vc_max_pu", 1)):
            limit = net.vsc.loc[idx].get(field)
            value = float(row.v_converter_pu)
            if limit is not None and np.isfinite(float(limit)) and sign*(value-float(limit)) > tolerance:
                violations.append(dict(equipment="vsc", id=int(idx), quantity=field, value=value, limit=float(limit)))
    finite = all(np.isfinite(table[[c for c in fields if c in table]].to_numpy(dtype=float)).all()
                 for table, fields in (
                     (net.res_ac_bus, ("v_pu", "v_angle_deg")),
                     (net.res_dc_bus, ("v_dc_pu",)),
                     (net.res_vsc, ("p_ac_mw", "q_ac_mvar", "p_dc_mw", "v_converter_pu", "i_ac_ka")),
                 ))
    net.converged = bool(converged and finite)
    net._tool5_report = dict(backend_info(), policy=policy, warnings=messages, control_changes=changes,
                            equipment_violations=violations, equipment_limits_status="partially_checked",
                            residuals={}, residuals_status="not_exposed_by_public_tool5",
                            unchecked_limits=["generator dispatch bounds", "storage SOC evolution", "OPF feasibility"])
    return net.converged


def _use_solved_branch_flows(work):
    """Use PYPOWER's solved terminal powers, including phase-shifting taps.

    Public Tool5 recomputes branch results separately from its AC solve. Reading
    the solved branch rows avoids its phase-shift sign discrepancy in that report.
    """
    from pypower.idx_brch import PF, QF, PT, QT
    for buses, ppc in getattr(work, "_ppc_results", {}).items():
        lines = work.ac_line[work.ac_line.from_bus.isin(buses) & work.ac_line.to_bus.isin(buses)]
        if len(lines) != len(ppc["branch"]):
            raise ValueError("Tool5 solved branch mapping differs from its source network.")
        for (idx, row), values in zip(lines.iterrows(), ppc["branch"]):
            if idx not in work.res_ac_line.index:
                continue
            for field, column in (("p_from_mw", PF), ("q_from_mvar", QF), ("p_to_mw", PT), ("q_to_mvar", QT)):
                work.res_ac_line.at[idx, field] = values[column]
            work.res_ac_line.at[idx, "p_loss_mw"] = values[PF]+values[PT]
            work.res_ac_line.at[idx, "q_loss_mvar"] = values[QF]+values[QT]
            currents = [np.hypot(values[p], values[q]) / (np.sqrt(3)*float(work.ac_bus.at[int(bus), "vr_kv"])*float(work.res_ac_bus.at[int(bus), "v_pu"]))
                        for p, q, bus in ((PF, QF, row.from_bus), (PT, QT, row.to_bus))]
            work.res_ac_line.at[idx, "i_ka"] = max(currents)
            limit = row.get("max_i_ka")
            if limit is not None and np.isfinite(float(limit)) and float(limit) > 0:
                work.res_ac_line.at[idx, "loading_percent"] = max(currents)/float(limit)*100


def _prepare(net, pf):
    work = copy.deepcopy(net)
    maps = {}
    for name in ("ac_bus", "dc_bus", *ELEMENT_REFERENCES):
        table = getattr(work, name)
        table = table.loc[table.in_service.astype(bool)].copy() if "in_service" in table else table.copy()
        if name == "ac_gen" and not table.empty:
            # Public Tool5 chooses the first generator in each island as reference.
            slack = set(net.ac_bus.index[net.ac_bus.is_slack.astype(bool)])
            table = table.loc[sorted(table.index, key=lambda i: int(table.loc[i, "bus"]) not in slack)]
        maps[name] = dict(enumerate(table.index))
        table.index = range(len(table))
        setattr(work, name, table)
    inverse = {name: {external: dense for dense, external in ids.items()} for name, ids in maps.items()}
    for name, refs in ELEMENT_REFERENCES.items():
        table = getattr(work, name)
        if name == "storage":
            for idx, row in table.iterrows():
                table.at[idx, "bus"] = inverse[str(row.bus_type)+"_bus"][row.bus]
        else:
            for column, bus_table in refs.items():
                if column in table:
                    table[column] = table[column].map(inverse[bus_table])
    if not work.vsc.empty and work.vsc.control_mode.str.startswith("pdc").any():
        raise NotImplementedError("Public Tool5 does not support fixed-Pdc VSC controls. Use supported P-ac/Vdc/droop controls or a backend implementing fixed-Pdc.")
    # The public _net_to_ppc passes these columns to PYPOWER in MW/MVAr.
    # Tool1 keeps them per-unit; scale only the temporary PF view.
    for column in ("gs_pu", "bs_pu"):
        work.ac_bus[column] = work.ac_bus[column].astype(float)*float(net.s_base)
    line_sources = {i: ("ac_line", external) for i, external in maps["ac_line"].items()}
    for idx, row in work.trafo.iterrows():
        kv = float(work.ac_bus.loc[int(row.from_bus), "vr_kv"])
        nominal = float(row.vn_from_kv) if pd.notna(row.vn_from_kv) else kv
        to_kv = float(work.ac_bus.loc[int(row.to_bus), "vr_kv"])
        nominal_to = float(row.vn_to_kv) if pd.notna(row.vn_to_kv) else to_kv
        ratio = (nominal/nominal_to)/(kv/to_kv)
        zbase = nominal**2/float(row.sn_mva)
        line = pf.create_ac_line(work, int(row.from_bus), int(row.to_bus), length_km=1.,
                                 r_ohm_per_km=float(row.r_pu)*zbase,
                                 x_ohm_per_km=float(row.x_pu)*zbase,
                                 b_us_per_km=float(row.b_pu)/zbase*1e6,
                                 tap=float(row.tap)*ratio, shift_deg=float(row.shift_deg), name=str(row['name']))
        line_sources[line] = ("trafo", maps["trafo"][idx])
    for _, row in work.storage.iterrows():
        if row.bus_type == "ac":
            pf.create_ac_load(work, int(row.bus), p_mw=-float(row.p_mw), q_mvar=-float(row.q_mvar))
        else:
            pf.create_dc_load(work, int(row.bus), p_mw=-float(row.p_mw))
    return work, maps, line_sources
