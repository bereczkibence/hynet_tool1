# September 2026 Correctness Fixes

## Scope

This change fixes reproducible numerical and integration defects from the
September audit. It does not replace ACDCPF, change the loss objective to an
economic objective, introduce PyFlow into the solver path, or refactor the
dashboard/benchmark architecture. Update the ACDCPF and OPF repositories
together: the native adapter now uses `acdcpf.validation.validate_network`.

## ACDCPF Changes

- `powerflow/runpf.py`: successful termination requires converged AC and DC
  subsystems, finite working values, and the existing converter power-change
  tolerance. A failed inner AC solve is no longer a successful PF.
- `validation.py`: checks unique integer IDs, bus references, power inputs,
  bound ordering, converter modes and unsupported branch conductances.
- `powerflow/indexing.py`: solves a fresh, active, densely indexed copy and
  restores external IDs in result tables. Source tables are not renumbered.
  Private numerical arrays are dense; `_pf_index_maps` explains their IDs.
  Real generator results are mapped by island, excluding VSC dummy generators.
- An active device connected to an out-of-service bus is an input error.
  Disable its incident devices explicitly. A source-free AC island is rejected
  instead of being supplied by an invented slack generator.
- `create_ac_gen` accepts keyword-only `p_min_mw` and `p_max_mw`. PF carries
  these values into PYPOWER; PF itself is not an equipment-constrained OPF.

### Shunts and Converter Filter

Bus admittances are per-unit on the system base. At the PYPOWER boundary:

```text
GS_MW   = gs_pu * S_base_MVA
BS_MVAr = bs_pu * S_base_MVA
```

The IEEE RTS case's original MATPOWER susceptances are converted from MVAr
to pu at case construction. Its reference data and test tolerances are not
rewritten to accommodate the fix.

For a capacitive VSC filter with `Bf > 0`, the station currents satisfy:

```text
I_filter  = j * Bf * V_filter
I_reactor = I_transformer - I_filter
Q_filter  = -Bf * abs(V_filter)^2
S_reactor = S_transformer_at_filter - j * Q_filter
```

The final minus sign was wrong in both native PF converter calculations.
Correcting it reconciles PF replay with OPF. This is a KCL/sign correction,
not a numerical tolerance adjustment. The convention follows the filter and
terminal-flow equations in the
[MATACDC manual](https://www.esat.kuleuven.be/electa/teaching/matacdc/MatACDCManual).
The native regression additionally checks the complex-current balance directly.

## OPF Constraints

Explicit source AC-generator P limits now reach the `Pg` variable bounds.
Legacy networks without finite P limits retain their previous broad numerical
bounds; those are not a verified equipment rating. Real engineering studies
must provide generator limits.

For a DCDC rating `R`, both terminal powers satisfy:

```text
-R <= P_from <= R
-R <= P_to   <= R
```

AC line current limits are separate from MVA limits. At each terminal:

```text
P_pu^2 + Q_pu^2 <= Imax_pu^2 * V_pu^2
Imax_pu = Imax_kA * sqrt(3) * Vbase_kV / Sbase_MVA
```

DC line current and power ratings are also independent. A MW rating is no
longer treated as a current rating at nominal voltage, or allowed to overwrite
an explicitly supplied current rating.

Fixed `pdc_q` and `pdc_vac` setpoints use `Pcv_dc = -Pdc_set/Sbase` and are
mapped back to the same native PF control for replay. At a shared generator/VSC
voltage-controlled bus, the adapter mirrors ACDCPF's generator-priority policy.
AC-only networks no longer require artificial DC buses.

## Storage and Time

The existing signed dispatch, energy transition, P/Q capability, SOC bounds
and terminal-SOC condition are retained. The missing physical condition is:

```text
P_charge >= 0; P_discharge >= 0
P_storage = P_discharge - P_charge
P_charge * P_discharge = 0
```

It is enforced as continuous complementarity, without binary decisions.
IPOPT first solves two relaxed initialization NLPs, then the exact model.
The temporary products are capped relative to each device's power bounds;
fixed or one-directional storage is not relaxed. Initialization failures do
not certify infeasibility of the exact problem. Only the last exact solve and
post-solve checks determine acceptance. No relaxed dispatch is published as
a successful final result.

Complementarity makes the problem nonconvex and numerically sensitive. A
successful result is a feasible local solution, not a global optimum or a
guarantee of the best charging/discharging pattern. No energy-throughput penalty
or battery degradation cost has been silently added to the objective.

For unequal study intervals, the horizon objective is now loss energy:

```text
min sum_t(P_network_loss_MW[t] * duration_hours[t])  [MWh]
```

Per-period outputs still report MW. Extracted linked results also contain
`horizon_active_loss_energy_mwh`. Timestamps must be parseable, consistently
timezone-aware or naive, and strictly increasing. The last interval repeats
the preceding interval. Without timestamps, snapshots are one hour each;
`time_index` is an ID, not a duration. Single-snapshot conversion also exposes
`snapshot_duration_hours` explicitly.

## Acceptance and Reporting

`opf/validation.py` checks finite model values, all active constraint residuals,
variable bounds, and simultaneous storage throughput. The default absolute
model residual tolerance is `1e-6`; raw residuals retain their actual model units
(pu, squared pu, or MWh). Storage overlap is checked separately in MW against
`1e-5 MW`. This is not presented as an independent equipment-data audit.

An `optimal` termination alone is insufficient. Solver status and these checks
must pass. When native PF replay is enabled, AC/DC voltage magnitudes, VSC
terminal powers, and total loss must also match within `1e-5 pu` and `1e-3 MW`.
Replay diagnostics retain the observed differences. Failed iterates remain
available internally for debugging but are excluded from successful export
tables. The browser dashboard distinguishes a finished Python job from failed
PF/OPF timestamps.

## Verification

New tests: `tests/test_audit_regressions.py` in OPF and
`tests/test_pf_regressions.py` in ACDCPF. They cover the original counterexamples,
native PF replay, unsupported inputs, AC-only operation, current limits, fixed
DC-power controls, incorrect durations, and a real 24-hour linked-storage solve.
The day test independently reconstructs energy from signed P, verifies SOC
bounds and the 50% final SOC, and requires both charging and discharging.

After the filter correction, the frozen original Stagg5 baseline is
8.63675283443 MW and the benchmark OPF result is approximately 8.62474722201 MW.
Old reports used the former filter sign and should not be reused as the new
baseline. Tests use explicit numerical tolerances, not exact float equality.

Local verification on 27 September 2026 used Python 3.13.1, Pyomo 6.10.0 and
IPOPT 3.13.2:

- OPF suite: 146 passed, 18 skipped because optional PyFlow is unavailable.
- ACDCPF suite: 76 passed, including the existing MATACDC reference tests.
- End-to-end hybrid 24-hour service run: all PF and OPF timestamps succeeded;
  split CSV/XLSX, Markdown and HTML outputs were generated. The exported
  storage sheet has 24 rows and ends at 25 MWh / 50% SOC.
- This is a numerical/service verification, not a live browser UI test or
  a clean-install/platform-compatibility test.

## Remaining Work

- Nonzero AC line/transformer shunt conductance is rejected explicitly, not
  silently ignored. Adding the complete model remains a separate extension.
- DC-only OPF, general island/reference policies and large-grid scalability
  are not certified by these tests.
- PF profile baselines remain independent snapshots; a repeated battery PF
  setpoint is not automatically an SOC-feasible full-day baseline schedule.
- Internal battery efficiency losses affect energy balance, but are not a new
  term in the network-loss objective. Bus shunt consumption is not reclassified
  as branch loss.
- Public study API extraction, benchmark/service separation, clean-install CI,
  pinned cross-repository releases and external-tool validation remain separate
  engineering tasks. Passing local tests does not establish universal model
  compatibility or production readiness.
