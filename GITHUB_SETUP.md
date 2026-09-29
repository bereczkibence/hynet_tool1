# GitHub source handoff

This is a clean Tool1 (acdcopf) source repository. Tool5 is a separately supplied
runtime dependency, not a bundled source directory or Git submodule. Install the
compatible version identified in README.md before installing Tool1. No wheels,
release archives, private inputs, environments or generated reports are included.
Launchers are source scripts, not compiled executables.

This repository uses the main branch. Tool5 is maintained separately.
Before external redistribution, resolve the Tool1 license-grant evidence listed
in THIRD_PARTY_NOTICES.md. No license or copyright ownership has been invented.

Repository: https://github.com/bereczkibence/hynet_tool1_v1

This repository is private. Partners must be granted repository access before
the link will work for them.

Installation after cloning:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "C:\path\to\compatible-tool5"
.\.venv\Scripts\python.exe -m pip install ".[dashboard,dev]"
.\.venv\Scripts\idaes.exe get-extensions
.\.venv\Scripts\tool1-dashboard.exe
```

Open http://127.0.0.1:8521/. For separate backend/frontend operation and the
standalone Tool5 API, see docs/COMMON_TOOL5.md. No installation was performed
while creating this source handoff.

Verification:

```powershell
python scripts/verify_engines.py
python -m pytest tests
```

Tests requiring private supplied inputs skip when those inputs are absent.
Git attributes preserve original line endings because the protected engine
manifest verifies file bytes. No Tool5 source is tracked by this repository.
