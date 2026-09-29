"""Tool1 backend, combined launcher, and JSON-request command line."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def backend():
    parser = argparse.ArgumentParser(description="Tool1 (acdcopf) standalone API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8520)
    parser.add_argument("--root-path", default="")
    parser.add_argument("--cors-origin", action="append", default=None)
    args = parser.parse_args()
    import uvicorn
    from acdcpf_opf.dashboard.web import create_app
    app = create_app(serve_frontend=False, cors_origins=args.cors_origin)
    uvicorn.run(app, host=args.host, port=args.port, root_path=args.root_path)


def run():
    parser = argparse.ArgumentParser(description="Run Tool1 from a JSON request without a frontend")
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    try:
        from acdcpf_opf.dashboard import web
        from acdcpf_opf.dashboard.schemas import RunRequest, ImportRequest
        from acdcpf_opf.benchmarks.stagg5.service import run_benchmark_request
        payload = json.loads(args.request.read_text(encoding="utf-8-sig"))
        network = payload.pop("network", None)
        if network is not None:
            for side in ("ac", "dc"):
                key = side + "_path"
                if key in network:
                    path = args.request.resolve().parent / network.pop(key)
                    network[side + "_text"] = path.read_text(encoding="utf-8-sig")
                    network[side + "_name"] = path.name
            imported = web.import_network_payload(ImportRequest.model_validate(network).model_dump(exclude_none=True))
            payload["grid_case"] = imported["import_id"]
        validated = RunRequest.model_validate(payload).model_dump(exclude_none=True)
        request = web.benchmark_request_from_payload(validated)
        # Keep solver console output separate from the machine-readable response.
        from contextlib import redirect_stdout
        with redirect_stdout(sys.stderr):
            result = run_benchmark_request(request)
        print(json.dumps(web.result_payload(result), allow_nan=False))
        return 0 if result.success else 1
    except Exception as exc:
        print(json.dumps({"error": {"code": "invalid_request_or_run", "message": str(exc)}}))
        return 2


def dashboard():
    parser = argparse.ArgumentParser(description="Launch Tool1 API and demonstration frontend")
    parser.add_argument("--backend-port", type=int, default=8520)
    parser.add_argument("--frontend-port", type=int, default=8521)
    args = parser.parse_args()
    origin = f"http://127.0.0.1:{args.frontend_port}"
    worker = subprocess.Popen([sys.executable, "-m", "acdcopf", "backend", "--port", str(args.backend_port), "--cors-origin", origin])
    try:
        from .frontend import main
        return main(["--port", str(args.frontend_port), "--backend-url", f"http://127.0.0.1:{args.backend_port}"])
    finally:
        worker.terminate()
        worker.wait()
