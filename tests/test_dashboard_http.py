"""Installed dashboard smoke test over localhost, without a browser dependency."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

from acdcpf_opf.runtime_paths import ipopt_executable_path


def test_dashboard_http_run_and_exports(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    if not ipopt_executable_path().is_file():
        pytest.skip("IPOPT is required for the dashboard solve smoke test")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = dict(os.environ, TOOL1_REPORT_DIR=str(tmp_path / "reports"))
    environment.pop("PYTHONPATH", None)
    url = f"http://127.0.0.1:{port}"

    def get(path):
        with urlopen(url + path, timeout=10) as response:
            return response.read()

    # Write logs to a file so a noisy solver cannot block on a full pipe.
    with (tmp_path / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "acdcpf_opf.dashboard.web:app",
             "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
            cwd=tmp_path, env=environment, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, "Dashboard server exited; inspect server.log"
                try:
                    config = json.loads(get("/api/config"))
                    break
                except URLError:
                    assert time.monotonic() < deadline, "Dashboard startup timed out"
                    time.sleep(0.1)
            assert any(case["key"] == "original" for case in config["cases"])
            assert b"Tool1 (acdcopf)" in get("/")
            assert get("/static/dashboard.js")
            inputs = json.loads(get("/api/network-inputs/original/original"))
            assert inputs["editable"]
            assert inputs["tables"]["ac_load"]["rows"]
            payload = json.dumps({"grid_case": "original", "include_pyflow_reference": False}).encode()
            request = Request(url + "/api/runs", data=payload, headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=10) as response:
                assert response.status == 200
            deadline = time.monotonic() + 90
            while True:
                state = json.loads(get("/api/runs/current"))
                if state["state"] in {"succeeded", "failed"}:
                    break
                assert time.monotonic() < deadline, "Dashboard solve timed out"
                time.sleep(0.1)
            assert state["state"] == "succeeded", state
            result = state["result"]
            assert result["losses"]["opf_mw"] <= result["losses"]["pf_mw"] + 1e-5
            assert len(result["exports"]["csv"]) == 2
            assert len(result["exports"]["xlsx"]) == 2
            for paths in result["exports"].values():
                for path in paths if isinstance(paths, list) else [paths]:
                    assert Path(path).is_relative_to(tmp_path)
                    assert Path(path).stat().st_size > 0
            assert b"<html" in get("/api/runs/current/grid-html").lower()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
