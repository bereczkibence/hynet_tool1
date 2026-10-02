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

**Tool5 is required separately and is not bundled.** Copy the public [slazar394/acdcpf](https://github.com/slazar394/acdcpf) source checkout into `tool5/` inside this project. The resulting path must be `tool5/acdcpf/__init__.py`. Tool1 loads it directly; no Tool5 package installation or special fork is required.

```powershell
git clone https://github.com/bereczkibence/hynet_tool1.git
cd hynet_tool1
git clone https://github.com/slazar394/acdcpf.git tool5
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dashboard]"
.\.venv\Scripts\idaes.exe get-extensions
.\.venv\Scripts\tool1-doctor.exe --solve
```

Copying an extracted Tool5 folder instead of cloning it works too; a checkout named `acdcpf/` is also detected. On Windows, run `Install_Tool1.bat` after copying it. Python dependencies and IPOPT still require the normal Tool1 installation above. Restart the backend after replacing Tool5. For a source folder elsewhere, set `TOOL1_TOOL5_PATH` before launching Tool1.

Public Tool5 does not support fixed-Pdc converter controls. Tool1 follows the selected Tool5 converter-filter formulation. Results can differ from the previously used fork; review PF/OPF physics diagnostics. See [integration details and limitations](docs/COMMON_TOOL5.md).

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
- [Validation results and limitations](docs/PUBLIC_TOOL5_VALIDATION.md)

## License

Tool1 is distributed under the [MIT License](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for dependency attribution. The development status does not change the license terms.
