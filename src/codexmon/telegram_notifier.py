"""Telegram notifier boundary for outbound alerts and inbound operator actions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import time
from typing import Protocol
from urllib import error, request

from codexmon.ledger import RunLedger
from codexmon.state_machine import TERMINAL_STATES


class TelegramNotifierError(RuntimeError):
    """Raised when Telegram notifier processing fails."""


@dataclass(frozen=True)
class TelegramTransportMessage:
    chat_id: str
    message_id: str
    raw: dict[str, object]


@dataclass(frozen=True)
class TelegramDeliveryResult:
    run_id: str
    chat_id: str
    delivered: bool
    message_id: str
    message_ref: str
    text: str
    error: str


@dataclass(frozen=True)
class TelegramCommand:
    action: str
    run_id: str
    approval_request_id: str
    raw_text: str


@dataclass(frozen=True)
class TelegramCommandResult:
    action: str
    run_id: str
    accepted: bool
    final_state: str
    state_reason: str
    reply_text: str
    operator_id: str
    approval_request_id: str
    inbound_message_ref: str
    reply_message_ref: str


class TelegramTransport(Protocol):
    """Transport protocol for sending Telegram messages."""

    def send_message(
        self, chat_id: str, text: str, reply_to_message_id: str = ""
    ) -> TelegramTransportMessage:
        """Send a Telegram message and return the raw transport result."""


class TelegramBotApiTransport:
    """Minimal Telegram Bot API client using the standard library only."""

    def __init__(self, bot_token: str, api_base: str = "https://api.telegram.org") -> None:
        if not bot_token:
            raise TelegramNotifierError("Telegram bot token is required")
        self.bot_token = bot_token
        self.api_base = api_base.rstrip("/")

    def send_message(
        self, chat_id: str, text: str, reply_to_message_id: str = ""
    ) -> TelegramTransportMessage:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = int(reply_to_message_id)

        response = self._post("sendMessage", payload)
        result = response.get("result")
        if not isinstance(result, dict):
            raise TelegramNotifierError("Telegram sendMessage response is missing result")

        message_id = str(result.get("message_id", ""))
        if not message_id:
            raise TelegramNotifierError("Telegram sendMessage response is missing message_id")

        result_chat = result.get("chat")
        resolved_chat_id = chat_id
        if isinstance(result_chat, dict) and result_chat.get("id") is not None:
            resolved_chat_id = str(result_chat["id"])

        return TelegramTransportMessage(
            chat_id=resolved_chat_id,
            message_id=message_id,
            raw=response,
        )

    def _post(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        api_url = f"{self.api_base}/bot{self.bot_token}/{method}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            api_url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=10) as resp:
                raw_body = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise TelegramNotifierError(
                f"Telegram API request failed with HTTP {exc.code}: {details}"
            ) from exc
        except error.URLError as exc:
            raise TelegramNotifierError(f"Telegram API request failed: {exc.reason}") from exc

        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise TelegramNotifierError("Telegram API returned invalid JSON") from exc

        if not parsed.get("ok", False):
            raise TelegramNotifierError(f"Telegram API returned ok=false: {parsed}")
        return parsed


class TelegramNotifier:
    """Compose outbound Telegram alerts and apply inbound operator actions."""

    def __init__(
        self,
        ledger: RunLedger,
        transport: TelegramTransport | None = None,
        default_chat_id: str = "",
    ) -> None:
        self.ledger = ledger
        self.transport = transport
        self.default_chat_id = default_chat_id

    def notify_run(
        self,
        run_id: str,
        event_label: str = "",
        chat_id: str = "",
        reply_to_message_id: str = "",
    ) -> TelegramDeliveryResult:
        run = self.ledger.get_run(run_id)
        text = self.format_run_summary(
            run_id=run_id,
            event_label=event_label or self._state_label(run.current_state),
        )
        return self._send_message(
            run_id=run_id,
            chat_id=chat_id or self.default_chat_id,
            text=text,
            reason_code="telegram notification sent",
            reply_to_message_id=reply_to_message_id,
        )

    def format_run_summary(self, run_id: str, event_label: str = "") -> str:
        run = self.ledger.get_run(run_id)
        event_text = event_label or self._state_label(run.current_state)
        lines = [
            f"codexmon / {event_text}",
            f"run_id: {run.run_id}",
            f"state: {run.current_state}",
            f"outcome: {run.outcome or '<pending>'}",
            f"reason: {run.state_reason}",
            f"attempt: {run.attempt_number}",
            f"approval: {run.approval_status}",
        ]
        if run.active_branch:
            lines.append(f"branch: {run.active_branch}")
        if run.active_worktree:
            lines.append(f"worktree: {run.active_worktree}")
        if run.last_failure_fingerprint:
            lines.append(f"failure: {run.last_failure_fingerprint}")
        if run.pr_reference:
            lines.append(f"pr: {run.pr_reference}")
        return "\n".join(lines)

    def process_inbound_text(
        self,
        text: str,
        operator_id: str,
        chat_id: str = "",
        message_id: str = "",
        send_reply: bool = True,
    ) -> TelegramCommandResult:
        command = self.parse_command(text)
        inbound_message_ref = self._message_ref(chat_id, message_id)
        self.ledger.append_event(
            command.run_id,
            event_type="telegram.command.received",
            actor_type="operator",
            actor_id=operator_id,
            reason_code=f"telegram {command.action} received",
            payload={
                "action": command.action,
                "approval_request_id": command.approval_request_id,
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
            },
        )

        result = self._apply_command(
            command=command,
            operator_id=operator_id,
            inbound_message_ref=inbound_message_ref,
        )

        reply_message_ref = ""
        if send_reply and chat_id and self.transport is not None:
            delivery = self._send_message(
                run_id=command.run_id,
                chat_id=chat_id,
                text=result.reply_text,
                reason_code=f"telegram {command.action} reply sent",
                reply_to_message_id=message_id,
            )
            reply_message_ref = delivery.message_ref

        return TelegramCommandResult(
            action=result.action,
            run_id=result.run_id,
            accepted=result.accepted,
            final_state=result.final_state,
            state_reason=result.state_reason,
            reply_text=result.reply_text,
            operator_id=result.operator_id,
            approval_request_id=result.approval_request_id,
            inbound_message_ref=result.inbound_message_ref,
            reply_message_ref=reply_message_ref,
        )

    def parse_command(self, text: str) -> TelegramCommand:
        tokens = text.strip().split()
        if not tokens:
            raise TelegramNotifierError("Telegram command text is empty")

        verb = tokens[0]
        if not verb.startswith("/"):
            raise TelegramNotifierError("Telegram command must start with '/'")

        action = verb[1:].split("@", 1)[0].strip().lower()
        if action not in {"status", "stop", "retry", "approve"}:
            raise TelegramNotifierError(f"unsupported Telegram command '{action}'")
        if len(tokens) < 2:
            raise TelegramNotifierError("Telegram command must include run_id")

        approval_request_id = tokens[2] if action == "approve" and len(tokens) >= 3 else ""
        return TelegramCommand(
            action=action,
            run_id=tokens[1],
            approval_request_id=approval_request_id,
            raw_text=text,
        )

    def _apply_command(
        self,
        command: TelegramCommand,
        operator_id: str,
        inbound_message_ref: str,
    ) -> TelegramCommandResult:
        if command.action == "status":
            return self._apply_status(command, operator_id, inbound_message_ref)
        if command.action == "stop":
            return self._apply_stop(command, operator_id, inbound_message_ref)
        if command.action == "retry":
            return self._apply_retry(command, operator_id, inbound_message_ref)
        if command.action == "approve":
            return self._apply_approve(command, operator_id, inbound_message_ref)
        raise TelegramNotifierError(f"unsupported Telegram command '{command.action}'")

    def _apply_status(
        self,
        command: TelegramCommand,
        operator_id: str,
        inbound_message_ref: str,
    ) -> TelegramCommandResult:
        run = self.ledger.get_run(command.run_id)
        self.ledger.append_event(
            command.run_id,
            event_type="telegram.command.applied",
            actor_type="operator",
            actor_id=operator_id,
            reason_code="telegram status returned",
            payload={"action": "status", "inbound_message_ref": inbound_message_ref},
        )
        return TelegramCommandResult(
            action="status",
            run_id=command.run_id,
            accepted=True,
            final_state=run.current_state,
            state_reason=run.state_reason,
            reply_text=self.format_run_summary(command.run_id, event_label="상태 조회"),
            operator_id=operator_id,
            approval_request_id="",
            inbound_message_ref=inbound_message_ref,
            reply_message_ref="",
        )

    def _apply_stop(
        self,
        command: TelegramCommand,
        operator_id: str,
        inbound_message_ref: str,
    ) -> TelegramCommandResult:
        run = self.ledger.get_run(command.run_id)
        if run.current_state in TERMINAL_STATES:
            return self._rejected_result(
                command=command,
                operator_id=operator_id,
                inbound_message_ref=inbound_message_ref,
                reply_text=f"run {command.run_id} 는 이미 terminal state {run.current_state} 입니다.",
            )

        self.ledger.append_event(
            command.run_id,
            event_type="operator.stop.requested",
            actor_type="operator",
            actor_id=operator_id,
            reason_code="kill switch requested via Telegram",
            payload={"inbound_message_ref": inbound_message_ref},
        )
        self._interrupt_active_runner(command.run_id, operator_id, inbound_message_ref)
        run = self.ledger.transition_run(
            command.run_id,
            "halted",
            "kill switch requested via Telegram",
            actor_type="operator",
            actor_id=operator_id,
            runner_signal="telegram_stop",
            telegram_message_ref=inbound_message_ref,
        )
        self.ledger.release_repository_lock(
            command.run_id,
            actor_type="operator",
            actor_id=operator_id,
        )
        return TelegramCommandResult(
            action="stop",
            run_id=command.run_id,
            accepted=True,
            final_state=run.current_state,
            state_reason=run.state_reason,
            reply_text=self.format_run_summary(command.run_id, event_label="중지 요청 반영"),
            operator_id=operator_id,
            approval_request_id="",
            inbound_message_ref=inbound_message_ref,
            reply_message_ref="",
        )

    def _apply_retry(
        self,
        command: TelegramCommand,
        operator_id: str,
        inbound_message_ref: str,
    ) -> TelegramCommandResult:
        run = self.ledger.get_run(command.run_id)
        if run.current_state != "awaiting_human":
            return self._rejected_result(
                command=command,
                operator_id=operator_id,
                inbound_message_ref=inbound_message_ref,
                reply_text=f"run {command.run_id} 는 retry 가능한 상태가 아닙니다: {run.current_state}",
            )
        if "retryable-by-human" not in run.state_reason:
            return self._rejected_result(
                command=command,
                operator_id=operator_id,
                inbound_message_ref=inbound_message_ref,
                reply_text=(
                    f"run {command.run_id} 는 human retry 허용 사유가 없습니다: "
                    f"{run.state_reason}"
                ),
            )

        self.ledger.append_event(
            command.run_id,
            event_type="operator.retry.requested",
            actor_type="operator",
            actor_id=operator_id,
            reason_code="operator retry requested via Telegram",
            payload={"inbound_message_ref": inbound_message_ref},
        )
        run = self.ledger.transition_run(
            command.run_id,
            "retry_pending",
            "operator retry requested",
            actor_type="operator",
            actor_id=operator_id,
            telegram_message_ref=inbound_message_ref,
        )
        return TelegramCommandResult(
            action="retry",
            run_id=command.run_id,
            accepted=True,
            final_state=run.current_state,
            state_reason=run.state_reason,
            reply_text=self.format_run_summary(command.run_id, event_label="재시도 승인"),
            operator_id=operator_id,
            approval_request_id="",
            inbound_message_ref=inbound_message_ref,
            reply_message_ref="",
        )

    def _apply_approve(
        self,
        command: TelegramCommand,
        operator_id: str,
        inbound_message_ref: str,
    ) -> TelegramCommandResult:
        run = self.ledger.get_run(command.run_id)
        if run.current_state != "awaiting_human":
            return self._rejected_result(
                command=command,
                operator_id=operator_id,
                inbound_message_ref=inbound_message_ref,
                reply_text=f"run {command.run_id} 는 approve 가능한 상태가 아닙니다: {run.current_state}",
            )

        pending = self.ledger.list_approvals(command.run_id, status="pending")
        approval_request_id = command.approval_request_id or (pending[0].approval_request_id if pending else "")
        if not approval_request_id:
            return self._rejected_result(
                command=command,
                operator_id=operator_id,
                inbound_message_ref=inbound_message_ref,
                reply_text=f"run {command.run_id} 에 pending approval 이 없습니다.",
            )

        self.ledger.resolve_approval(
            approval_request_id=approval_request_id,
            status="approved",
            resolved_by=operator_id,
            decision_note="Telegram operator approval",
        )
        run = self.ledger.transition_run(
            command.run_id,
            "retry_pending",
            "operator approved continuation",
            actor_type="operator",
            actor_id=operator_id,
            approval_request_id=approval_request_id,
            approval_result="approved",
            telegram_message_ref=inbound_message_ref,
        )
        return TelegramCommandResult(
            action="approve",
            run_id=command.run_id,
            accepted=True,
            final_state=run.current_state,
            state_reason=run.state_reason,
            reply_text=self.format_run_summary(command.run_id, event_label="승인 반영"),
            operator_id=operator_id,
            approval_request_id=approval_request_id,
            inbound_message_ref=inbound_message_ref,
            reply_message_ref="",
        )

    def _rejected_result(
        self,
        command: TelegramCommand,
        operator_id: str,
        inbound_message_ref: str,
        reply_text: str,
    ) -> TelegramCommandResult:
        self.ledger.append_event(
            command.run_id,
            event_type="telegram.command.rejected",
            actor_type="operator",
            actor_id=operator_id,
            reason_code=f"telegram {command.action} rejected",
            payload={"inbound_message_ref": inbound_message_ref, "reply_text": reply_text},
        )
        run = self.ledger.get_run(command.run_id)
        return TelegramCommandResult(
            action=command.action,
            run_id=command.run_id,
            accepted=False,
            final_state=run.current_state,
            state_reason=run.state_reason,
            reply_text=reply_text,
            operator_id=operator_id,
            approval_request_id=command.approval_request_id,
            inbound_message_ref=inbound_message_ref,
            reply_message_ref="",
        )

    def _send_message(
        self,
        run_id: str,
        chat_id: str,
        text: str,
        reason_code: str,
        reply_to_message_id: str = "",
    ) -> TelegramDeliveryResult:
        if not chat_id:
            raise TelegramNotifierError("Telegram chat_id is required")
        if self.transport is None:
            raise TelegramNotifierError("Telegram transport is not configured")

        try:
            sent = self.transport.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
            )
        except TelegramNotifierError as exc:
            self.ledger.append_event(
                run_id,
                event_type="telegram.message.failed",
                actor_type="notifier",
                actor_id="telegram",
                reason_code="telegram delivery failed",
                payload={"chat_id": chat_id, "error": str(exc), "text": text},
            )
            raise

        message_ref = self._message_ref(sent.chat_id, sent.message_id)
        self.ledger.append_event(
            run_id,
            event_type="telegram.message.sent",
            actor_type="notifier",
            actor_id="telegram",
            reason_code=reason_code,
            payload={
                "chat_id": sent.chat_id,
                "message_id": sent.message_id,
                "message_ref": message_ref,
                "text": text,
            },
        )
        return TelegramDeliveryResult(
            run_id=run_id,
            chat_id=sent.chat_id,
            delivered=True,
            message_id=sent.message_id,
            message_ref=message_ref,
            text=text,
            error="",
        )

    def _message_ref(self, chat_id: str, message_id: str) -> str:
        if not chat_id or not message_id:
            return ""
        return f"telegram:{chat_id}:{message_id}"

    def _state_label(self, state: str) -> str:
        return {
            "queued": "run 생성",
            "preflight": "preflight 진행",
            "workspace_allocated": "workspace 할당",
            "running": "작업 진행 중",
            "analyzing_failure": "실패 분석 중",
            "retry_pending": "재시도 대기",
            "awaiting_human": "사람 결정 대기",
            "pr_handoff": "PR 인계 준비",
            "completed": "완료",
            "halted": "중단",
            "cancelled": "취소",
        }.get(state, state)

    def _interrupt_active_runner(
        self,
        run_id: str,
        operator_id: str,
        inbound_message_ref: str,
    ) -> None:
        pid = self._active_runner_pid(run_id)
        if pid is None:
            self.ledger.append_event(
                run_id,
                event_type="operator.stop.no_active_runner",
                actor_type="operator",
                actor_id=operator_id,
                reason_code="no active runner pid found for stop request",
                payload={"inbound_message_ref": inbound_message_ref},
            )
            return

        if not self._send_signal(run_id, operator_id, pid, signal.SIGINT, "interrupt_sent", inbound_message_ref):
            return
        if self._wait_for_process_exit(pid, timeout_seconds=1.0):
            return

        if not self._send_signal(
            run_id,
            operator_id,
            pid,
            signal.SIGTERM,
            "termination_sent",
            inbound_message_ref,
        ):
            return
        if self._wait_for_process_exit(pid, timeout_seconds=1.0):
            return

        self._send_signal(run_id, operator_id, pid, signal.SIGKILL, "kill_sent", inbound_message_ref)
        self._wait_for_process_exit(pid, timeout_seconds=1.0)

    def _active_runner_pid(self, run_id: str) -> int | None:
        for event in reversed(self.ledger.list_events(run_id)):
            if event.event_type == "runner.launched":
                pid = event.payload.get("pid")
                if isinstance(pid, int):
                    return pid
                if isinstance(pid, str) and pid.isdigit():
                    return int(pid)
        return None

    def _send_signal(
        self,
        run_id: str,
        operator_id: str,
        pid: int,
        sig: signal.Signals,
        event_suffix: str,
        inbound_message_ref: str,
    ) -> bool:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            self.ledger.append_event(
                run_id,
                event_type=f"operator.stop.{event_suffix}",
                actor_type="operator",
                actor_id=operator_id,
                reason_code="runner process already exited",
                payload={
                    "pid": pid,
                    "signal": sig.name,
                    "inbound_message_ref": inbound_message_ref,
                    "process_found": False,
                },
            )
            return False

        self.ledger.append_event(
            run_id,
            event_type=f"operator.stop.{event_suffix}",
            actor_type="operator",
            actor_id=operator_id,
            reason_code=f"runner {sig.name} sent",
            payload={
                "pid": pid,
                "signal": sig.name,
                "inbound_message_ref": inbound_message_ref,
                "process_found": True,
            },
        )
        return True

    def _wait_for_process_exit(self, pid: int, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self._is_process_alive(pid):
                return True
            time.sleep(0.05)
        return not self._is_process_alive(pid)

    def _is_process_alive(self, pid: int) -> bool:
        proc_stat = f"/proc/{pid}/stat"
        if os.path.exists(proc_stat):
            try:
                state = Path(proc_stat).read_text(encoding="utf-8").split()[2]
            except (OSError, IndexError):
                return False
            return state != "Z"
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
