"""Module equivalents of the installed Tool1 commands."""
import sys

command = sys.argv.pop(1) if len(sys.argv) > 1 else "help"
if command == "frontend":
    from .frontend import main
elif command in {"backend", "dashboard", "run"}:
    from . import cli
    main = getattr(cli, command)
elif command == "doctor":
    from acdcpf_opf.install_check import main
else:
    raise SystemExit("Usage: python -m acdcopf {backend|frontend|dashboard|run|doctor} [options]")
raise SystemExit(main())
