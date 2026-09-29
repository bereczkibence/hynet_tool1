# Tool1 (acdcopf)

**Hybrid AC/DC Optimal Power Flow for HYNET**

Tool1 minimizes active power losses using Pyomo/IPOPT, with Tool5 (acdcpf) providing power flow and initialization.

**Working development version.** Tool1 is a functional working version under active development. Features, interfaces and documentation may change as refinement and validation continue. Review solver diagnostics and validate results for the intended application.

## Features

- AC-only and hybrid AC/DC networks, including VSCs, transformers and storage.
- Custom PyPOWER AC and MatACDC DC case imports.
- PF-only and OPF runs, editable network data and time profiles.
- Dashboard, Python interface, REST API and result exports.
- Independent frontend and backend for workbench integration.

## Installation

Requires Python 3.10+ (tested on Windows/Python 3.10) and IPOPT for optimization.

**Tool5 is required separately and is not included.** Obtain the compatible `acdcpf==0.2.0+tool5.1` package (Tool5 API v1) from its maintainers. This version is not on PyPI; standard upstream `acdcpf` is not a drop-in replacement.

```powershell
git clone https://github.com/bereczkibence/hynet_tool1_v1.git
cd hynet_tool1_v1
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "C:\path\to\compatible-tool5" ".[dashboard]"
.\.venv\Scripts\idaes.exe get-extensions
```

Replace the example Tool5 path with its wheel or source folder. On Windows, `Install_Tool1.bat -Tool5Path "C:\path\to\compatible-tool5"` also installs and checks the application.

## Quick Start

```powershell
.\.venv\Scripts\tool1-dashboard.exe
```

Open **http://127.0.0.1:8521/**. Select **Original Stagg5** for a built-in example, or import `examples/two_bus_ac.py`. Choose PF-only for power flow; otherwise run OPF. PF convergence does not certify equipment-limit feasibility.

For independent operation, run these in separate terminals:

```powershell
.\.venv\Scripts\tool1-backend.exe --port 8520 --cors-origin http://127.0.0.1:8521
.\.venv\Scripts\tool1-frontend.exe --port 8521 --backend-url http://127.0.0.1:8520
```

API documentation: **http://127.0.0.1:8520/docs**.

## Documentation

- [Custom networks](docs/CUSTOM_NETWORKS.md)
- [Tool5 dependency and Python usage](docs/COMMON_TOOL5.md)
- [Workbench integration](docs/WORKBENCH.md)
- [Validation results and limitations](docs/COMMON_TOOL5_VALIDATION.md)

## License

Tool1 is distributed under the [MIT License](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for dependency attribution. The development status does not change the license terms.
