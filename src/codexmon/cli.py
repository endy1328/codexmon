"""CLI entrypoint for the codexmon baseline."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import platform
import sys

from codexmon import __version__
from codexmon.approval_policy import ApprovalPolicyError, ApprovalPolicyService
from codexmon.codex_adapter import CodexAdapter, CodexAdapterError
from codexmon.config import Settings
from codexmon.daemon_runtime import SupervisorDaemon
from codexmon.failure_policy import FailurePolicyResult, FailurePolicySettings, FailureSignalController
from codexmon.ledger import LedgerError, RecordNotFoundError, RunLedger
from codexmon.orchestrator import OrchestratorError, SupervisorRuntime
from codexmon.pr_handoff import GitHubApiClient, PRHandoffError, PRHandoffService
from codexmon.progress_monitor import ProgressMonitorError, ProgressMonitorService
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
    start_parser.add_argument(
        "--execute",
        action="store_true",
        help="run record 생성 직후 supervisor runtime으로 끝까지 실행합니다.",
    )
    start_parser.add_argument("--chat-id", default="", help="runtime 알림에 사용할 Telegram chat ID")
    start_parser.add_argument(
        "--residual-risk-note",
        default="",
        help="PR handoff 또는 runtime 완료 시 남길 residual risk note",
    )
    start_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    execute_parser = subparsers.add_parser("execute", help="기존 run을 supervisor runtime으로 재개합니다.")
    execute_parser.add_argument("run_id", help="실행 또는 재개할 run ID")
    execute_parser.add_argument("--instruction", default="", help="run instruction override")
    execute_parser.add_argument("--chat-id", default="", help="runtime 알림에 사용할 Telegram chat ID")
    execute_parser.add_argument(
        "--residual-risk-note",
        default="",
        help="PR handoff 또는 runtime 완료 시 남길 residual risk note",
    )
    execute_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    daemon_parser = subparsers.add_parser("daemon", help="background runtime worker를 실행하거나 상태를 봅니다.")
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command")
    daemon_run_once_parser = daemon_subparsers.add_parser(
        "run-once",
        help="runnable run 하나를 선택해 한 번만 처리합니다.",
    )
    daemon_run_once_parser.add_argument("--chat-id", default="", help="알림에 사용할 Telegram chat ID")
    daemon_run_once_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    daemon_serve_parser = daemon_subparsers.add_parser(
        "serve",
        help="polling loop로 background runtime worker를 실행합니다.",
    )
    daemon_serve_parser.add_argument("--chat-id", default="", help="알림에 사용할 Telegram chat ID")
    daemon_serve_parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.0,
        help="poll 간격 초 단위 override",
    )
    daemon_serve_parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="0이면 무한 루프, 양수면 해당 횟수만큼 tick 합니다.",
    )
    daemon_serve_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    daemon_status_parser = daemon_subparsers.add_parser(
        "status",
        help="최근 daemon heartbeat를 조회합니다.",
    )
    daemon_status_parser.add_argument("--limit", type=int, default=20, help="heartbeat 조회 개수")
    daemon_status_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    monitor_parser = subparsers.add_parser("monitor", help="live progress monitor를 조회하거나 서빙합니다.")
    monitor_subparsers = monitor_parser.add_subparsers(dest="monitor_command")
    monitor_snapshot_parser = monitor_subparsers.add_parser(
        "snapshot",
        help="DB 기준 live progress snapshot을 출력합니다.",
    )
    monitor_snapshot_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    monitor_serve_parser = monitor_subparsers.add_parser(
        "serve",
        help="progress monitor HTML과 live API를 함께 서빙합니다.",
    )
    monitor_serve_parser.add_argument("--host", default="127.0.0.1", help="bind host")
    monitor_serve_parser.add_argument("--port", type=int, default=8765, help="bind port")
    monitor_serve_parser.add_argument("--json", action="store_true", help="서버 정보만 JSON으로 출력합니다.")
    status_parser = subparsers.add_parser("status", help="run ledger 상태를 조회합니다.")
    status_parser.add_argument("run_id", nargs="?", help="조회할 run ID")
    status_parser.add_argument("--limit", type=int, default=10, help="최근 run 조회 개수")
    status_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    stop_parser = subparsers.add_parser("stop", help="로컬 kill-switch로 run을 중단합니다.")
    stop_parser.add_argument("run_id", help="중단할 run ID")
    stop_parser.add_argument("--operator", default="local-operator", help="operator 식별자")
    stop_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    retry_parser = subparsers.add_parser("retry", help="로컬 operator retry를 요청합니다.")
    retry_parser.add_argument("run_id", help="재시도할 run ID")
    retry_parser.add_argument("--operator", default="local-operator", help="operator 식별자")
    retry_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    approvals_parser = subparsers.add_parser("approvals", help="approval request를 조회하거나 해결합니다.")
    approvals_subparsers = approvals_parser.add_subparsers(dest="approvals_command")
    approvals_list_parser = approvals_subparsers.add_parser("list", help="run의 approval 목록을 조회합니다.")
    approvals_list_parser.add_argument("run_id", help="조회할 run ID")
    approvals_list_parser.add_argument("--status", default="", help="필터할 approval status")
    approvals_list_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    approvals_scan_parser = approvals_subparsers.add_parser(
        "scan",
        help="approval-required diff classification을 수행합니다.",
    )
    approvals_scan_parser.add_argument("run_id", help="스캔할 run ID")
    approvals_scan_parser.add_argument("--base-branch", default="", help="비교할 base branch")
    approvals_scan_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
    approvals_approve_parser = approvals_subparsers.add_parser(
        "approve",
        help="pending approval을 approved로 해결하고 run을 재개합니다.",
    )
    approvals_approve_parser.add_argument("run_id", help="approval을 해결할 run ID")
    approvals_approve_parser.add_argument("--approval-request-id", default="", help="특정 approval request ID")
    approvals_approve_parser.add_argument("--operator", default="local-operator", help="operator 식별자")
    approvals_approve_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")
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

    handoff_parser = subparsers.add_parser("handoff", help="PR handoff success path를 실행합니다.")
    handoff_parser.add_argument("run_id", help="PR handoff를 수행할 run ID")
    handoff_parser.add_argument("--title", default="", help="PR 제목 override")
    handoff_parser.add_argument("--base-branch", default="", help="기본 base branch override")
    handoff_parser.add_argument(
        "--residual-risk-note",
        default="",
        help="PR 본문에 남길 residual risk note",
    )
    handoff_parser.add_argument("--json", action="store_true", help="JSON으로 출력합니다.")

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
    print(f"github_token={'<set>' if settings.github_token else '<unset>'}")
    print(f"github_api_base={settings.github_api_base}")
    print(f"github_base_branch={settings.github_base_branch}")
    print(f"local_check_command={settings.local_check_command or '<unset>'}")
    print(f"daemon_worker_name={settings.daemon_worker_name}")
    print(f"daemon_poll_interval_seconds={settings.daemon_poll_interval_seconds}")
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
    if args.execute:
        runtime = build_supervisor_runtime(settings, ledger)
        result = runtime.execute_run(
            run_id=run.run_id,
            instruction=args.instruction_summary,
            residual_risk_note=args.residual_risk_note,
            chat_id=args.chat_id,
        )
        _print_mapping(result, args.json)
        return 0 if result.final_state in {"completed", "awaiting_human"} else 1
    if args.json:
        print(json.dumps(asdict(run), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print_run_projection(run)
    return 0


def command_execute(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    runtime = build_supervisor_runtime(settings, ledger)
    result = runtime.execute_run(
        run_id=args.run_id,
        instruction=args.instruction,
        residual_risk_note=args.residual_risk_note,
        chat_id=args.chat_id,
    )
    _print_mapping(result, args.json)
    return 0 if result.final_state in {"completed", "awaiting_human"} else 1


def command_daemon(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    daemon = build_supervisor_daemon(settings, ledger)

    if args.daemon_command == "run-once":
        result = daemon.run_once(chat_id=args.chat_id)
        _print_mapping(result, args.json)
        return 0 if result.ok else 1

    if args.daemon_command == "serve":
        result = daemon.serve(
            chat_id=args.chat_id,
            iterations=args.iterations,
            poll_interval_seconds=args.poll_interval or None,
        )
        _print_mapping(result, args.json)
        return 0

    if args.daemon_command == "status":
        items = daemon.status(limit=args.limit)
        if args.json:
            print(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2, sort_keys=True))
        else:
            if not items:
                print("no_heartbeats=true")
                return 0
            for item in items:
                print(f"heartbeat_id={item.heartbeat_id}")
                print(f"worker_name={item.worker_name}")
                print(f"status={item.status}")
                print(f"event_time={item.event_time}")
                print(f"run_id={item.run_id or '<unset>'}")
                print(f"payload={json.dumps(item.payload, ensure_ascii=False, sort_keys=True)}")
                print()
        return 0

    raise OrchestratorError("daemon 하위 명령이 필요합니다.")


def build_progress_monitor_service(settings: Settings, ledger: RunLedger) -> ProgressMonitorService:
    return ProgressMonitorService(
        ledger=ledger,
        worker_name=settings.daemon_worker_name,
    )


def command_monitor(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    service = build_progress_monitor_service(settings, ledger)

    if args.monitor_command == "snapshot":
        snapshot = service.build_snapshot()
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"updated_at={snapshot['meta']['updatedAt']}")
            print(f"execution_status={snapshot['runtime']['executionStatus']}")
            print(f"current_focus={snapshot['meta']['currentFocus']}")
            print(f"current_state={snapshot['summary']['currentState']}")
            print(f"next_checkpoint={snapshot['summary']['nextCheckpoint']}")
            print(f"active_agents={len(snapshot['runtime'].get('activeAgents', []))}")
        return 0

    if args.monitor_command == "serve":
        server, info = service.create_server(host=args.host, port=args.port)
        try:
            if args.json:
                print(json.dumps(asdict(info), ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"url={info.url}")
                print(f"html_asset={service.html_asset_path()}")
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    raise ProgressMonitorError("monitor 하위 명령이 필요합니다.")


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


def _print_mapping(data: object, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(asdict(data), ensure_ascii=False, indent=2, sort_keys=True))
        return
    for key, value in asdict(data).items():
        print(f"{key}={value}")


def command_stop(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    notifier = build_telegram_notifier(settings, ledger)
    result = notifier.process_inbound_text(
        text=f"/stop {args.run_id}",
        operator_id=args.operator,
        send_reply=False,
    )
    _print_mapping(result, args.json)
    return 0 if result.accepted else 1


def command_retry(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    notifier = build_telegram_notifier(settings, ledger)
    result = notifier.process_inbound_text(
        text=f"/retry {args.run_id}",
        operator_id=args.operator,
        send_reply=False,
    )
    _print_mapping(result, args.json)
    return 0 if result.accepted else 1


def command_approvals(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    if args.approvals_command == "scan":
        service = ApprovalPolicyService(ledger=ledger, default_base_branch=settings.github_base_branch)
        result = service.scan(args.run_id, base_branch=args.base_branch)
        _print_mapping(result, args.json)
        return 0

    if args.approvals_command == "list":
        approvals = ledger.list_approvals(args.run_id, status=args.status or None)
        if args.json:
            print(json.dumps([asdict(item) for item in approvals], ensure_ascii=False, indent=2, sort_keys=True))
        else:
            if not approvals:
                print("no_approvals=true")
                return 0
            for approval in approvals:
                print(f"approval_request_id={approval.approval_request_id}")
                print(f"status={approval.status}")
                print(f"requested_by={approval.requested_by or '<unset>'}")
                print(f"resolved_by={approval.resolved_by or '<unset>'}")
                print(f"requested_at={approval.requested_at}")
                print(f"resolved_at={approval.resolved_at or '<unset>'}")
                print(f"decision_note={approval.decision_note or '<unset>'}")
                print()
        return 0

    if args.approvals_command == "approve":
        notifier = build_telegram_notifier(settings, ledger)
        text = f"/approve {args.run_id}"
        if args.approval_request_id:
            text += f" {args.approval_request_id}"
        result = notifier.process_inbound_text(
            text=text,
            operator_id=args.operator,
            send_reply=False,
        )
        _print_mapping(result, args.json)
        return 0 if result.accepted else 1

    raise TelegramNotifierError("approvals 하위 명령이 필요합니다.")


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
        _print_mapping(result, args.json)
        return 0

    if args.workspace_command == "release":
        result = allocator.release(args.run_id, cleanup=args.cleanup)
        _print_mapping(result, args.json)
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
        _print_mapping(result, args.json)
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
        _print_mapping(result, args.json)
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
        _print_mapping(result, args.json)
        return 0

    if args.telegram_command == "receive":
        result = notifier.process_inbound_text(
            text=" ".join(args.text),
            operator_id=args.operator or f"telegram:{args.chat_id or 'unknown'}",
            chat_id=args.chat_id,
            message_id=args.message_id,
            send_reply=not args.no_reply,
        )
        _print_mapping(result, args.json)
        return 0 if result.accepted else 1

    raise TelegramNotifierError("telegram 하위 명령이 필요합니다.")


def build_pr_handoff_service(settings: Settings, ledger: RunLedger) -> PRHandoffService:
    github_client = None
    if settings.github_token:
        github_client = GitHubApiClient(
            token=settings.github_token,
            api_base=settings.github_api_base,
        )
    return PRHandoffService(
        ledger=ledger,
        github_client=github_client,
        default_repo_owner=settings.github_owner,
        default_repo_name=settings.github_repo,
        default_base_branch=settings.github_base_branch,
        local_check_command=settings.local_check_command,
    )


def build_supervisor_runtime(settings: Settings, ledger: RunLedger) -> SupervisorRuntime:
    allocator = WorktreeAllocator(
        ledger=ledger,
        repo_path=settings.repo_path,
        worktree_root=settings.worktree_root,
    )
    adapter = CodexAdapter(
        ledger=ledger,
        codex_command=settings.codex_command,
        model=settings.codex_model,
        sandbox_mode=settings.codex_sandbox,
    )
    failure_controller = FailureSignalController(
        ledger=ledger,
        adapter=adapter,
        settings=FailurePolicySettings(
            automatic_retry_budget=settings.automatic_retry_budget,
            idle_timeout_seconds=settings.idle_timeout_seconds,
            wall_clock_timeout_seconds=settings.wall_clock_timeout_seconds,
        ),
    )
    approval_policy = ApprovalPolicyService(
        ledger=ledger,
        default_base_branch=settings.github_base_branch,
    )
    notifier = build_telegram_notifier(settings, ledger)
    handoff_service = build_pr_handoff_service(settings, ledger)
    return SupervisorRuntime(
        ledger=ledger,
        allocator=allocator,
        failure_controller=failure_controller,
        approval_policy=approval_policy,
        handoff_service=handoff_service,
        notifier=notifier,
    )


def build_supervisor_daemon(settings: Settings, ledger: RunLedger) -> SupervisorDaemon:
    runtime = build_supervisor_runtime(settings, ledger)
    return SupervisorDaemon(
        ledger=ledger,
        runtime=runtime,
        worker_name=settings.daemon_worker_name,
        poll_interval_seconds=settings.daemon_poll_interval_seconds,
    )


def command_handoff(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ledger = RunLedger(settings.db_path)
    service = build_pr_handoff_service(settings, ledger)
    result = service.execute(
        run_id=args.run_id,
        title=args.title,
        base_branch=args.base_branch,
        residual_risk_note=args.residual_risk_note,
    )
    _print_mapping(result, args.json)
    return 0 if result.final_state == "completed" else 1


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
        if args.command == "execute":
            return command_execute(args)
        if args.command == "daemon":
            return command_daemon(args)
        if args.command == "monitor":
            return command_monitor(args)
        if args.command == "status":
            return command_status(args)
        if args.command == "stop":
            return command_stop(args)
        if args.command == "retry":
            return command_retry(args)
        if args.command == "approvals":
            return command_approvals(args)
        if args.command == "workspace":
            return command_workspace(args)
        if args.command == "runner":
            return command_runner(args)
        if args.command == "telegram":
            return command_telegram(args)
        if args.command == "handoff":
            return command_handoff(args)
    except (
        LedgerError,
        RecordNotFoundError,
        WorkspaceError,
        CodexAdapterError,
        ApprovalPolicyError,
        OrchestratorError,
        ProgressMonitorError,
        TelegramNotifierError,
        PRHandoffError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
