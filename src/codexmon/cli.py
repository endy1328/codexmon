"""CLI entrypoint for the codexmon baseline."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import platform
import sys

from codexmon import __version__
from codexmon.codex_adapter import CodexAdapter, CodexAdapterError
from codexmon.config import Settings
from codexmon.failure_policy import FailurePolicyResult, FailurePolicySettings, FailureSignalController
from codexmon.ledger import LedgerError, RecordNotFoundError, RunLedger
from codexmon.telegram_notifier import (
    TelegramBotApiTransport,
    TelegramNotifier,
    TelegramNotifierError,
)
from codexmon.workspace import WorktreeAllocator, WorkspaceError, dumps_diagnostic


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codexmon",
        description="Policy-first supervisor for unattended AI coding sessions.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("version", help="현재 codexmon 버전을 출력합니다.")
    subparsers.add_parser("doctor", help="개발 기준선 상태를 출력합니다.")
    start_parser = subparsers.add_parser("start", help="synthetic run record를 생성합니다.")
    start_parser.add_argument("instruction_summary", help="작업 요약 또는 지시문을 기록합니다.")
    start_parser.add_argument("--task-id", help="기존 task 식별자 대신 사용할 ID")
    start_parser.add_argument("--run-id", help="run 식별자를 직접 지정합니다.")
    start_parser.add_argument("--repo-owner", help="task가 속한 GitHub owner")
    start_parser.add_argument("--repo-name", help="task가 속한 GitHub repository")
    start_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    status_parser = subparsers.add_parser("status", help="run ledger 상태를 조회합니다.")
    status_parser.add_argument("run_id", nargs="?", help="조회할 run ID")
    status_parser.add_argument("--limit", type=int, default=10, help="최근 run 조회 개수")
    status_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    workspace_parser = subparsers.add_parser("workspace", help="worktree allocator 경로를 점검합니다.")
    workspace_subparsers = workspace_parser.add_subparsers(dest="workspace_command")

    allocate_parser = workspace_subparsers.add_parser("allocate", help="run 전용 worktree를 할당합니다.")
    allocate_parser.add_argument("run_id", help="worktree를 할당할 run ID")
    allocate_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")

    release_parser = workspace_subparsers.add_parser("release", help="repository lock을 반납합니다.")
    release_parser.add_argument("run_id", help="lock을 반납할 run ID")
    release_parser.add_argument(
        "--cleanup",
        action="store_true",
        help="linked worktree를 제거하고 released_at을 기록합니다.",
    )
    release_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")

    diagnose_parser = workspace_subparsers.add_parser("diagnose", help="lock/worktree 상태를 출력합니다.")
    diagnose_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")

    runner_parser = subparsers.add_parser("runner", help="Codex adapter 경로를 점검합니다.")
    runner_subparsers = runner_parser.add_subparsers(dest="runner_command")
    run_parser = runner_subparsers.add_parser("run", help="할당된 worktree 안에서 Codex를 실행합니다.")
    run_parser.add_argument("run_id", help="실행할 run ID")
    run_parser.add_argument("instruction", help="Codex에 전달할 지시문")
    run_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    supervise_parser = runner_subparsers.add_parser(
        "supervise",
        help="adapter 실행 뒤 timeout/fingerprint/retry 정책까지 적용합니다.",
    )
    supervise_parser.add_argument("run_id", help="실행할 run ID")
    supervise_parser.add_argument("instruction", help="Codex에 전달할 지시문")
    supervise_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")

    telegram_parser = subparsers.add_parser("telegram", help="Telegram notifier 경로를 점검합니다.")
    telegram_subparsers = telegram_parser.add_subparsers(dest="telegram_command")
    notify_parser = telegram_subparsers.add_parser("notify", help="현재 run summary를 Telegram으로 전송합니다.")
    notify_parser.add_argument("run_id", help="알림을 보낼 run ID")
    notify_parser.add_argument("--event-label", default="", help="메시지 상단 이벤트 라벨")
    notify_parser.add_argument("--chat-id", default="", help="기본 chat ID 대신 사용할 대상")
    notify_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    receive_parser = telegram_subparsers.add_parser(
        "receive",
        help="Telegram command text를 파싱하고 operator action을 적용합니다.",
    )
    receive_parser.add_argument("text", nargs="+", help="예: /status run_xxx")
    receive_parser.add_argument("--chat-id", default="", help="명령이 들어온 Telegram chat ID")
    receive_parser.add_argument("--operator", default="", help="operator 식별자")
    receive_parser.add_argument("--message-id", default="", help="원본 Telegram message ID")
    receive_parser.add_argument(
        "--no-reply",
        action="store_true",
        help="처리 결과를 Telegram으로 다시 보내지 않습니다.",
    )
    receive_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")

    return parser


def command_version() -> int:
    print(__version__)
    return 0


def command_doctor() -> int:
    settings = Settings.from_env()
    root = Path(__file__).resolve().parents[2]
    ledger = RunLedger(settings.db_path)
    ledger.initialize()
    print(f"version={__version__}")
    print(f"python={platform.python_version()}")
    print(f"root={root}")
    print(f"repo_path={settings.repo_path}")
    print(f"worktree_root={settings.worktree_root}")
    print(f"db_path={settings.db_path}")
    print(f"schema_version={ledger.schema_version()}")
    print(f"codex_command={settings.codex_command}")
    print(f"codex_sandbox={settings.codex_sandbox}")
    print(f"automatic_retry_budget={settings.automatic_retry_budget}")
    print(f"idle_timeout_seconds={settings.idle_timeout_seconds}")
    print(f"wall_clock_timeout_seconds={settings.wall_clock_timeout_seconds}")
    print(f"log_level={settings.log_level}")
    print(f"github_repo={settings.github_owner}/{settings.github_repo}".rstrip("/"))
    print(f"telegram_bot_token={'<set>' if settings.telegram_bot_token else '<unset>'}")
    print(f"telegram_api_base={settings.telegram_api_base}")
    print(f"telegram_chat_id={settings.telegram_chat_id or '<unset>'}")
    return 0


def command_start(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    repo_owner = args.repo_owner if args.repo_owner is not None else settings.github_owner
    repo_name = args.repo_name if args.repo_name is not None else settings.github_repo
    task = ledger.create_task(
        instruction_summary=args.instruction_summary,
        task_id=args.task_id,
        repo_owner=repo_owner,
        repo_name=repo_name,
    )
    run = ledger.create_run(
        task_id=task.task_id,
        run_id=args.run_id,
        instruction_summary=args.instruction_summary,
    )
    if args.json:
        print(json.dumps(asdict(run), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print_run_projection(run)
    return 0


def command_status(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    if args.run_id:
        run = ledger.get_run(args.run_id)
        if args.json:
            print(json.dumps(asdict(run), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_run_projection(run)
        return 0

    runs = ledger.list_runs(limit=args.limit)
    if args.json:
        print(json.dumps([asdict(run) for run in runs], ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not runs:
        print("no_runs=true")
        return 0
    for index, run in enumerate(runs):
        if index:
            print()
        print_run_projection(run)
    return 0


def command_workspace(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    allocator = WorktreeAllocator(
        ledger=ledger,
        repo_path=settings.repo_path,
        worktree_root=settings.worktree_root,
    )

    if args.workspace_command == "allocate":
        result = allocator.allocate(args.run_id)
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for key, value in asdict(result).items():
                print(f"{key}={value}")
        return 0

    if args.workspace_command == "release":
        result = allocator.release(args.run_id, cleanup=args.cleanup)
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for key, value in asdict(result).items():
                print(f"{key}={value}")
        return 0

    if args.workspace_command == "diagnose":
        diagnostic = allocator.diagnose()
        if args.json:
            print(dumps_diagnostic(diagnostic))
        else:
            print(f"repo_root={diagnostic['repo_root']}")
            print(f"worktree_root={diagnostic['worktree_root']}")
            print(f"locks={len(diagnostic['locks'])}")
            print(f"workspace_assignments={len(diagnostic['workspace_assignments'])}")
            print(f"git_worktrees={len(diagnostic['git_worktrees'])}")
        return 0

    raise WorkspaceError("workspace 하위 명령이 필요합니다.")


def command_runner(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    adapter = CodexAdapter(
        ledger=ledger,
        codex_command=settings.codex_command,
        model=settings.codex_model,
        sandbox_mode=settings.codex_sandbox,
    )

    if args.runner_command == "run":
        result = adapter.execute_run(args.run_id, args.instruction)
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for key, value in asdict(result).items():
                print(f"{key}={value}")
        return 0

    if args.runner_command == "supervise":
        controller = FailureSignalController(
            ledger=ledger,
            adapter=adapter,
            settings=FailurePolicySettings(
                automatic_retry_budget=settings.automatic_retry_budget,
                idle_timeout_seconds=settings.idle_timeout_seconds,
                wall_clock_timeout_seconds=settings.wall_clock_timeout_seconds,
            ),
        )
        result: FailurePolicyResult = controller.execute(args.run_id, args.instruction)
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for key, value in asdict(result).items():
                print(f"{key}={value}")
        return 0

    raise CodexAdapterError("runner 하위 명령이 필요합니다.")


def build_telegram_notifier(settings: Settings, ledger: RunLedger) -> TelegramNotifier:
    transport = None
    if settings.telegram_bot_token:
        transport = TelegramBotApiTransport(
            bot_token=settings.telegram_bot_token,
            api_base=settings.telegram_api_base,
        )
    return TelegramNotifier(
        ledger=ledger,
        transport=transport,
        default_chat_id=settings.telegram_chat_id,
    )


def command_telegram(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    notifier = build_telegram_notifier(settings, ledger)

    if args.telegram_command == "notify":
        result = notifier.notify_run(
            run_id=args.run_id,
            event_label=args.event_label,
            chat_id=args.chat_id,
        )
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for key, value in asdict(result).items():
                print(f"{key}={value}")
        return 0

    if args.telegram_command == "receive":
        result = notifier.process_inbound_text(
            text=" ".join(args.text),
            operator_id=args.operator or f"telegram:{args.chat_id or 'unknown'}",
            chat_id=args.chat_id,
            message_id=args.message_id,
            send_reply=not args.no_reply,
        )
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for key, value in asdict(result).items():
                print(f"{key}={value}")
        return 0 if result.accepted else 1

    raise TelegramNotifierError("telegram 하위 명령이 필요합니다.")


def print_run_projection(run: object) -> None:
    data = asdict(run)
    ordered_keys = [
        "run_id",
        "task_id",
        "instruction_summary",
        "current_state",
        "state_reason",
        "outcome",
        "attempt_number",
        "active_worktree",
        "active_branch",
        "last_failure_fingerprint",
        "approval_status",
        "pr_reference",
        "created_at",
        "updated_at",
    ]
    for key in ordered_keys:
        value = data.get(key, "")
        if value == "":
            value = "<unset>"
        print(f"{key}={value}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "version":
            return command_version()
        if args.command == "doctor":
            return command_doctor()
        if args.command == "start":
            return command_start(args)
        if args.command == "status":
            return command_status(args)
        if args.command == "workspace":
            return command_workspace(args)
        if args.command == "runner":
            return command_runner(args)
        if args.command == "telegram":
            return command_telegram(args)
    except (
        LedgerError,
        RecordNotFoundError,
        WorkspaceError,
        CodexAdapterError,
        TelegramNotifierError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
