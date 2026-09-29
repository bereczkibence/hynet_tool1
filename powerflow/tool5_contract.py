"""Translate Tool1 limit metadata to the public Tool5 v1 schema.

No setpoints, ratings or OPF equations change. All currents use the network
three-phase AC current base; original imported matrices remain untouched.
"""
import math


def prepare_converter_limits(net):
    for idx, row in net.vsc.iterrows():
        kv = float(net.ac_bus.loc[int(row.ac_bus), "vr_kv"])
        current = row.get("max_i_ac_ka")
        if current is not None and math.isfinite(float(current)):
            net.vsc.at[idx, "i_max_pu"] = float(current) / (float(net.s_base)/(math.sqrt(3)*kv))
        for source, target in (("v_converter_min_pu", "vc_min_pu"), ("v_converter_max_pu", "vc_max_pu")):
            value = row.get(source)
            if value is not None and math.isfinite(float(value)):
                net.vsc.at[idx, target] = float(value)
