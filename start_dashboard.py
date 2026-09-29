from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys
import time
import webbrowser


DASHBOARD_MODULES = ("fastapi", "uvicorn")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8520


def main(argv: list[str] | None = None) -> int:
    """Launch the local Tool1 (acdcopf) custom browser dashboard with minimal setup friction."""

    args = _parse_args(argv)
    project_root = Path(__file__).resolve().parent
    python = _selected_python(project_root, args.python)
    if Path(sys.executable).resolve() != python.resolve():
        return subprocess.call([
            str(python), "-m", "acdcpf_opf.start_dashboard",
            *(sys.argv[1:] if argv is None else argv),
        ])

    missing_modules = [module for module in DASHBOARD_MODULES if not _module_available(module)]
    if missing_modules:
        if args.no_install or not args.install_only:
            print(_missing_dashboard_message(python, project_root, missing_modules))
            return 1
        print(
            "Dashboard dependencies are not installed in the selected Python environment: "
            + ", ".join(missing_modules)
        )
        print("Installing Tool1 (acdcopf) dashboard dependencies now...")
        install_code = subprocess.call(
            [str(python), "-m", "pip", "install", "fastapi>=0.115",
             "uvicorn[standard]>=0.30", "openpyxl>=3.1"],
        )
        if install_code != 0:
            print("")
            print("Automatic dashboard dependency installation failed.")
            print(_missing_dashboard_message(python, project_root, missing_modules))
            return install_code

    if args.install_only:
        print("Dashboard dependencies are installed.")
        return 0

    host = args.host
    port = args.port
    url = f"http://{host}:{port}"
    command = _uvicorn_command(python, project_root, host=host, port=port)
    print("Starting Tool1 (acdcopf) custom dashboard...")
    print(f"Using Python: {python}")
    print(f"Local URL: {url}")
    print("Keep this terminal window open while using the dashboard.")
    process = subprocess.Popen(command)
    try:
        time.sleep(1.2)
        if process.poll() is None and not args.no_browser:
            webbrowser.open(url)
        return process.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the local Tool1 (acdcopf) dashboard.")
    parser.add_argument(
        "--python",
        type=Path,
        default=None,
        help="Optional Python interpreter to use instead of the current environment.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Dashboard server port. Default: {DEFAULT_PORT}.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Dashboard bind host. Default: {DEFAULT_HOST}.",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Never install dependencies (the default); overrides --install-only.",
    )
    parser.add_argument(
        "--install-only",
        action="store_true",
        help="Install dashboard dependencies and exit without launching the server.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the local server without opening the browser automatically.",
    )
    return parser.parse_args(argv)


def _selected_python(project_root: Path, explicit_python: Path | None) -> Path:
    if explicit_python is not None:
        return explicit_python.expanduser().resolve()
    return Path(sys.executable).resolve()


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _uvicorn_command(python: Path, project_root: Path, *, host: str, port: int) -> list[str]:
    return [
        str(python),
        "-m",
        "uvicorn",
        "acdcpf_opf.dashboard.web:app",
        "--host",
        host,
        "--port",
        str(port),
    ]


def _missing_dashboard_message(
    python: Path,
    project_root: Path,
    missing_modules: list[str],
) -> str:
    return "\n".join(
        [
            "FastAPI/Uvicorn are required for the Tool1 (acdcopf) custom dashboard.",
            f"Missing modules: {', '.join(missing_modules)}",
            "Install it with:",
            f'  "{python}" -m acdcpf_opf.start_dashboard --install-only',
            "",
            "Then launch again with:",
            f'  "{python}" -m acdcpf_opf.start_dashboard',
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
