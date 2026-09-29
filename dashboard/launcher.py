from __future__ import annotations

import sys


def main() -> None:
    """Launch the local custom dashboard from the installed console script."""

    from acdcpf_opf import start_dashboard

    raise SystemExit(start_dashboard.main(sys.argv[1:]))


if __name__ == "__main__":
    main()
