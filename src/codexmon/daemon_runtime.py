"""Background daemon worker for queued and resumable runs."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from codexmon.ledger import RunLedger, RuntimeHeartbeatRecord
from codexmon.orchestrator import OrchestrationResult, SupervisorRuntime


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
