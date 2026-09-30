# Public Tool5 integration validation — 30 September 2026

Working development version. This report describes source-folder integration, not certification for operational use.

## Source and scope

Tested public repository: `https://github.com/slazar394/acdcpf`, revision `fe6da1577b9e6a9dc97650826e2f301967f64ba3` (`acdcpf.__version__ == 0.2.0`). All 55 tracked files in the copied checkout match the downloaded repository byte for byte. Tool5 is not pip-installed in the validation environment.

Application changes cover source discovery, dependency metadata, network-table construction/validation, temporary PF translation, result mapping, installer/doctor/health diagnostics, documentation and tests. The original project and earlier compatible Tool5 source were not edited.

The maintainer explicitly requested that Tool1 use public Tool5's formulation. The authorized numerical change is the selected backend's converter-filter reactive-balance sign, together with its matching independent physical check. `docs/engine_checksums.json` retains the original baseline; `docs/integration_checksums.json` records the reviewed integration/formulation overrides. All 38 protected-file checks pass. Limits, solver tolerances and objectives were not relaxed.

## Tests

- Final full public-backend suite: **277 passed, 23 skipped** (65.11 seconds), including the phase-shift regression.
- Final focused public integration/import/OPF checks: 73 passed, 3 skipped.
- Earlier API-v1 backend compatibility: 58 passed, 3 skipped (import and audit regressions).
- `tool1-doctor --solve`: passed with the copied public backend and actual IPOPT 3.13.2.
- Wheel and source distribution built and inspected: new adapter included; Tool5, private inputs and DOCX files excluded; no `acdcpf` distribution requirement remains. An isolated wheel target imported `acdcopf` and passed `tool1-doctor --solve` with explicit source-folder selection. Distribution metadata confirms Tool5 was not installed.
- Public-example comparisons cover all seven built-in networks. The reference receives explicit conversion of Tool1 per-unit bus shunts to the MW/MVAr values consumed by upstream; this affects the RTS example. Voltage/converter-power results match after this declared unit conversion.
- Focused checks cover source selection and invalid paths; input isolation; nonconsecutive IDs; shunts and explicit slack selection; storage; transformer taps and phase shifts; unsupported fixed-Pdc controls; selected filter formulation.
- Existing tests cover HTTP runs/downloads, API-only routes, CORS/prefix handling, custom imports, editable metadata, profiles and export paths. Browser visual interaction was not rerun in this change.
- The unchanged upstream creation helpers emit pandas FutureWarnings. These remain visible in test logs; Tool1 does not modify upstream files to suppress them.

## Stagg5 numerical result

Windows, Python 3.10, NumPy 2.2.6, pandas 2.3.3, Pyomo 6.10.1; IPOPT tolerance `1e-8` and maximum 1000 iterations.

| Quantity | Result |
| --- | ---: |
| Baseline PF total active loss | 8.626045488922 MW |
| OPF total active loss | 8.602568042780 MW |
| Public Tool5 replay total active loss | 8.602568042722 MW |
| Maximum model constraint violation | 2.9533e-12 |
| Maximum variable-bound violation | 0 |
| Maximum AC-voltage replay difference | 1.9751e-13 pu |
| Total-loss replay difference | 5.7971e-11 MW |
| Independent physics checks | 157 passed, 0 failed, 14 not checked |

The unchecked physics items reflect missing source equipment limits. The result is therefore partially checked, not universally certified feasible. Numerical values intentionally differ from the old fork because Tool1 now follows public Tool5's filter convention.

## Supplied private-network checks

Private files were read from their existing local directory and were not copied into the distributable project. AC baseline: 33 buses, 35 branches including one transformer; PF converged (0.094077465946 MW loss). Hybrid: 21 AC buses, 17 DC buses, 20 AC branches, 14 DC branches and three VSCs; PF converged (0.407131120437 MW loss). Its unmodified OPF remains infeasible, correctly reported with maximum model residual 0.002785513004. Editing the source-ID DC35 generator to 0.4 MW through the service override table yields a converged PF (0.402164331619 MW loss). No rating or control was relaxed to obtain these outcomes.

## Remaining limitations

Fixed-Pdc converter controls are explicitly rejected with public Tool5; the earlier API-v1 backend retains them. Upstream `run_pf` does not expose the former API-v1 residual dictionary, so its absence is labelled rather than filled with zero. Tool1 still independently checks OPF physics and PF replay. Future Tool5 revisions must be revalidated, even if their package version string remains `0.2.0`.

Copying Tool5 removes the need to install its package. A working Python environment, Tool1 dependencies and IPOPT for OPF are still required. Restart after replacing the source folder.
