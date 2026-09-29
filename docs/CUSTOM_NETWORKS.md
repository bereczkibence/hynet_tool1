# Custom network import

**Working development version.** Tool1 is a functional working version under active development. Features, interfaces and documentation may change as refinement and validation continue. Review solver diagnostics and validate results for the intended application.

The dashboard and Python API accept a data-only PyPOWER AC `.py` case and an
optional MatACDC DC `.py` companion. Original case files are never modified.
Import validates structure and converts equipment; it does not certify that
the network is feasible, that PF converges, or that an optimum exists.

## Dashboard

Restart `Tool1_Dashboard.bat` after installing this update and reload the page.
In **Custom network**, choose the AC file and, for a hybrid study, its DC file.
Select the hybrid format, click **Import network**, and review **Import details**.
The imported case is selected automatically. Use the existing network tables,
profile selection, limit inventory, run controls, and exports as usual.

Tool #7 case files are private inputs and are not bundled in release artifacts.
When you have those files, select `case33h_ieee_ac.py` and
`case33h_ieee_dc.py`, then choose **Tool #7**. Select the loss coefficient units
explicitly: the supplied comments say per-unit, but the referenced importer is
not shipped in this pinned backend, so historical numerical equivalence has
not been established. The application makes neither interpretation automatic.
`case33_ieee_AC.py` can instead be imported alone for the original AC baseline.

AC-only imports default to PF-only. Enable suitable generator controls and
clear **PF only, skip Tool1 (acdcopf)** to run OPF. Hybrid imports default to eligible
VSC controls. DC-voltage slack active power remains a balancing result.
Stagg-specific perturbations and unrelated reference comparisons are disabled.
Imports last for the dashboard process; import the files again after restart.
Each calculation starts from an independent copy, then applies your operational
edits and profile values. Profile `element_id` is the native table ID displayed
in the editor; `source_bus_id` identifies the corresponding original bus.

## Python

```python
from acdcopf import ImportOptions, load_custom_network
from acdcpf_opf.benchmarks.stagg5.case_variants import custom_grid_case
from acdcpf_opf.benchmarks.stagg5.service import BenchmarkRequest, run_benchmark_request

imported = load_custom_network(
    "inputs/case33h_ieee_ac.py",
    "inputs/case33h_ieee_dc.py",
    options=ImportOptions(format="tool7", loss_units="per_unit"),
)
print(imported.report)  # Review units, converter ratings, source IDs and warnings.

case = custom_grid_case(imported)
result = run_benchmark_request(BenchmarkRequest(
    grid_case=case, include_pyflow_reference=False, skip_opf=True,
))
print(result.markdown)
```

The example's `per_unit` choice follows the file comments; select `physical`
only when those coefficients are confirmed to be MW/kV/ohm. Set
`skip_opf=False` to run the existing OPF pipeline. For standalone PF, pass
`imported.fresh_network()` to `acdcpf.run_pf` or `ACDCPFNetworkAdapter.solve`.

`convert_custom_network(ac_dictionary, dc_dictionary=None, *, options=...)`
accepts dictionaries directly. Both entrypoints return `ImportedNetwork` with
`network`, `ac_case`, `dc_case`, `id_maps`, `report`, and `fresh_network()`.
`ImportOptions.ac_function` and `.dc_function` resolve files with multiple
case functions when using the Python API.

## Matrix mapping and units

| Input | Native interpretation |
|---|---|
| AC `bus` (13 columns) | IDs, PQ/PV/slack/isolated type, demand MW/MVAr, shunts divided by baseMVA, voltage bounds and kV base |
| AC `gen` (10 or 21 columns) | Injection MW/MVAr, voltage targets, P/Q limits, status; nonzero capability/ramp extensions rejected |
| AC `branch` (11 or 13 columns) | Series R/X and charging B on baseMVA, long-term MVA rating, tap, phase shift, status |
| DC `busdc` (9 columns) | Original DC/AC IDs, grid ID, signed demand MW, voltage initial value/base/bounds |
| DC `branchdc` (9 or 10 columns) | Resistance on baseMVAdc, long-term MW rating, status; optional ratio must equal 1 |
| DC `convdc` (20 columns) | DC/AC control modes, AC injection setpoints, station impedances, current limit, bridge voltage limits and losses |

Positive `Pdc` becomes a load; negative `Pdc` becomes positive DC generation.
Converter `P_g`/`Q_g` injection signs are negated for native consumption signs.
Bus IDs map to zero-based internal indices and remain available in source
columns and export metadata. Branches retain their original row mapping even
when converted to separate transformer objects. Source matrices retain all
columns; rateB/rateC and generation costs are not optimization inputs.

AC series impedance in ohms is `r_pu * baseKV² / baseMVA`. A synthetic length
of 1 km carries the lumped impedance, not a claim about physical cable length.
Transformer impedances are rebased to the transformer's rateA. Transformers
without a positive rateA are rejected because the backend requires a rating.

DC resistance in ohms is `r_pu * basekVdc² / baseMVAdc`. The source numerical
DC voltage base and pole count are retained, with the source/native equation
`P_MW = baseMVAdc * pol * Vi * (Vi - Vj) / r_pu`. This is also the explicit
interpretation used for Tool #7's stated pole-to-pole voltage base; the importer
does not divide that base or the resistance by two. Confirm this convention
with the producing tool when establishing cross-tool equivalence.

For converter `Ibase = baseMVA / (sqrt(3) * basekVac)` in kA:

- **Standard MatACDC:** `Imax_kA = Imax_pu * Ibase`.
- **Tool #7:** `Imax_kA = supplied Imax`, matching its documented sizing formula.
- Both modes use `S_mva = sqrt(3) * basekVac * Imax_kA` and retain the explicit
  current limit. Tool #7's supplied ratings are about 0.215, 2.190 and 0.707 MVA.
- **Physical losses:** retain `a` in MW, `b` in kV, and directional `c` in ohm.
- **Per-unit losses:** `a_MW = a_pu * baseMVA`,
  `b_kV = b_pu * baseMVA / Ibase`,
  `c_ohm = c_pu * baseMVA / Ibase²` for each direction.

The loss equation is `P_loss_MW = a_MW + b_kV * I_kA + c_ohm * I_kA²`.
Converter voltage limits are carried into the existing OPF bridge bounds;
ordinary PF does not enforce all equipment limits. No solver equations or
limits are relaxed to force a successful result.

Standard format reference: [MatACDC manual](https://www.esat.kuleuven.be/electa/teaching/matacdc/MatACDCManual).

## Supported case syntax and limitations

Files are parsed as data, never executed. Supported syntax includes docstrings,
NumPy imports, literal numeric module constants, dictionary assignments,
literal NumPy arrays, function aliases, and a dictionary return. An example
`if __name__ == "__main__"` reporting block is ignored. Computed arrays, loops,
arbitrary calls, decorators, and imports other than supported NumPy symbols
produce errors. Each file is limited to 5 MiB.

Supported converter controls are constant AC P with Q/Vac and constant Vdc with
Q/Vac. Droop extensions, DC/DC links, nonzero dynamic L/C data, finite branch
angle-difference constraints, and unknown extra electrical fields are rejected
explicitly. Every active connected AC or DC island must have exactly one
voltage reference. No ZIP import, topology editor, or complementary CSV schema
is introduced by this module.

## Verification

Run from the checkout:

```powershell
.\.venv\Scripts\python.exe -m pytest tests --confcutdir=tests -q
```

`--confcutdir=tests` avoids pytest treating this checkout's hyphenated directory
name as an importable package. Tests cover parsing without execution, original
IDs, electrical conversions, controls, invalid inputs, isolation, dashboard
HTTP import/run, profiles, exports, and an actual successful small-case IPOPT
solve. Additional integration tests use the supplied files when present in
`inputs`; those files are not bundled as package resources.

### Supplied-network checks on September 28, 2026

These checks used the supplied files unchanged, native ACDCPF for PF, and
IPOPT through Pyomo. AC OPF enabled generator Q control; hybrid OPF used the
eligible VSC Q controls (all three converters are DC-voltage references).

| Case / selected loss units | PF converged | PF total loss MW | OPF termination | Maximum constraint residual pu |
|---|---|---:|---|---:|
| Original AC | Yes | 0.09407747 | Locally infeasible | 0.00016536 |
| Tool #7 hybrid / physical | Yes | 0.08696166 | Locally infeasible | 0.00274829 |
| Tool #7 hybrid / per-unit | Yes | 0.40713112 | Locally infeasible | 0.00278551 |

The AC baseline reaches 0.948179 pu against the specified 0.95 pu minimum.
In the hybrid PF, converter 0 carries approximately 0.748 MW with physical
loss coefficients or 0.638 MW with per-unit coefficients, against its inferred
0.215 MVA rating. Its current also exceeds the imported 0.00980492 kA limit
(approximately 0.034015 or 0.029041 kA respectively). These are operating-limit
violations, not reasons to change the source data during import.

The failed OPF iterates have zero variable-bound violation but fail independent
physics checks (5 checks for AC, 13 for each hybrid interpretation). They are
not exported as valid physical results. A separate small imported AC/DC test
converges through IPOPT, passes the independent physics checks, and has model
residual and variable-bound violation below 1e-6. Local infeasibility on the
supplied cases is not a proof of global infeasibility for other control choices.
