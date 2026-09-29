# Installation

Follow the [README](../README.md) for a clean environment, independent launch commands, and solver setup. Python metadata requires 3.10 or newer; the validated platform is Windows/Python 3.10. Git is needed for the pinned Tool5 dependency. No sibling source checkout is required.

Install with `python -m pip install ".[dashboard]"`; developer tests additionally require `.[dev]`. Run `idaes get-extensions` to obtain IPOPT, or set `TOOL1_IPOPT` to a compatible executable. `tool1-doctor --solve` validates the actual PF/OPF path. PF-only execution does not require IPOPT.

Use `TOOL1_REPORT_DIR` for writable reports. On Windows the default is `%LOCALAPPDATA%/Tool1/reports`; Linux uses `$XDG_DATA_HOME/Tool1/reports` or `~/.local/share/Tool1/reports`; macOS uses `~/Library/Application Support/Tool1/reports`. Use the Tool1 environment-variable names; former product aliases have been removed.

The frontend ZIP needs only standard Python. Run `python serve.py --backend-url http://127.0.0.1:8520`. Installing the solver package is unnecessary on the frontend host.

This is an integration candidate, not a published package. License provenance must be resolved before external redistribution; see the root notices file.
