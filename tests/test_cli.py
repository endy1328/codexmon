from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codexmon import __version__
from codexmon.cli import build_parser, main
from codexmon.daemon_runtime import DaemonTickResult
from codexmon.ledger import RunLedger
from codexmon.orchestrator import OrchestrationResult


class CliTestCase(unittest.TestCase):
    def test_parser_exposes_expected_commands(self) -> None:
        parser = build_parser()
        help_text = parser.format_help()
        self.assertIn("version", help_text)
        self.assertIn("doctor", help_text)
        self.assertIn("start", help_text)
        self.assertIn("execute", help_text)
        self.assertIn("daemon", help_text)
        self.assertIn("status", help_text)
        self.assertIn("stop", help_text)
        self.assertIn("retry", help_text)
        self.assertIn("approvals", help_text)
        self.assertIn("workspace", help_text)
        self.assertIn("runner", help_text)
        self.assertIn("telegram", help_text)
        self.assertIn("handoff", help_text)

    def test_version_command_prints_package_version(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            exit_code = main(["version"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(buffer.getvalue().strip(), __version__)

    def test_doctor_command_prints_baseline_fields(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            exit_code = main(["doctor"])
        output = buffer.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("version=", output)
        self.assertIn("python=", output)
        self.assertIn("db_path=", output)
        self.assertIn("schema_version=", output)
        self.assertIn("repo_path=", output)
        self.assertIn("worktree_root=", output)
        self.assertIn("codex_command=", output)
        self.assertIn("codex_sandbox=", output)
        self.assertIn("github_token=", output)
        self.assertIn("github_api_base=", output)
        self.assertIn("github_base_branch=", output)
        self.assertIn("local_check_command=", output)
        self.assertIn("daemon_worker_name=", output)
        self.assertIn("daemon_poll_interval_seconds=", output)
        self.assertIn("telegram_bot_token=", output)
        self.assertIn("telegram_api_base=", output)

    def test_start_and_status_commands_persist_and_read_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codexmon.db"
            env = os.environ.copy()
            env["CODEXMON_DB_PATH"] = str(db_path)
            with mock.patch.dict(os.environ, env, clear=True):
                start_buffer = StringIO()
                with redirect_stdout(start_buffer):
                    start_exit_code = main(["start", "Synthetic status smoke test"])
                self.assertEqual(start_exit_code, 0)
                start_output = start_buffer.getvalue()
                self.assertIn("run_id=", start_output)

                run_id = next(
                    line.split("=", 1)[1]
                    for line in start_output.splitlines()
                    if line.startswith("run_id=")
                )
                status_buffer = StringIO()
                with redirect_stdout(status_buffer):
                    status_exit_code = main(["status", run_id])
                status_output = status_buffer.getvalue()

        self.assertEqual(status_exit_code, 0)
        self.assertIn(f"run_id={run_id}", status_output)
        self.assertIn("current_state=queued", status_output)
        self.assertIn("instruction_summary=Synthetic status smoke test", status_output)

    def test_execute_command_delegates_to_supervisor_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codexmon.db"
            env = os.environ.copy()
            env["CODEXMON_DB_PATH"] = str(db_path)
            with mock.patch.dict(os.environ, env, clear=True):
                start_buffer = StringIO()
                with redirect_stdout(start_buffer):
                    self.assertEqual(main(["start", "Runtime delegate smoke test"]), 0)
                run_id = next(
                    line.split("=", 1)[1]
                    for line in start_buffer.getvalue().splitlines()
                    if line.startswith("run_id=")
                )

                fake_result = OrchestrationResult(
                    run_id=run_id,
                    task_id="task_123",
                    final_state="completed",
                    outcome="PR opened",
                    state_reason="PR opened successfully",
                    attempt_number=1,
                    active_branch="codexmon/test",
                    active_worktree="/tmp/codexmon/test",
                    approval_required=False,
                    approval_request_id="",
                    pr_reference="github#17",
                    retries_used=0,
                    lock_released=True,
                    notifications_sent=2,
                )
                runtime = mock.Mock()
                runtime.execute_run.return_value = fake_result

                buffer = StringIO()
                with mock.patch("codexmon.cli.build_supervisor_runtime", return_value=runtime):
                    with redirect_stdout(buffer):
                        exit_code = main(["execute", run_id, "--json"])

        self.assertEqual(exit_code, 0)
        runtime.execute_run.assert_called_once()
        self.assertIn('"final_state": "completed"', buffer.getvalue())

    def test_start_execute_option_uses_supervisor_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codexmon.db"
            env = os.environ.copy()
            env["CODEXMON_DB_PATH"] = str(db_path)
            fake_result = OrchestrationResult(
                run_id="",
                task_id="task_123",
                final_state="awaiting_human",
                outcome="needs human decision",
                state_reason="approval required: dependency-manifest-or-lockfile",
                attempt_number=1,
                active_branch="codexmon/test",
                active_worktree="/tmp/codexmon/test",
                approval_required=True,
                approval_request_id="approval_123",
                pr_reference="",
                retries_used=0,
                lock_released=False,
                notifications_sent=2,
            )
            runtime = mock.Mock()

            def _execute_run(run_id: str, instruction: str, residual_risk_note: str, chat_id: str) -> OrchestrationResult:
                return OrchestrationResult(
                    run_id=run_id,
                    task_id=fake_result.task_id,
                    final_state=fake_result.final_state,
                    outcome=fake_result.outcome,
                    state_reason=fake_result.state_reason,
                    attempt_number=fake_result.attempt_number,
                    active_branch=fake_result.active_branch,
                    active_worktree=fake_result.active_worktree,
                    approval_required=fake_result.approval_required,
                    approval_request_id=fake_result.approval_request_id,
                    pr_reference=fake_result.pr_reference,
                    retries_used=fake_result.retries_used,
                    lock_released=fake_result.lock_released,
                    notifications_sent=fake_result.notifications_sent,
                )

            runtime.execute_run.side_effect = _execute_run
            with mock.patch.dict(os.environ, env, clear=True):
                buffer = StringIO()
                with mock.patch("codexmon.cli.build_supervisor_runtime", return_value=runtime):
                    with redirect_stdout(buffer):
                        exit_code = main(
                            [
                                "start",
                                "Runtime execute option",
                                "--execute",
                                "--json",
                            ]
                        )

        self.assertEqual(exit_code, 0)
        runtime.execute_run.assert_called_once()
        self.assertIn('"final_state": "awaiting_human"', buffer.getvalue())

    def test_daemon_run_once_command_delegates_to_supervisor_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codexmon.db"
            env = os.environ.copy()
            env["CODEXMON_DB_PATH"] = str(db_path)
            daemon = mock.Mock()
            daemon.run_once.return_value = DaemonTickResult(
                worker_name="codexmon-daemon",
                processed=True,
                idle=False,
                ok=True,
                run_id="run_123",
                final_state="completed",
                outcome="PR opened",
                error="",
                heartbeat_status="completed",
                heartbeat_id=7,
            )
            with mock.patch.dict(os.environ, env, clear=True):
                buffer = StringIO()
                with mock.patch("codexmon.cli.build_supervisor_daemon", return_value=daemon):
                    with redirect_stdout(buffer):
                        exit_code = main(["daemon", "run-once", "--json"])

        self.assertEqual(exit_code, 0)
        daemon.run_once.assert_called_once()
        self.assertIn('"heartbeat_status": "completed"', buffer.getvalue())

    def test_daemon_status_command_reads_heartbeats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codexmon.db"
            ledger = RunLedger(db_path)
            ledger.initialize()
            ledger.record_runtime_heartbeat(
                worker_name="codexmon-daemon",
                status="idle",
                payload={"runnable_runs": 0},
            )
            env = os.environ.copy()
            env["CODEXMON_DB_PATH"] = str(db_path)
            buffer = StringIO()
            with mock.patch.dict(os.environ, env, clear=True):
                with redirect_stdout(buffer):
                    exit_code = main(["daemon", "status", "--limit", "1"])

        self.assertEqual(exit_code, 0)
        self.assertIn("worker_name=codexmon-daemon", buffer.getvalue())
        self.assertIn("status=idle", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
