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


class SupervisorRuntimeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.repo_path, self.remote_path = init_temp_repo_with_origin(self.base_path)
        self.db_path = self.base_path / "codexmon.db"
        self.ledger = RunLedger(self.db_path)
        self.ledger.initialize()
        self.transport = FakeTelegramTransport()

    def tearDown(self) -> None:
        shutil.rmtree(self.repo_path / ".codexmon", ignore_errors=True)
        self.temp_dir.cleanup()

    def _build_runtime(self, codex_command: str) -> SupervisorRuntime:
        allocator = WorktreeAllocator(
            ledger=self.ledger,
            repo_path=self.repo_path,
            worktree_root=self.repo_path / ".codexmon" / "worktrees",
        )
        controller = FailureSignalController(
            ledger=self.ledger,
            adapter=CodexAdapter(self.ledger, codex_command=codex_command),
            settings=FailurePolicySettings(
                automatic_retry_budget=1,
                idle_timeout_seconds=5.0,
                wall_clock_timeout_seconds=5.0,
            ),
        )
        notifier = TelegramNotifier(
            ledger=self.ledger,
            transport=self.transport,
            default_chat_id="1001",
        )
        approval_policy = ApprovalPolicyService(self.ledger, default_base_branch="main")
        handoff = PRHandoffService(
            ledger=self.ledger,
            github_client=FakeGitHubClient(),
            default_base_branch="main",
            local_check_command="python3 -c \"print('runtime-ok')\"",
        )
        return SupervisorRuntime(
            ledger=self.ledger,
            allocator=allocator,
            failure_controller=controller,
            approval_policy=approval_policy,
            handoff_service=handoff,
            notifier=notifier,
        )

    def test_execute_run_completes_success_path_and_releases_lock(self) -> None:
        script = make_script(
            self.base_path,
            "codex-success.sh",
            "printf 'runtime success\\n' > runtime.txt\nprintf '{\"status\":\"ok\"}\\n'\n",
        )
        runtime = self._build_runtime(str(script))
        task = self.ledger.create_task(
            "Runtime success path",
            repo_owner="octo",
            repo_name="hello-world",
        )
        run = self.ledger.create_run(task.task_id)

        result = runtime.execute_run(run.run_id, instruction="runtime success path", residual_risk_note="risk")
        events = self.ledger.list_events(run.run_id)

        self.assertEqual(result.final_state, "completed")
        self.assertEqual(result.pr_reference, "github#17")
        self.assertTrue(result.lock_released)
        self.assertGreaterEqual(result.notifications_sent, 2)
        self.assertEqual(self.ledger.get_run(run.run_id).current_state, "completed")
        self.assertEqual(len(self.ledger.list_repository_locks()), 0)
        self.assertIn("preflight.check", [event.event_type for event in events])
        self.assertIn("orchestrator.execution.finished", [event.event_type for event in events])
        self.assertGreaterEqual(len(self.transport.messages), 2)

    def test_execute_run_moves_risky_success_diff_to_awaiting_human(self) -> None:
        script = make_script(
            self.base_path,
            "codex-approval.sh",
            "printf '[project]\\nname = \"approval\"\\n' > pyproject.toml\n",
        )
        runtime = self._build_runtime(str(script))
        task = self.ledger.create_task(
            "Runtime approval path",
            repo_owner="octo",
            repo_name="hello-world",
        )
        run = self.ledger.create_run(task.task_id)

        result = runtime.execute_run(run.run_id, instruction="runtime approval path")

        self.assertEqual(result.final_state, "awaiting_human")
        self.assertTrue(result.approval_required)
        self.assertTrue(result.approval_request_id)
        self.assertFalse(result.lock_released)
        self.assertEqual(self.ledger.get_run(run.run_id).outcome, "needs human decision")
        self.assertEqual(len(self.ledger.list_approvals(run.run_id, status="pending")), 1)
        self.assertEqual(len(self.ledger.list_repository_locks()), 1)
        self.assertGreaterEqual(len(self.transport.messages), 2)


if __name__ == "__main__":
    unittest.main()
