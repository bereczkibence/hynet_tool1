# Tool1 development

**Working development version.** Tool1 is a functional working version under active development. Features, interfaces and documentation may change as refinement and validation continue. Review solver diagnostics and validate results for the intended application.

The numerical engine remains under `opf/`, with electrical import/conversion code in `data/` and PF adapters in `powerflow/`. These files are checksum-protected for this release. `acdcopf/` is the public interface and application presentation layer; `dashboard/` owns HTTP and demonstration UI integration.

Install `.[dashboard,dev]` into a dedicated virtual environment. Run:

```text
python -m pytest tests -q --confcutdir=tests
python scripts/verify_engines.py
python -m acdcopf doctor --solve
```

Legacy `acdcpf_opf` imports and wire keys remain for compatibility. Do not rename identifiers inside numerical equations for branding. Presentation labels are translated at the boundary. New public integrations should use `acdcopf` and the documented HTTP API.

Tool5 changes belong in its source repository and require a separate compatibility review. The pinned backend commit is unchanged. Private input files and validation caches must not enter distributions. `scripts/build_release.py` creates local artifacts; it never publishes them.
