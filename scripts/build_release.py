"""Build local artifacts from an explicit source allowlist; never publish."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, str(root / "scripts/verify_engines.py")], check=True)
release = root / "release"
release.mkdir(exist_ok=True)
folders = {"acdcopf", "acdcpf_pyflow_backend", "benchmarks", "dashboard", "data", "docs", "examples", "experiments", "opf", "powerflow", "scripts", "tests"}
excluded = {"__pycache__", "reports", ".pytest_cache", ".git", ".venv", "dist"}
files = [p for p in root.rglob("*") if p.is_file() and
         not any(part in excluded or part.endswith(".egg-info") for part in p.relative_to(root).parts) and
         (len(p.relative_to(root).parts) == 1 or p.relative_to(root).parts[0] in folders) and
         p.suffix not in {".pyc", ".log", ".tmp"} and p.name != "source_checksums.json"]
with tempfile.TemporaryDirectory(dir=root, prefix=".release-stage-") as temp:
    stage = Path(temp)
    for path in files:
        target = stage / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    subprocess.run([sys.executable, "-m", "build", "--no-isolation", "--outdir", str(release), str(stage)], check=True)
    with ZipFile(release / "hynet_tool1-0.3.0-source.zip", "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, "hynet_tool1/" + path.relative_to(root).as_posix())
with ZipFile(release / "hynet_tool1-0.3.0-frontend.zip", "w", ZIP_DEFLATED) as archive:
    for path in (root / "dashboard/static").iterdir():
        if path.is_file():
            archive.write(path, path.name)
    archive.write(root / "acdcopf/frontend.py", "serve.py")
    archive.writestr("README.txt", "Tool1 demonstration frontend\nRun: python serve.py --backend-url http://127.0.0.1:8520\nOpen http://127.0.0.1:8521/\nFor static hosting edit config.js. No solver dependencies required.\n")
    archive.write(root / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")
print("Local artifacts created in", release)
