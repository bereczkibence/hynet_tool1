# Portable installation verification (2026-09-28)

The release packages the current audit, physics checks and equipment-limit
work along with installation changes. The portability work does not change
the OPF equations or objective. Previously implemented numerical corrections
are described in `AUDIT_FIXES_2026_09.md` and the ACDCPF fork's extension notes.

## Verified locally

- Windows x86-64, Python 3.13.1, a new virtual environment without system packages.
- Non-editable OPF wheel; ACDCPF installed from GitHub at its pinned commit.
- Tests and smoke runs executed outside both source repositories, without a
  sibling ACDCPF override or an injected `PYTHONPATH`.
- Fresh IDAES extension download into an isolated directory; IPOPT 3.13.2
  actually executed through Pyomo 6.10.1. NumPy 2.5.3, pandas 2.3.3.
- OPF test suite: 210 passed, 18 skipped (optional PyFlow is not installed).
- ACDCPF source test suite: 76 passed.
- `pip check`: no broken requirements.
- Dashboard localhost HTTP test: load assets/config/network inputs, submit a
  real PF/OPF run, retrieve status and grid HTML, verify split CSV/XLSX files.
- Hybrid 24-hour load/PV study: 24 successful timestamps, no failed physics
  checks; report coverage remains partial because equipment limits are missing.

Stagg5 installation smoke result: PF loss 8.6367528344 MW; OPF loss
8.6247472395 MW; PF replay loss 8.6247472395 MW. Independent source-network
checks: 157 passed, 0 failed, 14 not checked. These numbers verify this
installation and case, not a general accuracy or global-optimality guarantee.

## Remaining boundaries

- Linux/Windows clean-install CI is configured in `.github/workflows/install.yml`;
  check the actual GitHub run before claiming a platform passed. macOS is not
  included in the validation matrix.
- A native IPOPT binary and its shared libraries must support the target OS/CPU.
- Numerical dependencies have compatible ranges, not a cross-platform lockfile.
- Unknown ratings remain empty and are not certified by the physics report.
- pandas emits existing DataFrame concatenation deprecation warnings; pandas
  is bounded below version 3 until backend migration is tested.
- No external network-model validation or mathematical formulation change is
  implied by packaging tests.
