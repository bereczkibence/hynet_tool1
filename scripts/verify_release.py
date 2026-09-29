"""Audit local release contents and test the frontend without site-packages."""
from pathlib import Path
import hashlib
import json
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import urlopen
from urllib.error import URLError
from zipfile import ZipFile

root = Path(__file__).resolve().parents[1]
release = root / "release"
for path in release.iterdir():
    if path.suffix in {".whl", ".zip"}:
        with ZipFile(path) as archive:
            entries = [(name, archive.read(name)) for name in archive.namelist() if not name.endswith("/")]
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path) as archive:
            entries = [(member.name, archive.extractfile(member).read()) for member in archive.getmembers() if member.isfile()]
    else:
        continue
    for name, content in entries:
        assert not set(Path(name).parts).intersection({"inputs", ".venv", ".git", "__pycache__", ".validation", ".pytest_cache"}), name
        assert not name.endswith((".log", ".pyc")), name
        text = content.decode("utf-8", errors="replace")
        assert not re.search(r"C:\\+Users\\+(?:berec|bence)", text, re.I), name
    print("Audited", path.name, len(entries), "files")

with tempfile.TemporaryDirectory(dir=root / ".validation") as temp:
    with ZipFile(release / "hynet_tool1-0.3.0-frontend.zip") as archive:
        archive.extractall(temp)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, "-S", str(Path(temp) / "serve.py"), "--port", str(port), "--prefix", "/demo/tool1", "--backend-url", "http://localhost:8510/tools/tool1"], cwd=temp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}/demo/tool1/"
        for _ in range(60):
            try:
                with urlopen(base) as response:
                    assert b"Tool1 (acdcopf)" in response.read()
                break
            except URLError:
                time.sleep(.1)
        else:
            raise RuntimeError("Standalone frontend failed to start")
        for asset in ("dashboard.js", "styles.css", "config.js"):
            with urlopen(base + asset) as response:
                assert response.read()
        print("Standalone prefixed frontend works with site-packages disabled.")
    finally:
        process.terminate()
        process.wait(timeout=10)
manifest = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in release.iterdir() if path.suffix in {".zip", ".whl"} or path.name.endswith(".tar.gz")}
(release / "SHA256SUMS.json").write_text(json.dumps(manifest, indent=2) + "\n")
