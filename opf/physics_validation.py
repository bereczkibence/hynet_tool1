"""Independent native-network checks, without Pyomo or OPF conversion helpers.

Branch terminal powers are positive into a branch. VSC AC and canonical DC
powers are positive into the station. Generation/storage inject; loads consume.
Equations and coverage limits are documented in docs/PHYSICS_VALIDATION.md.
"""

import cmath
import math

from .physics_report import (
    PhysicsReport, PhysicsTolerances, active_rows, finite, result_number,
    source_limit, source_number,
)
from .physics_storage import check_storage, check_storage_horizon


def validate_network_physics(net, results, *, options, baseline=None, duration_hours=1.0,
                             initial_energy_mwh=None, tolerances=None):
    """Recompute flows and limits from native input, not converted model data.

    Missing ratings produce partial coverage. Missing/nonfinite solved states
    and physics violations fail acceptance. This does not certify optimality.
    """
    report = PhysicsReport()
    tol = tolerances or PhysicsTolerances()
    try:
        _check_snapshot(net, results, report, tol, options, baseline, duration_hours,
                        initial_energy_mwh)
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        report.bounded("source_or_result_data", "network", None, lower=0, upper=0,
                       unit="", tolerance=tol.power_mw, note=str(exc))
    return report


def validate_time_series_physics(cases, results, *, options, baselines=None,
                                 durations=None, tolerances=None):
    """Check each snapshot and reconstruct energy from the initial source SOC."""
    tol = tolerances or PhysicsTolerances()
    reports = {}
    expected_energy = None
    baselines, durations = baselines or {}, durations or {}
    for key, net in cases.items():
        reports[key] = validate_network_physics(
            net, results.get(key, {}), options=options, baseline=baselines.get(key),
            duration_hours=durations.get(key, 1.0), initial_energy_mwh=expected_energy,
            tolerances=tol,
        )
        reports[key].equal("horizon_result_ids", "network",
                           len(set(cases).symmetric_difference(results)), 0,
                           unit="count", tolerance=0)
        expected_energy = check_storage_horizon(
            net, results.get(key, {}), reports[key], tol,
            duration_hours=durations.get(key, 1.0), previous_energy=expected_energy,
        )
        reference = dict(active_rows(next(iter(cases.values())), "storage"))
        current = dict(active_rows(net, "storage"))
        stable = set(reference) == set(current) and all(
            all(current[idx][field] == row[field] for field in (
                "bus", "bus_type", "energy_mwh", "soc_percent", "soc_min_percent",
                "soc_max_percent", "eta_charge", "eta_discharge"))
            for idx, row in reference.items()
        )
        reports[key].equal("storage_horizon_identity", "storage", int(stable), 1,
                           unit="boolean", tolerance=0)
    if reports:
        first = next(iter(cases.values()))
        last_key = next(reversed(reports))
        for idx, row in active_rows(first, "storage"):
            initial = float(row.energy_mwh) * float(row.soc_percent) / 100
            reports[last_key].equal("final_soc_restoration", f"storage[{idx}]",
                                    (expected_energy or {}).get(idx), initial,
                                    unit="MWh", tolerance=tol.energy_mwh)
        loss_energy = sum(
            next((c.upper if c.upper is not None else math.nan for c in report.checks
                  if c.check == "reported_network_loss"), math.nan)
            * durations.get(key, 1.0) for key, report in reports.items()
        )
        reports[last_key].equal("horizon_network_loss_energy", "network",
                                results.get(last_key, {}).get("horizon_active_loss_energy_mwh"),
                                loss_energy, unit="MWh", tolerance=tol.energy_mwh)
        # All snapshots are one linked study; a horizon failure is propagated
        # by the caller, rather than publishing a feasible-looking fragment.
    return reports


def _rating(report, check, element, value, rating, unit, tolerance):
    upper = rating if rating is not None and rating > 0 else None
    report.bounded(check, element, value, lower=0 if upper is not None else None, upper=upper,
                   unit=unit, tolerance=tolerance)


def _source_bounds(report, check, element, value, row, low, high, unit, tolerance):
    lower, upper = source_limit(row, *low), source_limit(row, *high)
    report.bounded(check, element, value, lower=lower, upper=upper,
                   unit=unit, tolerance=tolerance)
    if (lower is None) != (upper is None):
        report.skipped(check + "_missing_bound", element, unit, "Only one finite source bound supplied.")


def _check_snapshot(net, results, report, tol, options, baseline, dt, initial_energy):
    base = float(net.s_base)
    if not math.isfinite(base) or base <= 0 or not math.isfinite(dt) or dt <= 0:
        raise ValueError("System base and interval duration must be finite and positive.")
    ac_voltage, dc_voltage = {}, {}
    ac_balance, dc_balance = {}, {}
    shunt_power = 0.0
    for idx, row in active_rows(net, "ac_bus"):
        vm = result_number(results, "ac_bus_voltage_magnitude_pu", f"AC{idx}")
        angle = result_number(results, "ac_bus_voltage_angle_rad", f"AC{idx}")
        ac_voltage[idx] = cmath.rect(vm, angle)
        shunt = complex(source_number(row, "gs_pu"), -source_number(row, "bs_pu")) * vm**2 * base
        ac_balance[idx] = -shunt
        shunt_power += shunt.real
        _source_bounds(report, "voltage", f"ac_bus[{idx}]", vm, row,
                       ("v_min_pu",), ("v_max_pu",), "pu", tol.voltage_pu)
    for idx, row in active_rows(net, "dc_bus"):
        vm = result_number(results, "dc_bus_voltage_pu", f"DC{idx}")
        dc_voltage[idx] = vm * float(row.v_base)
        dc_balance[idx] = 0.0
        _source_bounds(report, "voltage", f"dc_bus[{idx}]", vm, row,
                       ("v_min",), ("v_max",), "pu", tol.voltage_pu)

    supplied = _check_injections(net, results, report, tol, options, ac_balance, dc_balance)
    storage_ac, storage_dc, storage_total = check_storage(
        net, results, report, tol, options=options, duration_hours=dt,
        initial_energy_mwh=initial_energy,
    )
    for bus, power in storage_ac.items():
        ac_balance[bus] += power
    for bus, power in storage_dc.items():
        dc_balance[bus] += power
    supplied += storage_total

    losses = _check_ac_branches(net, results, report, tol, ac_voltage, ac_balance)
    losses += _check_dc_branches(net, results, report, tol, dc_voltage, dc_balance, options)
    losses += _check_vsc(net, results, report, tol, ac_voltage, dc_voltage,
                         ac_balance, dc_balance, options, baseline)
    for idx, balance in ac_balance.items():
        report.equal("nodal_p_balance", f"ac_bus[{idx}]", balance.real, 0,
                     unit="MW", tolerance=tol.power_mw)
        report.equal("nodal_q_balance", f"ac_bus[{idx}]", balance.imag, 0,
                     unit="MVAr", tolerance=tol.reactive_mvar)
    for idx, balance in dc_balance.items():
        report.equal("nodal_p_balance", f"dc_bus[{idx}]", balance, 0,
                     unit="MW", tolerance=tol.power_mw)
    report.equal("global_p_balance", "network", supplied - shunt_power - losses, 0,
                 unit="MW", tolerance=tol.power_mw)
    report.equal("reported_network_loss", "network", results.get("objective_total_active_losses_mw"),
                 losses, unit="MW", tolerance=tol.power_mw)
    _check_controls(net, results, report, tol, options, baseline)
    _check_result_ids(net, results, report)


def _check_result_ids(net, results, report):
    for name, prefix, field in (
        ("ac_bus", "AC", "ac_bus_voltage_magnitude_pu"),
        ("dc_bus", "DC", "dc_bus_voltage_pu"),
        ("ac_gen", "G", "generator_pg_pu"),
        ("dc_gen", "DCG", "dc_generator_pg_pu"),
        ("vsc", "CONV", "converter_p_dc_pu"),
        ("dcdc", "DCDC", "dcdc_p_from_pu"),
        ("storage", "STORAGE", "storage_p_pu"),
    ):
        expected = {f"{prefix}{idx}" for idx, _ in active_rows(net, name)}
        difference = expected.symmetric_difference(results.get(field, {}))
        report.equal("result_element_ids", name, len(difference), 0, unit="count", tolerance=0)
    for field, tables in (
        ("ac_branch_p_from_pu", (("ac_line", "AC_LINE"), ("trafo", "TRAFO"))),
        ("dc_branch_p_from_pu", (("dc_line", "DC_LINE"),)),
    ):
        expected = {f"{prefix}{idx}" for name, prefix in tables for idx, _ in active_rows(net, name)}
        report.equal("result_element_ids", field, len(expected.symmetric_difference(results.get(field, {}))),
                     0, unit="count", tolerance=0)


def _selected(options, flag, indices, idx):
    scope = getattr(options, indices)
    return bool(getattr(options, flag)) and (scope is None or idx in scope)


def _check_injections(net, results, report, tol, options, ac_balance, dc_balance):
    base, total = float(net.s_base), 0.0
    for idx, row in active_rows(net, "ac_gen"):
        p = result_number(results, "generator_pg_pu", f"G{idx}", base)
        q = result_number(results, "generator_qg_pu", f"G{idx}", base)
        ac_balance[int(row.bus)] += complex(p, q)
        total += p
        for quantity, value, unit, tolerance in (("p", p, "MW", tol.power_mw), ("q", q, "MVAr", tol.reactive_mvar)):
            suffix = "mw" if quantity == "p" else "mvar"
            _source_bounds(report, quantity + "_dispatch", f"ac_gen[{idx}]", value, row,
                           (f"{quantity}_min_{suffix}",), (f"{quantity}_max_{suffix}",), unit, tolerance)
    for idx, row in active_rows(net, "dc_gen"):
        p = result_number(results, "dc_generator_pg_pu", f"DCG{idx}", base)
        available = float(row.p_mw)
        dc_balance[int(row.bus)] += p
        total += p
        _source_bounds(report, "p_dispatch", f"dc_gen[{idx}]", p, row,
                       ("p_min_mw", "min_p_mw", "pmin_mw"),
                       ("p_max_mw", "max_p_mw", "pmax_mw"), "MW", tol.power_mw)
        enabled = _selected(options, "optimize_dc_generator_active_power", "dc_generator_active_power_indices", idx)
        if not enabled or (options.dc_generator_curtailment_only and available <= 0):
            report.equal("fixed_dispatch", f"dc_gen[{idx}]", p, available, unit="MW", tolerance=tol.power_mw)
        elif options.dc_generator_curtailment_only:
            margin = 100.0 if options.control_margin_percent is None else options.control_margin_percent
            report.bounded("curtailment", f"dc_gen[{idx}]", p,
                           lower=max(0.0, available * (1 - margin / 100)), upper=available,
                           unit="MW", tolerance=tol.power_mw)
    for idx, row in active_rows(net, "ac_load"):
        power = complex(float(row.p_mw), float(row.q_mvar))
        ac_balance[int(row.bus)] -= power
        total -= power.real
    for idx, row in active_rows(net, "dc_load"):
        if str(row.get("load_type", "constant_power")) != "constant_power":
            raise ValueError(f"dc_load[{idx}]: unsupported load type in physics check.")
        dc_balance[int(row.bus)] -= float(row.p_mw)
        total -= float(row.p_mw)
    return total


def _check_ac_branches(net, results, report, tol, voltage, balance):
    base, losses = float(net.s_base), 0.0
    for name, prefix in (("ac_line", "AC_LINE"), ("trafo", "TRAFO")):
        for idx, row in active_rows(net, name):
            element, key = f"{name}[{idx}]", f"{prefix}{idx}"
            start, end = int(row.from_bus), int(row.to_bus)
            vbase = float(net.ac_bus.at[start, "vr_kv"])
            if name == "ac_line":
                zbase = vbase**2 / base
                length = float(row.length_km)
                impedance = complex(float(row.r_ohm_per_km), float(row.x_ohm_per_km)) * length / zbase
                shunt = complex(source_number(row, "g_us_per_km"), source_number(row, "b_us_per_km")) * length * 1e-6 * zbase
                rating = source_limit(row, "rate_mva", "rating_mva", "mva_rating", "MVA_rating", "s_mva", "sn_mva", "max_s_mva")
            else:
                factor = base / float(row.sn_mva) * (source_number(row, "vn_from_kv", vbase) / vbase)**2
                impedance = complex(float(row.r_pu), float(row.x_pu)) * factor
                shunt = complex(source_number(row, "g_pu"), source_number(row, "b_pu")) / factor
                rating = source_limit(row, "sn_mva")
            tap = cmath.rect(source_number(row, "tap", 1.0) or 1.0,
                             math.radians(source_number(row, "shift_deg")))
            vf, vt = voltage[start], voltage[end]
            series_current = (vf / tap - vt) / impedance
            currents = ((series_current + shunt * vf / tap / 2) / tap.conjugate(),
                        -series_current + shunt * vt / 2)
            powers = (vf * currents[0].conjugate() * base, vt * currents[1].conjugate() * base)
            for terminal, bus, power, current in zip(("from", "to"), (start, end), powers, currents):
                balance[bus] -= power
                for quantity, value, unit, tolerance in (("p", power.real, "MW", tol.power_mw), ("q", power.imag, "MVAr", tol.reactive_mvar)):
                    report.equal(f"{quantity}_{terminal}_flow", element,
                                 result_number(results, f"ac_branch_{quantity}_{terminal}_pu", key, base),
                                 value, unit=unit, tolerance=tolerance)
                _rating(report, f"s_{terminal}_rating", element, abs(power), rating, "MVA", tol.power_mw)
                ika = abs(current) * base / (math.sqrt(3) * float(net.ac_bus.at[bus, "vr_kv"]))
                _rating(report, f"i_{terminal}_rating", element, ika, source_limit(row, "max_i_ka"), "kA", tol.current_ka)
            loss = sum(p.real for p in powers)
            report.bounded("nonnegative_loss", element, loss, lower=0, unit="MW", tolerance=tol.power_mw)
            losses += loss
    return losses


def _check_dc_branches(net, results, report, tol, voltage, balance, options):
    base, poles, losses = float(net.s_base), float(net.pol), 0.0
    for name, prefix in (("dc_line", "DC_LINE"), ("dcdc", "DCDC")):
        for idx, row in active_rows(net, name):
            element, key = f"{name}[{idx}]", f"{prefix}{idx}"
            start, end = int(row.from_bus), int(row.to_bus)
            vf, vt = voltage[start], voltage[end]
            if name == "dc_line":
                if float(net.dc_bus.at[start, "v_base"]) != float(net.dc_bus.at[end, "v_base"]):
                    raise ValueError(f"{element}: unequal DC voltage bases require a DCDC converter.")
                resistance = float(row.r_ohm_per_km) * float(row.length_km)
                current = (vf - vt) / resistance
                pf, pt = poles * vf * current, -poles * vt * current
                field = "dc_branch"
                measured = result_number(results, "dc_branch_current_pu", key, base / float(net.dc_bus.at[start, "v_base"]))
                report.equal("series_current", element, measured, current, unit="kA", tolerance=tol.current_ka)
                _rating(report, "current_rating", element, abs(current), source_limit(row, "max_i_ka"), "kA", tol.current_ka)
            else:
                dpu = result_number(results, "dcdc_ratio_pu", key)
                ratio = dpu * float(net.dc_bus.at[end, "v_base"]) / float(net.dc_bus.at[start, "v_base"])
                resistance = float(row.r_ohm)
                if resistance <= 0:
                    raise ValueError(f"{element}: zero-resistance DCDC physics is not supported.")
                current = (ratio * vf - vt) / resistance
                pf = poles * vf * (ratio * current + source_number(row, "g_us") * 1e-6 * vf)
                pt = -poles * vt * current
                field = "dcdc"
                low = source_limit(row, "d_pu_min", "d_min_pu")
                high = source_limit(row, "d_pu_max", "d_max_pu")
                if low is not None or high is not None:
                    report.bounded("voltage_ratio", element, dpu, lower=low, upper=high, unit="pu", tolerance=tol.voltage_pu)
                else:
                    _source_bounds(report, "voltage_ratio", element, ratio, row,
                                   ("d_ratio_min", "d_min", "ratio_min"), ("d_ratio_max", "d_max", "ratio_max"), "ratio", tol.voltage_pu)
                if not _selected(options, "optimize_dcdc_voltage_ratio", "dcdc_voltage_ratio_indices", idx):
                    report.equal("fixed_ratio", element, ratio, row.d_ratio, unit="ratio", tolerance=tol.voltage_pu)
            balance[start] -= pf
            balance[end] -= pt
            rating = source_limit(row, "rate_mw", "rating_mw", "mw_rating", "MW_rating", "p_max_mw", "max_p_mw")
            for terminal, power in (("from", pf), ("to", pt)):
                report.equal(f"p_{terminal}_flow", element, result_number(results, f"{field}_p_{terminal}_pu", key, base), power, unit="MW", tolerance=tol.power_mw)
                _rating(report, f"p_{terminal}_rating", element, abs(power), rating, "MW", tol.power_mw)
            loss = pf + pt
            report.equal("reported_loss", element, result_number(results, f"{field}_loss_pu", key, base), loss, unit="MW", tolerance=tol.power_mw)
            report.bounded("nonnegative_loss", element, loss, lower=0, unit="MW", tolerance=tol.power_mw)
            losses += loss
    return losses


def _check_vsc(net, results, report, tol, ac_voltage, dc_voltage, ac_balance, dc_balance, options, baseline):
    base, losses = float(net.s_base), 0.0
    for idx, row in active_rows(net, "vsc"):
        element, key = f"vsc[{idx}]", f"CONV{idx}"
        ac_bus, dc_bus = int(row.ac_bus), int(row.dc_bus)
        pac = result_number(results, "converter_p_ac_terminal_absorbed_pu", key, base)
        qac = result_number(results, "converter_q_ac_terminal_absorbed_pu", key, base)
        pdc = result_number(results, "converter_p_dc_pu", key, base)
        vs = ac_voltage[ac_bus]
        itf = (complex(pac, qac) / base / vs).conjugate()
        vf = vs - complex(float(row.r_tf_pu), float(row.x_tf_pu)) * itf
        ic = itf - 1j * float(row.b_filter_pu) * vf
        vc = vf - complex(float(row.r_c_pu), float(row.x_c_pu)) * ic
        sc = vc * ic.conjugate() * base
        loss_base = source_limit(row, "loss_base_kv") or float(net.ac_bus.at[ac_bus, "vr_kv"])
        ika = abs(ic) * base / (math.sqrt(3) * loss_base)
        cneg = float(row.loss_c)
        cpos = source_number(row, "loss_c_inv", cneg)
        if options.converter_loss_mode in {"smooth_directional", "directional_smooth"}:
            selector = (1 + math.tanh(options.converter_loss_switch_sharpness * sc.real / base / 2)) / 2
            coefficient = cneg + (cpos - cneg) * selector
        else:
            initial_p = _baseline_value(baseline, "res_vsc", idx, "p_ac_mw", float(row.p_mw))
            coefficient = cpos if initial_p >= 0 else cneg
        electronic = float(row.loss_a) + float(row.loss_b) * ika + coefficient * ika**2
        tf_loss = float(row.r_tf_pu) * abs(itf)**2 * base
        reactor_loss = float(row.r_c_pu) * abs(ic)**2 * base
        station_loss = tf_loss + reactor_loss + electronic
        for field, expected in (("converter_p_ac_pu", sc.real), ("converter_q_ac_pu", sc.imag),
                                ("converter_loss_pu", electronic), ("converter_transformer_loss_pu", tf_loss),
                                ("converter_phase_reactor_loss_pu", reactor_loss)):
            report.equal(field.removesuffix("_pu"), element, result_number(results, field, key, base), expected,
                         unit="MVAr" if field == "converter_q_ac_pu" else "MW",
                         tolerance=tol.reactive_mvar if field == "converter_q_ac_pu" else tol.power_mw)
        report.equal("station_power_balance", element, pac + pdc, station_loss, unit="MW", tolerance=tol.power_mw)
        report.equal("ac_current", element, result_number(results, "converter_i_ac_pu", key, base / (math.sqrt(3) * loss_base)), ika, unit="kA", tolerance=tol.current_ka)
        poles = float(net.pol)
        report.equal("dc_current_per_pole", element,
                     result_number(results, "converter_i_dc_pu", key, base / (poles * float(net.dc_bus.at[dc_bus, "v_base"]))),
                     pdc / (poles * dc_voltage[dc_bus]), unit="kA", tolerance=tol.current_ka)
        _rating(report, "bridge_apparent_rating", element, abs(sc), source_limit(row, "s_mva"), "MVA", tol.power_mw)
        _rating(report, "ac_current_rating", element, ika, source_limit(row, "max_i_ac_ka"), "kA", tol.current_ka)
        _rating(report, "dc_current_rating", element, abs(pdc / (poles * dc_voltage[dc_bus])),
                source_limit(row, "max_i_dc_ka"), "kA", tol.current_ka)
        _source_bounds(report, "filter_voltage", element, abs(vf), row,
                       ("v_filter_min_pu",), ("v_filter_max_pu",), "pu", tol.voltage_pu)
        _source_bounds(report, "bridge_voltage", element, abs(vc), row,
                       ("v_converter_min_pu",), ("v_converter_max_pu",), "pu", tol.voltage_pu)
        report.bounded("nonnegative_loss", element, station_loss, lower=0, unit="MW", tolerance=tol.power_mw)
        margin = options.control_margin_percent
        for quantity, value, column in (("p", pac, "p_ac_mw"), ("q", qac, "q_ac_mvar")):
            if margin is not None:
                anchor = _baseline_value(baseline, "res_vsc", idx, column, None)
                if anchor is None:
                    report.skipped(quantity + "_control_margin", element, "MW" if quantity == "p" else "MVAr", "PF baseline required for the control-margin anchor.")
                else:
                    width = float(row.s_mva) * margin / 100
                    report.bounded(quantity + "_control_margin", element, value, lower=anchor-width, upper=anchor+width,
                                   unit="MW" if quantity == "p" else "MVAr",
                                   tolerance=tol.power_mw if quantity == "p" else tol.reactive_mvar)
        ac_balance[ac_bus] -= complex(pac, qac)
        dc_balance[dc_bus] -= pdc
        losses += station_loss
    return losses


def _baseline_value(baseline, table_name, idx, column, fallback):
    table = getattr(baseline, table_name, None)
    if table is not None and idx in table.index and column in table:
        return finite(table.at[idx, column])
    return fallback


def _reference_generators(net):
    """Identify reference sources from active topology, not OPF fixed variables."""
    graph = {idx: set() for idx, _ in active_rows(net, "ac_bus")}
    for name in ("ac_line", "trafo"):
        for _, row in active_rows(net, name):
            a, b = int(row.from_bus), int(row.to_bus)
            graph[a].add(b)
            graph[b].add(a)
    remaining, refs = set(graph), set()
    generators = sorted(active_rows(net, "ac_gen"), key=lambda pair: pair[0])
    while remaining:
        pending, component = [min(remaining)], set()
        while pending:
            bus = pending.pop()
            if bus not in component:
                component.add(bus)
                pending.extend(graph[bus] - component)
        remaining -= component
        explicit = sorted(b for b in component if bool(net.ac_bus.loc[b].get("is_slack", False)))
        candidates = [idx for idx, row in generators if int(row.bus) in component
                      and (not explicit or int(row.bus) == explicit[0])]
        if candidates:
            refs.add(candidates[0])
    return refs


def _check_controls(net, results, report, tol, options, baseline):
    base = float(net.s_base)
    refs = _reference_generators(net)
    generator_buses = set()
    for idx, row in active_rows(net, "ac_gen"):
        element, key, bus = f"ac_gen[{idx}]", f"G{idx}", int(row.bus)
        generator_buses.add(bus)
        if idx not in refs:
            for quantity, field, suffix in (("p", "active", "mw"), ("q", "reactive", "mvar")):
                if not _selected(options, f"optimize_non_slack_generator_{field}_power", f"ac_generator_{field}_power_indices", idx):
                    anchor = _baseline_value(baseline, "res_ac_gen", idx, f"{quantity}_{suffix}", float(row[f"{quantity}_{suffix}"]))
                    report.equal(quantity + "_fixed_dispatch", element,
                                 result_number(results, f"generator_{quantity}g_pu", key, base), anchor,
                                 unit="MW" if quantity == "p" else "MVAr",
                                 tolerance=tol.power_mw if quantity == "p" else tol.reactive_mvar)
        voltage_fixed = (options.fix_ac_slack_voltage if idx in refs else not _selected(
            options, "optimize_generator_voltage_setpoints", "ac_generator_voltage_node_indices", bus))
        if voltage_fixed and finite(row.v_pu) is not None:
            report.equal("fixed_voltage", element, result_number(results, "ac_bus_voltage_magnitude_pu", f"AC{bus}"),
                         float(row.v_pu), unit="pu", tolerance=tol.voltage_pu)
    for idx, row in active_rows(net, "vsc"):
        element, key = f"vsc[{idx}]", f"CONV{idx}"
        mode = str(row.control_mode).lower()
        pac = result_number(results, "converter_p_ac_terminal_absorbed_pu", key, base)
        qac = result_number(results, "converter_q_ac_terminal_absorbed_pu", key, base)
        pdc = result_number(results, "converter_p_dc_pu", key, base)
        vdc = result_number(results, "dc_bus_voltage_pu", f"DC{int(row.dc_bus)}")
        p_enabled = options.optimize_converter_pq_setpoints and _selected(options, "optimize_converter_active_power", "converter_active_power_indices", idx)
        q_enabled = options.optimize_converter_pq_setpoints and _selected(options, "optimize_converter_reactive_power", "converter_reactive_power_indices", idx)
        if not p_enabled and mode in {"p_q", "p_vac", "pdc_q", "pdc_vac"}:
            is_dc = mode.startswith("pdc")
            report.equal("fixed_p", element, pdc if is_dc else pac,
                         -float(row.p_dc_set_mw) if is_dc else float(row.p_mw), unit="MW", tolerance=tol.power_mw)
        if not q_enabled and mode.endswith("_q"):
            report.equal("fixed_q", element, qac, float(row.q_mvar), unit="MVAr", tolerance=tol.reactive_mvar)
        if "vac" in mode:
            if int(row.ac_bus) in generator_buses:
                report.equal("generator_voltage_priority_q", element, qac, 0, unit="MVAr", tolerance=tol.reactive_mvar)
            elif options.fix_converter_vdc_vac_controls:
                report.equal("fixed_ac_voltage", element, result_number(results, "ac_bus_voltage_magnitude_pu", f"AC{int(row.ac_bus)}"),
                             source_number(row, "v_ac_pu", 1.0), unit="pu", tolerance=tol.voltage_pu)
        if options.fix_converter_vdc_vac_controls and "vdc" in mode:
            report.equal("fixed_dc_voltage", element, vdc, source_number(row, "v_dc_pu", 1.0), unit="pu", tolerance=tol.voltage_pu)
        if "droop" in mode:
            droop = source_number(row, "droop_kv_per_mw")
            if droop:
                target = -source_number(row, "p_dc_set_mw") + float(net.dc_bus.at[int(row.dc_bus), "v_base"]) / droop * (vdc - source_number(row, "v_dc_set_pu", 1.0))
                report.equal("dc_droop", element, pdc, target, unit="MW", tolerance=tol.power_mw)
            else:
                report.skipped("dc_droop", element, "MW", "No nonzero droop coefficient supplied.")
