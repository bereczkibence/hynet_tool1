# Source-Network Physics Validation

## Purpose and Scope

Native ACDCPF OPF results are checked against the original network and the
requested control policy before successful publication. This check does not
import Pyomo, the OPF formulation, or the OPF data-conversion helpers. Changing
or accidentally omitting a Pyomo constraint cannot remove the source check.
The existing Pyomo residual validator and optional ACDCPF replay remain active.

This is an independent implementation of checks for the same mathematical
network assumptions, not external certification of the input data or a proof
of global optimality. Baseline PF equipment feasibility and an SOC-feasible PF
daily operating schedule are not certified by this OPF report. The optional
PyFlow path does not have this native-source validator.

## API and Files

- `opf/physics_validation.py`: `validate_network_physics(...)` and
  `validate_time_series_physics(...)`; source-network flows, balances, ratings
  and selected operational controls.
- `opf/physics_storage.py`: signed-dispatch energy reconstruction, SOC, P/Q,
  apparent-power limits and simultaneous-mode checks.
- `opf/physics_report.py`: `PhysicsReport`, row-level `PhysicsCheck`, and
  configurable `PhysicsTolerances`.
- `opf/pyomo_acdc_loss_min.py`: acceptance integration through
  `PyomoACDCOPFResult.physics_validation` and
  `PyomoACDCOPFConfig.physics_tolerances`.

The validator consumes the original native network, extracted numeric result
dictionaries, conversion options as the requested control policy, and the PF
baseline only for fixed-generator-Q and VSC margin anchors. It never reads
variable bounds, constraint bodies, or converted equipment limits from Pyomo.

## Equations and Units

Branch powers point into the element at each terminal. Generator and storage
powers inject into buses; positive load powers consume. The extracted VSC AC
and canonical DC powers both point into the converter station. These signs
differ from native `res_vsc.p_dc_mw`, which is injection into the DC bus.

AC line and transformer currents are reconstructed from complex voltage
phasors and the tapped pi-equivalent:

```text
I_series = (V_from / tap_complex - V_to) / Z
I_from   = (I_series + Y_shunt * V_from / tap_complex / 2) / conj(tap_complex)
I_to     = -I_series + Y_shunt * V_to / 2
S_end    = V_end * conj(I_end) * S_base                 [MW + j MVAr]
I_end_kA = abs(I_end_pu) * S_base / (sqrt(3) * Vbase_kV)
```

Line impedance is converted from ohm/km using its length and bus base.
Transformer impedance uses its own nameplate MVA and voltage bases. Both
terminal apparent-power ratings and explicitly supplied current ratings are
checked independently. See the [MATPOWER branch-admittance reference](https://matpower.org/docs/ref/matpower7.1/lib/makeYbus.html).

DC quantities are reconstructed in kV, kA and MW:

```text
I = (V_from - V_to) / R
P_from = poles * V_from * I
P_to   = -poles * V_to * I
```

For DCDC, use the physical voltage ratio D, output-side resistance R, and
input-side conductance G:

```text
I = (D * V_from - V_to) / R
P_from = poles * V_from * (D * I + G * V_from)
P_to   = -poles * V_to * I
```

For VSCs, the filter and bridge states are reconstructed from the AC terminal
voltage and power using the original transformer/reactor impedances and filter
susceptance. The filter-current relation is `I_reactor = I_transformer - j*Bf*Vf`.
Electronic losses are recomputed as `a + b*I_kA + c*I_kA^2`, using the configured
fixed or smooth-direction coefficient policy. Station balance includes
electronic, transformer and reactor losses. These equations follow the
[MATACDC station model](https://www.esat.kuleuven.be/electa/teaching/matacdc/MatACDCManual)
with the explicitly documented project sign and smoothing conventions.
The VSC DC-current check reports current per pole, `Pdc / (poles * Vdc_kV)`;
the extracted aggregate per-unit current is converted accordingly.

Nodal AC P/Q and DC P balances are recomputed using these flows, original
loads, solved generation/storage and VSC terminals. Global active balance is:

```text
sum(generation + signed_storage - loads) - bus_shunt_consumption
    = AC_line_loss + transformer_loss + DC_line_loss + DCDC_loss + VSC_station_loss
```

The reported objective must agree with this independently reconstructed
network loss. Internal battery efficiency losses are not silently added to
the network-loss objective; they enter the energy equation.

Storage energy is reconstructed from signed power, not from exported SOC or
the solver's charge/discharge split:

```text
E_next = E_previous - max(P, 0) * dt / eta_discharge
                    + max(-P, 0) * dt * eta_charge
SOC_percent = 100 * E_next / capacity_MWh
```

Charge/discharge split consistency, nonnegativity, exclusive modes, apparent
power, SOC limits and fixed dispatch are checked separately. For linked runs,
the initial energy comes from the first source network. Every subsequent
energy is reconstructed cumulatively from signed P. The final energy must
return to the initial energy. Storage identity and energy parameters must be
stable across the horizon; the loss-energy objective must equal the weighted
sum of reconstructed losses.

Control checks include DC curtailment against available profiled generation,
VSC/storage movement margins, fixed generator/VSC/storage dispatch, voltage
setpoints, DC droop, and fixed/bounded DCDC ratios. AC slack sources retain the
existing active-island reference policy and are not ordinary dispatch controls.

## Acceptance and Outputs

Each row contains `check`, `element`, `status`, `value`, `lower`, `upper`,
`violation`, `tolerance`, `unit`, and an explanatory `note`.

- `passed`: a supported check is within its declared tolerance.
- `failed`: an equation/limit is violated or a required numeric result is
  absent/nonfinite. OPF success is blocked. A failure in a linked study blocks
  successful publication of all its timestamps.
- `not_checked`: the source does not supply the required finite equipment
  limit or the check is outside the documented coverage.

Report status is `failed`, `partial` (no detected violations but coverage gaps),
or `passed`. A successful solver run with partial rating coverage is not called
fully equipment-validated. Checks survive even when failed dispatch tables are
withheld, so a failure can be investigated.

The split OPF CSV/XLSX exports have a `physics_checks` section/sheet and summary
fields in `run_status`. Markdown contains coverage counts and failures. The
browser dashboard exposes the status and `physics_checks` result table; like
its other representative tables, profiled runs display the first timestamp.
The CSV/XLSX exports contain all timestamps. The standalone grid-view HTML is
unchanged; it is not the detailed verification report.

Default absolute tolerances:

| Quantity | Tolerance |
| --- | --- |
| P, apparent power | 0.0001 MW / MVA |
| Q | 0.0001 MVAr |
| Voltage / normalized ratio | 0.000001 pu |
| Current | 0.000001 kA |
| Storage energy / horizon loss energy | 0.00001 MWh |
| SOC | 0.00001 percentage points |
| Simultaneous charge/discharge | 0.00001 MW |

## Coverage Limits

- Unknown/zero/unbounded equipment ratings remain unverified; no numerical
  fallback bound is presented as a physical nameplate limit.
- VSC `s_mva` is interpreted at the converter bridge. A separate station-side
  apparent-power rating is not inferred. Optional explicit filter/bridge
  voltage limits and current limits can be entered through the operational
  editor; absent source limits remain not checked. The OPF retains inherited
  internal-voltage bounds when explicit values are missing. See
  [Equipment Limits](EQUIPMENT_LIMITS.md) for fields and current inventory.
- DC lines with unequal voltage bases and zero-resistance DCDC elements are
  rejected by this check instead of silently approximated.
- Only the existing constant-power load and fixed-topology/tap assumptions
  are supported. This feature adds checks, not new device equations or controls.
- Checking source equations does not prove that the source model matches real
  equipment. Independent reference cases and external measurements remain useful.

## Tests

`tests/test_physics_validation.py` covers real Stagg5/hybrid/two-area solutions,
source limits tightened without changing Pyomo, a deliberately omitted model
limit, corrupted/missing outputs, unknown ratings, curtailment, exclusive battery
modes, two batteries with unequal intervals, final SOC and report exports.
The existing 24-hour real-IPOPT regression additionally exercises this new
acceptance path for every timestamp.

Verification on 2026-09-28 using the project Python environment and IPOPT:

The following numbers record the initial physics-check implementation, before
the equipment-limit editor extension split filter and bridge voltage coverage
into separate checks. Current limit-entry behavior is documented above.

- Run `python -m pytest -q` from each repository root. Tool1 (acdcopf) explicitly
  collects `tests/`; ignored historical copies under `backups/` are not part of
  the active suite. Their files remain unchanged.
- Tool1 (acdcopf): 175 tests passed, including 29 source-physics tests; 18 optional
  PyFlow tests skipped because that reference dependency was unavailable.
- ACDCPF: 76 tests passed.
- End-to-end hybrid 24-hour service run: all 24 OPF timestamps succeeded;
  exported checks contained 4,466 passes, no failures and 384 unchecked limits.
  The largest active-power check violation was approximately 3.21e-7 MW,
  below the 1e-4 MW tolerance. Final storage SOC returned to 50%.
- Each timestamp had 16 unchecked source limits: three generator active-power
  bounds, six VSC AC/DC current ratings, three VSC internal-voltage bounds and
  four DCDC terminal-power ratings. These are coverage gaps, not evidence of
  violations, and the report remains `partial`.

The browser payload and exported files were tested; no visual browser QA was
performed for this change. The next validation step is to supply justified
equipment limits and define an SOC-feasible baseline operating schedule before
claiming fully equipment-validated operation or daily loss savings.
