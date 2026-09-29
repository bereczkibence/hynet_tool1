# Equipment Limits: Entry and Coverage

## Two Different Settings

Equipment limits describe admissible operation: generator P/Q bounds, bus
voltage bounds, branch ratings, converter currents, storage S and SOC.
The control margin limits how far selected controls may move from their
starting dispatch. A 10% control margin does not supply a missing equipment
rating, and it does not replace voltage/current/SOC limits.

Unknown equipment values remain unset. The benchmark cases have not been
assigned new ratings to obtain a passing validation status. Engineering data
will be supplied separately by the user. Numerical fallbacks retained for
compatibility are explicitly identified, not represented as nameplate data.

## Entering Limits

In the local dashboard, use **Operational Inputs**, select an element table,
edit the fields, then open **Equipment limit inventory** and click
**Check pending limits**. This inspects the current edits without running a
solver. The inventory becomes stale when inputs change and must be checked
again. Run PF/OPF to evaluate feasibility against the supplied limits.

Previously saved edits are merged with current table columns so newly added
limit fields are not hidden by older browser settings. IDs, endpoints,
impedances and other topology fields remain read-only.

The existing Python entry point also accepts optional limit columns that do
not yet exist in a native table:

```python
from acdcpf_opf.benchmarks.stagg5.service import apply_table_overrides
from acdcpf_opf.data.equipment_limits import equipment_limit_inventory

# approved_limits is a mapping of table names to records with element_id and
# actual engineering limits. No limits are inferred from this example.
apply_table_overrides(net, approved_limits)
inventory = equipment_limit_inventory(net)
```

Overrides are validated atomically. Negative ratings, invalid numeric inputs,
reversed bounds and SOC bounds outside 0..100% are rejected. Blank optional
limits stay unknown. Legacy zero branch/current ratings remain unrated for
compatibility. Clearing AC generator limits restores the native unbounded
convention, not a zero-power limit.

Native ACDCPF creator signatures have not changed in this work. New VSC
fields are optional Tool1 (acdcopf) extensions to `net.vsc`; set them after creation
through the override entry point or explicitly on the network table. PF uses
the original device equations; it is not an equipment-limit optimizer.

## Supported Fields

| Element | Editable limit fields | OPF and independent check |
| --- | --- | --- |
| AC bus | `v_min_pu`, `v_max_pu` | Bus magnitude bounds; reconstructed-state voltage check |
| DC bus | `v_min`, `v_max` | DC magnitude bounds; voltage check |
| AC generator | `p_min_mw`, `p_max_mw`, `q_min_mvar`, `q_max_mvar` | P/Q bounds including balancing generators; dispatch check |
| DC generator | `p_min_mw`, `p_max_mw` | P bounds plus selected downward curtailment policy; dispatch/curtailment checks |
| AC line | `rate_mva`, `max_i_ka` | S and current limits at both ends; independent terminal checks |
| DC line | `rate_mw`, `max_i_ka` | Both terminal powers and per-pole current |
| Transformer | `sn_mva`, `max_i_ka` | Both terminal S/current limits |
| VSC | `s_mva`, `max_i_ac_ka`, `max_i_dc_ka` | Bridge S, bridge AC current, per-pole DC current |
| VSC | `v_filter_min_pu`, `v_filter_max_pu`, `v_converter_min_pu`, `v_converter_max_pu` | Filter and bridge voltage bounds, checked using independently reconstructed phasors |
| DCDC | `rate_mw`, `d_ratio_min`, `d_ratio_max` | Both terminal powers and physical voltage-ratio bounds |
| Storage | `sn_mva`, P/Q bounds, `energy_mwh`, `soc_min_percent`, `soc_max_percent` | Apparent power, dispatch, energy and SOC checks |

Important interpretations:

- VSC `s_mva` is the **bridge** rating, not a separately specified grid-terminal
  rating. `max_i_ac_ka` uses the same bridge-current/loss voltage base as the
  existing formulation (`loss_base_kv`, or the AC-bus base when absent).
- `max_i_dc_ka` is current per pole. With `N` DC poles, the converter's aggregate
  per-unit current bound is `Imax_kA * N * Vbase_kV / Sbase_MVA`.
- AC current conversion is `Imax_kA * sqrt(3) * Vbase_kV / Sbase_MVA`.
- Explicit filter/bridge voltage bounds replace inherited AC-bus bounds for
  the specified side. Missing sides retain the previous fallback; conflicting
  explicit/inherited bounds are rejected during conversion.
- A transformer `sn_mva` also defines its impedance base. Changing it while
  leaving nameplate per-unit impedance unchanged changes the physical model;
  it is not just editing a thermal limit.
- Positive DC generation curtailment remains capped by the available profile.
  A nameplate `p_max_mw` does not create additional nighttime PV availability.
- DCDC editor ratios are physical `V_to/V_from`. Legacy per-unit ratio bounds
  are shown converted to physical ratios; edits are canonicalized so old
  columns cannot override the new value. Branch-rating aliases are handled
  similarly, including clearing limits.

No optimization objective, solver, or PF backend was changed. The added
constraints use the existing Pyomo variable bounds and existing branch/device
equations; independent checks read original source fields, not Pyomo bounds.

## Inventory and Reports

`equipment_limit_inventory(net)` returns one row per element and limit field:
element ID/name, parameter, value, unit, status, source field, OPF behavior and
corresponding physics check. Status is `specified`, `missing` or
`out_of_service`. The source field identifies where a number is stored;
it does **not** establish its provenance as a manufacturer rating.

The inventory is included in OPF CSV/XLSX as `equipment_limits`, including
profile timestamps, and in the dashboard results. Markdown summarizes missing
fields. Physics findings remain in `physics_checks`. A specified input is not
automatically feasible, and a physics `passed` status does not prove input
provenance or global optimality.

Current native case inventory, without user edits:

| Case | Specified fields | Missing fields |
| --- | ---: | ---: |
| Original Stagg5 | 43 | 22 |
| Hybrid Stagg5 | 59 | 26 |
| Two-area Stagg5 | 95 | 48 |

Missing fields are counted separately: one missing minimum and one missing
maximum count as two fields. This count differs from `not_checked` equation
rows, which can combine both bounds or check two terminals.

- All three cases lack explicit AC generator P minima/maxima and explicit
  VSC AC/DC current and internal filter/bridge voltage limits.
- The hybrid case additionally lacks DC generator P limits and both DCDC
  power ratings.
- The two-area case additionally lacks separate transformer current ratings;
  its transformer MVA limits and coupling DCDC power rating are present.
- Existing Stagg5 line ratings are assigned in
  `benchmarks/stagg5/case_variants.py::_apply_stagg5_reference_line_ratings`:
  AC S and DC P ratings, with currents derived at nominal voltage. These are
  benchmark conventions, not independent cable ampacity measurements.
- Synthetic transformer and coupling DCDC ratings are defined in
  `case_variants.py`; original generator Q bounds, bus voltage bounds and VSC
  S ratings originate in the native ACDCPF case builders.

## Files and Verification

- `data/equipment_limits.py`: source coverage inventory and validation.
- `data/acdcpf_to_pyomo.py`: explicit VSC current/internal-voltage limits and
  inventory metadata.
- `opf/physics_validation.py`: independent filter/bridge voltage checks.
- `benchmarks/stagg5/service.py`: editable fields, alias normalization, atomic
  input updates and solver-free preview.
- `benchmarks/stagg5/benchmark_pf_opf_comparison.py`: inventory exports and
  Markdown coverage summary.
- `dashboard/web.py`, `dashboard/static/`: pending-limit inspection and saved
  input compatibility.
- `tests/test_equipment_limits.py`: limit entry, malformed/absent inputs,
  native-to-Pyomo units, legacy aliases, real-IPOPT current limits, tampered
  results, export coverage and frontend interaction logic.

Next step: supply justified missing values. Keep each source or synthetic
design assumption documented; do not replace unknowns with arbitrary large
limits to clear the coverage report.
