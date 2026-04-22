"""Deterministic failure signal handling and automatic retry policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from codexmon.codex_adapter import CodexAdapter, CodexExecutionResult
from codexmon.ledger import RunLedger


@dataclass(frozen=True)
class FailurePolicySettings:
    automatic_retry_budget: int = 1
    idle_timeout_seconds: float = 900.0
    wall_clock_timeout_seconds: float = 7200.0


@dataclass(frozen=True)
class FailurePolicyResult:
    run_id: str
    final_state: str
    retries_used: int
    attempt_number: int
    last_failure_fingerprint: str
    reason_code: str


class FailureSignalController:
    """Apply timeout, fingerprint, and retry policy on top of a runner adapter."""

    def __init__(
        self,
        ledger: RunLedger,
        adapter: CodexAdapter,
        settings: FailurePolicySettings | None = None,
    ) -> None:
        self.ledger = ledger
        self.adapter = adapter
        self.settings = settings or FailurePolicySettings()

    def execute(self, run_id: str, instruction: str) -> FailurePolicyResult:
        return self.execute_with_options(run_id, instruction)

    def execute_with_options(
        self,
        run_id: str,
        instruction: str,
        defer_success_transition: bool = False,
    ) -> FailurePolicyResult:
        retries_used = 0
        while True:
            execution = self.adapter.execute_run(
                run_id,
                instruction,
                idle_timeout_seconds=self.settings.idle_timeout_seconds,
                wall_clock_timeout_seconds=self.settings.wall_clock_timeout_seconds,
                defer_success_transition=defer_success_transition,
            )
            run = self.ledger.get_run(run_id)
            if execution.final_state != "analyzing_failure":
                return FailurePolicyResult(
                    run_id=run_id,
                    final_state=run.current_state,
                    retries_used=retries_used,
                    attempt_number=run.attempt_number,
                    last_failure_fingerprint=run.last_failure_fingerprint,
                    reason_code=run.state_reason,
                )

            fingerprint = self._normalize_failure_fingerprint(execution, run_id)
            existing = [item.fingerprint for item in self.ledger.list_failure_fingerprints(run_id)]
            self.ledger.record_failure_fingerprint(
                run_id=run_id,
                fingerprint=fingerprint,
                command_name=Path(execution.command[0]).name,
                failure_class=self._failure_class(execution),
                dominant_token=self._dominant_token(run_id),
            )

            if fingerprint in existing:
                reason_code = "retry denied: duplicate failure fingerprint"
                self.ledger.append_event(
                    run_id,
                    event_type="failure.policy.decision",
                    reason_code=reason_code,
                    payload={"fingerprint": fingerprint, "decision": "halted"},
                )
                run = self.ledger.transition_run(
                    run_id,
                    "halted",
                    reason_code,
                    failure_fingerprint=fingerprint,
                )
                return FailurePolicyResult(
                    run_id=run_id,
                    final_state=run.current_state,
                    retries_used=retries_used,
                    attempt_number=run.attempt_number,
                    last_failure_fingerprint=fingerprint,
                    reason_code=reason_code,
                )

            if run.attempt_number <= self.settings.automatic_retry_budget:
                retries_used += 1
                reason_code = "retry allowed"
                self.ledger.append_event(
                    run_id,
                    event_type="failure.policy.decision",
                    reason_code=reason_code,
                    payload={"fingerprint": fingerprint, "decision": "retry_pending"},
                )
                self.ledger.transition_run(
                    run_id,
                    "retry_pending",
                    reason_code,
                    failure_fingerprint=fingerprint,
                )
                continue

            reason_code = "retry denied: automatic retry budget exhausted"
            self.ledger.append_event(
                run_id,
                event_type="failure.policy.decision",
                reason_code=reason_code,
                payload={"fingerprint": fingerprint, "decision": "halted"},
            )
            run = self.ledger.transition_run(
                run_id,
                "halted",
                reason_code,
                failure_fingerprint=fingerprint,
            )
            return FailurePolicyResult(
                run_id=run_id,
                final_state=run.current_state,
                retries_used=retries_used,
                attempt_number=run.attempt_number,
                last_failure_fingerprint=fingerprint,
                reason_code=reason_code,
            )

    def _normalize_failure_fingerprint(self, execution: CodexExecutionResult, run_id: str) -> str:
        command_name = Path(execution.command[0]).name
        failure_class = self._failure_class(execution)
        dominant_token = self._dominant_token(run_id)
        return f"{command_name}|{failure_class}|{dominant_token}"

    def _failure_class(self, execution: CodexExecutionResult) -> str:
        if execution.failure_signal:
            return execution.failure_signal
        if execution.exit_code is None:
            return "launch_failed"
        return f"exit-{execution.exit_code}"

    def _dominant_token(self, run_id: str) -> str:
        events = self.ledger.list_events(run_id)
        output_lines = [
            event.payload.get("line", "")
            for event in events
            if event.event_type == "runner.output"
        ]
        return self._normalize_token(reversed(output_lines))

    def _normalize_token(self, items: Iterable[str]) -> str:
        for item in items:
            token = " ".join(str(item).strip().split())
            if token:
                return token[:120]
        return "no-output"
