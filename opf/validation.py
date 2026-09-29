"""Post-solve acceptance checks, independent of solver termination messages."""

from dataclasses import dataclass, field
import math

import pyomo.environ as pyo


@dataclass
class SolutionValidation:
    """Raw model residuals and physical storage checks with explicit tolerances."""

    tolerance: float
    max_constraint_violation: float = 0.0
    max_bound_violation: float = 0.0
    max_storage_overlap_mw: float = 0.0
    nonfinite: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.nonfinite and not self.violations


def validate_solution(model, *, tolerance=1e-6, storage_power_tolerance_mw=1e-5):
    """Check active constraints, all variable bounds and both battery modes.

    Raw residuals retain the model's units (pu powers, squared pu currents,
    MWh energy equations); they are not labelled universally as pu. No relative
    tolerance enlarges an equipment limit. Storage overlap is checked in MW.
    """
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Validation tolerance must be finite and positive.")
    report = SolutionValidation(tolerance=tolerance)
    for component_type in (pyo.Constraint, pyo.Var):
        for item in model.component_data_objects(component_type, active=True):
            is_constraint = component_type is pyo.Constraint
            value = pyo.value(item.body if is_constraint else item, exception=False)
            if value is None or not math.isfinite(value):
                report.nonfinite.append(item.name)
                continue
            lower = pyo.value(item.lower if is_constraint else item.lb) if item.has_lb() else -math.inf
            upper = pyo.value(item.upper if is_constraint else item.ub) if item.has_ub() else math.inf
            error = max(0.0, lower - value, value - upper)
            if is_constraint:
                report.max_constraint_violation = max(report.max_constraint_violation, error)
            else:
                report.max_bound_violation = max(report.max_bound_violation, error)
            if error > tolerance:
                report.violations.append(f"{item.name}: violation {error:.6g}")
    periods = getattr(model, "_period_models", {None: model})
    for period in periods.values():
        base = float(period._opf_data["base_mva"])
        for key in period.STORAGE:
            charge = pyo.value(period.Pst_ch[key], exception=False)
            discharge = pyo.value(period.Pst_dis[key], exception=False)
            if charge is None or discharge is None:
                continue
            overlap = max(0.0, min(charge, discharge)) * base
            report.max_storage_overlap_mw = max(report.max_storage_overlap_mw, overlap)
            if overlap > storage_power_tolerance_mw:
                report.violations.append(f"{period.Pst[key].name}: simultaneous charge/discharge {overlap:.6g} MW")
    return report


def compare_pf_replay(extracted, pf_result, data, *, power_tolerance_mw=1e-3, voltage_tolerance_pu=1e-5):
    """Compare native ACDCPF bus voltages and converter terminal powers."""
    net = pf_result.raw_result
    if not hasattr(net, "res_ac_bus"):
        return {"available": False, "reason": "Native ACDCPF tables unavailable."}
    comparisons = (
        ("ac_buses", "ac_bus_voltage_magnitude_pu", "res_ac_bus", "v_pu", 1.0, voltage_tolerance_pu),
        ("dc_buses", "dc_bus_voltage_pu", "res_dc_bus", "v_dc_pu", 1.0, voltage_tolerance_pu),
        ("converters", "converter_p_ac_terminal_absorbed_pu", "res_vsc", "p_ac_mw", data["base_mva"], power_tolerance_mw),
        ("converters", "converter_q_ac_terminal_absorbed_pu", "res_vsc", "q_ac_mvar", data["base_mva"], power_tolerance_mw),
        ("converters", "converter_p_dc_pu", "res_vsc", "p_dc_mw", -data["base_mva"], power_tolerance_mw),
    )
    errors = {}
    matches = True
    for section, result_key, table_name, column, scale, tolerance in comparisons:
        table = getattr(net, table_name)
        difference = 0.0
        for key, device in data[section].items():
            expected = extracted[result_key][key] * scale
            actual = float(table.at[device["index"], column])
            error = abs(actual - expected) if math.isfinite(actual) else math.inf
            difference = max(difference, error)
        errors[result_key] = difference
        matches = matches and difference <= tolerance
    loss_difference = abs(pf_result.total_active_losses - extracted["objective_total_active_losses_mw"])
    errors["total_loss_mw"] = loss_difference
    matches = matches and math.isfinite(loss_difference) and loss_difference <= power_tolerance_mw
    return {"available": True, "matches": matches, "max_differences": errors,
            "power_tolerance_mw": power_tolerance_mw, "voltage_tolerance_pu": voltage_tolerance_pu}
