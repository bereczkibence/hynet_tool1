# Common Tool5 integration

Tool1 depends on the independently maintained common Tool5 package. Tool5 source
and distributions are not included here. Obtain `acdcpf==0.2.0+tool5.1` with API
v1 separately; see the root README for installation. No Tool5 remote URL is
assumed and no upstream release is claimed to be equivalent.

## Install and run this copy

Python 3.10+; validated on Python 3.10/Windows.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "C:\path\to\compatible-tool5"
.\.venv\Scripts\python.exe -m pip install ".[dashboard,dev]"
.\.venv\Scripts\tool1-doctor.exe --solve
.\.venv\Scripts\tool1-dashboard.exe
```

After installing, open
http://127.0.0.1:8521/ (backend 8520). The earlier project uses 8520/8521 and is
untouched. Copy reports use `Tool1Common` application data, or `TOOL1_REPORT_DIR`.
If IPOPT is absent, install the appropriate IDAES solver binaries or set
`TOOL1_IPOPT`; PF-only and standalone Tool5 do not require IPOPT.

Independent workbench processes:

```powershell
.\.venv\Scripts\tool1-backend.exe --port 8520 --cors-origin http://127.0.0.1:8521
.\.venv\Scripts\tool1-frontend.exe --port 8521 --backend-url http://127.0.0.1:8520
```

Backend OpenAPI: `/docs`. `/api/health` advertises Tool5 version, API version,
capabilities and limitations. Existing import/edit/run/poll/export endpoints are
unchanged. JSON run requests accept `pf_policy`: `unconstrained` (default) or
`converter_limited`. Limited mode requires `skip_opf: true`; it is not silently
used to change the controls fixed by OPF. Result runs contain `tool5` diagnostics,
including policy, warnings, changes to controls, residuals and converter limit
violations. These are also retained in the service diagnostics and exports.

## Use Tool5 from another application

Install the separately supplied compatible Tool5 source or wheel:

```python
import acdcpf
from acdcpf.networks import create_case5_stagg_mtdc_slack

print(acdcpf.capabilities())
network = create_case5_stagg_mtdc_slack()
result = acdcpf.solve(network, options=acdcpf.PFOptions(policy="unconstrained"))
print(result.converged, result.diagnostics["residuals"])
print(result.warnings, result.control_changes)
print(result.network.res_vsc)
```

`solve` copies the caller's network by default; input tables and source IDs are
preserved. `copy_network=False` writes results onto the caller's network.
Invalid input raises an exception; numerical non-convergence returns
`converged=False`. The v1 contract and conventions are in
the Tool5 package's own `docs/CONTRACT.md`.

Do not install upstream `acdcpf` and this package together: they share the same
import name. This is a versioned common fork combining documented upstream
changes with required existing extensions. It is not a claim that arbitrary
upstream versions are drop-in compatible. Tool1 pins the tested release and checks
API major version/capabilities. Upgrades must pass both test suites and the baseline
comparison before changing that pin.

## Electrical boundaries

OPF equations, optimizer settings and equipment limits remain unchanged.
Tool1's adapter maps physical converter current limits to the system AC base and
preserves voltage limits. Tool5 retains the existing fork's shunt, converter-filter
KCL, storage, transformer, fixed-Pdc and dense-indexing behavior.

The upstream capability limiter is available explicitly. It may clamp non-slack
converter P/Q or drop voltage/droop control. It never optimizes DC generation.
Slack reactive limiting is separately opt-in through the Python API and does not
curtail slack active power. Fixed-Pdc controls are supported in unconstrained mode;
limited mode rejects them until that combination has been validated.

Source ICMAX interpretation remains explicit: Standard MatACDC is per-unit;
Tool #7 uses its documented kA-based rating. Current-circle utilization,
physical reactor-current utilization and Tool1's OPF limit checks are distinct.
Convergence alone is not a feasibility certificate.

For DC imports, select `per_pole` or `pole_to_pole` in the dashboard or
`ImportOptions(dc_voltage_convention=...)`. Symmetric bipolar pole-to-pole input
is normalized to half the voltage and one quarter of the physical resistance
computed from its unchanged per-unit resistance and power base. Per-unit voltages,
powers and losses are invariant; physical conductor current doubles relative to
treating the same source value as per-pole. This assumes the stated per-unit
resistance convention: confirmation from the source owner is still required.
`legacy` preserves prior numbers with an explicit unconfirmed-convention warning;
it does not infer the intended physical interpretation of the supplied Tool #7 file.
Original source matrices and files are never edited.

## Validation and release

```powershell
.\.venv\Scripts\python.exe -m pytest tests --basetemp=.validation/tests
.\.venv\Scripts\python.exe scripts/compare_baseline.py ../hynet_tool1
.\.venv\Scripts\python.exe scripts/build_release.py
.\.venv\Scripts\python.exe scripts/verify_release.py
```

The original checksum manifest is preserved. `integration_checksums.json` records
only the four authorized adapter/importer/bootstrap integration changes; OPF and
case checksums retain the original baseline. Tool5 has its own provenance record.
Private `inputs/`, virtual environments, Git state and reports are excluded from
artifacts. Tool1 is a working development version distributed under MIT. See
LICENSE and THIRD_PARTY_NOTICES.md for the applicable license and attribution.
Further refinement and validation remain in progress.
