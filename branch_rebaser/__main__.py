from __future__ import annotations

import argparse
from pathlib import Path

from .tui import BranchRebaserApp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="branch-rebaser",
        description="Safely rebase selected local branches onto a chosen primary branch.",
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Git working tree to manage. Defaults to the current directory.",
    )
    args = parser.parse_args()

    app = BranchRebaserApp(Path(args.repo).expanduser())
    app.run()


if __name__ == "__main__":
    main()

