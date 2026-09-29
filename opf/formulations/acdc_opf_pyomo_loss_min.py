"""Nonlinear AC/DC OPF model for Pyomo + IPOPT.

This file is intended as a clean starting point for a hybrid AC/DC OPF solver.
It follows the nonlinear AC/DC OPF formulation of Ergun et al. (2019), while
using total active power loss minimization as the objective.

The model is equation-based, not a black-box PF wrapper. Use your ACDCPF solver
for initial values and validation, then let IPOPT solve the explicit NLP model.

Implemented model scope
-----------------------
- AC branch nonlinear power-flow equations with optional tap and phase shift.
- DC branch nonlinear equations using voltage, current, and resistive losses.
- VSC/MMC-style AC/DC converter station model:
  transformer, filter, phase reactor, electronic converter.
- Quadratic converter loss model: P_loss = a + b*I + c*I^2.
  The coefficient ``c`` can either be fixed before the solve, or selected with
  a smooth direction-dependent approximation based on the sign of ``Pcv_ac``.
- AC active/reactive nodal balances.
- DC active nodal balances.
- One-timestamp storage/BESS active and reactive injections with apparent-power
  and SOC limits.
- ACDCPF-style DCDC converter branches with optional voltage-ratio control.
- Voltage, current, branch rating, converter apparent-power, and generator limits.
- Optional fixed-value controls through a generic ``fixed`` dictionary.
- Optional DC droop constraint for converters when the data explicitly provides it.

Not implemented intentionally
-----------------------------
- Discrete transformer tap optimization.
- Convex relaxations such as SOC/SDP/QC.
- Security-constrained OPF.

Paper formulation traceability
------------------------------
The implemented nonlinear VSC AC/DC equations follow Ergun et al. (2019):

- DC branch model: equations (3)-(8).
- DCDC converter branches: ACDCPF constant-ratio DC transformer model.
- Converter transformer, filter, and phase reactor: equations (9)-(19).
- VSC converter limits, loss, and current relations: equations (20)-(30).
- AC/DC nodal balances: equations (33)-(35).

The paper's LCC firing-angle equations (31)-(32) and convex/SOC/SDP/linear
reformulations are intentionally outside this VSC/IPOPT NLP scope.

Internal units
--------------
Use per-unit internally. Convert MW/MVAr/kV data before passing it into this
model. Convert results back to MW/MVAr only for reports.

Canonical converter sign convention
-----------------------------------
Pcv_ac[c] > 0:
    active power absorbed by the electronic converter from the AC terminal.

Pcv_dc[c] < 0:
    active power injected by the electronic converter into the DC grid.

Converter balance:
    Pcv_ac[c] + Pcv_dc[c] = Pcv_loss[c]

Example rectifier behavior:
    Pcv_ac = +1.00 pu
    Pcv_dc = -0.98 pu
    Pcv_loss = +0.02 pu

If ACDCPF or pyflow uses another sign convention, convert signs in the adapter.
Do not change signs randomly inside the OPF equations. That way lies madness.
"""

from __future__ import annotations

from dataclasses import dataclass
import cmath
import math
from typing import Any, Mapping

import pyomo.environ as pyo


_EPS = 1e-10


@dataclass(frozen=True)
class OPFBuildOptions:
    """Options controlling model construction."""

    objective: str = "total_active_loss_minimization"
    enforce_ac_branch_thermal_limits: bool = True
    enforce_dc_branch_limits: bool = True
    enforce_converter_apparent_power_limits: bool = True
    enforce_optional_droop_controls: bool = True


@dataclass(frozen=True)
class OPFSolveOptions:
    """Options used when calling IPOPT through Pyomo."""

    ipopt_executable: str | None = None
    tee: bool = True
    max_iter: int = 500
    tol: float = 1e-6
    print_level: int = 5


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------


def _active_items(items: Mapping[str, Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Return items whose status is not explicitly set to zero."""
    return {
        name: values
        for name, values in items.items()
        if int(values.get("status", 1)) != 0
    }


def _normal_tap(value: Any) -> float:
    """MATPOWER-style tap value: zero means no off-nominal tap."""
    if value is None:
        return 1.0
    value = float(value)
    return 1.0 if abs(value) <= _EPS else value


def _angle_to_rad(value: Any, *, degrees: bool = True) -> float:
    """Convert an angle value to radians."""
    if value is None:
        return 0.0
    value = float(value)
    return math.radians(value) if degrees else value


def _series_admittance(r: float, x: float) -> tuple[float, float]:
    """Return the real and imaginary parts of 1/(r + jx)."""
    denom = r * r + x * x
    if denom <= _EPS:
        raise ValueError("Cannot compute admittance for near-zero impedance.")
    return r / denom, -x / denom


def _has_impedance(component: Mapping[str, Any]) -> bool:
    """Check if a passive converter component should use lossy equations."""
    if not bool(component.get("enabled", True)):
        return False
    r = abs(float(component.get("r", 0.0)))
    x = abs(float(component.get("x", 0.0)))
    return r + x > _EPS


def _sum_or_zero(expressions: list[Any]) -> Any:
    """Return a Pyomo-safe sum for possibly empty lists."""
    return sum(expressions) if expressions else 0.0


def _fixed_converter_loss_c(converter: Mapping[str, Any]) -> float:
    """Return the converter quadratic loss coefficient for a fixed mode."""
    if "loss_c" in converter:
        return float(converter["loss_c"])

    mode = str(converter.get("loss_mode", "rectifier")).lower()
    if mode in {"rectifier", "rec"}:
        return float(converter.get("loss_c_rectifier", converter.get("loss_crec", 0.0)))
    if mode in {"inverter", "inv"}:
        return float(converter.get("loss_c_inverter", converter.get("loss_cinv", 0.0)))

    raise ValueError(
        "Unknown converter loss_mode. Use 'rectifier', 'inverter', or provide loss_c."
    )


def _converter_loss_c_expression(converter: Mapping[str, Any], p_ac: Any) -> Any:
    """Return the effective quadratic loss coefficient.

    Ergun et al. parameterize converter losses as ``a + bI + cI^2`` and note
    that ``c`` can differ between rectifying and inverting operation. A hard
    switch on a decision variable would make the IPOPT NLP nonsmooth, so the
    optional ``smooth_directional`` mode uses a logistic transition:

    ``Pcv_ac >= 0`` tends to ``loss_c_positive_p_ac`` and ``Pcv_ac < 0`` tends
    to ``loss_c_negative_p_ac``.

    The legacy ``fixed`` mode keeps the pre-selected ``loss_c`` value.
    """
    mode = str(converter.get("loss_mode", "fixed")).lower()
    if mode in {"smooth_directional", "directional_smooth"}:
        c_positive = float(converter["loss_c_positive_p_ac"])
        c_negative = float(converter["loss_c_negative_p_ac"])
        sharpness = float(converter.get("loss_switch_sharpness", 50.0))
        selector = 1.0 / (1.0 + pyo.exp(-sharpness * p_ac))
        return c_negative + (c_positive - c_negative) * selector
    if mode == "fixed":
        return _fixed_converter_loss_c(converter)
    return _fixed_converter_loss_c(converter)


def _ac_branch_y_terms(branch: Mapping[str, Any]) -> dict[str, float]:
    """Build admittance terms for a MATPOWER-style AC branch.

    The branch is modeled as a pi-equivalent with an ideal transformer at the
    from side. The phase-shift angle is expected in degrees when using the
    common MATPOWER key ``angle`` or ``shift_degree``.
    """
    r = float(branch["r"])
    x = float(branch["x"])
    b_ch = float(branch.get("b", 0.0))

    tap_mag = _normal_tap(branch.get("tap", branch.get("ratio", 1.0)))
    shift = _angle_to_rad(branch.get("shift_degree", branch.get("angle", 0.0)))

    y = 1 / complex(r, x)
    y_sh = complex(0.0, b_ch / 2.0)
    tap = tap_mag * cmath.exp(1j * shift)

    yff = (y + y_sh) / (tap * tap.conjugate())
    yft = -y / tap.conjugate()
    ytf = -y / tap
    ytt = y + y_sh

    return {
        "gff": yff.real,
        "bff": yff.imag,
        "gft": yft.real,
        "bft": yft.imag,
        "gtf": ytf.real,
        "btf": ytf.imag,
        "gtt": ytt.real,
        "btt": ytt.imag,
    }


# -----------------------------------------------------------------------------
# Model construction
# -----------------------------------------------------------------------------


def build_acdc_opf_model(
    data: Mapping[str, Any],
    options: OPFBuildOptions | None = None,
) -> pyo.ConcreteModel:
    """Build a nonlinear AC/DC OPF model for Pyomo.

    Parameters
    ----------
    data:
        Per-unit grid data. See the module docstring for modeling assumptions.
    options:
        Build options controlling optional limits and controls.

    Returns
    -------
    pyomo.ConcreteModel
        A complete NLP model ready for IPOPT.
    """
    options = options or OPFBuildOptions()
    validate_required_data(data)

    ac_buses = _active_items(data.get("ac_buses", {}))
    dc_buses = _active_items(data.get("dc_buses", {}))
    ac_branches = _active_items(data.get("ac_branches", {}))
    dc_branches = _active_items(data.get("dc_branches", {}))
    generators = _active_items(data.get("generators", {}))
    ac_loads = _active_items(data.get("ac_loads", {}))
    dc_loads = _active_items(data.get("dc_loads", {}))
    dc_generators = _active_items(data.get("dc_generators", {}))
    storage_units = _active_items(data.get("storage_units", {}))
    converters = _active_items(data.get("converters", {}))
    dcdc_converters = _active_items(data.get("dcdc_converters", {}))

    model = pyo.ConcreteModel(name="acdc_opf_total_active_loss_minimization")
    model._opf_data = data
    model._storage_units = storage_units

    # Sets
    model.AC_BUS = pyo.Set(initialize=list(ac_buses.keys()), ordered=True)
    model.DC_BUS = pyo.Set(initialize=list(dc_buses.keys()), ordered=True)
    model.AC_BRANCH = pyo.Set(initialize=list(ac_branches.keys()), ordered=True)
    model.AC_LINE_BRANCH = pyo.Set(
        initialize=[
            key for key, branch in ac_branches.items()
            if str(branch.get("kind", "line")).lower() != "transformer"
        ],
        ordered=True,
    )
    model.AC_TRANSFORMER_BRANCH = pyo.Set(
        initialize=[
            key for key, branch in ac_branches.items()
            if str(branch.get("kind", "line")).lower() == "transformer"
        ],
        ordered=True,
    )
    model.DC_BRANCH = pyo.Set(initialize=list(dc_branches.keys()), ordered=True)
    model.GEN = pyo.Set(initialize=list(generators.keys()), ordered=True)
    model.DC_GEN = pyo.Set(initialize=list(dc_generators.keys()), ordered=True)
    model.AC_LOAD = pyo.Set(initialize=list(ac_loads.keys()), ordered=True)
    model.DC_LOAD = pyo.Set(initialize=list(dc_loads.keys()), ordered=True)
    model.CONV = pyo.Set(initialize=list(converters.keys()), ordered=True)
    model.DCDC = pyo.Set(initialize=list(dcdc_converters.keys()), ordered=True)
    model.STORAGE = pyo.Set(initialize=list(storage_units.keys()), ordered=True)

    # AC bus variables
    model.Vmag = pyo.Var(
        model.AC_BUS,
        initialize=lambda m, i: ac_buses[i].get("v0", 1.0),
        bounds=lambda m, i: (ac_buses[i]["v_min"], ac_buses[i]["v_max"]),
    )
    model.theta = pyo.Var(
        model.AC_BUS,
        initialize=lambda m, i: ac_buses[i].get("theta0", 0.0),
    )

    # AC branch flow variables. Positive direction follows the branch endpoint.
    model.P_ac_f = pyo.Var(model.AC_BRANCH, initialize=0.0)
    model.Q_ac_f = pyo.Var(model.AC_BRANCH, initialize=0.0)
    model.P_ac_t = pyo.Var(model.AC_BRANCH, initialize=0.0)
    model.Q_ac_t = pyo.Var(model.AC_BRANCH, initialize=0.0)

    # DC variables
    model.Vdc = pyo.Var(
        model.DC_BUS,
        initialize=lambda m, e: dc_buses[e].get("v0", 1.0),
        bounds=lambda m, e: (dc_buses[e]["v_min"], dc_buses[e]["v_max"]),
    )
    model.Idc = pyo.Var(
        model.DC_BRANCH,
        initialize=lambda m, d: dc_branches[d].get("i0", 0.0),
        bounds=lambda m, d: (
            -dc_branches[d]["i_max"] if dc_branches[d].get("i_max") is not None else None,
            dc_branches[d].get("i_max"),
        ),
    )
    model.Pdc_f = pyo.Var(model.DC_BRANCH, initialize=0.0)
    model.Pdc_t = pyo.Var(model.DC_BRANCH, initialize=0.0)
    model.Pdc_loss = pyo.Var(model.DC_BRANCH, initialize=0.0, bounds=(0.0, None))

    # DCDC converter variables. ``Ddcdc`` is the per-unit voltage ratio used by
    # the ACDCPF transformer-conductance model. It is fixed unless the data
    # explicitly marks the converter ratio as controllable and provides bounds.
    model.Ddcdc = pyo.Var(
        model.DCDC,
        initialize=lambda m, k: dcdc_converters[k].get("d_pu", 1.0),
        bounds=lambda m, k: (
            dcdc_converters[k]["d_pu_min"],
            dcdc_converters[k]["d_pu_max"],
        ),
    )
    model.Pdcdc_from = pyo.Var(
        model.DCDC,
        initialize=lambda m, k: dcdc_converters[k].get("initial", {}).get("p_from", 0.0),
    )
    model.Pdcdc_to = pyo.Var(
        model.DCDC,
        initialize=lambda m, k: dcdc_converters[k].get("initial", {}).get("p_to", 0.0),
    )
    model.Pdcdc_loss = pyo.Var(
        model.DCDC,
        initialize=lambda m, k: dcdc_converters[k].get("initial", {}).get("loss", 0.0),
        bounds=(0.0, None),
    )

    # Generator variables
    model.Pg = pyo.Var(
        model.GEN,
        initialize=lambda m, g: generators[g].get("pg0", 0.0),
        bounds=lambda m, g: (generators[g]["pg_min"], generators[g]["pg_max"]),
    )
    model.Qg = pyo.Var(
        model.GEN,
        initialize=lambda m, g: generators[g].get("qg0", 0.0),
        bounds=lambda m, g: (generators[g]["qg_min"], generators[g]["qg_max"]),
    )
    model.Pdcg = pyo.Var(
        model.DC_GEN,
        initialize=lambda m, g: dc_generators[g].get("p0", 0.0),
        bounds=lambda m, g: (dc_generators[g]["p_min"], dc_generators[g]["p_max"]),
    )
    model.Pst = pyo.Var(
        model.STORAGE,
        initialize=lambda m, s: storage_units[s].get("p0", 0.0),
        bounds=lambda m, s: (storage_units[s]["p_min"], storage_units[s]["p_max"]),
    )
    model.Pst_ch = pyo.Var(
        model.STORAGE,
        initialize=lambda m, s: max(-float(storage_units[s].get("p0", 0.0)), 0.0),
        bounds=lambda m, s: (0.0, max(-float(storage_units[s]["p_min"]), 0.0)),
    )
    model.Pst_dis = pyo.Var(
        model.STORAGE,
        initialize=lambda m, s: max(float(storage_units[s].get("p0", 0.0)), 0.0),
        bounds=lambda m, s: (0.0, max(float(storage_units[s]["p_max"]), 0.0)),
    )
    model.Qst = pyo.Var(
        model.STORAGE,
        initialize=lambda m, s: storage_units[s].get("q0", 0.0),
        bounds=lambda m, s: (storage_units[s]["q_min"], storage_units[s]["q_max"]),
    )
    model.Est_final = pyo.Var(
        model.STORAGE,
        initialize=lambda m, s: float(storage_units[s]["soc0"]) * float(storage_units[s]["energy_mwh"]),
        bounds=lambda m, s: (
            float(storage_units[s]["soc_min"]) * float(storage_units[s]["energy_mwh"]),
            float(storage_units[s]["soc_max"]) * float(storage_units[s]["energy_mwh"]),
        ),
    )

    # Converter station internal variables
    model.Uf = pyo.Var(
        model.CONV,
        initialize=lambda m, c: converters[c].get("initial", {}).get("u_f", 1.0),
        bounds=lambda m, c: (
            converters[c].get("u_f_min", ac_buses[converters[c]["ac_bus"]]["v_min"]),
            converters[c].get("u_f_max", ac_buses[converters[c]["ac_bus"]]["v_max"]),
        ),
    )
    model.theta_f = pyo.Var(
        model.CONV,
        initialize=lambda m, c: converters[c].get("initial", {}).get("theta_f", 0.0),
    )
    model.Ucv = pyo.Var(
        model.CONV,
        initialize=lambda m, c: converters[c].get("initial", {}).get("u_cv", 1.0),
        bounds=lambda m, c: (converters[c]["u_cv_min"], converters[c]["u_cv_max"]),
    )
    model.theta_cv = pyo.Var(
        model.CONV,
        initialize=lambda m, c: converters[c].get("initial", {}).get("theta_cv", 0.0),
    )

    # Transformer flows: AC bus -> filter and filter -> AC bus.
    model.Ptf_if = pyo.Var(
        model.CONV,
        initialize=lambda m, c: converters[c].get("initial", {}).get("p_ac", 0.0),
        bounds=lambda m, c: (
            converters[c].get("p_terminal_min"),
            converters[c].get("p_terminal_max"),
        ),
    )
    model.Qtf_if = pyo.Var(
        model.CONV,
        initialize=lambda m, c: converters[c].get("initial", {}).get("q_ac", 0.0),
        bounds=lambda m, c: (
            converters[c].get("q_terminal_min"),
            converters[c].get("q_terminal_max"),
        ),
    )
    model.Ptf_fi = pyo.Var(model.CONV, initialize=0.0)
    model.Qtf_fi = pyo.Var(model.CONV, initialize=0.0)

    # Phase reactor flows: filter -> converter and converter -> filter.
    model.Ppr_fc = pyo.Var(model.CONV, initialize=0.0)
    model.Qpr_fc = pyo.Var(model.CONV, initialize=0.0)
    model.Ppr_cf = pyo.Var(model.CONV, initialize=0.0)
    model.Qpr_cf = pyo.Var(model.CONV, initialize=0.0)

    model.Q_filter = pyo.Var(model.CONV, initialize=0.0)

    # Electronic converter variables
    model.Pcv_ac = pyo.Var(
        model.CONV,
        initialize=lambda m, c: converters[c].get("initial", {}).get("p_ac", 0.0),
        bounds=lambda m, c: (converters[c]["p_ac_min"], converters[c]["p_ac_max"]),
    )
    model.Qcv_ac = pyo.Var(
        model.CONV,
        initialize=lambda m, c: converters[c].get("initial", {}).get("q_ac", 0.0),
        bounds=lambda m, c: (converters[c]["q_ac_min"], converters[c]["q_ac_max"]),
    )
    model.Pcv_dc = pyo.Var(
        model.CONV,
        initialize=lambda m, c: converters[c].get("initial", {}).get("p_dc", 0.0),
        bounds=lambda m, c: (converters[c]["p_dc_min"], converters[c]["p_dc_max"]),
    )
    model.Pcv_loss = pyo.Var(model.CONV, initialize=0.0, bounds=(0.0, None))
    model.Icv_ac = pyo.Var(
        model.CONV,
        initialize=lambda m, c: converters[c].get("initial", {}).get("i_ac", 0.1),
        bounds=lambda m, c: (0.0, converters[c]["i_ac_max"]),
    )
    model.Icv_dc = pyo.Var(
        model.CONV,
        initialize=lambda m, c: converters[c].get("initial", {}).get("i_dc", 0.0),
        bounds=lambda m, c: (converters[c]["i_dc_min"], converters[c]["i_dc_max"]),
    )

    # Fix reference angles and user-specified controls before constraints are built.
    for bus, values in ac_buses.items():
        if bool(values.get("is_slack", False)):
            model.theta[bus].fix(float(values.get("theta0", 0.0)))

    for converter_name, converter in dcdc_converters.items():
        if not bool(converter.get("optimize_ratio", False)):
            model.Ddcdc[converter_name].fix(float(converter["d_pu"]))

    _apply_fixed_values(model, data.get("fixed", {}))

    # ------------------------------------------------------------------
    # AC branch equations
    # ------------------------------------------------------------------
    def ac_branch_from_p_rule(m: pyo.ConcreteModel, l: str) -> Any:
        br = ac_branches[l]
        i = br["from"]
        j = br["to"]
        y = _ac_branch_y_terms(br)
        angle = m.theta[i] - m.theta[j]
        return m.P_ac_f[l] == (
            y["gff"] * m.Vmag[i] ** 2
            + m.Vmag[i]
            * m.Vmag[j]
            * (y["gft"] * pyo.cos(angle) + y["bft"] * pyo.sin(angle))
        )

    def ac_branch_from_q_rule(m: pyo.ConcreteModel, l: str) -> Any:
        br = ac_branches[l]
        i = br["from"]
        j = br["to"]
        y = _ac_branch_y_terms(br)
        angle = m.theta[i] - m.theta[j]
        return m.Q_ac_f[l] == (
            -y["bff"] * m.Vmag[i] ** 2
            + m.Vmag[i]
            * m.Vmag[j]
            * (y["gft"] * pyo.sin(angle) - y["bft"] * pyo.cos(angle))
        )

    def ac_branch_to_p_rule(m: pyo.ConcreteModel, l: str) -> Any:
        br = ac_branches[l]
        i = br["from"]
        j = br["to"]
        y = _ac_branch_y_terms(br)
        angle = m.theta[j] - m.theta[i]
        return m.P_ac_t[l] == (
            y["gtt"] * m.Vmag[j] ** 2
            + m.Vmag[j]
            * m.Vmag[i]
            * (y["gtf"] * pyo.cos(angle) + y["btf"] * pyo.sin(angle))
        )

    def ac_branch_to_q_rule(m: pyo.ConcreteModel, l: str) -> Any:
        br = ac_branches[l]
        i = br["from"]
        j = br["to"]
        y = _ac_branch_y_terms(br)
        angle = m.theta[j] - m.theta[i]
        return m.Q_ac_t[l] == (
            -y["btt"] * m.Vmag[j] ** 2
            + m.Vmag[j]
            * m.Vmag[i]
            * (y["gtf"] * pyo.sin(angle) - y["btf"] * pyo.cos(angle))
        )

    model.ac_branch_from_p = pyo.Constraint(model.AC_BRANCH, rule=ac_branch_from_p_rule)
    model.ac_branch_from_q = pyo.Constraint(model.AC_BRANCH, rule=ac_branch_from_q_rule)
    model.ac_branch_to_p = pyo.Constraint(model.AC_BRANCH, rule=ac_branch_to_p_rule)
    model.ac_branch_to_q = pyo.Constraint(model.AC_BRANCH, rule=ac_branch_to_q_rule)

    def ac_branch_from_rate_rule(m: pyo.ConcreteModel, l: str) -> Any:
        if not options.enforce_ac_branch_thermal_limits:
            return pyo.Constraint.Skip
        rate = ac_branches[l].get("rate")
        if rate is None or float(rate) <= 0.0:
            return pyo.Constraint.Skip
        return m.P_ac_f[l] ** 2 + m.Q_ac_f[l] ** 2 <= float(rate) ** 2

    def ac_branch_to_rate_rule(m: pyo.ConcreteModel, l: str) -> Any:
        if not options.enforce_ac_branch_thermal_limits:
            return pyo.Constraint.Skip
        rate = ac_branches[l].get("rate")
        if rate is None or float(rate) <= 0.0:
            return pyo.Constraint.Skip
        return m.P_ac_t[l] ** 2 + m.Q_ac_t[l] ** 2 <= float(rate) ** 2

    model.ac_branch_from_rate = pyo.Constraint(model.AC_BRANCH, rule=ac_branch_from_rate_rule)
    model.ac_branch_to_rate = pyo.Constraint(model.AC_BRANCH, rule=ac_branch_to_rate_rule)

    def ac_current_rule(m, l, terminal):
        br = ac_branches[l]
        limit = br.get(f"i_{terminal}_max")
        if not options.enforce_ac_branch_thermal_limits or limit is None:
            return pyo.Constraint.Skip
        p = m.P_ac_f[l] if terminal == "from" else m.P_ac_t[l]
        q = m.Q_ac_f[l] if terminal == "from" else m.Q_ac_t[l]
        return p**2 + q**2 <= (float(limit) * m.Vmag[br[terminal]])**2

    model.ac_branch_current = pyo.Constraint(model.AC_BRANCH, ["from", "to"], rule=ac_current_rule)

    # ------------------------------------------------------------------
    # DC branch equations
    # ------------------------------------------------------------------
    def dc_branch_ohm_rule(m: pyo.ConcreteModel, d: str) -> Any:
        br = dc_branches[d]
        e = br["from"]
        f = br["to"]
        return m.Vdc[e] - m.Vdc[f] == float(br["r"]) * m.Idc[d]

    def dc_branch_from_power_rule(m: pyo.ConcreteModel, d: str) -> Any:
        br = dc_branches[d]
        e = br["from"]
        poles = float(br.get("poles", 1.0))
        return m.Pdc_f[d] == poles * m.Vdc[e] * m.Idc[d]

    def dc_branch_to_power_rule(m: pyo.ConcreteModel, d: str) -> Any:
        br = dc_branches[d]
        f = br["to"]
        poles = float(br.get("poles", 1.0))
        return m.Pdc_t[d] == -poles * m.Vdc[f] * m.Idc[d]

    def dc_branch_loss_rule(m: pyo.ConcreteModel, d: str) -> Any:
        br = dc_branches[d]
        poles = float(br.get("poles", 1.0))
        return m.Pdc_loss[d] == poles * float(br["r"]) * m.Idc[d] ** 2

    model.dc_branch_ohm = pyo.Constraint(model.DC_BRANCH, rule=dc_branch_ohm_rule)
    model.dc_branch_from_power = pyo.Constraint(model.DC_BRANCH, rule=dc_branch_from_power_rule)
    model.dc_branch_to_power = pyo.Constraint(model.DC_BRANCH, rule=dc_branch_to_power_rule)
    model.dc_branch_loss = pyo.Constraint(model.DC_BRANCH, rule=dc_branch_loss_rule)

    def dc_branch_from_rate_rule(m: pyo.ConcreteModel, d: str) -> Any:
        if not options.enforce_dc_branch_limits:
            return pyo.Constraint.Skip
        rate = dc_branches[d].get("rate")
        if rate is None or float(rate) <= 0.0:
            return pyo.Constraint.Skip
        return pyo.inequality(-float(rate), m.Pdc_f[d], float(rate))

    def dc_branch_to_rate_rule(m: pyo.ConcreteModel, d: str) -> Any:
        if not options.enforce_dc_branch_limits:
            return pyo.Constraint.Skip
        rate = dc_branches[d].get("rate")
        if rate is None or float(rate) <= 0.0:
            return pyo.Constraint.Skip
        return pyo.inequality(-float(rate), m.Pdc_t[d], float(rate))

    model.dc_branch_from_rate = pyo.Constraint(model.DC_BRANCH, rule=dc_branch_from_rate_rule)
    model.dc_branch_to_rate = pyo.Constraint(model.DC_BRANCH, rule=dc_branch_to_rate_rule)

    # ------------------------------------------------------------------
    # DCDC converter equations
    # ------------------------------------------------------------------
    def dcdc_from_power_rule(m: pyo.ConcreteModel, k: str) -> Any:
        cv = dcdc_converters[k]
        e = cv["from"]
        f = cv["to"]
        poles = float(cv.get("poles", 1.0))
        g_series = float(cv.get("g_series", 0.0))
        g_shunt = float(cv.get("g_shunt", 0.0))
        return m.Pdcdc_from[k] == poles * m.Vdc[e] * (
            m.Ddcdc[k] ** 2 * g_series * m.Vdc[e]
            - m.Ddcdc[k] * g_series * m.Vdc[f]
            + g_shunt * m.Vdc[e]
        )

    def dcdc_to_power_rule(m: pyo.ConcreteModel, k: str) -> Any:
        cv = dcdc_converters[k]
        e = cv["from"]
        f = cv["to"]
        poles = float(cv.get("poles", 1.0))
        g_series = float(cv.get("g_series", 0.0))
        return m.Pdcdc_to[k] == poles * m.Vdc[f] * (
            -m.Ddcdc[k] * g_series * m.Vdc[e]
            + g_series * m.Vdc[f]
        )

    def dcdc_loss_rule(m: pyo.ConcreteModel, k: str) -> Any:
        return m.Pdcdc_loss[k] == m.Pdcdc_from[k] + m.Pdcdc_to[k]

    model.dcdc_from_power = pyo.Constraint(model.DCDC, rule=dcdc_from_power_rule)
    model.dcdc_to_power = pyo.Constraint(model.DCDC, rule=dcdc_to_power_rule)
    model.dcdc_loss = pyo.Constraint(model.DCDC, rule=dcdc_loss_rule)

    def dcdc_rating_rule(m, k, terminal):
        rate = dcdc_converters[k].get("rate")
        if rate is None or float(rate) <= 0:
            return pyo.Constraint.Skip
        power = m.Pdcdc_from[k] if terminal == "from" else m.Pdcdc_to[k]
        return pyo.inequality(-float(rate), power, float(rate))

    model.dcdc_rating = pyo.Constraint(model.DCDC, ["from", "to"], rule=dcdc_rating_rule)

    # ------------------------------------------------------------------
    # Converter station equations
    # ------------------------------------------------------------------
    def transformer_p_if_rule(m: pyo.ConcreteModel, c: str) -> Any:
        cv = converters[c]
        tf = cv.get("transformer", {})
        i = cv["ac_bus"]
        if not _has_impedance(tf):
            return m.Ptf_if[c] + m.Ptf_fi[c] == 0.0

        g, b = _series_admittance(float(tf.get("r", 0.0)), float(tf.get("x", 0.0)))
        tap = _normal_tap(tf.get("tap", 1.0))
        angle = m.theta[i] - m.theta_f[c]
        return m.Ptf_if[c] == (
            g * (m.Vmag[i] / tap) ** 2
            - g * (m.Vmag[i] / tap) * m.Uf[c] * pyo.cos(angle)
            - b * (m.Vmag[i] / tap) * m.Uf[c] * pyo.sin(angle)
        )

    def transformer_q_if_rule(m: pyo.ConcreteModel, c: str) -> Any:
        cv = converters[c]
        tf = cv.get("transformer", {})
        i = cv["ac_bus"]
        if not _has_impedance(tf):
            return m.Qtf_if[c] + m.Qtf_fi[c] == 0.0

        g, b = _series_admittance(float(tf.get("r", 0.0)), float(tf.get("x", 0.0)))
        tap = _normal_tap(tf.get("tap", 1.0))
        angle = m.theta[i] - m.theta_f[c]
        return m.Qtf_if[c] == (
            -b * (m.Vmag[i] / tap) ** 2
            + b * (m.Vmag[i] / tap) * m.Uf[c] * pyo.cos(angle)
            - g * (m.Vmag[i] / tap) * m.Uf[c] * pyo.sin(angle)
        )

    def transformer_p_fi_rule(m: pyo.ConcreteModel, c: str) -> Any:
        cv = converters[c]
        tf = cv.get("transformer", {})
        i = cv["ac_bus"]
        if not _has_impedance(tf):
            return pyo.Constraint.Skip

        g, b = _series_admittance(float(tf.get("r", 0.0)), float(tf.get("x", 0.0)))
        tap = _normal_tap(tf.get("tap", 1.0))
        angle = m.theta_f[c] - m.theta[i]
        return m.Ptf_fi[c] == (
            g * m.Uf[c] ** 2
            - g * m.Uf[c] * (m.Vmag[i] / tap) * pyo.cos(angle)
            - b * m.Uf[c] * (m.Vmag[i] / tap) * pyo.sin(angle)
        )

    def transformer_q_fi_rule(m: pyo.ConcreteModel, c: str) -> Any:
        cv = converters[c]
        tf = cv.get("transformer", {})
        i = cv["ac_bus"]
        if not _has_impedance(tf):
            return pyo.Constraint.Skip

        g, b = _series_admittance(float(tf.get("r", 0.0)), float(tf.get("x", 0.0)))
        tap = _normal_tap(tf.get("tap", 1.0))
        angle = m.theta_f[c] - m.theta[i]
        return m.Qtf_fi[c] == (
            -b * m.Uf[c] ** 2
            + b * m.Uf[c] * (m.Vmag[i] / tap) * pyo.cos(angle)
            - g * m.Uf[c] * (m.Vmag[i] / tap) * pyo.sin(angle)
        )

    def transformer_lossless_voltage_rule(m: pyo.ConcreteModel, c: str) -> Any:
        cv = converters[c]
        tf = cv.get("transformer", {})
        if _has_impedance(tf):
            return pyo.Constraint.Skip
        i = cv["ac_bus"]
        return m.Vmag[i] / _normal_tap(tf.get("tap", 1.0)) == m.Uf[c]

    def transformer_lossless_angle_rule(m: pyo.ConcreteModel, c: str) -> Any:
        cv = converters[c]
        tf = cv.get("transformer", {})
        if _has_impedance(tf):
            return pyo.Constraint.Skip
        i = cv["ac_bus"]
        return m.theta_f[c] == m.theta[i]

    model.transformer_p_if = pyo.Constraint(model.CONV, rule=transformer_p_if_rule)
    model.transformer_q_if = pyo.Constraint(model.CONV, rule=transformer_q_if_rule)
    model.transformer_p_fi = pyo.Constraint(model.CONV, rule=transformer_p_fi_rule)
    model.transformer_q_fi = pyo.Constraint(model.CONV, rule=transformer_q_fi_rule)
    model.transformer_lossless_voltage = pyo.Constraint(model.CONV, rule=transformer_lossless_voltage_rule)
    model.transformer_lossless_angle = pyo.Constraint(model.CONV, rule=transformer_lossless_angle_rule)

    def filter_q_rule(m: pyo.ConcreteModel, c: str) -> Any:
        bf = float(converters[c].get("filter", {}).get("b", 0.0))
        return m.Q_filter[c] == -bf * m.Uf[c] ** 2

    model.filter_q = pyo.Constraint(model.CONV, rule=filter_q_rule)

    def phase_reactor_p_fc_rule(m: pyo.ConcreteModel, c: str) -> Any:
        pr = converters[c].get("phase_reactor", {})
        if not _has_impedance(pr):
            return m.Ppr_fc[c] + m.Ppr_cf[c] == 0.0

        g, b = _series_admittance(float(pr.get("r", 0.0)), float(pr.get("x", 0.0)))
        angle = m.theta_f[c] - m.theta_cv[c]
        return m.Ppr_fc[c] == (
            g * m.Uf[c] ** 2
            - g * m.Uf[c] * m.Ucv[c] * pyo.cos(angle)
            - b * m.Uf[c] * m.Ucv[c] * pyo.sin(angle)
        )

    def phase_reactor_q_fc_rule(m: pyo.ConcreteModel, c: str) -> Any:
        pr = converters[c].get("phase_reactor", {})
        if not _has_impedance(pr):
            return m.Qpr_fc[c] + m.Qpr_cf[c] == 0.0

        g, b = _series_admittance(float(pr.get("r", 0.0)), float(pr.get("x", 0.0)))
        angle = m.theta_f[c] - m.theta_cv[c]
        return m.Qpr_fc[c] == (
            -b * m.Uf[c] ** 2
            + b * m.Uf[c] * m.Ucv[c] * pyo.cos(angle)
            - g * m.Uf[c] * m.Ucv[c] * pyo.sin(angle)
        )

    def phase_reactor_p_cf_rule(m: pyo.ConcreteModel, c: str) -> Any:
        pr = converters[c].get("phase_reactor", {})
        if not _has_impedance(pr):
            return pyo.Constraint.Skip

        g, b = _series_admittance(float(pr.get("r", 0.0)), float(pr.get("x", 0.0)))
        angle = m.theta_cv[c] - m.theta_f[c]
        return m.Ppr_cf[c] == (
            g * m.Ucv[c] ** 2
            - g * m.Ucv[c] * m.Uf[c] * pyo.cos(angle)
            - b * m.Ucv[c] * m.Uf[c] * pyo.sin(angle)
        )

    def phase_reactor_q_cf_rule(m: pyo.ConcreteModel, c: str) -> Any:
        pr = converters[c].get("phase_reactor", {})
        if not _has_impedance(pr):
            return pyo.Constraint.Skip

        g, b = _series_admittance(float(pr.get("r", 0.0)), float(pr.get("x", 0.0)))
        angle = m.theta_cv[c] - m.theta_f[c]
        return m.Qpr_cf[c] == (
            -b * m.Ucv[c] ** 2
            + b * m.Ucv[c] * m.Uf[c] * pyo.cos(angle)
            - g * m.Ucv[c] * m.Uf[c] * pyo.sin(angle)
        )

    def phase_reactor_lossless_voltage_rule(m: pyo.ConcreteModel, c: str) -> Any:
        pr = converters[c].get("phase_reactor", {})
        if _has_impedance(pr):
            return pyo.Constraint.Skip
        return m.Uf[c] == m.Ucv[c]

    def phase_reactor_lossless_angle_rule(m: pyo.ConcreteModel, c: str) -> Any:
        pr = converters[c].get("phase_reactor", {})
        if _has_impedance(pr):
            return pyo.Constraint.Skip
        return m.theta_f[c] == m.theta_cv[c]

    model.phase_reactor_p_fc = pyo.Constraint(model.CONV, rule=phase_reactor_p_fc_rule)
    model.phase_reactor_q_fc = pyo.Constraint(model.CONV, rule=phase_reactor_q_fc_rule)
    model.phase_reactor_p_cf = pyo.Constraint(model.CONV, rule=phase_reactor_p_cf_rule)
    model.phase_reactor_q_cf = pyo.Constraint(model.CONV, rule=phase_reactor_q_cf_rule)
    model.phase_reactor_lossless_voltage = pyo.Constraint(model.CONV, rule=phase_reactor_lossless_voltage_rule)
    model.phase_reactor_lossless_angle = pyo.Constraint(model.CONV, rule=phase_reactor_lossless_angle_rule)

    def filter_active_balance_rule(m: pyo.ConcreteModel, c: str) -> Any:
        return m.Ppr_fc[c] + m.Ptf_fi[c] == 0.0

    def filter_reactive_balance_rule(m: pyo.ConcreteModel, c: str) -> Any:
        return m.Qpr_fc[c] + m.Qtf_fi[c] + m.Q_filter[c] == 0.0

    model.filter_active_balance = pyo.Constraint(model.CONV, rule=filter_active_balance_rule)
    model.filter_reactive_balance = pyo.Constraint(model.CONV, rule=filter_reactive_balance_rule)

    def converter_terminal_active_balance_rule(m: pyo.ConcreteModel, c: str) -> Any:
        return m.Ppr_cf[c] + m.Pcv_ac[c] == 0.0

    def converter_terminal_reactive_balance_rule(m: pyo.ConcreteModel, c: str) -> Any:
        return m.Qpr_cf[c] + m.Qcv_ac[c] == 0.0

    model.converter_terminal_active_balance = pyo.Constraint(
        model.CONV, rule=converter_terminal_active_balance_rule
    )
    model.converter_terminal_reactive_balance = pyo.Constraint(
        model.CONV, rule=converter_terminal_reactive_balance_rule
    )

    def converter_loss_rule(m: pyo.ConcreteModel, c: str) -> Any:
        cv = converters[c]
        parallel_converters = float(cv.get("parallel_converters", 1.0))
        loss_c = _converter_loss_c_expression(cv, m.Pcv_ac[c])
        return m.Pcv_loss[c] == (
            parallel_converters * float(cv.get("loss_a", 0.0))
            + float(cv.get("loss_b", 0.0)) * m.Icv_ac[c]
            + (loss_c / parallel_converters) * m.Icv_ac[c] ** 2
        )

    def converter_ac_dc_power_balance_rule(m: pyo.ConcreteModel, c: str) -> Any:
        return m.Pcv_ac[c] + m.Pcv_dc[c] == m.Pcv_loss[c]

    def converter_ac_current_rule(m: pyo.ConcreteModel, c: str) -> Any:
        return m.Pcv_ac[c] ** 2 + m.Qcv_ac[c] ** 2 == m.Ucv[c] ** 2 * m.Icv_ac[c] ** 2

    def converter_dc_current_rule(m: pyo.ConcreteModel, c: str) -> Any:
        e = converters[c]["dc_bus"]
        return m.Pcv_dc[c] == m.Vdc[e] * m.Icv_dc[c]

    def converter_apparent_power_rule(m: pyo.ConcreteModel, c: str) -> Any:
        if not options.enforce_converter_apparent_power_limits:
            return pyo.Constraint.Skip
        s_rated = converters[c].get("s_ac_rated")
        if s_rated is None or float(s_rated) <= 0.0:
            return pyo.Constraint.Skip
        return m.Pcv_ac[c] ** 2 + m.Qcv_ac[c] ** 2 <= float(s_rated) ** 2

    def converter_droop_rule(m: pyo.ConcreteModel, c: str) -> Any:
        if not options.enforce_optional_droop_controls:
            return pyo.Constraint.Skip
        droop = converters[c].get("droop")
        if not droop or not bool(droop.get("enabled", False)):
            return pyo.Constraint.Skip

        e = converters[c]["dc_bus"]
        # This uses this file's canonical sign convention for Pcv_dc.
        # If ACDCPF uses the opposite sign, convert pdc_set in the data adapter.
        return m.Pcv_dc[c] == float(droop["pdc_set"]) + float(droop["k"]) * (
            m.Vdc[e] - float(droop["vdc_set"])
        )

    model.converter_loss = pyo.Constraint(model.CONV, rule=converter_loss_rule)
    model.converter_ac_dc_power_balance = pyo.Constraint(
        model.CONV, rule=converter_ac_dc_power_balance_rule
    )
    model.converter_ac_current = pyo.Constraint(model.CONV, rule=converter_ac_current_rule)
    model.converter_dc_current = pyo.Constraint(model.CONV, rule=converter_dc_current_rule)
    model.converter_apparent_power = pyo.Constraint(model.CONV, rule=converter_apparent_power_rule)
    model.converter_droop = pyo.Constraint(model.CONV, rule=converter_droop_rule)

    def storage_apparent_power_rule(m: pyo.ConcreteModel, s: str) -> Any:
        return m.Pst[s] ** 2 + m.Qst[s] ** 2 <= float(storage_units[s]["s_rating"]) ** 2

    def storage_power_split_rule(m: pyo.ConcreteModel, s: str) -> Any:
        return m.Pst[s] == m.Pst_dis[s] - m.Pst_ch[s]

    def storage_single_period_soc_rule(m: pyo.ConcreteModel, s: str) -> Any:
        storage = storage_units[s]
        duration_hours = float(data.get("metadata", {}).get("snapshot_duration_hours", 1.0))
        base_mva = float(data["base_mva"])
        energy_initial = float(storage["soc0"]) * float(storage["energy_mwh"])
        return m.Est_final[s] == (
            energy_initial
            - m.Pst_dis[s] * base_mva * duration_hours / float(storage["eta_discharge"])
            + m.Pst_ch[s] * base_mva * duration_hours * float(storage["eta_charge"])
        )

    model.storage_apparent_power = pyo.Constraint(
        model.STORAGE,
        rule=storage_apparent_power_rule,
    )
    model.storage_power_split = pyo.Constraint(
        model.STORAGE,
        rule=storage_power_split_rule,
    )
    # A single battery cannot charge and discharge in the same interval.
    # Continuous complementarity retains IPOPT; this is a nonconvex constraint.
    model.storage_mode_relaxation = pyo.Param(model.STORAGE, initialize=0.0, mutable=True)
    def storage_exclusive_mode_rule(m, s):
        can_reverse = storage_units[s]["p_min"] < 0 < storage_units[s]["p_max"]
        relaxation = m.storage_mode_relaxation[s] if can_reverse and not m.Pst[s].fixed else 0.0
        return m.Pst_ch[s] * m.Pst_dis[s] == relaxation

    model.storage_exclusive_mode = pyo.Constraint(
        model.STORAGE,
        rule=storage_exclusive_mode_rule,
    )
    model.storage_single_period_soc = pyo.Constraint(
        model.STORAGE,
        rule=storage_single_period_soc_rule,
    )

    # ------------------------------------------------------------------
    # Nodal balances
    # ------------------------------------------------------------------
    def ac_bus_active_balance_rule(m: pyo.ConcreteModel, i: str) -> Any:
        converter_out = [m.Ptf_if[c] for c, cv in converters.items() if cv["ac_bus"] == i]
        branch_out = [
            m.P_ac_f[l] for l, br in ac_branches.items() if br["from"] == i
        ] + [m.P_ac_t[l] for l, br in ac_branches.items() if br["to"] == i]
        gen_in = [m.Pg[g] for g, gen in generators.items() if gen["bus"] == i]
        storage_in = [
            m.Pst[s]
            for s, storage in storage_units.items()
            if storage["bus_type"] == "ac" and storage["bus"] == i
        ]
        load = sum(float(load_values.get("p", 0.0)) for load_values in ac_loads.values() if load_values["bus"] == i)
        g_shunt = float(ac_buses[i].get("g_shunt", 0.0))

        return _sum_or_zero(converter_out) + _sum_or_zero(branch_out) == (
            _sum_or_zero(gen_in) + _sum_or_zero(storage_in) - load - g_shunt * m.Vmag[i] ** 2
        )

    def ac_bus_reactive_balance_rule(m: pyo.ConcreteModel, i: str) -> Any:
        converter_out = [m.Qtf_if[c] for c, cv in converters.items() if cv["ac_bus"] == i]
        branch_out = [
            m.Q_ac_f[l] for l, br in ac_branches.items() if br["from"] == i
        ] + [m.Q_ac_t[l] for l, br in ac_branches.items() if br["to"] == i]
        gen_in = [m.Qg[g] for g, gen in generators.items() if gen["bus"] == i]
        storage_in = [
            m.Qst[s]
            for s, storage in storage_units.items()
            if storage["bus_type"] == "ac" and storage["bus"] == i
        ]
        load = sum(float(load_values.get("q", 0.0)) for load_values in ac_loads.values() if load_values["bus"] == i)
        b_shunt = float(ac_buses[i].get("b_shunt", 0.0))

        return _sum_or_zero(converter_out) + _sum_or_zero(branch_out) == (
            _sum_or_zero(gen_in) + _sum_or_zero(storage_in) - load + b_shunt * m.Vmag[i] ** 2
        )

    def dc_bus_active_balance_rule(m: pyo.ConcreteModel, e: str) -> Any:
        converter_terms = [m.Pcv_dc[c] for c, cv in converters.items() if cv["dc_bus"] == e]
        dcdc_terms = [
            m.Pdcdc_from[k] for k, cv in dcdc_converters.items() if cv["from"] == e
        ] + [
            m.Pdcdc_to[k] for k, cv in dcdc_converters.items() if cv["to"] == e
        ]
        branch_terms = [
            m.Pdc_f[d] for d, br in dc_branches.items() if br["from"] == e
        ] + [m.Pdc_t[d] for d, br in dc_branches.items() if br["to"] == e]
        dc_gen = [
            m.Pdcg[g] for g, gen in dc_generators.items() if gen["bus"] == e
        ]
        storage_in = [
            m.Pst[s]
            for s, storage in storage_units.items()
            if storage["bus_type"] == "dc" and storage["bus"] == e
        ]
        dc_load = sum(float(load_values.get("p", 0.0)) for load_values in dc_loads.values() if load_values["bus"] == e)
        return (
            _sum_or_zero(converter_terms)
            + _sum_or_zero(dcdc_terms)
            + _sum_or_zero(branch_terms)
            == _sum_or_zero(dc_gen) + _sum_or_zero(storage_in) - dc_load
        )

    model.ac_bus_active_balance = pyo.Constraint(model.AC_BUS, rule=ac_bus_active_balance_rule)
    model.ac_bus_reactive_balance = pyo.Constraint(model.AC_BUS, rule=ac_bus_reactive_balance_rule)
    model.dc_bus_active_balance = pyo.Constraint(model.DC_BUS, rule=dc_bus_active_balance_rule)

    # ------------------------------------------------------------------
    # Objective: total active power loss minimization
    # ------------------------------------------------------------------
    def total_active_loss_expression(m: pyo.ConcreteModel) -> Any:
        ac_branch_losses = sum(m.P_ac_f[l] + m.P_ac_t[l] for l in m.AC_BRANCH)
        dc_branch_losses = sum(m.Pdc_loss[d] for d in m.DC_BRANCH)
        dcdc_converter_losses = sum(m.Pdcdc_loss[k] for k in m.DCDC)
        transformer_losses = sum(m.Ptf_if[c] + m.Ptf_fi[c] for c in m.CONV)
        phase_reactor_losses = sum(m.Ppr_fc[c] + m.Ppr_cf[c] for c in m.CONV)
        converter_losses = sum(m.Pcv_loss[c] for c in m.CONV)
        return (
            ac_branch_losses
            + dc_branch_losses
            + dcdc_converter_losses
            + transformer_losses
            + phase_reactor_losses
            + converter_losses
        )

    model.total_active_losses = pyo.Expression(rule=total_active_loss_expression)

    if options.objective != "total_active_loss_minimization":
        raise ValueError(f"Unsupported objective: {options.objective}")

    model.objective = pyo.Objective(expr=model.total_active_losses, sense=pyo.minimize)
    return model


# -----------------------------------------------------------------------------
# Fixed controls, solving, and result extraction
# -----------------------------------------------------------------------------


def _apply_fixed_values(model: pyo.ConcreteModel, fixed: Mapping[str, Mapping[str, float]]) -> None:
    """Fix selected variables from a simple nested dictionary.

    Example:
        fixed = {
            "Vmag": {"1": 1.06},
            "theta": {"1": 0.0},
            "Ptf_if": {"C1": 0.6},
            "Qtf_if": {"C1": 0.0},
            "Vdc": {"dc1": 1.0},
        }
    """
    var_map = {
        "Vmag": model.Vmag,
        "theta": model.theta,
        "Vdc": model.Vdc,
        "Pg": model.Pg,
        "Qg": model.Qg,
        "Pdcg": model.Pdcg,
        "Pst": model.Pst,
        "Qst": model.Qst,
        "Ddcdc": model.Ddcdc,
        "Pcv_ac": model.Pcv_ac,
        "Qcv_ac": model.Qcv_ac,
        "Pcv_dc": model.Pcv_dc,
        "Ptf_if": model.Ptf_if,
        "Qtf_if": model.Qtf_if,
        "Uf": model.Uf,
        "theta_f": model.theta_f,
        "Ucv": model.Ucv,
        "theta_cv": model.theta_cv,
    }

    for var_name, values in fixed.items():
        if var_name not in var_map:
            raise KeyError(f"Unknown fixed variable group: {var_name}")
        variable = var_map[var_name]
        for index, value in values.items():
            if index not in variable:
                raise KeyError(f"Cannot fix {var_name}[{index!r}]: index does not exist.")
            variable[index].fix(float(value))
            if var_name == "Pst":
                model.Pst_ch[index].fix(max(-float(value), 0.0))
                model.Pst_dis[index].fix(max(float(value), 0.0))


def solve_acdc_opf(
    data: Mapping[str, Any],
    build_options: OPFBuildOptions | None = None,
    solve_options: OPFSolveOptions | None = None,
    *,
    model: pyo.ConcreteModel | None = None,
) -> tuple[pyo.ConcreteModel, Any]:
    """Build and solve the AC/DC OPF model with IPOPT."""
    solve_options = solve_options or OPFSolveOptions()
    if model is None:
        model = build_acdc_opf_model(data, build_options)

    solver = pyo.SolverFactory("ipopt", executable=solve_options.ipopt_executable)
    solver.options["max_iter"] = solve_options.max_iter
    solver.options["tol"] = solve_options.tol
    solver.options["print_level"] = solve_options.print_level

    result = _solve_storage_continuation(model, solver, tee=solve_options.tee)
    return model, result


def _solve_storage_continuation(model, solver, *, tee):
    """Warm-start exact complementarity from relaxed NLPs, using IPOPT only.

    The intermediate relaxations avoid trapping an initially charging battery
    at P=0 when it needs to discharge. No relaxed result is returned as the
    final dispatch, and the network-loss objective is unchanged throughout.
    """
    periods = list(getattr(model, "_period_models", {None: model}).values())
    has_free_storage = any(
        not period.Pst[s].fixed and period._storage_units[s]["p_min"] < 0 < period._storage_units[s]["p_max"]
        for period in periods for s in period.STORAGE
    )
    relaxations = (1e-4, 1e-8, 0.0) if has_free_storage else (0.0,)
    # Do not let bound relaxation reintroduce negative charge/discharge.
    solver.options["bound_relax_factor"] = 0.0
    model._storage_solve_stages = []
    for relaxation in relaxations:
        for period in periods:
            for s in period.STORAGE:
                storage = period._storage_units[s]
                product_limit = max(-storage["p_min"], 0.0) * max(storage["p_max"], 0.0)
                period.storage_mode_relaxation[s].set_value(min(relaxation, 0.01 * product_limit))
        result = solver.solve(model, tee=tee, load_solutions=False)
        if result.solver.status in {pyo.SolverStatus.ok, pyo.SolverStatus.warning} and len(result.solution):
            model.solutions.load_from(result)
        model._storage_solve_stages.append({"relaxation_pu_squared": relaxation,
                                            "termination": str(result.solver.termination_condition)})
        # A relaxed initialization can be infeasible even when the exact
        # problem is feasible. Only the final exact solve decides acceptance.
    for period in periods:
        for s in period.STORAGE:
            period.storage_mode_relaxation[s].set_value(0.0)
    return result


def build_multiperiod_acdc_opf_model(
    period_data: Mapping[Any, Mapping[str, Any]],
    *,
    build_options: OPFBuildOptions | None = None,
) -> pyo.ConcreteModel:
    """Build a linked snapshot OPF model with storage SOC coupling.

    Each timestamp keeps the existing single-period AC/DC OPF equations. The
    outer model links storage energy between timestamps and minimizes network
    loss energy, sum(P_loss_MW * duration_hours), in MWh.
    """

    if not period_data:
        raise ValueError("At least one period is required for multi-period OPF.")

    build_options = build_options or OPFBuildOptions()
    period_keys = list(period_data.keys())
    model = pyo.ConcreteModel(name="multiperiod_acdc_opf_total_active_loss_minimization")
    model.TIME = pyo.Set(initialize=period_keys, ordered=True)
    model.TIME_POS = pyo.Set(initialize=range(len(period_keys)), ordered=True)
    model.ENERGY_POS = pyo.Set(initialize=range(len(period_keys) + 1), ordered=True)
    model._period_keys = period_keys
    model._period_key_by_position = {pos: key for pos, key in enumerate(period_keys)}

    period_models: dict[Any, pyo.ConcreteModel] = {}
    for pos, key in enumerate(period_keys):
        period_model = build_acdc_opf_model(period_data[key], build_options)
        period_model.objective.deactivate()
        period_model.storage_single_period_soc.deactivate()
        component_name = f"period_{pos}"
        model.add_component(component_name, period_model)
        period_models[key] = period_model
    model._period_models = period_models

    first_period = period_models[period_keys[0]]
    storage_units = getattr(first_period, "_storage_units", {})
    _validate_multiperiod_storage_compatibility(period_models, storage_units)
    model.STORAGE = pyo.Set(initialize=list(storage_units.keys()), ordered=True)
    model.E_storage = pyo.Var(
        model.ENERGY_POS,
        model.STORAGE,
        initialize=lambda m, _, s: float(storage_units[s]["soc0"]) * float(storage_units[s]["energy_mwh"]),
        bounds=lambda m, _, s: (
            float(storage_units[s]["soc_min"]) * float(storage_units[s]["energy_mwh"]),
            float(storage_units[s]["soc_max"]) * float(storage_units[s]["energy_mwh"]),
        ),
    )

    def initial_energy_rule(m: pyo.ConcreteModel, s: str) -> Any:
        return m.E_storage[0, s] == float(storage_units[s]["soc0"]) * float(storage_units[s]["energy_mwh"])

    def storage_energy_transition_rule(m: pyo.ConcreteModel, pos: int, s: str) -> Any:
        key = m._period_key_by_position[pos]
        period_model = m._period_models[key]
        period_storage = getattr(period_model, "_storage_units", {})[s]
        duration_hours = float(period_data[key].get("metadata", {}).get("snapshot_duration_hours", 1.0))
        base_mva = float(period_data[key]["base_mva"])
        return m.E_storage[pos + 1, s] == (
            m.E_storage[pos, s]
            - period_model.Pst_dis[s] * base_mva * duration_hours / float(period_storage["eta_discharge"])
            + period_model.Pst_ch[s] * base_mva * duration_hours * float(period_storage["eta_charge"])
        )

    def final_energy_rule(m: pyo.ConcreteModel, s: str) -> Any:
        return m.E_storage[len(period_keys), s] == m.E_storage[0, s]

    model.storage_initial_energy = pyo.Constraint(model.STORAGE, rule=initial_energy_rule)
    model.storage_energy_transition = pyo.Constraint(
        model.TIME_POS,
        model.STORAGE,
        rule=storage_energy_transition_rule,
    )
    model.storage_final_energy = pyo.Constraint(model.STORAGE, rule=final_energy_rule)
    model.total_active_losses = pyo.Expression(
        expr=sum(
            period_models[key].total_active_losses * float(period_data[key]["base_mva"])
            * float(period_data[key].get("metadata", {}).get("snapshot_duration_hours", 1.0))
            for key in period_keys
        )
    )
    model.objective = pyo.Objective(expr=model.total_active_losses, sense=pyo.minimize)
    return model


def solve_multiperiod_acdc_opf(
    period_data: Mapping[Any, Mapping[str, Any]],
    build_options: OPFBuildOptions | None = None,
    solve_options: OPFSolveOptions | None = None,
) -> tuple[pyo.ConcreteModel, Any]:
    """Build and solve the linked snapshot OPF model with IPOPT."""

    solve_options = solve_options or OPFSolveOptions()
    model = build_multiperiod_acdc_opf_model(period_data, build_options=build_options)

    solver = pyo.SolverFactory("ipopt", executable=solve_options.ipopt_executable)
    solver.options["max_iter"] = solve_options.max_iter
    solver.options["tol"] = solve_options.tol
    solver.options["print_level"] = solve_options.print_level

    result = _solve_storage_continuation(model, solver, tee=solve_options.tee)
    return model, result


def _validate_multiperiod_storage_compatibility(
    period_models: Mapping[Any, pyo.ConcreteModel],
    reference_storage_units: Mapping[str, Mapping[str, Any]],
) -> None:
    """Ensure storage identity and energy data are stable across periods."""

    reference_keys = set(reference_storage_units)
    reference_fields = (
        "bus",
        "bus_type",
        "energy_mwh",
        "soc0",
        "soc_min",
        "soc_max",
        "eta_charge",
        "eta_discharge",
    )
    for key, period_model in period_models.items():
        period_storage_units = getattr(period_model, "_storage_units", {})
        if set(period_storage_units) != reference_keys:
            raise ValueError(
                "Multi-period storage coupling requires the same storage units in every "
                f"period. Period {key!r} does not match the first period."
            )
        for storage_key in reference_keys:
            reference = reference_storage_units[storage_key]
            period_storage = period_storage_units[storage_key]
            for field in reference_fields:
                if not _multiperiod_storage_field_equal(
                    period_storage.get(field),
                    reference.get(field),
                ):
                    raise ValueError(
                        "Multi-period storage coupling requires stable storage energy data. "
                        f"Period {key!r}, storage {storage_key!r}, field {field!r} differs."
                    )


def _multiperiod_storage_field_equal(left: Any, right: Any) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        return str(left) == str(right)


def extract_active_loss_breakdown(
    model: pyo.ConcreteModel,
    base_mva: float | None = None,
) -> dict[str, float]:
    """Return active-loss components from a solved Pyomo model.

    Values are reported in per-unit by default. If ``base_mva`` is provided,
    values are reported in MW with keys matching ``PFResult`` where possible.
    Converter station losses are also split into transformer, phase reactor,
    and electronic converter components.
    """
    value = pyo.value
    ac_line = value(sum(model.P_ac_f[l] + model.P_ac_t[l] for l in model.AC_LINE_BRANCH))
    transformer = value(
        sum(model.P_ac_f[l] + model.P_ac_t[l] for l in model.AC_TRANSFORMER_BRANCH)
    )
    ac_branch = ac_line + transformer
    dc_branch = value(sum(model.Pdc_loss[d] for d in model.DC_BRANCH))
    dcdc_converter = value(sum(model.Pdcdc_loss[k] for k in model.DCDC))
    converter_transformer = value(
        sum(model.Ptf_if[c] + model.Ptf_fi[c] for c in model.CONV)
    )
    converter_phase_reactor = value(
        sum(model.Ppr_fc[c] + model.Ppr_cf[c] for c in model.CONV)
    )
    converter_electronic = value(sum(model.Pcv_loss[c] for c in model.CONV))
    converter = converter_transformer + converter_phase_reactor + converter_electronic
    total = ac_branch + dc_branch + dcdc_converter + converter

    if base_mva is None:
        return {
            "ac_branch_pu": float(ac_line),
            "transformer_pu": float(transformer),
            "dc_branch_pu": float(dc_branch),
            "dcdc_converter_pu": float(dcdc_converter),
            "converter_transformer_pu": float(converter_transformer),
            "converter_phase_reactor_pu": float(converter_phase_reactor),
            "converter_electronic_pu": float(converter_electronic),
            "converter_pu": float(converter),
            "total_active_losses_pu": float(total),
        }

    multiplier = float(base_mva)
    return {
        "ac_branch_mw": float(ac_line * multiplier),
        "transformer_mw": float(transformer * multiplier),
        "dc_branch_mw": float(dc_branch * multiplier),
        "dcdc_converter_mw": float(dcdc_converter * multiplier),
        "converter_transformer_mw": float(converter_transformer * multiplier),
        "converter_phase_reactor_mw": float(converter_phase_reactor * multiplier),
        "converter_electronic_mw": float(converter_electronic * multiplier),
        "converter_mw": float(converter * multiplier),
        "total_active_losses_mw": float(total * multiplier),
    }


def extract_opf_results(model: pyo.ConcreteModel, base_mva: float | None = None) -> dict[str, Any]:
    """Extract a compact result dictionary from a solved model.

    If ``base_mva`` is provided, the result also includes total active losses in MW.
    """
    value = pyo.value
    total_loss_pu = value(model.total_active_losses)
    storage_units = getattr(model, "_storage_units", {})
    results = {
        "objective_total_active_losses_pu": total_loss_pu,
        "ac_bus_voltage_magnitude_pu": {i: value(model.Vmag[i]) for i in model.AC_BUS},
        "ac_bus_voltage_angle_rad": {i: value(model.theta[i]) for i in model.AC_BUS},
        "dc_bus_voltage_pu": {e: value(model.Vdc[e]) for e in model.DC_BUS},
        "generator_pg_pu": {g: value(model.Pg[g]) for g in model.GEN},
        "generator_qg_pu": {g: value(model.Qg[g]) for g in model.GEN},
        "dc_generator_pg_pu": {g: value(model.Pdcg[g]) for g in model.DC_GEN},
        "storage_p_pu": {s: value(model.Pst[s]) for s in model.STORAGE},
        "storage_charge_p_pu": {s: value(model.Pst_ch[s]) for s in model.STORAGE},
        "storage_discharge_p_pu": {s: value(model.Pst_dis[s]) for s in model.STORAGE},
        "storage_q_pu": {s: value(model.Qst[s]) for s in model.STORAGE},
        "storage_energy_mwh": {s: value(model.Est_final[s]) for s in model.STORAGE},
        "storage_soc_percent": {
            s: 100.0 * value(model.Est_final[s]) / float(storage_units[s]["energy_mwh"])
            for s in model.STORAGE
        },
        "ac_branch_p_from_pu": {l: value(model.P_ac_f[l]) for l in model.AC_BRANCH},
        "ac_branch_q_from_pu": {l: value(model.Q_ac_f[l]) for l in model.AC_BRANCH},
        "ac_branch_p_to_pu": {l: value(model.P_ac_t[l]) for l in model.AC_BRANCH},
        "ac_branch_q_to_pu": {l: value(model.Q_ac_t[l]) for l in model.AC_BRANCH},
        "dc_branch_p_from_pu": {d: value(model.Pdc_f[d]) for d in model.DC_BRANCH},
        "dc_branch_p_to_pu": {d: value(model.Pdc_t[d]) for d in model.DC_BRANCH},
        "dc_branch_loss_pu": {d: value(model.Pdc_loss[d]) for d in model.DC_BRANCH},
        "dc_branch_current_pu": {d: value(model.Idc[d]) for d in model.DC_BRANCH},
        "dcdc_ratio_pu": {k: value(model.Ddcdc[k]) for k in model.DCDC},
        "dcdc_p_from_pu": {k: value(model.Pdcdc_from[k]) for k in model.DCDC},
        "dcdc_p_to_pu": {k: value(model.Pdcdc_to[k]) for k in model.DCDC},
        "dcdc_loss_pu": {k: value(model.Pdcdc_loss[k]) for k in model.DCDC},
        "converter_p_ac_pu": {c: value(model.Pcv_ac[c]) for c in model.CONV},
        "converter_q_ac_pu": {c: value(model.Qcv_ac[c]) for c in model.CONV},
        "converter_p_ac_terminal_absorbed_pu": {
            c: value(model.Ptf_if[c]) for c in model.CONV
        },
        "converter_q_ac_terminal_absorbed_pu": {
            c: value(model.Qtf_if[c]) for c in model.CONV
        },
        "converter_p_dc_pu": {c: value(model.Pcv_dc[c]) for c in model.CONV},
        "converter_loss_pu": {c: value(model.Pcv_loss[c]) for c in model.CONV},
        "converter_i_ac_pu": {c: value(model.Icv_ac[c]) for c in model.CONV},
        "converter_i_dc_pu": {c: value(model.Icv_dc[c]) for c in model.CONV},
        "converter_transformer_loss_pu": {
            c: value(model.Ptf_if[c] + model.Ptf_fi[c]) for c in model.CONV
        },
        "converter_phase_reactor_loss_pu": {
            c: value(model.Ppr_fc[c] + model.Ppr_cf[c]) for c in model.CONV
        },
        "active_loss_breakdown_pu": extract_active_loss_breakdown(model),
    }
    if base_mva is not None:
        results["objective_total_active_losses_mw"] = total_loss_pu * float(base_mva)
        results["active_loss_breakdown_mw"] = extract_active_loss_breakdown(
            model,
            base_mva=base_mva,
        )
    return results


def extract_multiperiod_opf_results(
    model: pyo.ConcreteModel,
    base_mva_by_period: Mapping[Any, float] | float | None = None,
) -> dict[Any, dict[str, Any]]:
    """Extract per-period result dictionaries from a linked multi-period model."""

    period_keys = list(getattr(model, "_period_keys", []))
    period_models: Mapping[Any, pyo.ConcreteModel] = getattr(model, "_period_models", {})
    extracted: dict[Any, dict[str, Any]] = {}
    for pos, key in enumerate(period_keys):
        period_model = period_models[key]
        for storage_key in period_model.STORAGE:
            period_model.Est_final[storage_key].value = pyo.value(
                model.E_storage[pos + 1, storage_key]
            )
        extracted[key] = extract_opf_results(
            period_model,
            base_mva=_period_base_mva(period_model, key, base_mva_by_period),
        )
        extracted[key]["horizon_active_loss_energy_mwh"] = pyo.value(model.total_active_losses)
    return extracted


def _period_base_mva(
    period_model: pyo.ConcreteModel,
    key: Any,
    base_mva_by_period: Mapping[Any, float] | float | None,
) -> float | None:
    if isinstance(base_mva_by_period, Mapping):
        return float(base_mva_by_period[key]) if key in base_mva_by_period else None
    if base_mva_by_period is not None:
        return float(base_mva_by_period)
    period_data = getattr(period_model, "_opf_data", {})
    base_mva = period_data.get("base_mva") if isinstance(period_data, Mapping) else None
    return None if base_mva is None else float(base_mva)


def validate_required_data(data: Mapping[str, Any]) -> None:
    """Run basic structural validation before model construction."""
    for name, value in (("base_mva", data.get("base_mva", 100.0)),
                        ("snapshot_duration_hours", data.get("metadata", {}).get("snapshot_duration_hours", 1.0))):
        if not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f"{name} must be finite and positive.")
    required_top_level = [
        "ac_buses",
        "ac_branches",
        "dc_buses",
        "dc_branches",
        "generators",
        "ac_loads",
        "dc_loads",
        "converters",
    ]
    for key in required_top_level:
        if key not in data:
            raise KeyError(f"Missing required data section: {key}")

    if not data["ac_buses"]:
        raise ValueError("At least one AC bus is required.")

    has_reference = any(bool(bus.get("is_slack", False)) for bus in data["ac_buses"].values())
    has_fixed_angle = bool(data.get("fixed", {}).get("theta"))
    if not has_reference and not has_fixed_angle:
        raise ValueError("Fix one AC voltage angle or mark one AC bus as is_slack=True.")

    for branch_name, branch in data["ac_branches"].items():
        if branch.get("status", 1) == 0:
            continue
        if branch["from"] not in data["ac_buses"]:
            raise KeyError(f"AC branch {branch_name} has unknown from bus {branch['from']}")
        if branch["to"] not in data["ac_buses"]:
            raise KeyError(f"AC branch {branch_name} has unknown to bus {branch['to']}")

    for branch_name, branch in data["dc_branches"].items():
        if branch.get("status", 1) == 0:
            continue
        if branch["from"] not in data["dc_buses"]:
            raise KeyError(f"DC branch {branch_name} has unknown from bus {branch['from']}")
        if branch["to"] not in data["dc_buses"]:
            raise KeyError(f"DC branch {branch_name} has unknown to bus {branch['to']}")
        if float(branch["r"]) <= 0.0:
            raise ValueError(f"DC branch {branch_name} must have positive resistance.")

    for load_name, load in data.get("ac_loads", {}).items():
        if load.get("status", 1) and load["bus"] not in data["ac_buses"]:
            raise KeyError(f"AC load {load_name} has unknown AC bus {load['bus']}")

    for load_name, load in data.get("dc_loads", {}).items():
        if load.get("status", 1) == 0:
            continue
        if load["bus"] not in data["dc_buses"]:
            raise KeyError(f"DC load {load_name} has unknown DC bus {load['bus']}")

    for gen_name, gen in data.get("dc_generators", {}).items():
        if gen.get("status", 1) == 0:
            continue
        if gen["bus"] not in data["dc_buses"]:
            raise KeyError(f"DC generator {gen_name} has unknown DC bus {gen['bus']}")
        if float(gen["p_min"]) > float(gen["p_max"]):
            raise ValueError(f"DC generator {gen_name} has p_min greater than p_max.")

    for storage_name, storage in data.get("storage_units", {}).items():
        if storage.get("status", 1) == 0:
            continue
        bus_type = str(storage.get("bus_type", "")).lower()
        if bus_type == "ac":
            if storage["bus"] not in data["ac_buses"]:
                raise KeyError(
                    f"Storage unit {storage_name} has unknown AC bus {storage['bus']}"
                )
        elif bus_type == "dc":
            if storage["bus"] not in data["dc_buses"]:
                raise KeyError(
                    f"Storage unit {storage_name} has unknown DC bus {storage['bus']}"
                )
            if abs(float(storage.get("q_min", 0.0))) > _EPS or abs(float(storage.get("q_max", 0.0))) > _EPS:
                raise ValueError(
                    f"DC storage unit {storage_name} cannot have reactive-power bounds."
                )
        else:
            raise ValueError(
                f"Storage unit {storage_name} must use bus_type 'ac' or 'dc'."
            )

        if float(storage["s_rating"]) <= 0.0:
            raise ValueError(f"Storage unit {storage_name} must have positive s_rating.")
        if float(storage.get("energy_mwh", 0.0)) <= 0.0:
            raise ValueError(f"Storage unit {storage_name} must have positive energy_mwh.")
        soc_min = float(storage.get("soc_min", 0.0))
        soc0 = float(storage.get("soc0", 0.5))
        soc_max = float(storage.get("soc_max", 1.0))
        if not (0.0 <= soc_min <= soc0 <= soc_max <= 1.0):
            raise ValueError(
                f"Storage unit {storage_name} SOC must satisfy 0 <= min <= initial <= max <= 1."
            )
        eta_charge = float(storage.get("eta_charge", 0.0))
        eta_discharge = float(storage.get("eta_discharge", 0.0))
        if not (0.0 < eta_charge <= 1.0 and 0.0 < eta_discharge <= 1.0):
            raise ValueError(f"Storage unit {storage_name} efficiencies must be in the interval (0, 1].")
        if float(storage["p_min"]) > float(storage["p_max"]):
            raise ValueError(f"Storage unit {storage_name} has p_min greater than p_max.")
        if float(storage["q_min"]) > float(storage["q_max"]):
            raise ValueError(f"Storage unit {storage_name} has q_min greater than q_max.")
        p0 = float(storage.get("p0", 0.0))
        q0 = float(storage.get("q0", 0.0))
        if p0 * p0 + q0 * q0 > (float(storage["s_rating"]) + 1e-9) ** 2:
            raise ValueError(
                f"Storage unit {storage_name} initial P/Q exceeds its apparent rating."
            )

    for converter_name, converter in data.get("dcdc_converters", {}).items():
        if converter.get("status", 1) == 0:
            continue
        if converter["from"] not in data["dc_buses"]:
            raise KeyError(
                f"DCDC converter {converter_name} has unknown from bus {converter['from']}"
            )
        if converter["to"] not in data["dc_buses"]:
            raise KeyError(
                f"DCDC converter {converter_name} has unknown to bus {converter['to']}"
            )
        if float(converter["d_pu"]) <= 0.0:
            raise ValueError(f"DCDC converter {converter_name} must have positive d_pu.")
        if float(converter["d_pu_min"]) <= 0.0 or float(converter["d_pu_max"]) <= 0.0:
            raise ValueError(
                f"DCDC converter {converter_name} must have positive ratio bounds."
            )
        if float(converter["d_pu_min"]) > float(converter["d_pu_max"]):
            raise ValueError(
                f"DCDC converter {converter_name} has d_pu_min greater than d_pu_max."
            )
        if float(converter.get("g_series", 0.0)) < 0.0:
            raise ValueError(
                f"DCDC converter {converter_name} must have nonnegative series conductance."
            )
        if float(converter.get("g_shunt", 0.0)) < 0.0:
            raise ValueError(
                f"DCDC converter {converter_name} must have nonnegative shunt conductance."
            )
        if float(converter.get("poles", 1.0)) <= 0.0:
            raise ValueError(f"DCDC converter {converter_name} must have positive poles.")

    for gen_name, gen in data["generators"].items():
        if gen.get("status", 1) == 0:
            continue
        if gen["bus"] not in data["ac_buses"]:
            raise KeyError(f"Generator {gen_name} has unknown AC bus {gen['bus']}")

    for converter_name, converter in data["converters"].items():
        if converter.get("status", 1) == 0:
            continue
        if float(converter.get("parallel_converters", 1.0)) <= 0.0:
            raise ValueError(
                f"Converter {converter_name} must have positive parallel_converters."
            )
        if converter["ac_bus"] not in data["ac_buses"]:
            raise KeyError(
                f"Converter {converter_name} has unknown AC bus {converter['ac_bus']}"
            )
        if converter["dc_bus"] not in data["dc_buses"]:
            raise KeyError(
                f"Converter {converter_name} has unknown DC bus {converter['dc_bus']}"
            )


if __name__ == "__main__":
    print(
        "This module defines a nonlinear AC/DC OPF formulation for Pyomo + IPOPT. "
        "Build a per-unit data dictionary and call solve_acdc_opf(data)."
    )
