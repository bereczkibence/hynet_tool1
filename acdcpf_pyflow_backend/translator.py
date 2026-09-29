from __future__ import annotations

from dataclasses import dataclass
from inspect import signature
from math import degrees, sqrt

from ._bootstrap import ensure_acdcpf_importable
from .datatypes import BackendMapping


@dataclass
class TranslationArtifacts:
    net: object
    mapping: BackendMapping


def build_acdcpf_network_from_pyflow(grid) -> TranslationArtifacts:
    """
    Translate a pyflow_acdc Grid into an acdcpf Network.

    The mapping preserves node and converter order so result tables can be
    written back to the original grid predictably.
    """
    ensure_acdcpf_importable()
    import acdcpf as pf

    if getattr(grid, "Converters_DCDC", []):
        raise NotImplementedError(
            "The separate acdcpf backend project does not yet translate "
            "pyflow DCDC converters."
        )

    net = pf.create_empty_network(
        name=getattr(grid, "name", "pyflow-grid"),
        s_base=float(grid.S_base),
        pol=_infer_global_dc_polarity(grid),
    )
    supports_ac_slack_flag = "is_slack" in signature(pf.create_ac_bus).parameters

    ac_bus_map: dict[int, int] = {}
    dc_bus_map: dict[int, int] = {}
    ac_line_map: dict[int, int] = {}
    dc_line_map: dict[int, int] = {}
    vsc_map: dict[int, int] = {}

    extra_bus_shunts = _collect_ac_line_shunts(grid)

    for node in grid.nodes_AC:
        shunt_g, shunt_b = extra_bus_shunts.get(node.nodeNumber, (0.0, 0.0))
        bus_kwargs = {
            "vr_kv": float(node.kV_base),
            "name": node.name,
            "v_min_pu": float(node.Umin),
            "v_max_pu": float(node.Umax),
            "gs_pu": float(getattr(node.Reactor, "real", 0.0)) + shunt_g,
            "bs_pu": float(getattr(node.Reactor, "imag", 0.0)) + shunt_b,
        }
        if supports_ac_slack_flag:
            bus_kwargs["is_slack"] = str(node.type) == "Slack"
        bus_idx = pf.create_ac_bus(net, **bus_kwargs)
        ac_bus_map[node.nodeNumber] = bus_idx

    for node in grid.nodes_DC:
        bus_idx = pf.create_dc_bus(
            net,
            v_base=float(node.kV_base),
            name=node.name,
            dc_grid=int(_dc_grid_id(grid, node.nodeNumber)),
            bus_type=_map_dc_bus_type(node.type),
            v_dc_pu=float(node.V),
            v_min=float(node.Umin),
            v_max=float(node.Umax),
        )
        dc_bus_map[node.nodeNumber] = bus_idx

    for line in grid.lines_AC:
        vr_kv = float(line.fromNode.kV_base)
        z_base = vr_kv ** 2 / float(grid.S_base)
        line_idx = pf.create_ac_line(
            net,
            from_bus=ac_bus_map[line.fromNode.nodeNumber],
            to_bus=ac_bus_map[line.toNode.nodeNumber],
            length_km=max(float(line.Length_km), 1.0),
            r_ohm_per_km=float(line.R * z_base / max(float(line.Length_km), 1.0)),
            x_ohm_per_km=float(line.X * z_base / max(float(line.Length_km), 1.0)),
            b_us_per_km=float(line.B / z_base * 1e6 / max(float(line.Length_km), 1.0)),
            g_us_per_km=0.0,
            tap=float(getattr(line, "m", 1.0)),
            shift_deg=degrees(float(getattr(line, "shift", 0.0))),
            max_i_ka=_ac_line_rating_to_ka(line, vr_kv),
            name=line.name,
        )
        ac_line_map[line.lineNumber] = line_idx

    for line in grid.lines_DC:
        z_base = float(line.kV_base) ** 2 / float(grid.S_base)
        length_km = max(float(getattr(line, "km", 1.0)), 1.0)
        line_idx = pf.create_dc_line(
            net,
            from_bus=dc_bus_map[line.fromNode.nodeNumber],
            to_bus=dc_bus_map[line.toNode.nodeNumber],
            length_km=length_km,
            r_ohm_per_km=float(line.R * z_base / length_km),
            max_i_ka=_dc_line_rating_to_ka(line),
            name=line.name,
        )
        dc_line_map[line.lineNumber] = line_idx

    for node in grid.nodes_AC:
        _add_ac_injections(pf, net, grid, node, ac_bus_map[node.nodeNumber])

    for node in grid.nodes_DC:
        _add_dc_injections(pf, net, grid, node, dc_bus_map[node.nodeNumber])

    for conv in grid.Converters_ACDC:
        control = _map_converter_control_mode(conv)
        kwargs = _build_vsc_kwargs(grid, conv, control, ac_bus_map, dc_bus_map)
        vsc_idx = pf.create_vsc(net, **kwargs)
        vsc_map[conv.ConvNumber] = vsc_idx

    mapping = BackendMapping(
        ac_bus_by_pyflow_index=ac_bus_map,
        dc_bus_by_pyflow_index=dc_bus_map,
        ac_line_by_pyflow_index=ac_line_map,
        dc_line_by_pyflow_index=dc_line_map,
        vsc_by_pyflow_index=vsc_map,
    )
    return TranslationArtifacts(net=net, mapping=mapping)


def _collect_ac_line_shunts(grid) -> dict[int, tuple[float, float]]:
    """
    acdcpf's line builder does not model line conductance explicitly.

    Preserve pyflow's line shunt conductance by splitting G/2 onto the
    connected buses as bus shunts, while keeping B on the line model.
    """
    shunts: dict[int, tuple[float, float]] = {}
    for node in grid.nodes_AC:
        shunts[node.nodeNumber] = (0.0, 0.0)

    for line in grid.lines_AC:
        if float(getattr(line, "G", 0.0)) == 0.0:
            continue
        half_g = float(line.G) / 2.0
        from_g, from_b = shunts[line.fromNode.nodeNumber]
        to_g, to_b = shunts[line.toNode.nodeNumber]
        shunts[line.fromNode.nodeNumber] = (from_g + half_g, from_b)
        shunts[line.toNode.nodeNumber] = (to_g + half_g, to_b)
    return shunts


def _infer_global_dc_polarity(grid) -> int:
    if getattr(grid, "lines_DC", []):
        polarities = {int(getattr(line, "pol", 1)) for line in grid.lines_DC}
        if len(polarities) > 1:
            raise NotImplementedError(
                "acdcpf uses one network-wide DC pole count. Mixed pyflow DC "
                "line polarities are not supported by this adapter."
            )
        return polarities.pop()

    if getattr(grid, "Converters_ACDC", []):
        polarities = {int(getattr(conv, "cn_pol", 1)) for conv in grid.Converters_ACDC}
        if len(polarities) == 1:
            return polarities.pop()
    return 2


def _dc_grid_id(grid, node_number: int) -> int:
    return int(getattr(grid, "Graph_node_to_Grid_index_DC", {}).get(node_number, 0))


def _map_dc_bus_type(node_type: str) -> str:
    if node_type == "Slack":
        return "vdc"
    if node_type == "Droop":
        return "droop"
    return "p"


def _ac_line_rating_to_ka(line, vr_kv: float) -> float | None:
    rating_mva = float(getattr(line, "MVA_rating", 0.0))
    if rating_mva <= 0.0 or rating_mva >= 9999:
        return None
    return rating_mva / (sqrt(3.0) * vr_kv)


def _dc_line_rating_to_ka(line) -> float | None:
    rating_mw = float(getattr(line, "MW_rating", 0.0))
    if rating_mw <= 0.0 or rating_mw >= 9999:
        return None
    return rating_mw / float(line.kV_base)


def _add_ac_injections(pf, net, grid, node, bus_idx: int) -> None:
    s_base = float(grid.S_base)

    if float(node.PLi) != 0.0 or float(node.QLi) != 0.0:
        pf.create_ac_load(
            net,
            bus=bus_idx,
            p_mw=float(node.PLi) * s_base,
            q_mvar=float(node.QLi) * s_base,
            name=f"{node.name}_load",
        )

    added_generator = False
    bus_q_min, bus_q_max = _node_q_limits(node)

    if (
        float(node.PGi) != 0.0
        or float(node.QGi) != 0.0
        or (
            node.type in {"Slack", "PV"}
            and not getattr(node, "connected_gen", [])
            and not _has_voltage_controlling_vsc(grid, node)
        )
    ):
        pf.create_ac_gen(
            net,
            bus=bus_idx,
            p_mw=float(node.PGi) * s_base,
            q_mvar=float(node.QGi) * s_base,
            v_pu=float(node.V if node.type in {"Slack", "PV"} else None)
            if node.type in {"Slack", "PV"}
            else None,
            q_min_mvar=bus_q_min * s_base,
            q_max_mvar=bus_q_max * s_base,
            name=f"{node.name}_busgen",
        )
        added_generator = True

    for gen in getattr(node, "connected_gen", []):
        pf.create_ac_gen(
            net,
            bus=bus_idx,
            p_mw=float(gen.PGen) * s_base,
            q_mvar=float(gen.QGen) * s_base,
            v_pu=float(node.V) if node.type in {"Slack", "PV"} else None,
            q_min_mvar=_finite_or_default(float(getattr(gen, "Min_pow_genR", -1e9)), -1e9) * s_base,
            q_max_mvar=_finite_or_default(float(getattr(gen, "Max_pow_genR", 1e9)), 1e9) * s_base,
            name=gen.name,
        )
        added_generator = True

    for ren in getattr(node, "connected_RenSource", []):
        p_ren = float(ren.PGi_ren) * float(getattr(ren, "gamma", 1.0))
        q_ren = float(getattr(ren, "QGi_ren", 0.0))
        if p_ren == 0.0 and q_ren == 0.0:
            continue
        pf.create_ac_gen(
            net,
            bus=bus_idx,
            p_mw=p_ren * s_base,
            q_mvar=q_ren * s_base,
            v_pu=None,
            q_min_mvar=-1e9,
            q_max_mvar=1e9,
            name=ren.name,
        )
        added_generator = True

    if (
        node.type in {"Slack", "PV"}
        and not added_generator
        and not _has_voltage_controlling_vsc(grid, node)
    ):
        pf.create_ac_gen(
            net,
            bus=bus_idx,
            p_mw=0.0,
            q_mvar=0.0,
            v_pu=float(node.V),
            q_min_mvar=-1e9,
            q_max_mvar=1e9,
            name=f"{node.name}_slackgen",
        )


def _add_dc_injections(pf, net, grid, node, bus_idx: int) -> None:
    s_base = float(grid.S_base)

    if float(node.PLi) != 0.0:
        pf.create_dc_load(
            net,
            bus=bus_idx,
            p_mw=float(node.PLi) * s_base,
            name=f"{node.name}_load",
        )

    if float(node.PGi) != 0.0:
        pf.create_dc_gen(
            net,
            bus=bus_idx,
            p_mw=float(node.PGi) * s_base,
            name=f"{node.name}_busgen",
        )

    for gen in getattr(node, "connected_gen", []):
        p_mw = float(getattr(gen, "PGen", 0.0)) * s_base
        if p_mw >= 0.0:
            pf.create_dc_gen(net, bus=bus_idx, p_mw=p_mw, name=gen.name)
        else:
            pf.create_dc_load(net, bus=bus_idx, p_mw=-p_mw, name=gen.name)

    for ren in getattr(node, "connected_RenSource", []):
        p_mw = float(ren.PGi_ren) * float(getattr(ren, "gamma", 1.0)) * s_base
        if p_mw != 0.0:
            pf.create_dc_gen(net, bus=bus_idx, p_mw=p_mw, name=ren.name)


def _map_converter_control_mode(conv) -> str:
    dc_type = str(conv.type)
    ac_type = str(conv.AC_type)

    if dc_type == "PAC":
        if ac_type in {"PV", "Slack"}:
            return "p_vac"
        return "p_q"

    if dc_type == "P":
        if ac_type in {"PV", "Slack"}:
            return "pdc_vac"
        return "pdc_q"

    if dc_type == "Slack":
        if ac_type in {"PV", "Slack"}:
            return "vdc_vac"
        return "vdc_q"

    if dc_type == "Droop":
        if ac_type in {"PV", "Slack"}:
            return "droop_vac"
        return "droop_q"

    raise NotImplementedError(
        f"Unsupported pyflow converter control combination: DC={dc_type}, AC={ac_type}"
    )


def _build_vsc_kwargs(grid, conv, control: str, ac_bus_map, dc_bus_map) -> dict:
    s_base = float(grid.S_base)
    kwargs = {
        "ac_bus": ac_bus_map[conv.Node_AC.nodeNumber],
        "dc_bus": dc_bus_map[conv.Node_DC.nodeNumber],
        "s_mva": float(conv.MVA_max),
        "control_mode": control,
        # pyflow and acdcpf use opposite AC-side converter sign conventions.
        # For PyFlow DC-side P control, p_mw is only an initial AC-side guess;
        # p_dc_set_mw below is the actual active-power setpoint.
        "p_mw": (
            float(conv.P_DC) * s_base
            if str(conv.type) == "P"
            else -float(conv.P_AC) * s_base
        ),
        "q_mvar": -float(conv.Q_AC) * s_base,
        "loss_a": float(conv.a_conv_og),
        "loss_b": float(conv.b_conv_og),
        "loss_c": float(conv.c_rect_og),
        "loss_c_inv": float(conv.c_inver_og),
        "r_tf_pu": float(conv.R_t),
        "x_tf_pu": float(conv.X_t),
        "r_c_pu": float(conv.PR_R),
        "x_c_pu": float(conv.PR_X),
        "b_filter_pu": float(conv.Bf),
        "loss_base_kv": float(conv.AC_kV_base),
        "name": conv.name,
    }

    if "vac" in control:
        kwargs["v_ac_pu"] = float(conv.Node_AC.V)
    if "vdc" in control:
        kwargs["v_dc_pu"] = float(conv.Node_DC.V)
    if control.startswith("pdc"):
        kwargs["p_dc_set_mw"] = float(conv.P_DC) * s_base
    if "droop" in control:
        kwargs["droop_kv_per_mw"] = _droop_kv_per_mw(grid, conv)
        kwargs["p_dc_set_mw"] = float(conv.P_DC) * s_base
        kwargs["v_dc_set_pu"] = float(conv.Node_DC.V_ini)

    # acdcpf uses P=0 with p_vac as an AC-slack converter for island balance.
    if control == "p_vac" and conv.AC_type == "Slack":
        kwargs["p_mw"] = 0.0

    return kwargs


def _droop_kv_per_mw(grid, conv) -> float | None:
    droop_rate = float(getattr(conv, "Droop_rate", 0.0))
    if droop_rate == 0.0:
        return None
    return float(conv.Node_DC.kV_base) / (droop_rate * float(grid.S_base))


def _finite_or_default(value: float, default: float) -> float:
    if value != value:
        return default
    if value == float("inf") or value == float("-inf"):
        return default
    return value


def _node_q_limits(node) -> tuple[float, float]:
    q_min = _finite_or_default(float(getattr(node, "Qmin", 0.0)), default=-1e9)
    q_max = _finite_or_default(float(getattr(node, "Qmax", 0.0)), default=1e9)
    if q_min == 0.0 and q_max == 0.0:
        return -1e9, 1e9
    return q_min, q_max


def _has_voltage_controlling_vsc(grid, node) -> bool:
    for conv_idx in getattr(node, "connected_conv", set()):
        conv = grid.Converters_ACDC[conv_idx]
        if conv.AC_type in {"PV", "Slack"}:
            return True
    return False
