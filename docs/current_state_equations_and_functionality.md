> Numerical-model reference retained from the source project. Legacy command names and internal identifiers remain compatibility references; use README.md and WORKBENCH.md for the supported Tool1 entry points.

# Current State, Implemented Equations, and Functionality

This document describes the current OPF implementation state as of
2026-07-24. It is intentionally written from the code that is currently in the
repository, not from the desired final architecture.

The main implemented solver path is the explicit nonlinear Pyomo/IPOPT AC/DC
OPF in:

```text
opf/formulations/acdc_opf_pyomo_loss_min.py
opf/pyomo_acdc_loss_min.py
data/acdcpf_to_pyomo.py
```

The benchmark/reporting path is:

```text
benchmarks/stagg5/benchmark_pf_opf_comparison.py
```

## Scope Summary

The project currently has two OPF paths:

| Path | Solver | Current role |
|---|---|---|
| `opf/formulations/acdc_opf_pyomo_loss_min.py` | Pyomo + IPOPT | Main equation-based nonlinear AC/DC OPF implementation. |
| `opf_acdc_loss_min.py` | SLSQP around repeated PF solves | Older black-box prototype, kept for reference/backward compatibility. |

The current main objective is active power loss minimization:

```text
minimize total active losses
```

No generation-cost objective, market dispatch, emissions objective, voltage
deviation objective, or multi-period objective is active in the current Pyomo
solver.

## Paper Formulation Basis

The nonlinear VSC AC/DC model follows the VSC formulation described in Ergun et
al. (2019), *Optimal Power Flow for AC-DC Grids: Formulation, Convex
Relaxation, Linear Approximation, and Implementation*.

Current mapping to the paper:

| Paper equations/features | Current implementation status |
|---|---|
| AC voltage limits, paper Eq. (1) | Implemented as bounds on `Vmag`. |
| DC voltage limits, paper Eq. (2) | Implemented as bounds on `Vdc`. |
| DC branch model, paper Eq. (3)-(8) | Implemented with voltage-current-power equations and branch limits. |
| Converter transformer, filter, phase reactor, paper Eq. (9)-(19) | Implemented for VSC stations. |
| Converter limits/loss/current model, paper Eq. (20)-(30) | Implemented. |
| AC/DC nodal balances, paper Eq. (33)-(35) | Implemented. |
| LCC firing-angle equations, paper Eq. (31)-(32) | Not implemented; out of current VSC-only scope. |
| SOC/SDP/QC relaxations and linear approximation | Not implemented; current solver is exact nonlinear IPOPT NLP. |

The paper also discusses formulations that are not part of the current solver.
Those are documented later under "Not Implemented".

## Internal Units and Sign Convention

All equations in the Pyomo model use per-unit values internally.

User-facing reports convert losses and setpoints to MW/MVAr where appropriate.

### AC Convention

For AC buses:

```text
Pg > 0      generation injection into AC grid
Qg > 0      reactive generation injection into AC grid
load p/q    positive demand
```

AC branch flow variables are endpoint-outgoing:

```text
P_ac_f[l], Q_ac_f[l]  active/reactive flow leaving the from bus
P_ac_t[l], Q_ac_t[l]  active/reactive flow leaving the to bus
```

### Converter Convention

The electronic converter variables use the convention in
`acdc_opf_pyomo_loss_min.py`:

```text
Pcv_ac[c] > 0   active power absorbed by the electronic converter from AC
Pcv_dc[c] < 0   active power injected by the electronic converter into DC
Pcv_loss[c] > 0 converter electronic active loss
```

The implemented electronic converter active-power coupling is:

```text
Pcv_ac[c] + Pcv_dc[c] = Pcv_loss[c]
```

Example rectifier behavior:

```text
Pcv_ac  = +1.00 pu
Pcv_dc  = -0.98 pu
Pcv_loss = +0.02 pu
```

The ACDCPF adapter and PyFlow adapter convert their local signs into this
canonical convention before the Pyomo model is built.

## Sets

The model builds these ordered Pyomo sets:

| Set | Meaning |
|---|---|
| `AC_BUS` | AC buses |
| `DC_BUS` | DC buses |
| `AC_BRANCH` | All AC branch-like elements used by the admittance equations |
| `AC_LINE_BRANCH` | Subset of `AC_BRANCH` created from physical AC lines |
| `AC_TRANSFORMER_BRANCH` | Subset of `AC_BRANCH` created from standalone transformers |
| `DC_BRANCH` | DC branches/lines |
| `GEN` | AC generators |
| `AC_LOAD` | AC loads |
| `DC_LOAD` | DC loads |
| `CONV` | AC/DC VSC converters |

Inactive elements are removed before set construction.

## Decision Variables

### AC Variables

| Variable | Meaning |
|---|---|
| `Vmag[i]` | AC voltage magnitude at bus `i` |
| `theta[i]` | AC voltage angle at bus `i` |
| `P_ac_f[l]`, `Q_ac_f[l]` | AC branch flow leaving the from side |
| `P_ac_t[l]`, `Q_ac_t[l]` | AC branch flow leaving the to side |
| `Pg[g]`, `Qg[g]` | Generator active/reactive output |

### DC Variables

| Variable | Meaning |
|---|---|
| `Vdc[e]` | DC voltage at DC bus `e` |
| `Idc[d]` | DC branch current |
| `Pdc_f[d]`, `Pdc_t[d]` | DC branch endpoint powers |
| `Pdc_loss[d]` | DC branch active loss |

### Converter Variables

| Variable | Meaning |
|---|---|
| `Uf[c]`, `theta_f[c]` | Filter-side AC voltage magnitude/angle |
| `Ucv[c]`, `theta_cv[c]` | Converter internal AC voltage magnitude/angle |
| `Ptf_if[c]`, `Qtf_if[c]` | Transformer flow from AC bus to filter |
| `Ptf_fi[c]`, `Qtf_fi[c]` | Transformer flow from filter to AC bus |
| `Ppr_fc[c]`, `Qpr_fc[c]` | Phase-reactor flow from filter to converter |
| `Ppr_cf[c]`, `Qpr_cf[c]` | Phase-reactor flow from converter to filter |
| `Q_filter[c]` | Reactive injection/absorption of AC filter |
| `Pcv_ac[c]`, `Qcv_ac[c]` | Electronic converter AC-side active/reactive power |
| `Pcv_dc[c]` | Electronic converter DC-side active power |
| `Pcv_loss[c]` | Electronic converter active loss |
| `Icv_ac[c]`, `Icv_dc[c]` | Converter AC/DC current variables |

## Implemented Equations

### AC Voltage and Generator Limits

AC voltage limits are implemented as variable bounds:

```text
Vmin_i <= Vmag_i <= Vmax_i
```

Generator limits are implemented as variable bounds:

```text
Pgmin_g <= Pg_g <= Pgmax_g
Qgmin_g <= Qg_g <= Qgmax_g
```

One AC voltage angle is fixed for each slack/reference bus:

```text
theta_slack = theta0_slack
```

### AC Branch Power Flow

AC branches are modeled as a pi-equivalent with optional tap and phase shift.
This common branch equation is used for both physical AC lines and standalone
transformers. The admittance terms `gff`, `bff`, `gft`, `bft`, `gtf`, `btf`,
`gtt`, `btt` are computed from the branch impedance, charging/shunt
susceptance, tap, and shift.

For branch `l` from AC bus `i` to AC bus `j`:

```text
theta_ij = theta_i - theta_j
theta_ji = theta_j - theta_i
```

From-side active/reactive flow:

```text
P_ac_f_l =
    gff_l * V_i^2
  + V_i * V_j * (gft_l * cos(theta_ij) + bft_l * sin(theta_ij))

Q_ac_f_l =
   -bff_l * V_i^2
  + V_i * V_j * (gft_l * sin(theta_ij) - bft_l * cos(theta_ij))
```

To-side active/reactive flow:

```text
P_ac_t_l =
    gtt_l * V_j^2
  + V_j * V_i * (gtf_l * cos(theta_ji) + btf_l * sin(theta_ji))

Q_ac_t_l =
   -btt_l * V_j^2
  + V_j * V_i * (gtf_l * sin(theta_ji) - btf_l * cos(theta_ji))
```

Thermal limits are enforced in both directions when a positive rating is
available:

```text
P_ac_f_l^2 + Q_ac_f_l^2 <= rate_l^2
P_ac_t_l^2 + Q_ac_t_l^2 <= rate_l^2
```

If `rate <= 0` or missing, the thermal constraint is skipped for that branch.

Standalone transformers are converted to the same branch equation with:

```text
r_pu, x_pu    transformer series impedance on its own MVA/kV base
b_pu          optional total charging susceptance on the transformer base
tap           fixed off-nominal tap ratio
shift_deg     fixed phase-shift angle
```

Before building the OPF model, transformer impedance is converted from the
transformer rating to the system base. Tap optimization fields are carried in
the data model (`tap_min`, `tap_max`, `tap_step_percent`,
`tap_controllable`), but taps are fixed parameters in the current
implementation.

### DC Voltage, Current, Power, and Losses

DC voltage limits are implemented as variable bounds:

```text
Vdc_min_e <= Vdc_e <= Vdc_max_e
```

For DC branch `d` from DC bus `e` to DC bus `f`, with branch resistance `R_d`
and pole factor `pol_d`, the implemented equations are:

```text
Vdc_e - Vdc_f = R_d * Idc_d

Pdc_f_d =  pol_d * Vdc_e * Idc_d
Pdc_t_d = -pol_d * Vdc_f * Idc_d

Pdc_loss_d = pol_d * R_d * Idc_d^2
```

Current limits are variable bounds on `Idc_d`:

```text
-Imax_d <= Idc_d <= Imax_d
```

If a DC branch power rating is provided, endpoint power limits are enforced:

```text
-rate_d <= Pdc_f_d <= rate_d
-rate_d <= Pdc_t_d <= rate_d
```

### Converter Transformer

The transformer is between the AC bus voltage and filter-side voltage. Let:

```text
V_i, theta_i       AC bus voltage
Uf_c, theta_f_c    filter-side voltage
tau_c              transformer tap magnitude
g_tf, b_tf          series admittance of transformer
```

For a lossy transformer, the implemented bus-to-filter equations are:

```text
Ptf_if_c =
    g_tf * (V_i / tau_c)^2
  - g_tf * (V_i / tau_c) * Uf_c * cos(theta_i - theta_f_c)
  - b_tf * (V_i / tau_c) * Uf_c * sin(theta_i - theta_f_c)

Qtf_if_c =
   -b_tf * (V_i / tau_c)^2
  + b_tf * (V_i / tau_c) * Uf_c * cos(theta_i - theta_f_c)
  - g_tf * (V_i / tau_c) * Uf_c * sin(theta_i - theta_f_c)
```

Filter-to-bus equations:

```text
Ptf_fi_c =
    g_tf * Uf_c^2
  - g_tf * Uf_c * (V_i / tau_c) * cos(theta_f_c - theta_i)
  - b_tf * Uf_c * (V_i / tau_c) * sin(theta_f_c - theta_i)

Qtf_fi_c =
   -b_tf * Uf_c^2
  + b_tf * Uf_c * (V_i / tau_c) * cos(theta_f_c - theta_i)
  - g_tf * Uf_c * (V_i / tau_c) * sin(theta_f_c - theta_i)
```

If transformer impedance is disabled or numerically zero, the model uses
lossless fallback equations:

```text
Ptf_if_c + Ptf_fi_c = 0
Qtf_if_c + Qtf_fi_c = 0
V_i / tau_c = Uf_c
theta_f_c = theta_i
```

### AC Filter

The filter is modeled as a shunt susceptance at the filter-side node:

```text
Q_filter_c = -Bf_c * Uf_c^2
```

Filter-node balances:

```text
Ppr_fc_c + Ptf_fi_c = 0
Qpr_fc_c + Qtf_fi_c + Q_filter_c = 0
```

### Phase Reactor

The phase reactor is modeled with the same two-terminal series-admittance form
as the transformer, between the filter-side voltage and converter internal
voltage. Let:

```text
Uf_c, theta_f_c      filter-side voltage
Ucv_c, theta_cv_c    converter internal voltage
g_pr, b_pr           series admittance of phase reactor
```

Filter-to-converter equations:

```text
Ppr_fc_c =
    g_pr * Uf_c^2
  - g_pr * Uf_c * Ucv_c * cos(theta_f_c - theta_cv_c)
  - b_pr * Uf_c * Ucv_c * sin(theta_f_c - theta_cv_c)

Qpr_fc_c =
   -b_pr * Uf_c^2
  + b_pr * Uf_c * Ucv_c * cos(theta_f_c - theta_cv_c)
  - g_pr * Uf_c * Ucv_c * sin(theta_f_c - theta_cv_c)
```

Converter-to-filter equations:

```text
Ppr_cf_c =
    g_pr * Ucv_c^2
  - g_pr * Ucv_c * Uf_c * cos(theta_cv_c - theta_f_c)
  - b_pr * Ucv_c * Uf_c * sin(theta_cv_c - theta_f_c)

Qpr_cf_c =
   -b_pr * Ucv_c^2
  + b_pr * Ucv_c * Uf_c * cos(theta_cv_c - theta_f_c)
  - g_pr * Ucv_c * Uf_c * sin(theta_cv_c - theta_f_c)
```

If phase-reactor impedance is disabled or numerically zero, the model uses
lossless fallback equations:

```text
Ppr_fc_c + Ppr_cf_c = 0
Qpr_fc_c + Qpr_cf_c = 0
Uf_c = Ucv_c
theta_f_c = theta_cv_c
```

### Electronic Converter Terminal Balance

The electronic converter AC-side powers are tied to the phase-reactor
converter-side powers:

```text
Ppr_cf_c + Pcv_ac_c = 0
Qpr_cf_c + Qcv_ac_c = 0
```

### Converter Loss Model

The electronic converter loss model is:

```text
Pcv_loss_c =
    N_c * a_c
  + b_c * Icv_ac_c
  + (c_eff_c / N_c) * Icv_ac_c^2
```

where:

```text
N_c       number of parallel converters represented as one station
a_c       constant loss coefficient
b_c       linear current loss coefficient
c_eff_c   quadratic current loss coefficient
```

The model supports two coefficient modes:

| Mode | Behavior |
|---|---|
| `fixed` | Uses the preselected `loss_c` coefficient. |
| `smooth_directional` | Uses a logistic transition between positive- and negative-power coefficients based on solved `Pcv_ac`. |

The smooth directional selector is:

```text
selector_c = 1 / (1 + exp(-sharpness_c * Pcv_ac_c))
c_eff_c = c_negative_c + (c_positive_c - c_negative_c) * selector_c
```

For the Stagg5 comparison benchmark, Tool1 (acdcopf) currently passes
`converter_loss_mode="fixed"` to keep the comparison closer to the fixed-mode
reference setup.

### Converter AC/DC Coupling and Current Relations

Implemented AC/DC active-power coupling:

```text
Pcv_ac_c + Pcv_dc_c = Pcv_loss_c
```

AC current relation:

```text
Pcv_ac_c^2 + Qcv_ac_c^2 = Ucv_c^2 * Icv_ac_c^2
```

DC current relation:

```text
Pcv_dc_c = Vdc_e * Icv_dc_c
```

where converter `c` is connected to DC bus `e`.

Converter apparent-power limit:

```text
Pcv_ac_c^2 + Qcv_ac_c^2 <= S_rated_c^2
```

Converter P/Q/DC power and current limits are also enforced through variable
bounds.

### Optional Converter Droop Constraint

If a converter has enabled droop metadata, the model adds:

```text
Pcv_dc_c = Pdc_set_c + k_c * (Vdc_e - Vdc_set_c)
```

This is implemented but is not the main Stagg5 VSC-only benchmark mode.

### AC Nodal Balances

For each AC bus `i`, the active-power balance is:

```text
sum_{c at i} Ptf_if_c
+ sum_{branches leaving/entering i} P_ac_endpoint
=
sum_{g at i} Pg_g
- Pload_i
- Gsh_i * Vmag_i^2
```

Equivalently, generation minus load and shunt consumption balances outgoing
branch flows and converter terminal absorption.

The reactive-power balance is:

```text
sum_{c at i} Qtf_if_c
+ sum_{branches leaving/entering i} Q_ac_endpoint
=
sum_{g at i} Qg_g
- Qload_i
+ Bsh_i * Vmag_i^2
```

### DC Nodal Balance

For each DC bus `e`:

```text
sum_{c at e} Pcv_dc_c
+ sum_{branches leaving/entering e} Pdc_endpoint
=
-Pdc_load_e
```

Under the current sign convention, `Pcv_dc < 0` means converter injection into
the DC grid.

### Objective Function

The objective is total active loss minimization:

```text
minimize P_loss_total
```

with:

```text
P_loss_total =
    sum_l (P_ac_f_l + P_ac_t_l)
  + sum_d Pdc_loss_d
  + sum_c (Ptf_if_c + Ptf_fi_c)
  + sum_c (Ppr_fc_c + Ppr_cf_c)
  + sum_c Pcv_loss_c
```

The reported loss breakdown is:

```text
AC line losses
standalone transformer losses
DC branch losses
converter transformer losses
converter phase reactor losses
converter electronic losses
total converter losses
total active losses
```

## Data Conversion and Control Selection

The native ACDCPF path converts `acdcpf.Network` into the Pyomo data dictionary
in `data/acdcpf_to_pyomo.py`.

Important conversion behavior:

| Data item | Current behavior |
|---|---|
| AC lines | Converts ohm/km and length into per-unit `r`, `x`, `b`. Nonzero AC line conductance is recorded as unsupported metadata and ignored by the current branch model. |
| AC transformers | Converts standalone `net.trafo` entries into tapped AC branch admittance terms. Transformer active losses are included in the objective and exported separately through `res_trafo`. Optional shunt conductance `g_pu` is recorded as unsupported metadata and ignored by the current PYPOWER/OPF branch model. |
| DC lines | Converts ohm/km and length into per-unit total resistance. The pole count is kept separately and used in DC power/loss equations. |
| Generators | Uses PF result values for initialization when available. Non-slack generator active dispatch is fixed by default. |
| VSC converters | Converts ACDCPF/PyFlow signs into the OPF converter convention. |
| DCDC converters | Native ACDCPF DCDC converters are converted into the OPF data model using the ACDCPF constant-ratio DC transformer model. Their voltage ratio is fixed by default and controllable only when ratio bounds are provided. |
| DC loads | Only constant-power DC loads are supported. |
| DC generators | Native ACDCPF DC generators are included in the DC nodal balance and fixed by default. |
| Storage units | One-timestamp storage units are converted as signed controllable DC injections. Negative `p_mw` means charging/load behavior; positive `p_mw` means discharging/generation behavior. |

For benchmark loss totals, the Tool5 (acdcpf) PF adapter uses full VSC station active
loss when terminal powers are available:

```text
P_station_loss = P_ac_terminal - P_dc_terminal
```

This includes electronic converter loss plus the converter transformer and
phase-reactor active losses. The raw ACDCPF `res_vsc.p_loss_mw` column remains
the electronic converter loss reported by ACDCPF. In the benchmark CSV/XLSX
exports, VSC `pl_mw` is the full station active loss and `p_elec_loss_mw` keeps
the electronic-only component where the source model exposes it.

Default `ACDCPFToPyomoOptions`:

```text
optimize_non_slack_generator_active_power = False
optimize_generator_voltage_setpoints      = False
optimize_dc_generator_active_power        = False
optimize_converter_pq_setpoints           = True
optimize_converter_active_power           = True
optimize_converter_reactive_power         = True
optimize_dcdc_voltage_ratio               = False
optimize_storage_active_power             = True
optimize_storage_reactive_power           = True
fix_ac_slack_voltage                      = True
fix_converter_vdc_vac_controls            = True
converter_loss_mode                       = "smooth_directional"
control_margin_percent                    = None
```

The Stagg5 benchmark overrides the converter loss mode to:

```text
converter_loss_mode = "fixed"
```

and optimizes only selected VSC controls:

```text
converter_active_power_indices   = (0, 2)
converter_reactive_power_indices = (0, 2)
```

This means:

```text
VSC 0 P/Q can move
VSC 2 P/Q can move
unselected VSC controls are fixed
non-slack AC generator P is fixed
generator voltage setpoints are fixed
```

`control_margin_percent` is an optional engineering margin around the PF
starting point. For example, `control_margin_percent = 10` limits each selected
VSC terminal active/reactive control to:

```text
PF starting value +/- 10% of the converter apparent-power rating
```

and limits each controllable storage active/reactive power to:

```text
PF starting value +/- 10% of the storage apparent-power rating
```

The resulting bounds are clipped by the physical lower/upper limits. If the
margin is omitted, Tool1 (acdcopf) uses the full technical bounds from the converted
model. The margin currently applies to VSC P/Q and storage P/Q controls; DCDC
voltage ratios continue to use their explicit ratio bounds.

The slack generator active/reactive output is allowed to solve the system
balance, as in standard power flow/OPF formulations.

## Current Benchmark Functionality

The main benchmark command is:

```powershell
.\.venv\Scripts\tool1-benchmark.exe
```

The legacy command `acdcpf-opf-stagg5-benchmark.exe` still points to the same
runner for backward compatibility. The benchmark currently supports these
registered grid cases:

| Grid case | Command | Notes |
|---|---|---|
| `original` | `.\.venv\Scripts\tool1-benchmark.exe --grid-case original` | Original 5-bus Stagg AC system with the 3-terminal 345 kV MTDC grid. PyFlow and MATACDC reference checks are available. |
| `hybrid_dcdc` | `.\.venv\Scripts\tool1-benchmark.exe --grid-case hybrid_dcdc` | Original Stagg5 MTDC grid plus a 20 kV DC PV source and a native 20 kV battery storage unit, each connected to the DC grid through a bounded-ratio DCDC converter. PyFlow reference is unavailable for this native ACDCPF-only topology. |
| `two_area_stagg5_dcdc` | `.\.venv\Scripts\tool1-benchmark.exe --grid-case two_area_stagg5_dcdc` | Synthetic two-area benchmark made from two Stagg5 MTDC systems, one common 345 kV external slack bus, two standalone 345/345 kV transformers, and one inter-area DCDC converter. Area B loads are scaled by 1.10, and factory VSC setpoints are mildly perturbed so OPF movement is visible with generator dispatch fixed. PyFlow reference is unavailable until the same topology is built there. |
| `ieee39_acdc` | `.\.venv\Scripts\tool1-benchmark.exe --grid-case ieee39_acdc` | PyFlow IEEE 39 AC/DC example with 39 AC buses, 10 DC buses, 12 DC branches, and 10 VSCs. The benchmark normalizes the first VSC/DC node to DC slack and uses a 600 MVA balancing rating. PF/export smoke works; Tool1 (acdcopf) and PyFlow OPF are not yet validated on this case. |

When run interactively, the benchmark asks which grid case to load. For
non-interactive runs, the default is `original`.

It runs:

| Run | Purpose |
|---|---|
| `Tool5 (acdcpf) PF` | Baseline PF using native `acdcpf.run_pf`. |
| `Tool1 (acdcopf) objective` | Internal Pyomo/IPOPT objective value. |
| Internal Tool1 (acdcopf) validation re-solve | Tool5 (acdcpf) PF after applying Tool1 (acdcopf) optimized setpoints. Used as a consistency check, not as a separate exported result run. |
| `PyFlow PF` | PyFlow sequential PF reference. |
| `PyFlow OPF VSC-only` | PyFlow OPF reference with generator dispatch restricted and VSCs free. |

The benchmark produces:

```text
PF baseline comparison: Tool5 (acdcpf) PF vs PyFlow PF
OPF improvement from each PF baseline
Final OPF comparison: Tool1 (acdcopf) objective result vs PyFlow OPF
VSC setpoint movement table
generator/converter/branch result tables
AC/DC voltage movement table
MATACDC reference check when original setpoints are used
diagnostics table
```

The Markdown report is written by default to:

```text
benchmarks/stagg5/reports/latest_stagg5_pf_opf_benchmark.md
```

The benchmark also writes machine-readable comparison exports:

| File | Contents |
|---|---|
| `benchmarks/stagg5/reports/latest_stagg5_pf_results.csv` | Semicolon-delimited PF result file. Contains only the `Tool5 (acdcpf) PF` run. |
| `benchmarks/stagg5/reports/latest_stagg5_opf_results.csv` | Semicolon-delimited OPF result file. Contains only the `Tool1 (acdcopf) objective` run. |
| `benchmarks/stagg5/reports/latest_stagg5_pf_results.xlsx` | Excel PF result workbook. Contains one worksheet per result group/list. |
| `benchmarks/stagg5/reports/latest_stagg5_opf_results.xlsx` | Excel OPF result workbook. Contains the same worksheet structure as the PF workbook. |

The PF and OPF result files intentionally use the same pandapower-style
`res_*` groups or worksheets, so the baseline load-flow output and optimized
OPF output can be compared sheet-by-sheet with matching columns. If
`--csv-output results.csv` is used, the script writes `results_pf.csv` and
`results_opf.csv`; if `--xlsx-output results.xlsx` is used, it writes
`results_pf.xlsx` and `results_opf.xlsx`.
Metadata uses readable study-case names rather than internal case identifiers.

The validation re-solve after applying Tool1 (acdcopf) setpoints is still calculated
when available and remains useful as an internal consistency check, but it is
not written as a separate PF/OPF result run. The split files also keep PyFlow
reference values out of the main result tables; PyFlow comparisons stay in the
Markdown benchmark report.

Excel is the easier format for manual inspection because sections such as
`res_bus`, `res_line`, `res_trafo`, `res_load`, `res_gen`, `res_dc_bus`,
`res_dc_line`, `res_dc_load`, `res_dc_gen`, `res_storage`, `res_vsc`, and
`res_dcdc` appear as separate sheets. CSV and Excel headers use compact names
for readability, with units carried directly in column names where practical.

`res_bus` reports solved bus voltage and net bus injection. `res_line` reports
AC branch terminal powers, active/reactive losses, derived current, ratings when
available, and `loading_pct`. `res_vsc` reports converter powers, losses,
rating, AC/DC currents, and loading. `res_dcdc` reports DCDC terminal powers,
losses, and voltage ratio. `res_trafo` reports standalone transformer terminal
powers, active/reactive losses, current, loading, tap, and phase shift when the
selected case contains transformer elements.

### Benchmark Starting Setpoints

The benchmark supports three starting-setpoint modes:

| Mode | Behavior |
|---|---|
| `original` | Uses original Stagg5 VSC setpoints for Stagg cases; keeps case-factory VSC setpoints for non-Stagg cases. |
| `perturbed` | Uses larger manual deviations to make OPF movement easier to see on Stagg cases; non-Stagg cases keep their factory values unless custom values are provided. |
| `custom` | User-provided VSC P/Q values from command line or interactive prompt. |

Example custom run:

```powershell
.\.venv\Scripts\tool1-benchmark.exe --vsc-setpoint 0:90:50 --vsc-setpoint 2:-80:40
```

Example run with selected VSC and storage controls restricted to +/-10% of
their apparent-power rating around the PF starting point:

```powershell
.\.venv\Scripts\tool1-benchmark.exe --grid-case hybrid_dcdc --control-margin-percent 10
```

The custom setpoint convention is the report/ACDCPF convention:

```text
p_ac_mw > 0 means the converter absorbs active power from the AC grid
```

The same physical setpoints are applied to PyFlow with the required internal
sign conversion.

### Benchmark Percentages

The report keeps two percentage meanings separate.

PF-to-OPF improvement:

```text
100 * (OPF loss - PF baseline loss) / abs(PF baseline loss)
```

Negative means losses decreased after optimization.

Cross-solver difference against PyFlow:

```text
100 * (Tool1 (acdcopf) result - PyFlow result) / abs(PyFlow result)
```

Positive means the Tool1 (acdcopf) reported loss is higher than PyFlow. Negative means
the Tool1 (acdcopf) reported loss is lower than PyFlow.

## Current Validation and Tests

The current test suite covers:

```text
ACDCPF native adapter
legacy PyFlow adapter
ACDCPF-to-Pyomo conversion
PyFlow-to-Pyomo conversion
Pyomo model construction
objective composition
converter loss equations
parallel converter scaling
droop conversion/sign convention
fixed variable/control selection
benchmark Markdown table formatting
benchmark custom VSC setpoint parsing
hybrid Stagg5 DCDC/PV/battery case construction
standalone transformer creation and PF result processing
two-area Stagg5 transformer/DCDC case construction and PF convergence
dynamic CSV/XLSX export shape for non-Stagg element counts
PyFlow IEEE39 AC/DC case selection and normalization
```

The benchmark has been exercised on the Stagg5 AC/DC case with:

```text
Tool5 (acdcpf) PF baseline
Tool1 (acdcopf) Pyomo/IPOPT OPF
internal ACDCPF consistency re-solve after Tool1 (acdcopf)
PyFlow PF baseline
PyFlow OPF reference
```

The hybrid DCDC case has been exercised with:

```text
Tool5 (acdcpf) PF baseline
Tool1 (acdcopf) Pyomo/IPOPT OPF with VSC P/Q, storage P/Q, and DCDC ratio controls
internal ACDCPF consistency re-solve after Tool1 (acdcopf)
CSV/XLSX/HTML export smoke checks
```

The two-area transformer/DCDC case has been exercised with:

```text
valid AC/DC element references
exactly one AC slack source
Tool5 (acdcpf) PF convergence with transformer `res_trafo` output
Tool1 (acdcopf) Pyomo/IPOPT convergence with selected VSC P/Q controls and DCDC ratio control
transformer active losses included in the objective loss breakdown
```

The PyFlow IEEE39 AC/DC case has been exercised with:

```text
Tool5 (acdcpf) PF through the PyFlow-to-ACDCPF bridge
PyFlow PF after DC-slack normalization
dynamic report/export construction for larger element counts
```

Current smoke tests show Tool1 (acdcopf) and PyFlow OPF returning infeasible
terminations on the normalized IEEE39 AC/DC case. The reporting code now treats
those runs as failed and exports `n/a` for OPF values instead of using raw
infeasible iterate values.

The solver is not yet validated as a general "any grid" OPF engine.

## Current Limitations

The following are intentionally not implemented in the current Pyomo/IPOPT
solver:

| Missing feature | Current behavior |
|---|---|
| LCC converter equations | Not implemented; VSC/MMC scope only. |
| Dynamic PV/battery storage model | Native signed storage P/Q control is implemented. Time-profile OPF runs now include inter-temporal SOC, charge/discharge efficiencies, SOC limits, and final SOC return. Full market/dispatch logic, degradation, and binary no-simultaneous-charge/discharge status are not modeled yet. |
| Convex relaxations | Not implemented; exact nonlinear IPOPT NLP only. |
| Linear approximations | Not implemented. |
| Unit commitment / discrete controls | Not implemented. |
| Discrete tap optimization | Not implemented. Existing tap values are parameters. |
| Transformer shunt conductance | Nonzero `g_pu` is flagged as unsupported and ignored by the current PYPOWER/OPF branch model. Transformer `b_pu` is supported. |
| Security-constrained OPF | Not implemented. |
| General large-grid validation | Not yet complete. Stagg5 remains the main solved OPF benchmark; IEEE39 AC/DC currently works as a larger PF/export smoke case but not as a validated OPF case. |
| Mixed/unsupported PyFlow features | Some features are rejected or require adapter work. |
| AC line conductance in native conversion | Nonzero `g_us_per_km` is flagged in metadata and ignored by the current AC branch model. |

## Practical Current State

The project is currently best described as:

```text
A working small-benchmark nonlinear AC/DC loss-minimization OPF prototype,
implemented with explicit Pyomo/IPOPT equations, validated against Tool5 (acdcpf) PF
and compared against PyFlow PF/OPF on Stagg5. The result/export layer can
handle larger registered cases, but the OPF formulation has not yet been
validated for arbitrary grids.
```

It is not yet:

```text
A fully general industrial OPF package for arbitrary AC/DC grids.
```

The immediate next engineering steps are:

1. Add a residual/limit diagnostic module for solved Pyomo results.
2. Freeze Stagg5 expected benchmark outputs under documented tolerances.
3. Add the HVDC point-to-point case as the next mandatory regression case.
4. Extend time-series storage from snapshot scheduling toward full multi-period studies, including optional terminal SOC policies, degradation costs, and no-simultaneous-charge/discharge logic if required.
5. Investigate the IEEE39 AC/DC OPF infeasibility before treating it as a
   formal OPF benchmark.
6. Add larger AC-only and hybrid cases only after the small cases remain stable.
