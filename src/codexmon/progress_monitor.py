"""Live progress monitor snapshot builder and lightweight HTTP server."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from codexmon.ledger import RunLedger, RunProjection, RuntimeHeartbeatRecord
from codexmon.state_machine import TERMINAL_STATES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "agent-docs" / "status" / "progress.json"
DEFAULT_HTML_PATH = PROJECT_ROOT / "agent-docs" / "status" / "progress-monitor.html"

RUNNING_STATES = frozenset(
    {
        "queued",
        "preflight",
        "workspace_allocated",
        "running",
        "analyzing_failure",
        "pr_handoff",
    }
)
PAUSED_STATES = frozenset({"retry_pending", "awaiting_human"})
STATE_PROGRESS = {
    "queued": 5,
    "preflight": 12,
    "workspace_allocated": 22,
    "running": 58,
    "analyzing_failure": 72,
    "retry_pending": 46,
    "awaiting_human": 82,
    "pr_handoff": 92,
    "completed": 100,
    "halted": 100,
    "cancelled": 100,
}


class ProgressMonitorError(RuntimeError):
    """Raised when live monitor assets or state cannot be loaded."""


@dataclass(frozen=True)
class MonitorServerInfo:
    host: str
    port: int
    url: str


class ProgressMonitorService:
    """Build live monitor snapshots and expose them over a tiny HTTP server."""

    def __init__(
        self,
        ledger: RunLedger,
        worker_name: str,
        snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
        html_path: Path = DEFAULT_HTML_PATH,
    ) -> None:
        self.ledger = ledger
        self.worker_name = worker_name
        self.snapshot_path = snapshot_path
        self.html_path = html_path

    def build_snapshot(self) -> dict[str, Any]:
        base = self._load_base_snapshot()
        data = json.loads(json.dumps(base, ensure_ascii=False))
        runs = self.ledger.list_runs(limit=20)
        heartbeats = self.ledger.list_runtime_heartbeats(limit=20, worker_name=self.worker_name)
        latest_heartbeat = heartbeats[0] if heartbeats else None
        active_runs = [run for run in runs if run.current_state not in TERMINAL_STATES]
        runnable_runs = self.ledger.list_runnable_runs(limit=20)
        recoverable_runs = self.ledger.list_recoverable_runs(limit=20)
        pending_approval_runs = [run for run in active_runs if run.current_state == "awaiting_human"]
        execution_status = self._derive_execution_status(active_runs, latest_heartbeat)
        updated_at = self._resolve_updated_at(base, runs, latest_heartbeat)
        current_focus = self._build_current_focus(base, active_runs, latest_heartbeat)
        current_summary = self._build_current_summary(
            active_runs=active_runs,
            runnable_runs=runnable_runs,
            recoverable_runs=recoverable_runs,
            pending_approval_runs=pending_approval_runs,
            latest_heartbeat=latest_heartbeat,
        )

        data["meta"]["updatedAt"] = updated_at
        data["meta"]["currentFocus"] = current_focus
        data["meta"]["currentSummary"] = current_summary
        data["summary"]["currentState"] = self._build_current_state(
            execution_status=execution_status,
            active_runs=active_runs,
            pending_approval_runs=pending_approval_runs,
            latest_heartbeat=latest_heartbeat,
        )
        data["summary"]["nextCheckpoint"] = self._build_next_checkpoint(
            base_next_checkpoint=data["summary"].get("nextCheckpoint", ""),
            active_runs=active_runs,
            pending_approval_runs=pending_approval_runs,
            runnable_runs=runnable_runs,
            recoverable_runs=recoverable_runs,
        )
        data["runtime"] = {
            "executionStatus": execution_status,
            "summary": self._build_runtime_summary(
                execution_status=execution_status,
                active_runs=active_runs,
                pending_approval_runs=pending_approval_runs,
                latest_heartbeat=latest_heartbeat,
            ),
            "lastHeartbeatAt": latest_heartbeat.event_time if latest_heartbeat else updated_at,
            "heartbeatGraceSeconds": int(base.get("runtime", {}).get("heartbeatGraceSeconds", 60) or 60),
            "activeAgents": self._build_active_agents(
                active_runs=active_runs,
                latest_heartbeat=latest_heartbeat,
                execution_status=execution_status,
            ),
        }
        data["activityLog"] = self._build_activity_log(
            base_items=base.get("activityLog", []),
            runs=runs,
            heartbeats=heartbeats,
        )
        data["watchItems"] = self._build_watch_items(
            base_items=base.get("watchItems", []),
            pending_approval_runs=pending_approval_runs,
            recoverable_runs=recoverable_runs,
            latest_heartbeat=latest_heartbeat,
            execution_status=execution_status,
        )
        return data

    def create_server(self, host: str = "127.0.0.1", port: int = 8765) -> tuple[ThreadingHTTPServer, MonitorServerInfo]:
        service = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "codexmon-monitor/0.1"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                route = parsed.path or "/"
                if route in {"/", "/index.html", "/progress-monitor.html", "/agent-docs/status/progress-monitor.html"}:
                    self._send_file(service.html_path, "text/html; charset=utf-8")
                    return
                if route in {
                    "/api/progress",
                    "/progress.json",
                    "/agent-docs/status/api/progress",
                    "/agent-docs/status/progress.json",
                }:
                    payload = json.dumps(service.build_snapshot(), ensure_ascii=False, indent=2, sort_keys=True).encode(
                        "utf-8"
                    )
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if route == "/healthz":
                    payload = b'{"ok": true}'
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_error(HTTPStatus.NOT_FOUND, "route not found")

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                return

            def _send_file(self, path: Path, content_type: str) -> None:
                if not path.exists():
                    self.send_error(HTTPStatus.NOT_FOUND, f"missing asset: {path}")
                    return
                payload = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer((host, port), Handler)
        actual_host, actual_port = server.server_address[:2]
        info = MonitorServerInfo(
            host=str(actual_host),
            port=int(actual_port),
            url=f"http://{actual_host}:{actual_port}/",
        )
        return server, info

    def _load_base_snapshot(self) -> dict[str, Any]:
        if not self.snapshot_path.exists():
            raise ProgressMonitorError(f"monitor snapshot file not found: {self.snapshot_path}")
        return json.loads(self.snapshot_path.read_text(encoding="utf-8"))

    def _resolve_updated_at(
        self,
        base: dict[str, Any],
        runs: list[RunProjection],
        latest_heartbeat: RuntimeHeartbeatRecord | None,
    ) -> str:
        candidates = [base.get("meta", {}).get("updatedAt", "")]
        candidates.extend(run.updated_at for run in runs if run.updated_at)
        if latest_heartbeat:
            candidates.append(latest_heartbeat.event_time)
        valid = [value for value in candidates if value]
        if not valid:
            return datetime.now(timezone.utc).isoformat()
        return max(valid, key=_iso_key)

    def _derive_execution_status(
        self,
        active_runs: list[RunProjection],
        latest_heartbeat: RuntimeHeartbeatRecord | None,
    ) -> str:
        if any(run.current_state in RUNNING_STATES for run in active_runs):
            return "running"
        if any(run.current_state in PAUSED_STATES for run in active_runs):
            return "paused"
        if latest_heartbeat and latest_heartbeat.status == "stopped":
            return "stopped"
        return "idle"

    def _build_current_focus(
        self,
        base: dict[str, Any],
        active_runs: list[RunProjection],
        latest_heartbeat: RuntimeHeartbeatRecord | None,
    ) -> str:
        if active_runs:
            run = active_runs[0]
            return f"{run.run_id} · {run.current_state} · {run.instruction_summary}"
        if latest_heartbeat and latest_heartbeat.run_id:
            return f"{latest_heartbeat.run_id} · daemon {latest_heartbeat.status}"
        return base.get("meta", {}).get("currentFocus", "활성 run 없음")

    def _build_current_summary(
        self,
        *,
        active_runs: list[RunProjection],
        runnable_runs: list[RunProjection],
        recoverable_runs: list[RunProjection],
        pending_approval_runs: list[RunProjection],
        latest_heartbeat: RuntimeHeartbeatRecord | None,
    ) -> str:
        parts = [
            f"live DB 기준 active run {len(active_runs)}건",
            f"runnable {len(runnable_runs)}건",
            f"approval 대기 {len(pending_approval_runs)}건",
            f"recoverable {len(recoverable_runs)}건",
        ]
        if latest_heartbeat:
            parts.append(f"최근 daemon heartbeat는 {latest_heartbeat.status}")
        return ", ".join(parts) + "."

    def _build_current_state(
        self,
        *,
        execution_status: str,
        active_runs: list[RunProjection],
        pending_approval_runs: list[RunProjection],
        latest_heartbeat: RuntimeHeartbeatRecord | None,
    ) -> str:
        if execution_status == "running":
            return f"실행 중 run {len(active_runs)}건"
        if execution_status == "paused":
            return f"사람 개입 대기 run {len(pending_approval_runs)}건"
        if execution_status == "stopped":
            return "daemon 중지 상태"
        if latest_heartbeat:
            return f"대기 중, 최근 heartbeat {latest_heartbeat.status}"
        return "대기 중"

    def _build_next_checkpoint(
        self,
        *,
        base_next_checkpoint: str,
        active_runs: list[RunProjection],
        pending_approval_runs: list[RunProjection],
        runnable_runs: list[RunProjection],
        recoverable_runs: list[RunProjection],
    ) -> str:
        if pending_approval_runs:
            run = pending_approval_runs[0]
            return f"{run.run_id} 승인 처리 또는 retry 판단"
        if recoverable_runs:
            run = recoverable_runs[0]
            return f"{run.run_id} recovery 처리"
        if active_runs:
            run = active_runs[0]
            return f"{run.run_id} 상태 전이 확인"
        if runnable_runs:
            return f"대기 중 runnable run {len(runnable_runs)}건 pickup"
        return base_next_checkpoint or "활성 run 없음"

    def _build_runtime_summary(
        self,
        *,
        execution_status: str,
        active_runs: list[RunProjection],
        pending_approval_runs: list[RunProjection],
        latest_heartbeat: RuntimeHeartbeatRecord | None,
    ) -> str:
        if execution_status == "running" and active_runs:
            run = active_runs[0]
            return f"{run.run_id}가 {run.current_state} 상태로 진행 중이다."
        if execution_status == "paused" and pending_approval_runs:
            run = pending_approval_runs[0]
            return f"{run.run_id}가 사람 승인 또는 retry 결정을 기다린다."
        if execution_status == "stopped":
            return "daemon heartbeat가 stopped로 기록되어 외부 process manager 상태 확인이 필요하다."
        if latest_heartbeat:
            return f"최근 daemon heartbeat는 {latest_heartbeat.status}이며 현재 활성 run은 없다."
        return "아직 live runtime heartbeat 또는 run 기록이 없다."

    def _build_active_agents(
        self,
        *,
        active_runs: list[RunProjection],
        latest_heartbeat: RuntimeHeartbeatRecord | None,
        execution_status: str,
    ) -> list[dict[str, Any]]:
        agents: list[dict[str, Any]] = []
        if latest_heartbeat:
            agents.append(
                {
                    "name": self.worker_name,
                    "role": "supervisor daemon",
                    "status": self._daemon_agent_status(latest_heartbeat.status, execution_status),
                    "task": self._daemon_task(latest_heartbeat),
                    "detail": self._daemon_detail(latest_heartbeat),
                    "updatedAt": latest_heartbeat.event_time,
                    "progress": 100 if latest_heartbeat.status in {"idle", "completed", "stopped"} else 68,
                }
            )
        for run in active_runs[:5]:
            agents.append(
                {
                    "name": f"Codex · {run.run_id}",
                    "role": "runner",
                    "status": self._run_agent_status(run.current_state),
                    "task": run.instruction_summary,
                    "detail": self._run_detail(run),
                    "updatedAt": run.updated_at,
                    "progress": STATE_PROGRESS.get(run.current_state, 0),
                }
            )
        return agents

    def _build_activity_log(
        self,
        *,
        base_items: list[dict[str, Any]],
        runs: list[RunProjection],
        heartbeats: list[RuntimeHeartbeatRecord],
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for heartbeat in heartbeats[:6]:
            title = f"daemon heartbeat · {heartbeat.status}"
            detail = self._daemon_detail(heartbeat)
            entries.append(
                {
                    "time": heartbeat.event_time,
                    "status": _activity_status_for_heartbeat(heartbeat.status),
                    "title": title,
                    "detail": detail,
                }
            )
        for run in runs[:6]:
            entries.append(
                {
                    "time": run.updated_at,
                    "status": _activity_status_for_run(run.current_state),
                    "title": f"{run.run_id} · {run.current_state}",
                    "detail": run.instruction_summary,
                }
            )
        entries.extend(base_items)
        unique_entries = {(item["time"], item["title"], item["detail"]): item for item in entries}
        ordered = sorted(unique_entries.values(), key=lambda item: _iso_key(item["time"]), reverse=True)
        return ordered[:14]

    def _build_watch_items(
        self,
        *,
        base_items: list[dict[str, Any]],
        pending_approval_runs: list[RunProjection],
        recoverable_runs: list[RunProjection],
        latest_heartbeat: RuntimeHeartbeatRecord | None,
        execution_status: str,
    ) -> list[dict[str, Any]]:
        filtered = [item for item in base_items if item.get("label") != "progress monitor DB 미구현"]
        live_items: list[dict[str, Any]] = []
        if pending_approval_runs:
            run_ids = ", ".join(run.run_id for run in pending_approval_runs[:3])
            live_items.append(
                {
                    "level": "watch",
                    "label": "사람 승인 대기",
                    "detail": f"현재 awaiting_human run: {run_ids}",
                }
            )
        if recoverable_runs:
            run_ids = ", ".join(run.run_id for run in recoverable_runs[:3])
            live_items.append(
                {
                    "level": "watch",
                    "label": "recoverable run 감지",
                    "detail": f"daemon restart 시 복구 대상 run: {run_ids}",
                }
            )
        if latest_heartbeat:
            live_items.append(
                {
                    "level": "info" if execution_status != "stopped" else "watch",
                    "label": "최근 daemon heartbeat",
                    "detail": self._daemon_detail(latest_heartbeat),
                }
            )
        else:
            live_items.append(
                {
                    "level": "watch",
                    "label": "daemon heartbeat 없음",
                    "detail": "아직 runtime heartbeat가 기록되지 않았다.",
                }
            )
        return live_items + filtered

    def _daemon_agent_status(self, heartbeat_status: str, execution_status: str) -> str:
        if heartbeat_status == "stopped":
            return "stopped"
        if execution_status == "paused":
            return "paused"
        if heartbeat_status in {"started", "picked", "recovered"} or execution_status == "running":
            return "running"
        return "idle"

    def _run_agent_status(self, run_state: str) -> str:
        if run_state in PAUSED_STATES:
            return "paused"
        if run_state in RUNNING_STATES:
            return "running"
        return "idle"

    def _daemon_task(self, heartbeat: RuntimeHeartbeatRecord) -> str:
        if heartbeat.run_id:
            return f"{heartbeat.run_id} 처리"
        return f"daemon {heartbeat.status}"

    def _daemon_detail(self, heartbeat: RuntimeHeartbeatRecord) -> str:
        fragments = [f"status={heartbeat.status}"]
        if heartbeat.run_id:
            fragments.append(f"run={heartbeat.run_id}")
        payload = heartbeat.payload or {}
        if "runnable_runs" in payload:
            fragments.append(f"runnable={payload['runnable_runs']}")
        if "stop_reason" in payload:
            fragments.append(f"stop_reason={payload['stop_reason']}")
        if "final_state" in payload:
            fragments.append(f"final_state={payload['final_state']}")
        return ", ".join(fragments)

    def _run_detail(self, run: RunProjection) -> str:
        fragments = [f"state={run.current_state}", f"attempt={run.attempt_number}"]
        if run.active_branch:
            fragments.append(f"branch={run.active_branch}")
        if run.active_worktree:
            fragments.append(f"workspace={run.active_worktree}")
        if run.pr_reference:
            fragments.append(f"pr={run.pr_reference}")
        return ", ".join(fragments)

    def serve_forever(self, host: str = "127.0.0.1", port: int = 8765) -> MonitorServerInfo:
        server, info = self.create_server(host=host, port=port)
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return info

    def html_asset_path(self) -> Path:
        return self.html_path


def _iso_key(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _activity_status_for_run(state: str) -> str:
    if state == "completed":
        return "completed"
    if state in {"halted", "cancelled"}:
        return "blocked"
    if state in PAUSED_STATES:
        return "next"
    return "active"


def _activity_status_for_heartbeat(status: str) -> str:
    if status in {"completed", "idle"}:
        return "completed"
    if status == "stopped":
        return "blocked"
    return "active"
