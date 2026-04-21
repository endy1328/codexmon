from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codexmon.cli import main
from codexmon.failure_policy import FailurePolicySettings, FailureSignalController
from codexmon.ledger import RunLedger
from codexmon.pr_handoff import PRHandoffService, PullRequestRecord
from codexmon.telegram_notifier import (
    TelegramNotifier,
    TelegramNotifierError,
    TelegramTransportMessage,
)
from codexmon.workspace import WorktreeAllocator


def init_temp_repo_with_origin(base_dir: Path) -> tuple[Path, Path]:
    remote_path = base_dir / "origin.git"
    subprocess.run(["git", "init", "--bare", str(remote_path)], check=True, capture_output=True)
    repo_path = base_dir / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_path), "config", "user.name", "codexmon-test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.email", "codexmon@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo_path), "remote", "add", "origin", str(remote_path)], check=True)
    (repo_path / "README.md").write_text("temp repo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo_path), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "push", "-u", "origin", "main"],
        check=True,
        capture_output=True,
    )
    return repo_path, remote_path


class FakeTelegramTransport:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def send_message(
        self, chat_id: str, text: str, reply_to_message_id: str = ""
    ) -> TelegramTransportMessage:
        message_id = str(len(self.messages) + 1)
        self.messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "message_id": message_id,
            }
        )
        return TelegramTransportMessage(chat_id=chat_id, message_id=message_id, raw={"ok": True})


class FakeGitHubClient:
    def __init__(self) -> None:
        self.created_prs: list[dict[str, str]] = []

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
    ) -> PullRequestRecord:
        self.created_prs.append(
            {
                "owner": owner,
                "repo": repo,
                "title": title,
                "body": body,
                "head_branch": head_branch,
                "base_branch": base_branch,
            }
        )
        return PullRequestRecord(number=17, url="https://example.com/pull/17")

    def get_ci_visibility(self, owner: str, repo: str, ref: str) -> str:
        return "success"


def make_script(base_dir: Path, name: str, body: str) -> Path:
    script_path = base_dir / name
    script_path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | 0o111)
    return script_path


class AcceptanceValidationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.repo_path, self.remote_path = init_temp_repo_with_origin(self.base_path)
        self.db_path = self.base_path / "codexmon.db"
        self.ledger = RunLedger(self.db_path)
        self.ledger.initialize()
        self.allocator = WorktreeAllocator(
            ledger=self.ledger,
            repo_path=self.repo_path,
            worktree_root=self.repo_path / ".codexmon" / "worktrees",
        )
        self.telegram_transport = FakeTelegramTransport()
        self.notifier = TelegramNotifier(
            ledger=self.ledger,
            transport=self.telegram_transport,
            default_chat_id="1001",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.repo_path / ".codexmon", ignore_errors=True)
        self.temp_dir.cleanup()

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["CODEXMON_DB_PATH"] = str(self.db_path)
        env["CODEXMON_REPO_PATH"] = str(self.repo_path)
        env["CODEXMON_WORKTREE_ROOT"] = str(self.repo_path / ".codexmon" / "worktrees")
        env["CODEXMON_GITHUB_BASE_BRANCH"] = "main"
        return env

    def test_success_path_covers_start_transition_telegram_and_pr_handoff(self) -> None:
        env = self._env()
        with mock.patch.dict(os.environ, env, clear=True):
            start_buffer = StringIO()
            with redirect_stdout(start_buffer):
                self.assertEqual(
                    main(
                        [
                            "start",
                            "Acceptance success path",
                            "--repo-owner",
                            "octo",
                            "--repo-name",
                            "hello-world",
                        ]
                    ),
                    0,
                )
            run_id = next(
                line.split("=", 1)[1]
                for line in start_buffer.getvalue().splitlines()
                if line.startswith("run_id=")
            )
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["workspace", "allocate", run_id]), 0)

        run = self.ledger.get_run(run_id)
        workspace_path = Path(run.active_worktree)
        (workspace_path / "feature.txt").write_text("acceptance feature\n", encoding="utf-8")
        run = self.ledger.transition_run(run_id, "running", "runner launched")
        self.notifier.notify_run(run_id, event_label="start")
        status_result = self.notifier.process_inbound_text(
            text=f"/status {run_id}",
            operator_id="operator-acceptance",
            chat_id="1001",
            message_id="201",
        )
        self.assertTrue(status_result.accepted)

        service = PRHandoffService(
            ledger=self.ledger,
            github_client=FakeGitHubClient(),
            default_base_branch="main",
            local_check_command="python3 -c \"print('acceptance-ok')\"",
        )
        self.ledger.transition_run(run_id, "pr_handoff", "success path reached")
        result = service.execute(run_id, residual_risk_note="acceptance residual risk")
        self.notifier.notify_run(run_id, event_label="completed")

        transitions = [
            event.payload.get("state_to")
            for event in self.ledger.list_events(run_id)
            if event.event_type == "state.transition"
        ]
        created_events = [
            event
            for event in self.ledger.list_events(run_id)
            if event.event_type == "run.created"
        ]
        self.assertEqual(created_events[0].payload["state_to"], "queued")
        self.assertEqual(
            transitions,
            ["preflight", "workspace_allocated", "running", "pr_handoff", "completed"],
        )
        self.assertEqual(result.final_state, "completed")
        self.assertEqual(self.ledger.get_run(run_id).outcome, "PR opened")
        self.assertEqual(self.ledger.get_run(run_id).pr_reference, "github#17")
        self.assertGreaterEqual(len(self.telegram_transport.messages), 3)

    def test_failure_signal_path_covers_timeout_fingerprint_and_retry_budget(self) -> None:
        script = make_script(
            self.base_path,
            "always-fail.sh",
            "echo 'dominant failure token' 1>&2\nexit 7\n",
        )
        task = self.ledger.create_task("Acceptance failure path")
        run = self.ledger.create_run(task.task_id)
        self.allocator.allocate(run.run_id)

        from codexmon.codex_adapter import CodexAdapter

        controller = FailureSignalController(
            ledger=self.ledger,
            adapter=CodexAdapter(self.ledger, codex_command=str(script)),
            settings=FailurePolicySettings(
                automatic_retry_budget=1,
                idle_timeout_seconds=5.0,
                wall_clock_timeout_seconds=5.0,
            ),
        )
        result = controller.execute(run.run_id, "Fail twice for acceptance")

        self.assertEqual(result.final_state, "halted")
        self.assertEqual(result.retries_used, 1)
        self.assertEqual(len(self.ledger.list_failure_fingerprints(run.run_id)), 2)

    def test_approval_required_change_moves_to_awaiting_human_and_can_resume(self) -> None:
        env = self._env()
        with mock.patch.dict(os.environ, env, clear=True):
            start_buffer = StringIO()
            with redirect_stdout(start_buffer):
                self.assertEqual(main(["start", "Acceptance approval path"]), 0)
            run_id = next(
                line.split("=", 1)[1]
                for line in start_buffer.getvalue().splitlines()
                if line.startswith("run_id=")
            )
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["workspace", "allocate", run_id]), 0)

        run = self.ledger.get_run(run_id)
        workspace_path = Path(run.active_worktree)
        self.ledger.transition_run(run_id, "running", "runner launched")
        (workspace_path / "pyproject.toml").write_text("[project]\nname='acceptance'\n", encoding="utf-8")

        with mock.patch.dict(os.environ, env, clear=True):
            scan_buffer = StringIO()
            with redirect_stdout(scan_buffer):
                self.assertEqual(main(["approvals", "scan", run_id]), 0)
        self.notifier.notify_run(run_id, event_label="approval waiting")
        self.assertEqual(self.ledger.get_run(run_id).current_state, "awaiting_human")
        self.assertEqual(self.ledger.get_run(run_id).outcome, "needs human decision")

        approvals = self.ledger.list_approvals(run_id, status="pending")
        self.assertEqual(len(approvals), 1)
        approve_result = self.notifier.process_inbound_text(
            text=f"/approve {run_id} {approvals[0].approval_request_id}",
            operator_id="operator-acceptance",
            chat_id="1001",
            message_id="301",
        )
        self.assertTrue(approve_result.accepted)
        self.assertEqual(self.ledger.get_run(run_id).current_state, "retry_pending")
        self.ledger.release_repository_lock(run_id)

        retry_task = self.ledger.create_task("Acceptance retry path")
        retry_run = self.ledger.create_run(retry_task.task_id)
        self.allocator.allocate(retry_run.run_id)
        self.ledger.transition_run(retry_run.run_id, "running", "runner launched")
        self.ledger.transition_run(
            retry_run.run_id,
            "awaiting_human",
            "retryable-by-human: acceptance retry requested",
        )
        retry_result = self.notifier.process_inbound_text(
            text=f"/retry {retry_run.run_id}",
            operator_id="operator-acceptance",
            chat_id="1001",
            message_id="302",
        )
        self.assertTrue(retry_result.accepted)
        self.assertEqual(self.ledger.get_run(retry_run.run_id).current_state, "retry_pending")

    def test_bounded_halt_stop_interrupts_runner_and_releases_lock(self) -> None:
        env = self._env()
        with mock.patch.dict(os.environ, env, clear=True):
            start_buffer = StringIO()
            with redirect_stdout(start_buffer):
                self.assertEqual(main(["start", "Acceptance halt path"]), 0)
            run_id = next(
                line.split("=", 1)[1]
                for line in start_buffer.getvalue().splitlines()
                if line.startswith("run_id=")
            )
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["workspace", "allocate", run_id]), 0)

        run = self.ledger.get_run(run_id)
        workspace_path = Path(run.active_worktree)
        self.ledger.transition_run(run_id, "running", "runner launched")
        process = subprocess.Popen(
            [
                "python3",
                "-c",
                (
                    "import signal, time, sys\n"
                    "signal.signal(signal.SIGINT, lambda *_: sys.exit(130))\n"
                    "signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))\n"
                    "while True:\n"
                    "    time.sleep(0.1)\n"
                ),
            ],
            cwd=workspace_path,
        )
        self.ledger.append_event(
            run_id,
            event_type="runner.launched",
            reason_code="runner launched",
            payload={"pid": process.pid, "command": ["python3", "-c", "loop"]},
            attempt_number=1,
        )

        stop_result = self.notifier.process_inbound_text(
            text=f"/stop {run_id}",
            operator_id="operator-acceptance",
            chat_id="1001",
            message_id="401",
        )
        process.wait(timeout=5)
        events = self.ledger.list_events(run_id)
        event_types = [event.event_type for event in events]
        halt_index = next(index for index, event in enumerate(events) if event.reason_code == "kill switch requested via Telegram" and event.event_type == "state.transition")
        release_index = next(index for index, event in enumerate(events) if event.event_type == "repository.lock.released")

        self.assertTrue(stop_result.accepted)
        self.assertIsNotNone(process.returncode)
        self.assertIn("operator.stop.interrupt_sent", event_types)
        self.assertEqual(self.ledger.get_run(run_id).current_state, "halted")
        self.assertTrue(Path(self.ledger.get_run(run_id).active_worktree).exists())
        self.assertGreater(release_index, halt_index)


if __name__ == "__main__":
    unittest.main()
