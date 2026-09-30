"""Locate a separately supplied Tool5 checkout without modifying it."""
from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
import sys


def _source_parent(path: Path) -> Path:
    path = path.expanduser().resolve()
    for parent in (path, path / "src", path.parent):
        if (parent / "acdcpf" / "__init__.py").is_file():
            return parent
    raise ImportError(f"Tool5 source not found at {path}; expected acdcpf/__init__.py.")


def ensure_acdcpf_importable() -> None:
    """Prefer an explicit path, then Tool1/tool5, then an installed package.

    Never replace an already imported backend within a running process.
    Restart the backend after copying or updating Tool5.
    """
    explicit = os.environ.get("TOOL1_TOOL5_PATH")
    root = Path(__file__).resolve().parents[1]
    local = next((root / name for name in ("tool5", "acdcpf") if (root / name).exists()), root / "tool5")
    source = _source_parent(Path(explicit)) if explicit else (
        _source_parent(local) if local.exists() else None
    )
    loaded = sys.modules.get("acdcpf")
    if source is not None:
        expected = (source / "acdcpf" / "__init__.py").resolve()
        if loaded is not None and (not getattr(loaded, "__file__", None) or Path(loaded.__file__).resolve() != expected):
            raise ImportError("A different Tool5 is already loaded. Restart Python/backend to use the copied source.")
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
        importlib.invalidate_caches()
    try:
        backend = importlib.import_module("acdcpf")
    except ImportError as exc:
        raise ImportError(
            "Cannot load Tool5. Copy the public slazar394/acdcpf checkout into Tool1/tool5 "
            "(tool5/acdcpf/__init__.py), or set TOOL1_TOOL5_PATH. Install Tool1 dependencies first. "
            f"Original error: {exc}"
        ) from exc
    required = {"enforce_limits", "enforce_slack_q_limits", "tol"}
    if not callable(getattr(backend, "run_pf", None)) or not required <= set(inspect.signature(backend.run_pf).parameters):
        raise ImportError(f"Unsupported Tool5 interface at {backend.__file__}: run_pf with limit policy options is required.")


def backend_info() -> dict:
    ensure_acdcpf_importable()
    import acdcpf
    common = callable(getattr(acdcpf, "capabilities", None))
    info = acdcpf.capabilities() if common else {
        "version": acdcpf.__version__, "api_version": "public-run_pf",
        "features": ["ac", "dc", "vsc", "dcdc", "tool1_storage_translation", "tool1_transformer_translation"],
        "limitations": ["Fixed-Pdc converter controls are unsupported by public Tool5.",
                        "Tool1 matches public Tool5 converter-filter equations; results differ from the previous fork."],
    }
    return {**info, "source": str(Path(acdcpf.__file__).resolve())}


def filter_balance_sign() -> float:
    """Public Tool5 uses S_cf=S_sf+jQ_f; API-v1 fork uses S_sf-jQ_f."""
    return -1.0 if backend_info()["api_version"] == "public-run_pf" else 1.0
