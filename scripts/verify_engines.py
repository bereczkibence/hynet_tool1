"""Verify the protected numerical engine and any available private input files."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "docs/engine_checksums.json").read_text())
# Only the explicitly reviewed integration boundary has a new baseline.
# OPF equations and original private cases still use the original manifest.
overrides = json.loads((root / "docs/integration_checksums.json").read_text())
allowed = {"acdcpf_pyflow_backend/_bootstrap.py", "acdcpf_pyflow_backend/runner.py", "data/custom_network.py", "powerflow/acdcpf_network_adapter.py"}
assert set(overrides) <= allowed
manifest.update(overrides)
failures = []
checked = 0
for name, expected in manifest.items():
    path = root / name
    if name.startswith("inputs/") and not path.exists():
        continue
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        failures.append(name)
    checked += 1
if failures:
    raise SystemExit("Protected files changed: " + ", ".join(failures))
print(f"Verified {checked} protected files.")
