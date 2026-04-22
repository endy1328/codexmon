"""Supervisor runtime that orchestrates a full unattended run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codexmon.approval_policy import ApprovalPolicyService
from codexmon.failure_policy import FailureSignalController
from codexmon.ledger import RunLedger
from codexmon.pr_handoff import PRHandoffError, PRHandoffService
from codexmon.telegram_notifier import TelegramNotifier, TelegramNotifierError
from codexmon.workspace import WorktreeAllocator


class OrchestratorError(RuntimeError):
    """Raised when a supervisor runtime request cannot proceed."""


@dataclass(frozen=True)
class OrchestrationResult:
    run_id: str
    task_id: str
    final_state: str
    outcome: str
    state_reason: str
    attempt_number: int
    active_branch: str
    active_worktree: str
    approval_required: bool
    approval_request_id: str
    pr_reference: str
    retries_used: int
    lock_released: bool
    notifications_sent: int


class SupervisorRuntime:
    """Glue the existing services into a synchronous single-run orchestrator."""

    def __init__(
        self,
        ledger: RunLedger,
        allocator: WorktreeAllocator,
        failure_controller: FailureSignalController,
        approval_policy: ApprovalPolicyService,
        handoff_service: PRHandoffService,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self.ledger = ledger
        self.allocator = allocator
        self.failure_controller = failure_controller
        self.approval_policy = approval_policy
        self.handoff_service = handoff_service
        self.notifier = notifier

    def create_and_execute(
        self,
        instruction_summary: str,
        task_id: str = "",
        run_id: str = "",
        repo_owner: str = "",
        repo_name: str = "",
        residual_risk_note: str = "",
        chat_id: str = "",
    ) -> OrchestrationResult:
        task = self.ledger.create_task(
            instruction_summary=instruction_summary,
            task_id=task_id or None,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        run = self.ledger.create_run(
            task_id=task.task_id,
            run_id=run_id or None,
            instruction_summary=instruction_summary,
        )
        return self.execute_run(
            run_id=run.run_id,
            instruction=instruction_summary,
            residual_risk_note=residual_risk_note,
            chat_id=chat_id,
        )

    def execute_run(
        self,
        run_id: str,
        instruction: str = "",
        residual_risk_note: str = "",
        chat_id: str = "",
    ) -> OrchestrationResult:
        run = self.ledger.get_run(run_id)
        instruction_text = instruction or run.instruction_summary
        if not instruction_text:
            raise OrchestratorError(f"run '{run_id}' does not have an instruction summary")
        if run.current_state not in {"queued", "preflight", "workspace_allocated", "retry_pending", "pr_handoff"}:
            raise OrchestratorError(
                f"run '{run_id}' must be in 'queued', 'preflight', 'workspace_allocated', "
                f"'retry_pending', or 'pr_handoff' before orchestration"
            )

        notifications_sent = 0
        retries_used = 0
        lock_released = False
        terminal_notification_sent = False

        self.ledger.append_event(
            run_id,
            event_type="orchestrator.execution.started",
            actor_type="system",
            actor_id="codexmon",
            reason_code="orchestrator execution started",
            payload={"instruction": instruction_text},
        )

        if run.current_state in {"queued", "preflight"}:
            run = self._run_preflight(run_id, chat_id)
            if run.current_state == "halted":
                notifications_sent += self._notify_if_possible(run_id, "preflight 실패", chat_id)
                terminal_notification_sent = True
                self.ledger.append_event(
                    run_id,
                    event_type="orchestrator.execution.finished",
                    actor_type="system",
                    actor_id="codexmon",
                    reason_code="orchestrator execution finished",
                    payload={"final_state": run.current_state, "notifications_sent": notifications_sent},
                )
                return self._build_result(
                    run_id,
                    retries_used=retries_used,
                    lock_released=lock_released,
                    notifications_sent=notifications_sent,
                )

        try:
            if self.ledger.get_run(run_id).current_state == "preflight":
                self.allocator.allocate(run_id)

            run = self.ledger.get_run(run_id)
            if run.current_state in {"workspace_allocated", "retry_pending"}:
                notifications_sent += self._notify_if_possible(run_id, "작업 시작", chat_id)
                policy_result = self.failure_controller.execute_with_options(
                    run_id,
                    instruction_text,
                    defer_success_transition=True,
                )
                retries_used = policy_result.retries_used
                run = self.ledger.get_run(run_id)

                if run.current_state == "running":
                    scan_result = self.approval_policy.scan(run_id)
                    run = self.ledger.get_run(run_id)
                    if scan_result.approval_required:
                        notifications_sent += self._notify_if_possible(run_id, "사람 결정 대기", chat_id)
                        self.ledger.append_event(
                            run_id,
                            event_type="orchestrator.execution.finished",
                            actor_type="system",
                            actor_id="codexmon",
                            reason_code="orchestrator execution finished",
                            payload={"final_state": run.current_state, "notifications_sent": notifications_sent},
                        )
                        return self._build_result(
                            run_id,
                            retries_used=retries_used,
                            lock_released=lock_released,
                            notifications_sent=notifications_sent,
                        )

                    run = self.ledger.transition_run(
                        run_id,
                        "pr_handoff",
                        "success path reached",
                        workspace_path=run.active_worktree,
                        branch_name=run.active_branch,
                        runner_signal="exit=0",
                    )

            if self.ledger.get_run(run_id).current_state == "pr_handoff":
                try:
                    self.handoff_service.execute(run_id, residual_risk_note=residual_risk_note)
                except PRHandoffError as exc:
                    self.ledger.transition_run(run_id, "halted", f"PR handoff failed: {exc}")
                current_after_handoff = self.ledger.get_run(run_id)
                notifications_sent += self._notify_if_possible(
                    run_id,
                    "완료" if current_after_handoff.current_state == "completed" else "중단",
                    chat_id,
                )
                terminal_notification_sent = current_after_handoff.current_state in {"completed", "halted"}
        finally:
            current_run = self.ledger.get_run(run_id)
            if current_run.current_state in {"completed", "halted", "cancelled"}:
                lock_released = self.allocator.release(run_id).lock_released

        run = self.ledger.get_run(run_id)
        if run.current_state == "halted" and not terminal_notification_sent:
            notifications_sent += self._notify_if_possible(run_id, "중단", chat_id)

        self.ledger.append_event(
            run_id,
            event_type="orchestrator.execution.finished",
            actor_type="system",
            actor_id="codexmon",
            reason_code="orchestrator execution finished",
            payload={"final_state": run.current_state, "notifications_sent": notifications_sent},
        )
        return self._build_result(
            run_id,
            retries_used=retries_used,
            lock_released=lock_released,
            notifications_sent=notifications_sent,
        )

    def _run_preflight(self, run_id: str, chat_id: str) -> object:
        run = self.ledger.get_run(run_id)
        if run.current_state == "queued":
            run = self.ledger.transition_run(run_id, "preflight", "orchestrator preflight started")

        checks = self._collect_preflight_checks(run_id, chat_id)
        failed = [item for item in checks if not item[1]]
        for name, passed, detail in checks:
            self.ledger.append_event(
                run_id,
                event_type="preflight.check",
                actor_type="system",
                actor_id="codexmon",
                reason_code=f"preflight check {'passed' if passed else 'failed'}: {name}",
                payload={"check_name": name, "passed": passed, "detail": detail},
            )

        if failed:
            failed_names = ", ".join(name for name, _, _ in failed)
            return self.ledger.transition_run(
                run_id,
                "halted",
                f"preflight failed: {failed_names}",
            )

        self.ledger.append_event(
            run_id,
            event_type="preflight.completed",
            actor_type="system",
            actor_id="codexmon",
            reason_code="preflight completed",
            payload={"checks_passed": len(checks)},
        )
        return self.ledger.get_run(run_id)

    def _collect_preflight_checks(self, run_id: str, chat_id: str) -> list[tuple[str, bool, str]]:
        run = self.ledger.get_run(run_id)
        task = self.ledger.get_task(run.task_id)
        repo_owner = task.repo_owner or self.handoff_service.default_repo_owner
        repo_name = task.repo_name or self.handoff_service.default_repo_name
        requested_chat = chat_id or (self.notifier.default_chat_id if self.notifier is not None else "")

        return [
            ("repo_root_exists", self.allocator.repo_root.exists(), str(self.allocator.repo_root)),
            ("worktree_root_exists", self.allocator.worktree_root.exists(), str(self.allocator.worktree_root)),
            ("codex_command_configured", bool(self.failure_controller.adapter.codex_command.strip()), self.failure_controller.adapter.codex_command),
            ("telegram_transport_configured", self.notifier is not None and self.notifier.transport is not None, "telegram transport"),
            ("telegram_chat_configured", bool(requested_chat), requested_chat or "<unset>"),
            ("github_client_configured", self.handoff_service.github_client is not None, "github client"),
            ("github_repo_configured", bool(repo_owner and repo_name), f"{repo_owner}/{repo_name}".rstrip("/")),
            ("local_check_configured", bool(self.handoff_service.local_check_command.strip()), self.handoff_service.local_check_command or "<unset>"),
        ]

    def _notify_if_possible(self, run_id: str, event_label: str, chat_id: str) -> int:
        if self.notifier is None or self.notifier.transport is None:
            self.ledger.append_event(
                run_id,
                event_type="telegram.message.skipped",
                actor_type="system",
                actor_id="codexmon",
                reason_code="telegram notification skipped",
                payload={"event_label": event_label, "reason": "transport not configured"},
            )
            return 0
        if not (chat_id or self.notifier.default_chat_id):
            self.ledger.append_event(
                run_id,
                event_type="telegram.message.skipped",
                actor_type="system",
                actor_id="codexmon",
                reason_code="telegram notification skipped",
                payload={"event_label": event_label, "reason": "chat_id not configured"},
            )
            return 0
        try:
            self.notifier.notify_run(run_id, event_label=event_label, chat_id=chat_id)
        except TelegramNotifierError as exc:
            self.ledger.append_event(
                run_id,
                event_type="telegram.message.skipped",
                actor_type="system",
                actor_id="codexmon",
                reason_code="telegram notification skipped",
                payload={"event_label": event_label, "reason": str(exc)},
            )
            return 0
        return 1

    def _build_result(
        self,
        run_id: str,
        retries_used: int,
        lock_released: bool,
        notifications_sent: int,
    ) -> OrchestrationResult:
        run = self.ledger.get_run(run_id)
        approvals = self.ledger.list_approvals(run_id, status="pending")
        approval_request_id = approvals[0].approval_request_id if approvals else ""
        return OrchestrationResult(
            run_id=run.run_id,
            task_id=run.task_id,
            final_state=run.current_state,
            outcome=run.outcome,
            state_reason=run.state_reason,
            attempt_number=run.attempt_number,
            active_branch=run.active_branch,
            active_worktree=run.active_worktree,
            approval_required=run.current_state == "awaiting_human",
            approval_request_id=approval_request_id,
            pr_reference=run.pr_reference,
            retries_used=retries_used,
            lock_released=lock_released,
            notifications_sent=notifications_sent,
        )
