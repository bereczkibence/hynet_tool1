"""Product naming stays consistent across requests, reports, and launchers."""
from pathlib import Path
from zipfile import ZipFile

from acdcopf import BenchmarkRequest, run_benchmark_request
from acdcpf_opf.dashboard.schemas import RunRequest


def test_public_pf_only_option():
    assert RunRequest(skip_opf=True).skip_opf
    assert BenchmarkRequest(skip_opf=True).skip_opf
    assert not any("bme" in name.lower() for name in RunRequest.model_json_schema()["properties"])


def test_report_branding_including_excel_metadata(tmp_path):
    result = run_benchmark_request(BenchmarkRequest(
        skip_opf=True, include_pyflow_reference=False, output_directory=tmp_path))
    assert result.success
    for path in [result.markdown_path, result.html_path, *result.csv_paths, *result.xlsx_paths]:
        assert "bme" not in path.name.lower()
        if path.suffix == ".xlsx":
            with ZipFile(path) as archive:
                texts = [archive.read(name).decode("utf-8") for name in archive.namelist() if name.endswith(".xml")]
        else:
            texts = [path.read_text(encoding="utf-8-sig")]
        assert all("bme" not in text.lower() for text in texts)
    assert "Tool1" in result.markdown


def test_public_launchers_and_metadata():
    root = Path(__file__).resolve().parents[1]
    assert not any("bme" in p.name.lower() for p in root.glob("*.bat"))
    assert "bme" not in (root / "pyproject.toml").read_text().lower()
