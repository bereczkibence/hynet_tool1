# Common Tool5 validation — 2026-09-29

Environment: separate Python 3.10.11 virtual environment in this copy, numpy
2.2.6, pandas 2.3.3, Pyomo 6.10.1 and installed IPOPT 3.13.2. Original Tool1
and its running dashboard were not modified or restarted.

- Tool1: **266 passed, 19 skipped** (optional reference/integration cases).
- Tool5: **115 passed**, including all 109 upstream tests and six public-contract
  tests. Eight expected warnings concern converter overload/control changes.
- Four additional Tool1 integration tests cover physical DC base scaling,
  immutable source matrices, policy restrictions and result-schema diagnostics.
- Existing HTTP tests exercise import/edit/PF/exports, separate frontend/backend,
  CORS, prefixed URLs, busy responses and restart/reimport behavior.
- Frontend JavaScript behavior checks pass for routing, connection and plain-text
  failures, labels, pending edits, refresh persistence and restart isolation.
- Installed both built wheels non-editably. In isolated Python mode from outside
  the source directory, imports resolved to site-packages, standalone Tool5 solved
  successfully, and Tool1 completed actual PF and IPOPT successfully.
- Standalone frontend served successfully with Python site-packages disabled.
- Artifacts audited for private inputs, logs, environments, Git state and absolute
  user paths. None found. SHA256SUMS.json records artifact checksums.
- All 93 recorded original Python source files remain unchanged. In this copy,
  37 protected files retain their original checksums; four explicitly documented
  importer/adapter/bootstrap files have reviewed integration baselines. OPF
  equations and supplied input files retain original checksums.

## Numerical comparison with the original project

Compared success classification, loss components, residuals and failed physics
check counts at relative/absolute tolerance 1e-8. All comparisons pass.

| Case | PF loss MW | OPF result |
|---|---:|---|
| Original Stagg5 | 8.636752834431 | Success; loss 8.624747239494 MW, maximum model residual 1.84e-11 |
| Supplied hybrid, original inputs | 0.407131120437 | Same infeasible classification; model residual 0.002785513005 |
| Supplied hybrid, DC35 = 0.4 MW, PF only | 0.402164331619 | PF converged; OPF not requested |

Standalone Tool5 wheel Stagg5 residuals: AC complex balance 5.86e-13 pu,
DC non-slack equations 9.33e-15 pu, converter internal-to-DC coupling
1.19e-11 pu. These residuals are not an equipment-feasibility certificate.

The new upstream limiter is opt-in through the common API. One upstream test's
strict physical-current-greater-than-circle assertion was changed to equality
within tolerance because the retained fork filter-KCL correction removes that
particular discrepancy. Independent current-circle and current-ratio tests pass;
no upstream test was suppressed or skipped. Full details are in Tool5 provenance.

## Limits of validation

No visual browser walkthrough was performed; browser logic and HTTP behavior were
tested programmatically. Other OS/Python combinations remain unvalidated. Fixed-Pdc
with converter-limited policy is explicitly rejected. Tool1 limited mode is PF-only
until control-switch semantics for OPF are designed and validated. The physical
interpretation of the supplied Tool #7 DC base still requires source-owner
confirmation; the importer now exposes that choice rather than guessing it.

These results describe the recorded local integration checks, not an upstream
Tool5 release. Tool1 is a working development version with continuing refinement
and validation. The maintainer subsequently confirmed the Tool1 MIT release;
see LICENSE and THIRD_PARTY_NOTICES.md. Tool5 retains its own license evidence.

Detailed execution output and comparison data are retained locally under
`.validation/`; generated logs and private data are excluded from releases.
