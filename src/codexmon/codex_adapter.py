"""Thin adapter for launching Codex inside an allocated worktree."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import threading
import time

from codexmon.ledger import RunLedger
from codexmon.state_machine import TERMINAL_STATES


class CodexAdapterError(RuntimeError):
    """Base error for adapter failures."""


@dataclass(frozen=True)
class CodexExecutionResult:
    run_id: str
    workspace_path: str
    branch_name: str
    command: list[str]
    launched: bool
    exit_code: int | None
    duration_seconds: float
    stdout_line_count: int
    stderr_line_count: int
    failure_signal: str
    timed_out: bool
    final_state: str


class CodexAdapter:
    """Launch Codex in a run-specific worktree and persist lifecycle events."""

    def __init__(
        self,
        ledger: RunLedger,
        codex_command: str = "codex",
        model: str = "",
        sandbox_mode: str = "workspace-write",
    ) -> None:
        self.ledger = ledger
        self.codex_command = codex_command
        self.model = model
        self.sandbox_mode = sandbox_mode

    def execute_run(
        self,
        run_id: str,
        instruction: str,
        idle_timeout_seconds: float | None = None,
        wall_clock_timeout_seconds: float | None = None,
        defer_success_transition: bool = False,
    ) -> CodexExecutionResult:
        run = self.ledger.get_run(run_id)
        assignment = self.ledger.get_workspace_assignment(run_id)
        if assignment is None:
            raise CodexAdapterError(f"run '{run_id}' has no assigned workspace")

        if run.current_state not in {"workspace_allocated", "retry_pending"}:
            raise CodexAdapterError(
                f"run '{run_id}' must be in 'workspace_allocated' or 'retry_pending' before launch"
            )

        workspace_path = Path(assignment.workspace_path)
        if not workspace_path.exists():
            raise CodexAdapterError(f"workspace path '{workspace_path}' does not exist")

        command = self._build_command(workspace_path, instruction)
        started_at = time.monotonic()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        activity_lock = threading.Lock()
        activity = {
            "last_activity": started_at,
            "diff_snapshot": self._diff_snapshot(workspace_path),
        }

        self.ledger.append_event(
            run_id,
            event_type="runner.launch_requested",
            reason_code="runner launch requested",
            payload={"command": command, "workspace_path": str(workspace_path)},
        )

        try:
            process = subprocess.Popen(
                command,
                cwd=workspace_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
            )
        except OSError as exc:
            self.ledger.append_event(
                run_id,
                event_type="runner.launch_failed",
                reason_code="runner launch failed",
                payload={"command": command, "error": str(exc)},
            )
            return CodexExecutionResult(
                run_id=run_id,
                workspace_path=str(workspace_path),
                branch_name=assignment.branch_name,
                command=command,
                launched=False,
                exit_code=None,
                duration_seconds=round(time.monotonic() - started_at, 3),
                stdout_line_count=0,
                stderr_line_count=0,
                failure_signal="launch_failed",
                timed_out=False,
                final_state=run.current_state,
            )

        run = self.ledger.transition_run(
            run_id,
            "running",
            "runner launched",
            workspace_path=str(workspace_path),
            branch_name=assignment.branch_name,
        )
        self.ledger.append_event(
            run_id,
            event_type="runner.launched",
            reason_code="runner launched",
            payload={"command": command, "pid": process.pid},
            attempt_number=run.attempt_number,
        )

        stdout_thread = threading.Thread(
            target=self._capture_stream,
            args=(
                run_id,
                run.attempt_number,
                process.stdout,
                "stdout",
                stdout_lines,
                activity_lock,
                activity,
            ),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._capture_stream,
            args=(
                run_id,
                run.attempt_number,
                process.stderr,
                "stderr",
                stderr_lines,
                activity_lock,
                activity,
            ),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        timeout_signal = self._wait_for_process(
            run_id=run_id,
            attempt_number=run.attempt_number,
            process=process,
            workspace_path=workspace_path,
            started_at=started_at,
            activity_lock=activity_lock,
            activity=activity,
            idle_timeout_seconds=idle_timeout_seconds,
            wall_clock_timeout_seconds=wall_clock_timeout_seconds,
        )
        exit_code = process.wait()
        stdout_thread.join()
        stderr_thread.join()

        duration_seconds = round(time.monotonic() - started_at, 3)
        self.ledger.append_event(
            run_id,
            event_type="runner.exit",
            reason_code="runner exited",
            payload={
                "command": command,
                "exit_code": exit_code,
                "duration_seconds": duration_seconds,
                "stdout_line_count": len(stdout_lines),
                "stderr_line_count": len(stderr_lines),
            },
            attempt_number=run.attempt_number,
        )

        refreshed_run = self.ledger.get_run(run_id)
        if refreshed_run.current_state in TERMINAL_STATES:
            return CodexExecutionResult(
                run_id=run_id,
                workspace_path=str(workspace_path),
                branch_name=assignment.branch_name,
                command=command,
                launched=True,
                exit_code=exit_code,
                duration_seconds=duration_seconds,
                stdout_line_count=len(stdout_lines),
                stderr_line_count=len(stderr_lines),
                failure_signal=timeout_signal or ("" if exit_code == 0 else f"exit={exit_code}"),
                timed_out=timeout_signal in {"idle_timeout", "wall_clock_timeout"},
                final_state=refreshed_run.current_state,
            )

        failure_signal = timeout_signal or ("" if exit_code == 0 else f"exit={exit_code}")
        timed_out = timeout_signal in {"idle_timeout", "wall_clock_timeout"}

        if exit_code == 0 and not timeout_signal:
            if defer_success_transition:
                final_run = self.ledger.get_run(run_id)
                return CodexExecutionResult(
                    run_id=run_id,
                    workspace_path=str(workspace_path),
                    branch_name=assignment.branch_name,
                    command=command,
                    launched=True,
                    exit_code=exit_code,
                    duration_seconds=duration_seconds,
                    stdout_line_count=len(stdout_lines),
                    stderr_line_count=len(stderr_lines),
                    failure_signal="",
                    timed_out=False,
                    final_state=final_run.current_state,
                )
            final_run = self.ledger.transition_run(
                run_id,
                "pr_handoff",
                "success path reached",
                workspace_path=str(workspace_path),
                branch_name=assignment.branch_name,
                runner_signal="exit=0",
            )
        else:
            final_run = self.ledger.transition_run(
                run_id,
                "analyzing_failure",
                "failure, timeout, or loop signal",
                workspace_path=str(workspace_path),
                branch_name=assignment.branch_name,
                runner_signal=failure_signal or f"exit={exit_code}",
            )

        return CodexExecutionResult(
            run_id=run_id,
            workspace_path=str(workspace_path),
            branch_name=assignment.branch_name,
            command=command,
            launched=True,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
            stdout_line_count=len(stdout_lines),
            stderr_line_count=len(stderr_lines),
            failure_signal=failure_signal,
            timed_out=timed_out,
            final_state=final_run.current_state,
        )

    def _build_command(self, workspace_path: Path, instruction: str) -> list[str]:
        command = [
            self.codex_command,
            "exec",
            "-C",
            str(workspace_path),
            "--json",
            "--ephemeral",
            "--sandbox",
            self.sandbox_mode,
        ]
        if self.model:
            command.extend(["-m", self.model])
        command.append(instruction)
        return command

    def _capture_stream(
        self,
        run_id: str,
        attempt_number: int,
        stream: subprocess.PIPE[str] | None,
        stream_name: str,
        sink: list[str],
        activity_lock: threading.Lock,
        activity: dict[str, object],
    ) -> None:
        if stream is None:
            return

        with stream:
            for raw_line in stream:
                line = raw_line.rstrip("\n")
                sink.append(line)
                with activity_lock:
                    activity["last_activity"] = time.monotonic()
                payload: dict[str, object] = {"stream": stream_name, "line": line}
                try:
                    payload["parsed"] = json.loads(line)
                except json.JSONDecodeError:
                    pass
                self.ledger.append_event(
                    run_id,
                    event_type="runner.output",
                    reason_code=f"{stream_name} output",
                    payload=payload,
                    attempt_number=attempt_number,
                )

    def _wait_for_process(
        self,
        run_id: str,
        attempt_number: int,
        process: subprocess.Popen[str],
        workspace_path: Path,
        started_at: float,
        activity_lock: threading.Lock,
        activity: dict[str, object],
        idle_timeout_seconds: float | None,
        wall_clock_timeout_seconds: float | None,
    ) -> str:
        timeout_signal = ""
        while process.poll() is None:
            now = time.monotonic()
            diff_snapshot = self._diff_snapshot(workspace_path)
            with activity_lock:
                if diff_snapshot != activity["diff_snapshot"]:
                    activity["diff_snapshot"] = diff_snapshot
                    activity["last_activity"] = now
                last_activity = float(activity["last_activity"])

            if wall_clock_timeout_seconds and (now - started_at) >= wall_clock_timeout_seconds:
                timeout_signal = "wall_clock_timeout"
                break
            if idle_timeout_seconds and (now - last_activity) >= idle_timeout_seconds:
                timeout_signal = "idle_timeout"
                break
            time.sleep(0.1)

        if timeout_signal:
            self.ledger.append_event(
                run_id,
                event_type="runner.timeout",
                reason_code=timeout_signal,
                payload={"timeout_type": timeout_signal},
                attempt_number=attempt_number,
            )
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        return timeout_signal

    def _diff_snapshot(self, workspace_path: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(workspace_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip()
