"""Portability regressions, also run against a non-editable installed wheel."""

from importlib import resources
from pathlib import Path
import sys
from types import SimpleNamespace

import acdcpf
import pytest

from acdcpf_opf import runtime_paths, start_dashboard
from acdcpf_pyflow_backend._bootstrap import ensure_acdcpf_importable


def test_backend_check_preserves_imported_module_and_search_path():
    original = sys.modules["acdcpf"]
    paths = list(sys.path)
    ensure_acdcpf_importable()
    assert sys.modules["acdcpf"] is original
    assert sys.path == paths
    assert hasattr(acdcpf, "create_storage")
    assert hasattr(acdcpf, "create_transformer")


def test_reports_override_does_not_create_files_on_import(tmp_path, monkeypatch):
    output = tmp_path / "new reports"
    monkeypatch.setenv("TOOL1_REPORT_DIR", str(output))
    assert runtime_paths.reports_directory() == output
    assert not output.exists()


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_default_reports_are_user_owned(tmp_path, monkeypatch, platform):
    monkeypatch.delenv("TOOL1_REPORT_DIR", raising=False)
    monkeypatch.setattr(runtime_paths.sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert runtime_paths.reports_directory().is_relative_to(tmp_path)
    assert runtime_paths.reports_directory().name == "reports"


def test_explicit_solver_path_never_silently_falls_back(tmp_path, monkeypatch):
    missing = tmp_path / "missing-ipopt"
    monkeypatch.setenv("TOOL1_IPOPT", str(missing))
    monkeypatch.setattr(runtime_paths.shutil, "which", lambda _: "another-solver")
    assert runtime_paths.ipopt_executable_path() == missing


def test_solver_from_path(tmp_path, monkeypatch):
    solver = tmp_path / "ipopt"
    monkeypatch.delenv("TOOL1_IPOPT", raising=False)
    monkeypatch.setattr(runtime_paths.shutil, "which", lambda _: str(solver))
    assert runtime_paths.ipopt_executable_path() == solver


def test_idaes_solver_discovery(tmp_path, monkeypatch):
    monkeypatch.delenv("TOOL1_IPOPT", raising=False)
    monkeypatch.setattr(runtime_paths.shutil, "which", lambda _: None)
    monkeypatch.setattr(runtime_paths, "user_data_directory", lambda: tmp_path / "app")
    monkeypatch.setitem(sys.modules, "idaes", SimpleNamespace(bin_directory=str(tmp_path)))
    solver = tmp_path / ("ipopt.exe" if sys.platform == "win32" else "ipopt")
    solver.touch()
    assert runtime_paths.ipopt_executable_path() == solver


def test_distribution_includes_profiles_and_dashboard_assets():
    profiles = resources.files("acdcpf_opf.benchmarks.stagg5").joinpath("profiles")
    assert profiles.joinpath("hybrid_dcdc_one_day_load_pv_profile.csv").is_file()
    assert profiles.joinpath("original_stagg5_load_profile.csv").is_file()
    static = resources.files("acdcpf_opf.dashboard").joinpath("static")
    for filename in ("index.html", "dashboard.js", "styles.css"):
        assert static.joinpath(filename).is_file()


def test_launcher_does_not_install_implicitly(monkeypatch, capsys):
    monkeypatch.setattr(start_dashboard, "_module_available", lambda _: False)
    monkeypatch.setattr(start_dashboard.subprocess, "call", lambda *a, **k: pytest.fail("Unexpected installer"))
    assert start_dashboard.main(["--no-browser"]) == 1
    assert "--install-only" in capsys.readouterr().out


def test_dashboard_app_imports_without_running_solver():
    pytest.importorskip("fastapi")
    from acdcpf_opf.dashboard.web import create_app

    app = create_app()
    assert any(getattr(route, "path", None) == "/api/config" for route in app.routes)
