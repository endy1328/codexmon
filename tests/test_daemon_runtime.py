from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codexmon.approval_policy import ApprovalPolicyService
from codexmon.codex_adapter import CodexAdapter
from codexmon.daemon_runtime import SupervisorDaemon
from codexmon.failure_policy import FailurePolicySettings, FailureSignalController
from codexmon.ledger import RunLedger
from codexmon.orchestrator import SupervisorRuntime
from codexmon.pr_handoff import PRHandoffService, PullRequestRecord
from codexmon.telegram_notifier import TelegramNotifier, TelegramTransportMessage
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
    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
    ) -> PullRequestRecord:
        return PullRequestRecord(number=17, url="https://example.com/pull/17")

    def get_ci_visibility(self, owner: str, repo: str, ref: str) -> str:
        return "success"


def make_script(base_dir: Path, name: str, body: str) -> Path:
    script_path = base_dir / name
    script_path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | 0o111)
    return script_path


class SupervisorDaemonTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.repo_path, self.remote_path = init_temp_repo_with_origin(self.base_path)
        self.db_path = self.base_path / "codexmon.db"
        self.ledger = RunLedger(self.db_path)
        self.ledger.initialize()
        self.transport = FakeTelegramTransport()
        self.notifier = TelegramNotifier(
            ledger=self.ledger,
            transport=self.transport,
            default_chat_id="1001",
        )
        self.allocator = WorktreeAllocator(
            ledger=self.ledger,
            repo_path=self.repo_path,
            worktree_root=self.repo_path / ".codexmon" / "worktrees",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.repo_path / ".codexmon", ignore_errors=True)
        self.temp_dir.cleanup()

    def _build_daemon(self, codex_command: str) -> SupervisorDaemon:
        runtime = SupervisorRuntime(
            ledger=self.ledger,
            allocator=self.allocator,
            failure_controller=FailureSignalController(
                ledger=self.ledger,
                adapter=CodexAdapter(self.ledger, codex_command=codex_command),
                settings=FailurePolicySettings(
                    automatic_retry_budget=1,
                    idle_timeout_seconds=5.0,
                    wall_clock_timeout_seconds=5.0,
                ),
            ),
            approval_policy=ApprovalPolicyService(self.ledger, default_base_branch="main"),
            handoff_service=PRHandoffService(
                ledger=self.ledger,
                github_client=FakeGitHubClient(),
                default_base_branch="main",
                local_check_command="python3 -c \"print('daemon-ok')\"",
            ),
            notifier=self.notifier,
        )
        return SupervisorDaemon(
            ledger=self.ledger,
            runtime=runtime,
            worker_name="codexmon-daemon",
            poll_interval_seconds=0.01,
        )

    def test_run_once_processes_queued_run_and_records_heartbeats(self) -> None:
        script = make_script(
            self.base_path,
            "daemon-success.sh",
            "printf 'daemon success\\n' > daemon.txt\n",
        )
        daemon = self._build_daemon(str(script))
        task = self.ledger.create_task("daemon success path", repo_owner="octo", repo_name="hello-world")
        run = self.ledger.create_run(task.task_id)

        result = daemon.run_once()
        heartbeats = self.ledger.list_runtime_heartbeats(limit=5, worker_name="codexmon-daemon")

        self.assertTrue(result.ok)
        self.assertTrue(result.processed)
        self.assertEqual(result.final_state, "completed")
        self.assertEqual(self.ledger.get_run(run.run_id).pr_reference, "github#17")
        self.assertEqual([item.status for item in heartbeats[:2]], ["completed", "picked"])

    def test_run_once_resumes_retry_pending_run_after_operator_approval(self) -> None:
        script = make_script(
            self.base_path,
            "daemon-approve-success.sh",
            "printf 'daemon approve success\\n' > approved.txt\n",
        )
        daemon = self._build_daemon(str(script))
        task = self.ledger.create_task("daemon approval path", repo_owner="octo", repo_name="hello-world")
        run = self.ledger.create_run(task.task_id)

        self.allocator.allocate(run.run_id)
        self.ledger.transition_run(run.run_id, "running", "runner launched")
        approval_request_id = self.ledger.request_approval(
            run.run_id,
            requested_by="policy",
            payload={"change_class": "dependency-manifest-or-lockfile"},
        )
        self.ledger.transition_run(
            run.run_id,
            "awaiting_human",
            "approval required: dependency-manifest-or-lockfile",
        )
        approval_result = self.notifier.process_inbound_text(
            text=f"/approve {run.run_id} {approval_request_id}",
            operator_id="operator-daemon",
            chat_id="1001",
            message_id="200",
        )
        result = daemon.run_once()

        self.assertTrue(approval_result.accepted)
        self.assertTrue(result.ok)
        self.assertEqual(result.final_state, "completed")
        self.assertEqual(self.ledger.get_run(run.run_id).current_state, "completed")

    def test_serve_records_started_idle_and_stopped_heartbeats(self) -> None:
        daemon = self._build_daemon(str(make_script(self.base_path, "noop.sh", "exit 0\n")))

        result = daemon.serve(iterations=2, sleep_fn=lambda *_: None)
        heartbeats = self.ledger.list_runtime_heartbeats(limit=10, worker_name="codexmon-daemon")
        statuses = [item.status for item in heartbeats]

        self.assertEqual(result.iterations, 2)
        self.assertEqual(result.idle_iterations, 2)
        self.assertIn("started", statuses)
        self.assertIn("idle", statuses)
        self.assertIn("stopped", statuses)


if __name__ == "__main__":
    unittest.main()
