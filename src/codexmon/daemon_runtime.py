"""Background daemon worker for queued and resumable runs."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import time
from typing import Callable

from codexmon.ledger import RunLedger, RuntimeHeartbeatRecord
from codexmon.orchestrator import OrchestrationResult, SupervisorRuntime
from codexmon.state_machine import TERMINAL_STATES


@dataclass(frozen=True)
class DaemonTickResult:
    worker_name: str
    processed: bool
    idle: bool
    ok: bool
    run_id: str
    final_state: str
    outcome: str
    error: str
    heartbeat_status: str
    heartbeat_id: int


@dataclass(frozen=True)
class DaemonServeResult:
    worker_name: str
    iterations: int
    processed_runs: int
    idle_iterations: int
    error_count: int
    last_run_id: str
    last_state: str
    stop_reason: str


@dataclass(frozen=True)
class RecoveryResult:
    run_id: str
    source_state: str
    final_state: str
    outcome: str
    failure_class: str
    lock_released: bool


class SupervisorDaemon:
    """Poll runnable runs, execute them through the orchestrator, and persist heartbeats."""

    def __init__(
        self,
        ledger: RunLedger,
        runtime: SupervisorRuntime,
        worker_name: str = "codexmon-daemon",
        poll_interval_seconds: float = 15.0,
    ) -> None:
        self.ledger = ledger
        self.runtime = runtime
        self.worker_name = worker_name
        self.poll_interval_seconds = poll_interval_seconds

    def run_once(self, chat_id: str = "") -> DaemonTickResult:
        run_id = ""
        try:
            recovery = self._recover_orphaned_run()
            if recovery is not None:
                heartbeat = self._record(
                    "recovered",
                    run_id=recovery.run_id,
                    payload={
                        "source_state": recovery.source_state,
                        "final_state": recovery.final_state,
                        "failure_class": recovery.failure_class,
                        "lock_released": recovery.lock_released,
                    },
                )
                if recovery.final_state not in {"queued", "preflight", "workspace_allocated", "retry_pending", "pr_handoff"}:
                    return self._tick_result(
                        processed=True,
                        idle=False,
                        ok=True,
                        run_id=recovery.run_id,
                        final_state=recovery.final_state,
                        outcome=recovery.outcome,
                        error="",
                        heartbeat=heartbeat,
                    )

            runnable = self.ledger.list_runnable_runs(limit=1)
            if not runnable:
                heartbeat = self._record("idle", payload={"runnable_runs": 0})
                return self._tick_result(
                    processed=False,
                    idle=True,
                    ok=True,
                    run_id="",
                    final_state="",
                    outcome="",
                    error="",
                    heartbeat=heartbeat,
                )

            run = runnable[0]
            run_id = run.run_id
            self._record(
                "picked",
                run_id=run.run_id,
                payload={"current_state": run.current_state, "instruction_summary": run.instruction_summary},
            )
            result = self.runtime.execute_run(
                run_id=run.run_id,
                instruction=run.instruction_summary,
                chat_id=chat_id,
            )
            heartbeat_status = self._status_for_result(result)
            heartbeat = self._record(
                heartbeat_status,
                run_id=run.run_id,
                payload={
                    "final_state": result.final_state,
                    "outcome": result.outcome,
                    "lock_released": result.lock_released,
                    "notifications_sent": result.notifications_sent,
                },
            )
            return self._tick_result(
                processed=True,
                idle=False,
                ok=True,
                run_id=result.run_id,
                final_state=result.final_state,
                outcome=result.outcome,
                error="",
                heartbeat=heartbeat,
            )
        except Exception as exc:
            heartbeat = self._record(
                "error",
                run_id=run_id,
                payload={"error": str(exc), "error_type": type(exc).__name__},
            )
            return self._tick_result(
                processed=bool(run_id),
                idle=False,
                ok=False,
                run_id=run_id,
                final_state="",
                outcome="",
                error=str(exc),
                heartbeat=heartbeat,
            )

    def serve(
        self,
        chat_id: str = "",
        iterations: int = 0,
        poll_interval_seconds: float | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> DaemonServeResult:
        actual_interval = poll_interval_seconds if poll_interval_seconds is not None else self.poll_interval_seconds
        stop_reason = "iterations exhausted"
        processed_runs = 0
        idle_iterations = 0
        error_count = 0
        total_iterations = 0
        last_run_id = ""
        last_state = ""

        self._record(
            "started",
            payload={"poll_interval_seconds": actual_interval, "iterations": iterations},
        )
        try:
            while True:
                total_iterations += 1
                tick = self.run_once(chat_id=chat_id)
                if tick.processed and tick.ok:
                    processed_runs += 1
                if tick.idle:
                    idle_iterations += 1
                if not tick.ok:
                    error_count += 1
                if tick.run_id:
                    last_run_id = tick.run_id
                if tick.final_state:
                    last_state = tick.final_state

                if iterations > 0 and total_iterations >= iterations:
                    break
                sleep_fn(actual_interval)
        except KeyboardInterrupt:
            stop_reason = "keyboard interrupt"
        self._record(
            "stopped",
            run_id=last_run_id,
            payload={
                "iterations": total_iterations,
                "processed_runs": processed_runs,
                "idle_iterations": idle_iterations,
                "error_count": error_count,
                "stop_reason": stop_reason,
            },
        )
        return DaemonServeResult(
            worker_name=self.worker_name,
            iterations=total_iterations,
            processed_runs=processed_runs,
            idle_iterations=idle_iterations,
            error_count=error_count,
            last_run_id=last_run_id,
            last_state=last_state,
            stop_reason=stop_reason,
        )

    def status(self, limit: int = 20) -> list[RuntimeHeartbeatRecord]:
        return self.ledger.list_runtime_heartbeats(limit=limit, worker_name=self.worker_name)

    def _record(
        self,
        status: str,
        run_id: str = "",
        payload: dict[str, object] | None = None,
    ) -> RuntimeHeartbeatRecord:
        return self.ledger.record_runtime_heartbeat(
            worker_name=self.worker_name,
            status=status,
            run_id=run_id,
            payload=payload or {},
        )

    def _status_for_result(self, result: OrchestrationResult) -> str:
        if result.final_state == "completed":
            return "completed"
        if result.final_state == "awaiting_human":
            return "paused"
        if result.final_state == "halted":
            return "halted"
        return result.final_state or "processed"

    def _recover_orphaned_run(self) -> RecoveryResult | None:
        candidates = self.ledger.list_recoverable_runs(limit=1)
        if not candidates:
            return None

        run = candidates[0]
        failure_class = self._failure_class_from_events(run.run_id)
        reason_code = f"orphaned {run.current_state} state recovered"
        lock_released = False

        if run.current_state == "running":
            failure_class = self._recover_running_process(run.run_id, run.attempt_number)
            if failure_class == "recovery_uninterruptible":
                halted = self.ledger.transition_run(
                    run.run_id,
                    "halted",
                    "recovery failed: orphaned runner could not be interrupted",
                )
                lock_released = self.runtime.allocator.release(run.run_id).lock_released
                self.ledger.append_event(
                    run.run_id,
                    event_type="daemon.recovery.failed",
                    reason_code="orphaned runner could not be interrupted",
                    payload={"lock_released": lock_released},
                    attempt_number=halted.attempt_number,
                )
                return RecoveryResult(
                    run_id=halted.run_id,
                    source_state=run.current_state,
                    final_state=halted.current_state,
                    outcome=halted.outcome,
                    failure_class=failure_class,
                    lock_released=lock_released,
                )

        self.ledger.append_event(
            run.run_id,
            event_type="daemon.recovery.detected",
            reason_code=reason_code,
            payload={"source_state": run.current_state, "failure_class": failure_class},
            attempt_number=run.attempt_number,
        )
        result = self.runtime.failure_controller.recover_orphaned_run(
            run_id=run.run_id,
            failure_class=failure_class,
            reason_code=reason_code,
        )
        recovered_run = self.ledger.get_run(run.run_id)
        if recovered_run.current_state in TERMINAL_STATES:
            lock_released = self.runtime.allocator.release(run.run_id).lock_released

        self.ledger.append_event(
            run.run_id,
            event_type="daemon.recovery.applied",
            reason_code="daemon recovery applied",
            payload={
                "source_state": run.current_state,
                "final_state": recovered_run.current_state,
                "failure_class": failure_class,
                "lock_released": lock_released,
                "policy_reason": result.reason_code,
            },
            attempt_number=recovered_run.attempt_number,
        )
        return RecoveryResult(
            run_id=recovered_run.run_id,
            source_state=run.current_state,
            final_state=recovered_run.current_state,
            outcome=recovered_run.outcome,
            failure_class=failure_class,
            lock_released=lock_released,
        )

    def _recover_running_process(self, run_id: str, attempt_number: int) -> str:
        launch_event, exit_recorded = self._latest_runner_launch(run_id, attempt_number)
        if exit_recorded:
            self.ledger.append_event(
                run_id,
                event_type="daemon.recovery.process_missing",
                reason_code="runner exit already recorded for orphaned run",
                payload={},
                attempt_number=attempt_number,
            )
            return "recovery_missing_process"

        if launch_event is None:
            self.ledger.append_event(
                run_id,
                event_type="daemon.recovery.process_missing",
                reason_code="runner launch event missing for orphaned run",
                payload={},
                attempt_number=attempt_number,
            )
            return "recovery_missing_launch"

        pid = self._event_pid(launch_event)
        command_name = self._event_command_name(launch_event)
        if pid is None:
            self.ledger.append_event(
                run_id,
                event_type="daemon.recovery.process_missing",
                reason_code="runner pid missing for orphaned run",
                payload={"command_name": command_name},
                attempt_number=attempt_number,
            )
            return "recovery_missing_pid"

        if not self._is_expected_process_alive(pid, command_name):
            self.ledger.append_event(
                run_id,
                event_type="daemon.recovery.process_missing",
                reason_code="runner process not found for orphaned run",
                payload={"pid": pid, "command_name": command_name},
                attempt_number=attempt_number,
            )
            return "recovery_missing_process"

        self.ledger.append_event(
            run_id,
            event_type="daemon.recovery.signal_sent",
            reason_code="sent SIGTERM to orphaned runner",
            payload={"pid": pid, "signal": "SIGTERM", "command_name": command_name},
            attempt_number=attempt_number,
        )
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return "recovery_missing_process"
        except PermissionError:
            return "recovery_uninterruptible"

        if self._wait_for_process_exit(pid, timeout_seconds=1.0):
            return "recovery_interrupted"

        self.ledger.append_event(
            run_id,
            event_type="daemon.recovery.signal_sent",
            reason_code="sent SIGKILL to orphaned runner",
            payload={"pid": pid, "signal": "SIGKILL", "command_name": command_name},
            attempt_number=attempt_number,
        )
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return "recovery_interrupted"
        except PermissionError:
            return "recovery_uninterruptible"

        if self._wait_for_process_exit(pid, timeout_seconds=1.0):
            return "recovery_interrupted"
        return "recovery_uninterruptible"

    def _latest_runner_launch(self, run_id: str, attempt_number: int) -> tuple[object | None, bool]:
        exit_recorded = False
        for event in reversed(self.ledger.list_events(run_id)):
            if event.attempt_number != attempt_number:
                continue
            if event.event_type == "runner.exit":
                exit_recorded = True
                continue
            if event.event_type == "runner.launched":
                return event, exit_recorded
        return None, exit_recorded

    def _failure_class_from_events(self, run_id: str) -> str:
        for event in reversed(self.ledger.list_events(run_id)):
            if event.event_type == "runner.timeout":
                timeout_type = event.payload.get("timeout_type")
                if isinstance(timeout_type, str) and timeout_type:
                    return timeout_type
            if event.event_type == "runner.exit":
                exit_code = event.payload.get("exit_code")
                if isinstance(exit_code, int):
                    return f"exit={exit_code}"
                if isinstance(exit_code, str) and exit_code:
                    return f"exit={exit_code}"
            if event.event_type == "runner.launch_failed":
                return "launch_failed"
            if event.event_type == "state.transition":
                runner_signal = event.payload.get("runner_signal")
                if isinstance(runner_signal, str) and runner_signal:
                    return runner_signal
        return "recovery_missing_process"

    def _event_pid(self, event: object) -> int | None:
        payload = getattr(event, "payload", {})
        pid = payload.get("pid")
        if isinstance(pid, int):
            return pid
        if isinstance(pid, str) and pid.isdigit():
            return int(pid)
        return None

    def _event_command_name(self, event: object) -> str:
        payload = getattr(event, "payload", {})
        command = payload.get("command")
        if isinstance(command, list) and command:
            return os.path.basename(str(command[0]))
        if isinstance(command, str) and command:
            return os.path.basename(command)
        return ""

    def _is_expected_process_alive(self, pid: int, command_name: str) -> bool:
        if not self._is_process_alive(pid):
            return False
        if not command_name:
            return True
        actual = self._process_command_name(pid)
        return actual == command_name

    def _process_command_name(self, pid: int) -> str:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                raw = handle.read().split(b"\0", 1)[0]
        except OSError:
            return ""
        if not raw:
            return ""
        return os.path.basename(raw.decode(errors="ignore"))

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

    def _tick_result(
        self,
        processed: bool,
        idle: bool,
        ok: bool,
        run_id: str,
        final_state: str,
        outcome: str,
        error: str,
        heartbeat: RuntimeHeartbeatRecord,
    ) -> DaemonTickResult:
        return DaemonTickResult(
            worker_name=self.worker_name,
            processed=processed,
            idle=idle,
            ok=ok,
            run_id=run_id,
            final_state=final_state,
            outcome=outcome,
            error=error,
            heartbeat_status=heartbeat.status,
            heartbeat_id=heartbeat.heartbeat_id,
        )
