"""Storage dispatch and energy checks from source data and signed power."""

import math

from .physics_report import active_rows, result_number, source_limit


def _energy_after(row, power, dt, initial):
    capacity = float(row.energy_mwh)
    charge_eta, discharge_eta = float(row.eta_charge), float(row.eta_discharge)
    if capacity <= 0 or not (0 < charge_eta <= 1 and 0 < discharge_eta <= 1):
        raise ValueError("Storage requires positive capacity and efficiencies in (0, 1].")
    return initial - max(power, 0) * dt / discharge_eta + max(-power, 0) * dt * charge_eta


def check_storage(net, results, report, tol, *, options, duration_hours, initial_energy_mwh):
    """Return injections and independently verify the end-of-interval SOC."""
    ac, dc, total = {}, {}, 0.0
    base = float(net.s_base)
    for idx, row in active_rows(net, "storage"):
        key, element = f"STORAGE{idx}", f"storage[{idx}]"
        p = result_number(results, "storage_p_pu", key, base)
        q = result_number(results, "storage_q_pu", key, base)
        charge = result_number(results, "storage_charge_p_pu", key, base)
        discharge = result_number(results, "storage_discharge_p_pu", key, base)
        energy = result_number(results, "storage_energy_mwh", key)
        soc = result_number(results, "storage_soc_percent", key)
        initial = (float(row.energy_mwh) * float(row.soc_percent) / 100
                   if initial_energy_mwh is None else initial_energy_mwh.get(idx, math.nan))
        expected = _energy_after(row, p, duration_hours, initial)
        for quantity, value, unit, suffix in (("p", p, "MW", "mw"), ("q", q, "MVAr", "mvar")):
            tolerance = tol.power_mw if quantity == "p" else tol.reactive_mvar
            lower = source_limit(row, f"{quantity}_min_{suffix}")
            upper = source_limit(row, f"{quantity}_max_{suffix}")
            report.bounded(quantity + "_dispatch", element, value, lower=lower, upper=upper,
                           unit=unit, tolerance=tolerance)
            selected = getattr(options, f"storage_{'active' if quantity == 'p' else 'reactive'}_power_indices")
            enabled = getattr(options, f"optimize_storage_{'active' if quantity == 'p' else 'reactive'}_power")
            if not enabled or (selected is not None and idx not in selected):
                report.equal(quantity + "_fixed_dispatch", element, value, row[f"{quantity}_{suffix}"], unit=unit, tolerance=tolerance)
            if options.control_margin_percent is not None:
                width = float(row.sn_mva) * options.control_margin_percent / 100
                anchor = float(row[f"{quantity}_{suffix}"])
                report.bounded(quantity + "_control_margin", element, value, lower=anchor-width,
                               upper=anchor+width, unit=unit, tolerance=tolerance)
        report.bounded("apparent_rating", element, math.hypot(p, q), lower=0, upper=float(row.sn_mva), unit="MVA", tolerance=tol.power_mw)
        report.bounded("charge_nonnegative", element, charge, lower=0, unit="MW", tolerance=tol.power_mw)
        report.bounded("discharge_nonnegative", element, discharge, lower=0, unit="MW", tolerance=tol.power_mw)
        report.equal("signed_dispatch", element, discharge - charge, p, unit="MW", tolerance=tol.power_mw)
        overlap = min(charge, discharge) if math.isfinite(charge) and math.isfinite(discharge) else math.nan
        report.bounded("exclusive_modes", element, overlap, upper=0, unit="MW", tolerance=tol.storage_overlap_mw)
        report.equal("energy_transition", element, energy, expected, unit="MWh", tolerance=tol.energy_mwh)
        report.equal("soc_energy_consistency", element, soc, energy / float(row.energy_mwh) * 100,
                     unit="%", tolerance=tol.soc_percent)
        report.bounded("soc_bounds", element, soc, lower=float(row.soc_min_percent), upper=float(row.soc_max_percent), unit="%", tolerance=tol.soc_percent)
        report.bounded("initial_energy_bounds", element, initial,
                       lower=float(row.energy_mwh) * float(row.soc_min_percent) / 100,
                       upper=float(row.energy_mwh) * float(row.soc_max_percent) / 100,
                       unit="MWh", tolerance=tol.energy_mwh)
        bus = int(row.bus)
        if str(row.bus_type).lower() == "ac":
            ac[bus] = ac.get(bus, 0j) + complex(p, q)
        elif str(row.bus_type).lower() == "dc":
            dc[bus] = dc.get(bus, 0.0) + p
            report.equal("dc_reactive_power", element, q, 0, unit="MVAr", tolerance=tol.reactive_mvar)
        else:
            raise ValueError(f"{element}: unknown bus_type {row.bus_type!r}.")
        total += p
    return ac, dc, total


def check_storage_horizon(net, results, report, tol, *, duration_hours, previous_energy):
    """Propagate signed-power energy, never trust the exported SOC as an anchor."""
    energy = {}
    for idx, row in active_rows(net, "storage"):
        try:
            initial = (float(row.energy_mwh) * float(row.soc_percent) / 100
                       if previous_energy is None else previous_energy.get(idx, math.nan))
            p = result_number(results, "storage_p_pu", f"STORAGE{idx}", float(net.s_base))
            energy[idx] = _energy_after(row, p, duration_hours, initial)
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            energy[idx] = math.nan
            report.bounded("energy_data", f"storage[{idx}]", None, lower=0, unit="MWh",
                           tolerance=tol.energy_mwh, note=str(exc))
    return energy
