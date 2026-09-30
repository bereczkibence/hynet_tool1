# Tool5 source-folder integration

Tool1 loads the public `slazar394/acdcpf` solver without changing its source or requiring the private `0.2.0+tool5.1` package. Tool1 remains a working development version.

## Setup

Copy or clone the **whole Tool5 checkout** into `tool5/` inside the Tool1 checkout:

```text
hynet_tool1/
  pyproject.toml
  tool5/
    pyproject.toml
    acdcpf/
      __init__.py
```

Install Tool1 once with `python -m pip install -e ".[dashboard]"` and install IPOPT with `idaes get-extensions`. Run `tool1-doctor --solve`, then `tool1-dashboard`. Tool5 itself is not pip-installed. Its Python dependencies are included in Tool1's dependencies. The copied folder is ignored by Git and excluded from Tool1 distributions.

For a different layout, set `TOOL1_TOOL5_PATH` to the Tool5 checkout (or its `acdcpf` package directory) in the launching environment. This is also required for wheel installations where the checkout is separate from the installed Tool1 package. Selection order is explicit environment path, Tool1's `tool5/` folder, Tool1's `acdcpf/` folder (the default GitHub checkout name), then an installed `acdcpf`. An invalid explicit or local source is an error, not a silent fallback. Once imported, a backend cannot be switched inside the same Python process: restart after replacing the folder.

`tool1-doctor` and `/api/health` identify the actual module path and version, rather than assuming an installed distribution's version describes the loaded source.

## Application adapter

Tool1 uses public `acdcpf.run_pf`. Compatibility logic resides in `acdcpf_pyflow_backend`, not in the copied repository. The earlier common API v1 backend remains supported when separately installed/selected.

- Solve on a temporary network view; preserve input matrices, source IDs, setpoints and limits.
- Map active equipment to dense internal indices and restore original IDs in result tables.
- Preserve explicit AC slack selection by ordering its generator first in each island.
- Convert bus shunts from Tool1 per-unit to the MW/MVAr values consumed by public Tool5's PYPOWER translation.
- Translate standalone transformer tables into tapped branches, retaining impedance bases and phase shifts. Read branch terminal powers from the solved PYPOWER matrices to avoid a public Tool5 phase-shift discrepancy in its separately recomputed branch report.
- Translate storage snapshot injections to temporary signed loads. Tool1's native storage tables, controls and SOC constraints remain unchanged.
- Pass limit-enforcement policy explicitly. OPF baselines use unconstrained PF; converter-limited PF may change controls and reports those changes.

The Tool1 converter-filter reactive balance follows the selected backend. With `Q_f=-B_f|V_f|^2`, public Tool5 uses `S_cf=S_sf+jQ_f`; the previous API-v1 fork uses `S_cf=S_sf-jQ_f`. Tool1 therefore selects `Q_pr+Q_tf-Q_f=0` for public Tool5 and retains `Q_pr+Q_tf+Q_f=0` for API v1. Independent source-data checks use the corresponding reactor current (`I_c=I_tf+jB_fV_f` for public Tool5). The selection is recorded in OPF data metadata as `tool5_filter_balance_sign`. This change was explicitly requested by the maintainer on 30 September 2026.

This matches Tool5's implemented convention; it is not a claim that the two conventions represent identical physical filter admittances. Network input values, equipment limits, optimizer settings and copied Tool5 files remain unchanged.

## Limits of compatibility

Public Tool5 does not implement fixed-Pdc converter modes. Those networks receive an explicit error before solving; they are not silently converted to fixed-Pac controls.

Public Tool5's converter-filter equations differ from the previous Tool1-compatible fork. Networks with nonzero filters may have different PF/OPF losses and currents compared with the earlier fork. Tool1 matches the selected backend and retains its independent physics/feasibility diagnostics. PF convergence alone is not OPF feasibility. Do not claim numerical equivalence between backends.

Public `run_pf` returns convergence but does not expose the previous fork's full residual report. Missing upstream residuals are labelled unavailable, never reported as zero. Tool1's separate physical diagnostics remain the basis for assessing OPF feasibility.

The tested upstream revision and actual validation outcomes are recorded in `PUBLIC_TOOL5_VALIDATION.md`. Later upstream changes require rerunning these checks; version `0.2.0` alone does not identify a Git revision.
