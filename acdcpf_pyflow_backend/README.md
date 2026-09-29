# ACDCPF Pyflow Backend

This is a separate adapter project that runs `acdcpf` as the power-flow backend
for a `pyflow_acdc` grid.

It does **not** patch the installed `pyflow_acdc` package. Instead it:

1. translates a `pyflow_acdc.Grid` into an `acdcpf.Network`
2. runs `acdcpf.run_pf(...)`
3. extracts the solved `net.res_*` tables
4. optionally writes solved voltages and converter powers back into the original
   `pyflow` grid object

## Files

- `translator.py`
  - builds the `acdcpf` network from a `pyflow` grid
- `runner.py`
  - runs the PF and writes the solved state back to `pyflow`
- `datatypes.py`
  - normalized result container and index mapping
- `_bootstrap.py`
  - makes the sibling `acdcpf` repo importable even when it is not installed
- `example_stagg5.py`
  - small end-to-end example using `pyflow_acdc.Stagg5MATACDC()`
- `compare_stagg5.py`
  - compares the new backend against native `pyflow_acdc.ACDC_sequential(...)`

## Current Scope

This first version supports:

- AC buses and AC lines
- DC buses and DC lines
- AC/DC VSC converters
- writing solved state back into `pyflow`

Current limitations:

- `pyflow` DCDC converters are not translated yet
- mixed DC pole counts inside one grid are not supported
- line shunt conductance is approximated as split bus shunts

## Main Function

```python
from acdcpf_pyflow_backend.runner import run_acdcpf_pf_on_pyflow

result = run_acdcpf_pf_on_pyflow(grid, write_back=True)
```

Returned object:

- `result.converged`
- `result.ac_bus`
- `result.ac_line`
- `result.dc_bus`
- `result.dc_line`
- `result.vsc`
- `result.dcdc`
- `result.mapping`
- `result.acdcpf_net`

## Example

Run:

```python
python acdcpf_pyflow_backend/example_stagg5.py
```

Reference comparison:

```python
python acdcpf_pyflow_backend/compare_stagg5.py
```

If `acdcpf` is not installed in the current environment, the adapter tries to
import it from the sibling workspace repo:

- `../acdcpf`
- `../acdcpf/.venv/Lib/site-packages`

## Intended Use

This adapter is meant to be the PF backend boundary for a future separate OPF
project. That means the recommended flow is:

1. `pyflow_acdc` defines the grid
2. this adapter runs `acdcpf`
3. a higher-level OPF script reads the returned result tables

This keeps `acdcpf` isolated as a black-box PF engine and avoids binding your
future OPF logic directly to the internal PF implementation in `pyflow_acdc`.

## Current Validation

The adapter has been exercised on:

- a simple AC two-bus pyflow case
- `pyflow_acdc.Stagg5MATACDC()`

On the Stagg case, the converter and voltage results are close to native
`pyflow_acdc.ACDC_sequential(...)`, which makes it a useful baseline for your
future separate OPF project.
