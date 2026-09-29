"""Tool1 public facade, separated HTTP interfaces, and unchanged engine checks."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_public_facade_and_protected_files():
    from acdcopf import load_custom_network, BenchmarkRequest
    assert load_custom_network(ROOT / "examples/two_bus_ac.py").report["counts"]["ac_bus"] == 2
    assert BenchmarkRequest(skip_opf=True).skip_opf
    subprocess.run([sys.executable, str(ROOT / "scripts/verify_engines.py")], check=True)


def test_tool1_environment_paths(monkeypatch, tmp_path):
    from acdcpf_opf import runtime_paths
    monkeypatch.setenv("TOOL1_REPORT_DIR", str(tmp_path / "new"))
    assert runtime_paths.reports_directory() == tmp_path / "new"
    monkeypatch.setenv("TOOL1_IPOPT", str(tmp_path / "new.exe"))
    assert runtime_paths.ipopt_executable_path() == tmp_path / "new.exe"


def test_schema_and_api_only_routes():
    from acdcpf_opf.dashboard.schemas import RunRequest
    from acdcpf_opf.dashboard.web import create_app
    with pytest.raises(ValueError):
        RunRequest(skip_opf="false")
    with pytest.raises(ValueError):
        RunRequest(unknown=1)
    paths = {getattr(r, "path", None) for r in create_app(serve_frontend=False).routes}
    assert "/" not in paths and "/static" not in paths
    assert "/api/health" in paths


def test_busy_missing_solver_and_restart(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from acdcpf_opf.dashboard import web
    from acdcpf_opf.dashboard.runtime import RunStatus
    from acdcpf_opf.dashboard.schemas import RunRequest
    from acdcpf_opf import runtime_paths
    monkeypatch.setattr(web, "STATE", web.WebDashboardState())
    app = web.create_app(serve_frontend=False)
    submit = next(r.endpoint for r in app.routes if getattr(r,"path",None) == "/api/runs")
    monkeypatch.setattr(web.STATE.run_manager, "status", lambda: RunStatus("running"))
    with pytest.raises(HTTPException) as error:
        submit(RunRequest())
    assert error.value.status_code == 409
    monkeypatch.setattr(web.STATE.run_manager, "status", lambda: RunStatus("idle"))
    monkeypatch.setattr(runtime_paths, "ipopt_executable_path", lambda: tmp_path / "missing.exe")
    with pytest.raises(HTTPException) as error:
        submit(RunRequest(skip_opf=False))
    assert error.value.status_code == 503
    imported = web.import_network_payload({"ac_text": (ROOT / "examples/two_bus_ac.py").read_text()})
    monkeypatch.setattr(web, "STATE", web.WebDashboardState())
    with pytest.raises(ValueError, match="Import the files again"):
        web.resolve_dashboard_case(imported["import_id"])


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_separate_backend_frontend_import_edit_pf_exports(tmp_path):
    port, front, proxy_port = free_port(), free_port(), free_port()
    origin = f"http://127.0.0.1:{front}"
    base = f"http://127.0.0.1:{proxy_port}/tools/tool1"
    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            self.forward()
        def do_POST(self):
            self.forward()
        def do_OPTIONS(self):
            self.forward()
        def log_message(self, *args):
            pass
        def forward(self):
            assert self.path.startswith("/tools/tool1/")
            path = self.path[len("/tools/tool1"):]
            length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(length) if length else None
            request = Request(f"http://127.0.0.1:{port}" + path, data=data, method=self.command,
                headers={k:v for k,v in self.headers.items() if k.lower() not in {"host", "content-length"}})
            try:
                response = urlopen(request, timeout=30)
            except HTTPError as exc:
                response = exc
            except URLError:
                self.send_error(503)
                return
            with response:
                body = response.read()
                self.send_response(response.status)
                for k,v in response.headers.items():
                    if k.lower() not in {"transfer-encoding", "content-length", "connection"}:
                        self.send_header(k,v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
    proxy = ThreadingHTTPServer(("127.0.0.1",proxy_port),Proxy)
    threading.Thread(target=proxy.serve_forever,daemon=True).start()
    env = dict(os.environ, TOOL1_REPORT_DIR=str(tmp_path / "reports"))
    backend_log = (tmp_path / "backend.log").open("w")
    backend = subprocess.Popen([sys.executable, "-m", "acdcopf", "backend", "--port", str(port), "--root-path", "/tools/tool1", "--cors-origin", origin], env=env, stdout=backend_log, stderr=backend_log)
    frontend = subprocess.Popen([sys.executable, "-m", "acdcopf", "frontend", "--port", str(front), "--prefix", "/demo/tool1", "--backend-url", base], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def call(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(base + path, data=data, headers={"Content-Type": "application/json", "Origin": origin})
        with urlopen(request, timeout=30) as response:
            assert response.headers["Access-Control-Allow-Origin"] == origin
            return json.load(response)
    try:
        for _ in range(100):
            try:
                health = call("/api/health")
                break
            except URLError:
                time.sleep(.1)
        else:
            pytest.fail("Backend did not start")
        assert health["power_flow"]["version"]
        schema = call("/openapi.json")
        assert "RunRequest" in schema["components"]["schemas"]
        preflight = Request(base + "/api/runs", method="OPTIONS", headers={"Origin":origin,"Access-Control-Request-Method":"POST","Access-Control-Request-Headers":"Content-Type"})
        with urlopen(preflight) as response:
            assert response.headers["Access-Control-Allow-Origin"] == origin
        with urlopen(origin + "/demo/tool1/config.js") as response:
            assert base in response.read().decode()
        with urlopen(origin + "/demo/tool1/") as response:
            html = response.read().decode()
            assert "Tool1 (acdcopf)" in html and 'src="config.js"' in html
        with pytest.raises(HTTPError) as error:
            call("/api/runs", {"skip_opf": "false"})
        assert error.value.code == 422
        assert json.load(error.value)["error"]["code"] == "validation_error"
        with pytest.raises(HTTPError) as error:
            call("/api/network-inputs/custom_expired/original")
        assert error.value.code == 400
        assert "Import" in json.load(error.value)["detail"]
        imported = call("/api/networks/import", {"ac_name": "two_bus_ac.py", "ac_text": (ROOT / "examples/two_bus_ac.py").read_text()})
        key = imported["import_id"]
        tables = call(f"/api/network-inputs/{key}/original")["tables"]
        row = tables["ac_load"]["rows"][0]
        assert row["source_bus_id"] == 40
        row["p_mw"] = .4
        payload = {"grid_case": key, "skip_opf": True, "table_overrides": {"ac_load": [row]}}
        call("/api/runs", payload)
        for _ in range(100):
            status = call("/api/runs/current")
            if status["state"] not in {"running", "queued"}:
                break
            time.sleep(.1)
        assert status["state"] == "succeeded", status
        assert status["result"]["runs"][0]["display_label"] == "Tool5 (acdcpf) PF"
        assert status["result"]["downloads"]
        for item in status["result"]["downloads"]:
            with urlopen(base + item["url"]) as response:
                assert response.read()
            assert item["name"].startswith("tool1_")
        assert call(f"/api/network-inputs/{key}/original")["tables"]["ac_load"]["rows"][0]["p_mw"] == .5
        private = ROOT / "inputs"
        if (private / "case33h_ieee_ac.py").exists():
            imported = call("/api/networks/import", {"ac_text": (private / "case33h_ieee_ac.py").read_text(), "dc_text": (private / "case33h_ieee_dc.py").read_text(), "format":"tool7", "loss_units":"per_unit"})
            key = imported["import_id"]
            rows = call(f"/api/network-inputs/{key}/original")["tables"]["dc_gen"]["rows"]
            row = next(r for r in rows if r["source_bus_id"] == 35)
            row["p_mw"] = .4
            call("/api/runs", {"grid_case":key,"skip_opf":True,"table_overrides":{"dc_gen":[row]}})
            for _ in range(100):
                status = call("/api/runs/current")
                if status["state"] not in {"running","queued"}:
                    break
                time.sleep(.1)
            assert status["state"] == "succeeded", status
            assert status["result"]["losses"]["pf_mw"] == pytest.approx(.4021643316194496)
    finally:
        frontend.terminate()
        backend.terminate()
        frontend.wait(timeout=20)
        backend.wait(timeout=20)
        backend_log.close()
        proxy.shutdown()
        proxy.server_close()
