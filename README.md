# hynet_tool1_v1 ? Tool1 (acdcopf)

Hybrid AC/DC optimal power flow for the HYNET workbench. Tool1 minimizes active power losses using Pyomo/IPOPT. Tool5 (acdcpf) supplies the power-flow baseline and initialization.

**Release status:** local integration candidate. OPF equations are unchanged; the common Tool5 backend adds explicit converter-limit policies. Redistribution requires resolution of the license evidence described in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

See [Common Tool5 integration](docs/COMMON_TOOL5.md) and [validation results](docs/COMMON_TOOL5_VALIDATION.md) for the standalone solver API, compatibility boundaries and release instructions.

**Tool5 is an external dependency and is not included in this repository.** Tool1 requires the compatible `acdcpf==0.2.0+tool5.1` package with Tool5 API v1 (including validation, transformers and storage). Obtain its wheel or source checkout separately from the Tool5 maintainers. The standard upstream `acdcpf` package is not a drop-in substitute for this compatible version. See [GitHub setup](GITHUB_SETUP.md).

## Install

Python 3.10 or newer, and an operating-system-compatible IPOPT executable are required. This release is tested on Windows/Python 3.10; other Python/OS combinations require validation.

After cloning this repository, open a terminal in its directory:

```powershell
python -m venv .venv
# Replace this example path with the separately obtained Tool5 wheel or source folder.
.\.venv\Scripts\python.exe -m pip install "C:\path\to\compatible-tool5"
.\.venv\Scripts\python.exe -m pip install ".[dashboard]"
.\.venv\Scripts\idaes.exe get-extensions
.\.venv\Scripts\tool1-doctor.exe --solve
```

On Windows, run `Install_Tool1.bat -Tool5Path "C:\path\to\compatible-tool5"`. If the required Tool5 version is already installed in this folder's `.venv`, the path may be omitted. On Linux/macOS use `.venv/bin/` instead of `.venv\Scripts\`. Install Tool5 separately in the same environment before Tool1; the exact common version is not on PyPI. Optional PyFlow comparisons require `.[reference]`; they are not part of the default workflow.

## Run the demonstration frontend

Double-click `Tool1_Dashboard.bat`, or run:

```powershell
.\.venv\Scripts\tool1-dashboard.exe
```

Open **http://127.0.0.1:8521/**. The backend runs on port 8520. The included original Stagg5 case is a quick PF/OPF example. Select PF-only for the baseline; clear it to run Tool1 optimization.

For a synthetic import, select `examples/two_bus_ac.py` as the AC case and run PF-only. Custom files are data-only Python case definitions, never executed. Hybrid imports require explicit format and loss-unit choices; see [Custom networks](docs/CUSTOM_NETWORKS.md).

PF convergence means the electrical equations were solved; it does not certify equipment limits. OPF success requires solver completion and passing physics diagnostics. Unconstrained PF preserves requested controls; converter-limited PF reports any control adjustments. Ratings are never resized.

## Independent components

```powershell
# Terminal 1: backend only
.\.venv\Scripts\tool1-backend.exe --port 8520 --cors-origin http://127.0.0.1:8521
# Terminal 2: demonstration frontend only
.\.venv\Scripts\tool1-frontend.exe --port 8521 --backend-url http://127.0.0.1:8520
```

The standalone frontend ZIP runs with standard Python and no solver packages: `python serve.py --backend-url http://127.0.0.1:8520`.

The common workbench frontend may call the API directly without this demonstration UI. OpenAPI: **http://127.0.0.1:8520/docs**. See [Workbench integration](docs/WORKBENCH.md).

## Python and command line

```python
from acdcopf import load_custom_network, custom_grid_case, BenchmarkRequest, run_benchmark_request
network = load_custom_network("examples/two_bus_ac.py")
result = run_benchmark_request(BenchmarkRequest(
    grid_case=custom_grid_case(network), skip_opf=True,
    include_pyflow_reference=False, write_exports=False))
print(result.success)
```

```powershell
.\.venv\Scripts\tool1-run.exe examples\pf_request.json
```

This reads a JSON request, runs the same service as the API, and writes JSON to stdout. Exit codes: 0 successful calculation, 1 failed calculation, 2 invalid request/application error. Solver console output goes to stderr.

`TOOL1_REPORT_DIR` sets the output directory; `TOOL1_IPOPT` sets the executable. Use these Tool1 variable names for configuration. Default reports live in the current user's Tool1Common application-data directory. The legacy internal import `acdcpf_opf` remains available.

## Development and release

See [Development](DEVELOPMENT.md), [Provenance](docs/PROVENANCE.md), and [Validation](docs/VALIDATION.md). Private `inputs/` are ignored and excluded from releases. Run `python scripts/build_release.py` to build the wheel, source distribution, source archive, and independent frontend archive after installing build requirements.
