"""User-writable outputs and explicit, platform-independent solver discovery."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


def user_data_directory() -> Path:
    """Return an application data path without creating directories on import."""
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "Tool1Common"


def reports_directory() -> Path:
    """Use TOOL1_REPORT_DIR or the current user's application data directory."""
    override = os.environ.get("TOOL1_REPORT_DIR")
    return Path(override).expanduser().resolve() if override else user_data_directory() / "reports"


def ipopt_executable_path() -> Path:
    """Find IPOPT without silently replacing an explicit user override.

    If absent, return the expected application solver path so PF-only runs can
    still start. Pyomo and ``tool1-doctor`` diagnose missing executables.
    """
    override = os.environ.get("TOOL1_IPOPT")
    if override:
        return Path(override).expanduser().resolve()
    executable = "ipopt.exe" if sys.platform == "win32" else "ipopt"
    on_path = shutil.which(executable)
    if on_path:
        return Path(on_path).resolve()
    local_solver = user_data_directory() / "solvers" / executable
    if local_solver.is_file():
        return local_solver
    # IDAES owns its binary location; do not duplicate its platform conventions.
    try:
        import idaes
    except ImportError:
        return local_solver
    candidate = Path(idaes.bin_directory) / executable
    return candidate if candidate.is_file() else local_solver
