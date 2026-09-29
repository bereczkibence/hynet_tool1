from __future__ import annotations

import importlib


def ensure_acdcpf_importable() -> None:
    """Use the installed backend without importing another environment's files."""
    try:
        backend = importlib.import_module("acdcpf")
        api = importlib.import_module("acdcpf.api")
    except ImportError as exc:
        raise ImportError(
            "Tool1 requires the common Tool5 backend. Install compatible Tool5 separately "
            "with Tool1; see docs/COMMON_TOOL5.md."
        ) from exc
    capabilities = api.capabilities()
    if capabilities["api_version"].split(".")[0] != "1" or not {"storage", "transformers", "external_ids", "validation"}.issubset(capabilities["features"]):
        raise ImportError("Tool1 requires Tool5 API v1 with storage, transformers, indexing and validation.")
