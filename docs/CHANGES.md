# Tool1 preparation changes

## Application changes

- Independent source copy with recorded provenance, engine checksums, and private input exclusions.
- Public lazy `acdcopf` facade, JSON request CLI, backend command, dependency-free frontend server, and combined launcher.
- Separate API serving, documented OpenAPI schemas, explicit errors, backend health/version/session metadata, CORS, and export downloads.
- Atomic single-run submission, preflight edit validation, missing-solver reporting, original source IDs listed read-only, and no stale result attached to a new submission.
- Tool1/Tool5 presentation labels and Tool1 report filenames, environment settings, prefixed frontend routing, persistent pending edits, and clearer convergence/feasibility text.
- Installation, integration, licensing evidence, and validation documents; public synthetic import example; repeatable local build and artifact audit scripts.

## Engine boundary and assumptions

No numerical engine files or equations were modified. Source data and converter ratings are unchanged. Requests now use `skip_opf`; benchmark identifiers and labels use Tool1. The internal package import remains available. The current pinned Tool5 version remains installed; the newer upstream PF repository has not been integrated. Single-user/trusted workbench operation, one active run, process-local imports, and external authentication are explicit defaults.

## Verification and next step

See VALIDATION.md for actual PF/IPOPT diagnostics and tests. The known release blockers are unresolved source license evidence and an outstanding visual browser review. Confirm those before distributing publicly. For local use, launch Tool1_Dashboard.bat; for integration, use the independent backend and WORKBENCH.md.

## Naming cleanup

Removed the former product name from application identifiers, benchmark/report labels, Excel company metadata, CLI commands, launchers, examples, and request schemas. The PF-only option is now `skip_opf` in Python/JSON and `--skip-opf` in the benchmark CLI. Set `TOOL1_REPORT_DIR` and `TOOL1_IPOPT`; old aliases were removed. Update older workbench clients to these names. Source-engine files, historical provenance, and the pinned dependency version remain unchanged.
