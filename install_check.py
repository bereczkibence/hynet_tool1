"""Installation diagnostics; an optional smoke run exercises the real solver."""

from __future__ import annotations

import argparse
from importlib import metadata
import json
import sys
import tempfile

from acdcpf_pyflow_backend._bootstrap import ensure_acdcpf_importable
from acdcopf.presentation import brand_text
from acdcpf_opf.runtime_paths import ipopt_executable_path, reports_directory


def main(argv: list[str] | None = None) -> int:
    """Report backend/solver availability and optionally solve native Stagg5."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solve", action="store_true", help="Run a real Stagg5 PF/OPF and physics checks.")
    args = parser.parse_args(argv)
    try:
        ensure_acdcpf_importable()
        import acdcpf
        import pyomo.environ as pyo

        print(f"Python: {sys.version.split()[0]} ({sys.executable})")
        for package in ("hynet-tool1", "acdcpf", "pyomo", "numpy", "pandas"):
            print(f"{package}: {metadata.version(package)}")
        print(f"ACDCPF source: {acdcpf.__file__}")
        executable = ipopt_executable_path()
        print(f"IPOPT: {executable}")
        if not executable.is_file():
            raise RuntimeError(
                "IPOPT was not found. Run `idaes get-extensions`, or set "
                "TOOL1_IPOPT to an executable built for this operating system."
            )
        solver = pyo.SolverFactory("ipopt", executable=str(executable))
        if not solver.available(exception_flag=False) or solver.version() is None:
            raise RuntimeError("IPOPT exists but cannot run. Check its platform and shared libraries.")
        print(f"IPOPT version: {solver.version()}")
        output = reports_directory()
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=output):
            pass
        print(f"Writable reports: {output}")
        if args.solve:
            from acdcpf_opf.benchmarks.stagg5.service import BenchmarkRequest, run_benchmark_request

            bundle = run_benchmark_request(BenchmarkRequest(
                include_pyflow_reference=False, write_exports=False,
            ))
            for run in bundle.runs:
                print(f"{brand_text(run.label)}: success={run.success}; {run.message}")
                if run.losses_mw:
                    print("Losses (MW): " + json.dumps(run.losses_mw))
                physics = run.diagnostics.get("physics_validation")
                if physics:
                    print("Physics: " + json.dumps({k: v for k, v in physics.items() if k != "checks"}))
            if not bundle.success:
                raise RuntimeError("The native Stagg5 PF/OPF smoke run failed.")
            print("Smoke run passed. Missing equipment ratings still limit physical validation coverage.")
        else:
            print("Installation checks passed. Use --solve to test PF/OPF execution.")
        return 0
    except Exception as exc:
        print(f"Installation check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
