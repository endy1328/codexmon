"""CLI entrypoint for the codexmon baseline."""

from __future__ import annotations

import argparse
from pathlib import Path
import platform
import sys

from codexmon import __version__
from codexmon.config import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codexmon",
        description="Policy-first supervisor for unattended AI coding sessions.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("version", help="현재 codexmon 버전을 출력합니다.")
    subparsers.add_parser("doctor", help="개발 기준선 상태를 출력합니다.")

    return parser


def command_version() -> int:
    print(__version__)
    return 0


def command_doctor() -> int:
    settings = Settings.from_env()
    root = Path(__file__).resolve().parents[2]
    print(f"version={__version__}")
    print(f"python={platform.python_version()}")
    print(f"root={root}")
    print(f"db_path={settings.db_path}")
    print(f"log_level={settings.log_level}")
    print(f"github_repo={settings.github_owner}/{settings.github_repo}".rstrip("/"))
    print(f"telegram_chat_id={settings.telegram_chat_id or '<unset>'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        return command_version()
    if args.command == "doctor":
        return command_doctor()

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
