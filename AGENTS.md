# HYNET OPF Agent Instructions

## Project purpose

This repository implements a steady-state hybrid AC/DC Optimal Power Flow solver for the HYNET project.

The working system is based on PyPOWER/PyACDCPF-style data and the existing project files:

```text
case5_stagg.py
case5_stagg_MTDCslack.py
case5_stagg_MTDCdroop.py
case5_stagg_HVDCptp.py
runacdcpf.py
```

The main goal is not generic OPF.

The main goal is:

```text
active power loss minimisation in a hybrid AC/DC grid
```

Do not implement generation-cost OPF, economic dispatch, emissions minimisation, voltage-deviation minimisation, stochastic OPF, unit commitment, or market dispatch unless explicitly instructed.

The default objective is:

```text
minimise P_loss_total

P_loss_total =
    P_loss_AC_branches
  + P_loss_DC_branches
  + P_loss_converters
```

## Current stable architecture

These rules describe the current Tool1 (acdcopf) project baseline. They supersede older v1 planning notes in this file whenever there is a conflict.

Stable solver path:

```text
network data -> ACDCPF power flow baseline -> Tool1 (acdcopf) Pyomo model -> IPOPT solve -> Tool1 (acdcopf) results/exports
```

Use ACDCPF as the power-flow backend. Do not replace the PF backend, bypass it with PyFlow, or introduce another PF engine unless explicitly requested.

Use Pyomo/IPOPT as the OPF backend. Do not implement brute-force control scans, metaheuristics, or silent fallback optimizers as the main OPF path.

Use PyFlow only as an optional reference/benchmark source. PyFlow is not part of the Tool1 (acdcopf) solver path. If PyFlow cannot reproduce a selected topology, profile, or control set, report it as `not comparable` instead of producing misleading `n/a` values or adapting the solver to PyFlow.

Use PowerFactory/DigSILENT only as an external validation reference. It is not a runtime dependency.

Do not edit files under `.venv`, `site-packages`, or installed third-party package folders. If reference package behavior must change, make the change in the package source repository on a separate branch and document the reason.

Generated Markdown, CSV, XLSX, and HTML reports are outputs, not source. Do not commit generated reports unless the user explicitly asks to preserve a specific benchmark artifact.

Keep Tool1 (acdcopf) changes separate from ACDCPF package changes. Prefer separate branches/commits when both repositories must change.

## Tool1 (acdcopf) modelling guardrails

The default objective remains active power loss minimisation. If another objective is added, it must be explicitly requested and clearly separated from the default loss objective.

Slack generator active/reactive power is treated as the system balance/reference result. Do not treat the AC slack generator as a normal dispatch control unless the formulation is deliberately changed and tested.

The following values are fixed by default unless the selected runtime controls explicitly enable them:

```text
AC generator active power, except slack balancing
AC generator reactive power, except slack/bus-voltage consistency
AC generator voltage setpoints
line and transformer impedances
topology-defining bus connections
equipment ratings
transformer tap positions
```

Currently selectable OPF controls are:

```text
VSC active power
VSC reactive power
DCDC voltage ratio
AC generator active power
AC generator reactive power
DC generator curtailment
storage active/reactive dispatch where native storage exists
```

DC generator curtailment is downward-only for positive DC generation. A curtailment margin limits how much available profiled generation may be reduced; it must not allow generation to increase above the available profile value.

DCDC voltage ratio may be optimised only when enabled and explicit bounds exist. Keep the ratio physically interpretable as the controlled relation between the connected DC-side voltages.

Storage must be represented as native signed storage, not hidden charging/discharging shadow load/gen rows. Use this sign convention:

```text
P_storage > 0 means discharging/injecting into the grid
P_storage < 0 means charging/consuming from the grid
```

For time-series storage, enforce SOC limits and the documented SOC transition. Do not add binary charge/discharge mode decisions, degradation costs, or market scheduling without a separate modelling decision.

Every equation or controllability change must state:

```text
equation added or changed
paper/reference source or explicit modelling assumption
expected physical effect
test or benchmark proving the behavior
known limitation
```

## Version 1 success definition

Version 1 is successful when the solver:

1. Builds a simultaneous nonlinear AC/DC OPF model using Pyomo.
2. Solves the model using IPOPT.
3. Supports at least two small network combinations:

   * `case5_stagg.py` + `case5_stagg_MTDCslack.py`
   * `case5_stagg.py` + `case5_stagg_HVDCptp.py`
4. Uses active power loss minimisation as the only active objective.
5. Separately reports:

   * AC branch losses
   * DC branch losses
   * converter losses
   * total active losses
6. Produces physically valid results:

   * small AC active-power balance residuals
   * small AC reactive-power balance residuals
   * small DC active-power balance residuals
   * small converter coupling residuals
   * no significant voltage, branch, generator, DC, or converter limit violations
7. Provides an independent comparison against the existing sequential AC/DC PF reference pipeline from `runacdcpf.py`.
8. Confirms that IPOPT was actually called through Pyomo.

The PF reference is not the OPF target.

The OPF may produce different setpoints from PF because OPF minimises losses. However:

```text
PF mode should reproduce the reference PF pipeline where equivalent.
OPF mode should satisfy all constraints and report whether losses are lower than or equal to the PF baseline.
```

Do not claim success only because IPOPT returns a termination message. A result is acceptable only if solver status and physical diagnostics are both valid.

## Implementation strategy

Build the solver in staged, testable steps.

Do not produce one large untested solver file.

Recommended stages:

```text
1. Data loading and indexing
2. AC-only nonlinear OPF for case5_stagg.py
3. DC-only network block
4. Converter block
5. Full simultaneous AC/DC OPF for slack-controlled DC case
6. HVDC point-to-point case
7. Droop-controlled MTDC case
8. Modified IEEE 33-bus hybrid benchmark
9. Larger AC-only and synthetic hybrid benchmarks
```

Each stage must include diagnostics and tests before moving to the next stage.

If a later stage fails, do not rewrite earlier working stages blindly. Isolate the failing block.

## Required technology

Use:

```text
Python
Pyomo
IPOPT
NumPy
PyPOWER/PyACDCPF-style case data
pytest
```

The code must check whether required packages and solver binaries are available before solving.

Required checks:

```text
import check for pyomo
import check for numpy
import check for pypower where applicable
import check for pyacdcpf where applicable
IPOPT availability check through Pyomo
clear error message if a dependency is missing
```

Do not add new production dependencies without asking first and explaining why the dependency is needed.

Acceptable development dependencies:

```text
pytest
```

Do not add pandas, scipy, networkx, matplotlib, pandapower, cvxpy, or any other dependency unless explicitly approved.

## Repository structure

Use a readable package structure.

Preferred structure:

```text
hynet_opf/
    __init__.py
    data_loader.py
    indices.py
    admittance.py
    ac_model.py
    dc_model.py
    converter_model.py
    model.py
    losses.py
    diagnostics.py
    validation.py
    solve.py

tests/
    test_data_loader.py
    test_ac_model.py
    test_dc_model.py
    test_converter_model.py
    test_hybrid_slack_case.py
    test_hvdc_ptp_case.py
    test_hybrid_droop_case.py
    test_validation_against_reference_pf.py
```

Keep modules small and focused.

Avoid unnecessary abstraction. If a helper is used only once and makes the equation harder to inspect, do not hide the equation behind a clever wrapper.

Prefer clear code over architectural theatre.

## Read-only reference files

Do not modify:

```text
runacdcpf.py
existing PyACDCPF/PyPOWER reference code
original case files
```

The existing PF package is reference code prepared by a colleague. Adapt the OPF code to the existing data format instead of changing the reference implementation.

If a case file appears inconsistent, report the issue and propose a separate fix. Do not silently rewrite the case data.

If new benchmark cases are needed, create new case files with new names.

Example:

```text
case33_hybrid_acdc_v1.py
```

Do not overwrite original benchmark cases.

## Data compatibility

Preserve compatibility with PyPOWER/PyACDCPF-style matrices.

Create named column constants in `indices.py` instead of using raw column numbers throughout the model.

Acceptable:

```python
BUS_I = 0
BUS_TYPE = 1
PD = 2
QD = 3
```

Not acceptable outside parser/index files:

```python
bus[i, 2]
bus[i, 3]
```

Keep external-to-internal indexing explicit.

Use zero-based Python indices internally.

Preserve original bus IDs in outputs and diagnostics.

Every parser must document:

```text
source matrix
column meaning
unit before conversion
unit after conversion
```

## Internal units and output units

Use per-unit internally.

Use MW, MVAr, and physical-style diagnostics in user-facing output where useful.

Diagnostics must print both:

```text
per-unit values for numerical verification
MW/MVAr values for readability
```

Do not mix per-unit and MW inside equations.

All objective and constraint equations should use per-unit values internally.

## Sign convention

Use injection convention consistently.

Definitions:

```text
Positive Pg means active power injection into the AC grid.
Positive Qg means reactive power injection into the AC grid.

Positive load Pd/Qd means demand.
Loads enter nodal balance with a negative sign.

Positive P_conv_ac means active power injection from converter into AC grid.
Positive Q_conv_ac means reactive power injection from converter into AC grid.

Positive P_conv_dc means active power injection from converter into DC grid.

Converter coupling:
P_conv_ac + P_conv_dc + P_loss_conv = 0
```

This means if the converter transfers power from AC to DC:

```text
P_conv_ac < 0
P_conv_dc > 0
P_loss_conv > 0
```

If the converter transfers power from DC to AC:

```text
P_conv_ac > 0
P_conv_dc < 0
P_loss_conv > 0
```

Document this convention at the top of every model file that defines equations.

Never mix injection convention and load convention silently.

If adapting values from `runacdcpf.py`, remember that the reference PF code may represent converters as modified AC loads. Convert those values explicitly into the OPF injection convention.

## AC model requirements

Use nonlinear AC power-flow equations in polar form.

Variables:

```text
V_ac[i]
theta_ac[i]
Pg[g]
Qg[g]
```

Required constraints:

```text
AC active power balance at every AC bus
AC reactive power balance at every AC bus
AC voltage magnitude limits
Generator active power limits
Generator reactive power limits
Reference angle constraint
AC branch apparent power limits
```

The AC active-power balance should follow injection convention:

```text
generation injection
+ converter active injection
- active demand
- calculated network active injection
= 0
```

The AC reactive-power balance should follow injection convention:

```text
generator reactive injection
+ converter reactive injection
- reactive demand
- calculated network reactive injection
= 0
```

AC branch limits should use apparent power in both directions:

```text
P_ij^2 + Q_ij^2 <= rateA^2
P_ji^2 + Q_ji^2 <= rateA^2
```

If `rateA <= 0`, treat the branch as unconstrained and report this in diagnostics.

AC branch active loss should be computed from both branch-end active powers:

```text
P_loss_ac_branch = P_ij + P_ji
```

Losses must be non-negative within numerical tolerance.

## DC model requirements

Variables:

```text
V_dc[k]
P_dc_inj[k] where needed
I_dc_branch[l] where useful
P_dc_branch_from[l]
P_dc_branch_to[l]
```

Use nonlinear DC equations based on voltage and resistance:

```text
I_ij = (V_i - V_j) / R_ij
P_ij = pol * V_i * I_ij
P_ji = -pol * V_j * I_ij
P_loss_ij = pol * R_ij * I_ij^2
```

Required constraints:

```text
DC active power balance at every DC bus
DC voltage limits
DC branch rate limits
```

Use `rateA` as the first-version DC branch power limit if available.

Always print DC branch currents in diagnostics, even if current limits are not enforced in v1.

DC active-power balance should follow injection convention:

```text
converter DC active injection
+ DC generation injection if present
- DC load
- calculated DC network active injection
= 0
```

DC branch active loss should be non-negative within numerical tolerance.

## Converter model requirements

Variables:

```text
P_conv_ac[c]
Q_conv_ac[c]
P_conv_dc[c]
I_conv[c]
P_loss_conv[c]
```

Required constraints:

```text
converter AC/DC active power coupling
converter active loss model
converter current limit
converter AC voltage/converter voltage limits where data is available
converter active/reactive operating limits where data is available
```

Use this coupling convention:

```text
P_conv_ac[c] + P_conv_dc[c] + P_loss_conv[c] == 0
```

Converter active losses must be non-negative.

Use the quadratic converter loss model:

```text
P_loss_conv = LossA + LossB * I_conv + LossC * I_conv^2
```

If both rectifier and inverter quadratic coefficients are available:

1. For v1, select the coefficient from the initial converter operating direction.
2. Document which coefficient was selected.
3. Print the selected mode in diagnostics.
4. Do not introduce binary variables for converter direction in the first IPOPT version.

Detailed converter current model:

```text
Use transformer/filter/reactor data when available in the case data.
```

Fallback current model:

```text
I_conv^2 = (P_conv_ac^2 + Q_conv_ac^2) / V_ac^2
```

When using the fallback model, print a warning in diagnostics.

Do not silently switch converter models.

## DC slack and droop handling

Version 1 must first support slack-controlled DC cases.

Slack-controlled DC case:

```text
Fix the DC slack voltage according to the case data.
Allow the slack converter active power to balance DC network losses.
Enforce converter coupling and loss constraints.
```

Point-to-point HVDC case:

```text
Support one DC branch and two converters.
Use one DC-side slack/voltage-controlled converter and one power-controlled converter if defined in the case.
```

Droop-controlled DC case:

```text
Implement only after the slack-controlled and point-to-point cases work.
Use the droop-related fields from the case data.
Add a diagnostic that prints the droop equation residual for every droop converter.
Do not treat droop converters as fixed-power converters.
```

Do not force the droop case to behave like the slack case.

## Objective function

The only active objective in v1 is active power loss minimisation.

```text
minimise:
P_loss_AC + P_loss_DC + P_loss_converter
```

Do not include these objective terms in v1:

```text
generation cost
emissions
voltage deviation
renewable curtailment
load shedding
market dispatch
battery degradation
multi-period energy terms
```

Optional future objective terms may be mentioned in comments only if they are clearly disabled and not part of the v1 model.

## Diagnostics

Every solve must print a compact terminal report.

Required output:

```text
Solver status
Termination condition
IPOPT availability
Objective value in pu
Objective value in MW

AC branch losses by branch
Total AC branch loss

DC branch losses by branch
Total DC branch loss

Converter losses by converter
Total converter loss

Total active loss

Maximum AC active-power residual
Maximum AC reactive-power residual
Maximum DC active-power residual
Maximum converter coupling residual

Maximum AC voltage violation
Maximum DC voltage violation
Maximum generator P violation
Maximum generator Q violation
Maximum AC branch loading violation
Maximum DC branch loading violation
Maximum converter current violation
Maximum converter voltage violation

Number of AC buses
Number of DC buses
Number of converters
Number of AC branches
Number of DC branches
IPOPT iterations if available
Solve time if available
```

Use compact terminal formatting.

Keep visualisation and export code separate from numerical model code. Markdown, CSV, XLSX, and HTML outputs are useful engineering reports, but they are not proof that the OPF formulation is correct.

Do not add new output formats, dashboard frameworks, or plotting dependencies unless they are explicitly requested and justified.

## Validation

Implement validation workflows that can run:

```text
1. The ACDCPF PF backend baseline.
2. The Tool1 (acdcopf) Pyomo/IPOPT pipeline.
3. Optional PyFlow reference runs when the selected case and controls are genuinely comparable.
```

The validation report must compare:

```text
AC bus voltage magnitudes
AC bus voltage angles
DC bus voltages
Generator P/Q
Converter AC-side P/Q
Converter DC-side P
AC branch losses
DC branch losses
Converter losses
Total active loss
```

For PF mode, the new pipeline should reproduce the reference PF results within tolerance where the formulations are equivalent.

PyFlow comparisons are reference checks only. They must not be used as the Tool1 (acdcopf) solver path, and they must be labelled `not comparable` when PyFlow cannot mirror the selected topology, controls, or time profile.

For OPF mode, the result does not need to match PF setpoints, but it must:

```text
satisfy all constraints
have small residuals
report total active loss
show whether losses are lower than or equal to the PF baseline
```

Do not validate OPF by checking only objective value.

Validate physical residuals and limit violations.

## Numerical tolerances

Default diagnostic tolerances:

```text
AC P residual <= 1e-6 pu
AC Q residual <= 1e-6 pu
DC P residual <= 1e-6 pu
Converter coupling residual <= 1e-6 pu
Limit violation <= 1e-6 pu
```

If IPOPT struggles during early development, temporary debug tolerance may be relaxed to:

```text
1e-5 pu
```

Any tolerance relaxation must be printed in diagnostics and not hidden.

Do not permanently relax tolerances just to make tests pass.

## Solver settings

Use IPOPT through Pyomo.

The solver interface must expose configurable IPOPT options, including:

```text
tol
constr_viol_tol
max_iter
print_level
```

Default values:

```text
tol = 1e-8
constr_viol_tol = 1e-8
max_iter = 1000
print_level = 5
```

Do not claim successful optimisation unless:

```text
Pyomo reports valid solver completion
IPOPT reports a successful termination condition
diagnostic residuals are within tolerance
limit violations are within tolerance
losses are physically plausible
```

## Tests

Use `pytest`.

Every modelling stage must include tests.

Required tests:

```text
data loading tests
AC model construction tests
DC model construction tests
converter model construction tests
hybrid model construction tests
solver availability tests
small-case solve tests
diagnostic residual tests
validation comparison tests
```

A task is not complete unless relevant tests run.

If tests fail, report the failure and fix the cause instead of hiding or deleting the test.

Do not hard-code expected OPF results unless they are derived from a documented reference run.

Prefer tests that verify:

```text
model builds
required variables exist
required constraints exist
objective contains AC, DC, and converter loss terms
solver is called
solver returns feasible status
residuals are small
limits are satisfied
losses are non-negative
```

## Mandatory small regression cases

Keep these as mandatory regression tests:

```text
case5_stagg.py + case5_stagg_HVDCptp.py
case5_stagg.py + case5_stagg_MTDCslack.py
case5_stagg.py + case5_stagg_MTDCdroop.py
```

The droop case may be added after the slack and point-to-point cases work.

Do not remove small-case tests after adding larger benchmarks.

Small cases are debugging anchors.

## Modified IEEE 33-bus hybrid AC/DC benchmark

After the small HYNET cases are working, add a modified IEEE 33-bus hybrid AC/DC distribution benchmark.

Use the standard Baran-Wu IEEE 33-bus radial distribution system as the source AC case.

There is no single universal hybrid AC/DC IEEE 33-bus benchmark. Therefore, this project must define and freeze its own documented version.

Required benchmark stages:

```text
IEEE33-AC-only
IEEE33-HVDC-link
IEEE33-Hybrid-radial-DC
IEEE33-Hybrid-MTDC
IEEE33-Hybrid-droop
```

Recommended first hybrid version:

```text
case33_hybrid_acdc_v1.py

AC buses:
1-18

DC buses:
19-33

Converter:
AC bus 18 connected to DC bus 19

Load conversion:
For buses converted to DC, use original active load Pd as DC active load.
Set reactive load Qd to zero for the DC subsystem.

Objective:
Active power loss minimisation.
```

Required documentation inside the case file:

```text
source AC case
AC bus set
DC bus set
converted branches
removed AC branches
added DC branches
converter locations
converter ratings
AC and DC voltage bases
load conversion rule
reactive-load handling
converter loss coefficients
branch-rating assumptions
```

Rules:

```text
1. First solve the original IEEE 33-bus system as AC-only OPF.
2. Then add a point-to-point HVDC link.
3. Then convert a downstream feeder section to DC.
4. Then add multiple converters.
5. Add droop only after the slack-controlled version solves correctly.
6. Every modified version must print full diagnostics and compare total active losses against the AC-only baseline.
```

## Larger validation cases

After the v1 solver works on the small HYNET cases and the modified IEEE 33-bus benchmark, validate the solver on progressively larger systems.

First larger PyACDCPF-style hybrid candidate:

```text
case24_ieee_rts1996_3zones + case24_ieee_rts1996_MTDC
```

Use this only if the corresponding case files are available in the repository or imported from the PyACDCPF ecosystem.

AC-only scalability cases:

```text
case30
case39
case57
case118
case300
```

Use these to validate the AC OPF block separately before blaming the hybrid converter model for every numerical disaster.

Synthetic hybrid scalability cases:

```text
case30 + 2-terminal HVDC
case30 + 3-terminal MTDC
case39 + 3-terminal MTDC
case57 + 3-terminal MTDC
case118 + 3-terminal or 4-terminal MTDC
```

Distribution-style hybrid benchmark candidates:

```text
modified IEEE 33-bus hybrid AC/DC
modified IEEE 69-bus hybrid AC/DC
```

Rules for larger cases:

```text
1. Do not test a large hybrid case before the same AC case works in AC-only OPF.
2. Do not add droop control until slack-controlled converters work.
3. Do not add synthetic DC grids without documenting converter placement.
4. Every larger case must report:
   - number of AC buses
   - number of DC buses
   - number of converters
   - number of AC branches
   - number of DC branches
   - IPOPT iterations
   - solve time
   - total active loss
   - residuals
   - limit violations
5. A larger case is accepted only if all residuals and limit violations are within tolerance.
```

## Code quality

Write code for maintainability, not artificial cleverness.

Use:

```text
clear function names
project-specific variable names
small modules
direct equations
explicit indexing
meaningful errors
compact diagnostics
focused tests
comments near equations
clear unit conversion
clear sign convention
```

Avoid:

```text
generic placeholder names
large monolithic files
unnecessary class hierarchies
broad try/except blocks that hide errors
silent unit conversion
magic column numbers
unexplained solver assumptions
fake production-ready abstractions
unused helper functions
dead code
decorative logging
hard-coded results that make tests pass without solving the model
```

Comments should explain:

```text
modelling choices
equations
units
sign conventions
assumptions
```

Do not add comments such as:

```python
# Loop through all buses
# Calculate value
# Return result
```

Those comments add noise and make the code harder to review.

## AI-assisted code quality rule

Do not optimise code to evade AI-code detectors.

Instead, write code that a competent engineer would actually maintain:

```text
project-specific names
concise comments tied to equations
explicit assumptions
tests that reflect the physical model
small commits
no generic boilerplate
no unexplained overengineering
no suspiciously perfect unused abstractions
```

The goal is transparent, reviewable engineering code.

Avoid common low-quality generated-code patterns:

```text
generic names such as data, result, process, manager, handler when domain-specific names are possible
over-commenting obvious syntax
under-commenting equations and assumptions
wrapping every operation in classes for no reason
creating functions that only forward one call
silent exception handling
unused imports
unused parameters
dead helper functions
hard-coded numerical outputs
tests that only check that code runs
long files with unrelated responsibilities
formatting that hides equations
```

## Controlled scope expansion

Do not expand into the following areas unless explicitly requested and the modelling/validation plan is clear:

```text
stochastic OPF
renewable uncertainty
unit commitment
discrete tap optimisation
multi-period planning beyond the existing snapshot/profile workflow and native storage SOC coupling
generation cost objective
emission objective
voltage deviation objective
market dispatch
machine learning
metaheuristics
convex relaxation
distributed OPF
new GUI/dashboard frameworks beyond the existing local Streamlit dashboard
new plotting libraries beyond the existing report/HTML visualisation path
battery scheduling objectives beyond the existing native SOC-constrained storage dispatch
demand response
security-constrained OPF
contingency analysis
```

Already-approved implemented extensions must be maintained, not removed:

```text
snapshot time-series profiles
native signed storage with SOC constraints
DCDC voltage-ratio control
standalone transformer objects
split PF/OPF CSV and XLSX exports
interactive HTML grid visualisation
local Streamlit dashboard prototype
```

These extensions are still engineering features, not validation by themselves. Keep tests and diagnostics as the source of confidence.

## Required task completion report

At the end of every Codex task, report:

```text
Files changed
Equations implemented
Assumptions made
Tests added
Tests run
Diagnostics observed
Known limitations
Next recommended step
```

Do not mark a task complete if:

```text
the model does not build
the solver was not actually called
IPOPT is unavailable
tests were not run
diagnostics are missing
residuals are not checked
limits are not checked
```

## How to handle uncertainty

If a modelling detail is unclear:

```text
1. Do not invent silently.
2. State the uncertainty.
3. Implement the simplest documented version only if it is needed to proceed.
4. Add a clear TODO or limitation.
5. Add a diagnostic that exposes the assumption.
```

Examples:

```text
If converter loss direction is unclear, use the initial operating direction and print selected rectifier/inverter mode.
If DC branch current ratings are unavailable, enforce rateA as a power limit and print branch currents as diagnostics.
If a branch has rateA = 0, treat it as unconstrained and report it.
```

## First recommended Codex task

The first task should be data loading and indexing only.

Prompt to use:

```text
Read AGENTS.md and inspect the HYNET case files.

Task:
Create the first stage of the HYNET OPF package: the data loading and indexing layer only.

Files to inspect:
- case5_stagg.py
- case5_stagg_MTDCslack.py
- case5_stagg_MTDCdroop.py
- case5_stagg_HVDCptp.py
- runacdcpf.py

Create:
- hynet_opf/data_loader.py
- hynet_opf/indices.py
- tests/test_data_loader.py

Requirements:
1. Load AC and DC cases in PyPOWER/PyACDCPF style.
2. Preserve original matrices.
3. Create internal zero-based mappings for:
   - AC buses
   - DC buses
   - generators
   - AC branches
   - DC branches
   - converters
4. Expose base values:
   - baseMVA
   - baseMVAac
   - baseMVAdc
   - pol
5. Do not build the Pyomo model yet.
6. Add tests proving:
   - case5_stagg has 5 buses, 2 generators, 7 branches
   - MTDC slack has 3 DC buses, 3 converters, 3 DC branches
   - MTDC droop has 3 DC buses, 3 converters, 3 DC branches
   - HVDC point-to-point has 2 DC buses, 2 converters, 1 DC branch
7. Run the tests and report results.

Before coding:
- explain which matrix columns you will use
- explain the internal indexing strategy
```
