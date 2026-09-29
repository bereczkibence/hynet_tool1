# Tool1 integration validation

Validated on Windows with Python 3.10.11, Tool5/acdcpf 0.1.1+opf.bme.1, Pyomo 6.10.1, and IPOPT 3.13.2.

## Preservation

- All 41 protected numerical/import/adapter/private-case files match the recorded SHA-256 checksums.
- All 109 original copied source files remain unchanged in the original project.
- The installed Tool5 Python source matches the previously installed pinned backend.
- No electrical equations, equipment limits, solver tolerances, or control eligibility rules changed.

## Numerical checks

Original and Tool1 results match within absolute/relative tolerance 1e-8 for Stagg5, the supplied hybrid case, and its DC35=0.4 MW PF variation. The comparison includes success classification, loss components, OPF residuals, and failed physics-check counts.

- Stagg5 PF total loss: 8.63675283443057 MW.
- Stagg5 OPF total loss: 8.624747239493995 MW; maximum constraint residual 1.8357482201025732e-11 pu; zero failed physics checks. Validation is partial: 14 unavailable-rating checks remain unchecked.
- Supplied hybrid case with per-unit converter loss coefficients: PF converges; OPF still reports infeasibility, residual 0.002785513004569121 pu. No failed iterate is promoted to a valid optimized result.
- Supplied hybrid case with DC35 generation set to 0.4 MW: PF converges, losses 0.4021643316194496 MW; first VSC loading approximately 18.4262%.

## Automated coverage

Full regression suite: 259 passed, 19 skipped. Skips cover unavailable optional reference backends/capabilities; they are not counted as passes. Existing warnings remain visible in pytest output. Tests include real IPOPT execution, custom imports, malformed inputs, table-edit provenance, profiles, exports, and fresh-network isolation.

Interface checks cover separate frontend and backend processes, actual HTTP requests through a stripping reverse proxy, CORS/preflight, OpenAPI, error schemas, stale import IDs, single-run busy rejection, missing IPOPT, edited DC35 PF, and downloadable Markdown/CSV/XLSX/HTML. Frontend JavaScript checks cover URL prefixes, non-JSON errors, connection errors, labels, source IDs, pending-edit persistence, and restart isolation.

Run `python scripts/verify_engines.py`, `python -m pytest tests -q --confcutdir=tests`, and optionally `node tests/frontend_behavior.mjs`. `scripts/compare_baseline.py ORIGINAL_CHECKOUT` reproduces the numerical comparison when the original environment and private inputs are available.

## Installed artifacts

Built the wheel, source distribution, source ZIP, and independent frontend ZIP. Installed the wheel and dependencies into a separate clean environment; verified imports resolve to its site-packages using isolated Python mode. The installed wheel passes the real PF/IPOPT doctor smoke run and all 20 installation/interface/branding checks. The editable source passes the full 259-test regression suite plus three naming regressions; 19 optional tests are skipped.

Artifact audits found no private input contents, virtual environments, logs, bytecode, or user-specific paths. The frontend ZIP serves its HTML, configuration, JavaScript, and styles under a URL prefix with Python site-packages disabled. SHA256SUMS.json records final artifact hashes. `python scripts/verify_release.py` reproduces these checks.

## Known limits

No enabled browser surface was available for visual browser testing. HTTP delivery and frontend logic were tested, but visual layout and real browser interaction remain a manual release check. Cross-platform solver installation has not been validated. The API has one active run and process-local state; workbench authentication and multi-user isolation are external responsibilities. Supplied private networks are not shipped. Tool1 is a working development version; validation remains ongoing. The maintainer has confirmed the MIT release. See LICENSE and THIRD_PARTY_NOTICES.md for attribution.

Naming cleanup verification: public request fields now use `skip_opf`; benchmark identifiers, launch commands, labels, export filenames, and Excel company metadata contain no former product branding. The protected engine files and pinned dependency version are unchanged.
