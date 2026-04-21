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
from codexmon.ledger import RunLedger
from codexmon.pr_handoff import PRHandoffService, PullRequestRecord
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
        return PullRequestRecord(number=7, url="https://example.com/pull/7")

    def get_ci_visibility(self, owner: str, repo: str, ref: str) -> str:
        return "success"


class PRHandoffServiceTestCase(unittest.TestCase):
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

    def tearDown(self) -> None:
        shutil.rmtree(self.repo_path / ".codexmon", ignore_errors=True)
        self.temp_dir.cleanup()

    def prepare_pr_handoff_run(self, summary: str = "Add feature") -> tuple[str, Path]:
        task = self.ledger.create_task(summary, repo_owner="octo", repo_name="hello-world")
        run = self.ledger.create_run(task.task_id)
        allocation = self.allocator.allocate(run.run_id)
        workspace_path = Path(allocation.workspace_path)
        (workspace_path / "feature.txt").write_text("feature change\n", encoding="utf-8")
        self.ledger.transition_run(run.run_id, "running", "runner launched")
        self.ledger.transition_run(run.run_id, "pr_handoff", "success path reached")
        return run.run_id, workspace_path

    def test_execute_creates_commit_pushes_branch_and_persists_pr_reference(self) -> None:
        run_id, workspace_path = self.prepare_pr_handoff_run("Add PR handoff feature")
        github = FakeGitHubClient()
        service = PRHandoffService(
            ledger=self.ledger,
            github_client=github,
            default_base_branch="main",
            local_check_command="python3 -c \"print('check-ok')\"",
        )

        result = service.execute(run_id, residual_risk_note="manual regression review")
        updated = self.ledger.get_run(run_id)
        events = [event.event_type for event in self.ledger.list_events(run_id)]
        remote_branch = subprocess.run(
            ["git", "--git-dir", str(self.remote_path), "branch", "--list", updated.active_branch],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

        self.assertEqual(result.final_state, "completed")
        self.assertEqual(updated.current_state, "completed")
        self.assertEqual(result.pr_reference, "github#7")
        self.assertEqual(result.ci_status, "success")
        self.assertIn("feature.txt", result.changed_files_summary)
        self.assertIn(updated.active_branch, remote_branch)
        self.assertIn("handoff.branch.pushed", events)
        self.assertIn("github.pr.created", events)
        self.assertIn("## Local Check Result", github.created_prs[0]["body"])
        self.assertIn("manual regression review", github.created_prs[0]["body"])
        self.assertTrue((workspace_path / "feature.txt").exists())

    def test_execute_halts_when_local_check_bundle_is_missing(self) -> None:
        run_id, _workspace_path = self.prepare_pr_handoff_run("Check missing")
        service = PRHandoffService(
            ledger=self.ledger,
            github_client=FakeGitHubClient(),
            default_base_branch="main",
            local_check_command="",
        )

        result = service.execute(run_id)

        self.assertEqual(result.final_state, "halted")
        self.assertEqual(self.ledger.get_run(run_id).current_state, "halted")
        self.assertIn("local check bundle", self.ledger.get_run(run_id).state_reason)


class PRHandoffCliTestCase(unittest.TestCase):
    def test_handoff_cli_executes_success_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            repo_path, _remote_path = init_temp_repo_with_origin(base_path)
            db_path = base_path / "codexmon.db"
            ledger = RunLedger(db_path)
            ledger.initialize()
            allocator = WorktreeAllocator(
                ledger=ledger,
                repo_path=repo_path,
                worktree_root=repo_path / ".codexmon" / "worktrees",
            )
            task = ledger.create_task("CLI handoff", repo_owner="octo", repo_name="hello-world")
            run = ledger.create_run(task.task_id)
            allocation = allocator.allocate(run.run_id)
            workspace_path = Path(allocation.workspace_path)
            (workspace_path / "cli.txt").write_text("cli path\n", encoding="utf-8")
            ledger.transition_run(run.run_id, "running", "runner launched")
            ledger.transition_run(run.run_id, "pr_handoff", "success path reached")

            service = PRHandoffService(
                ledger=ledger,
                github_client=FakeGitHubClient(),
                default_base_branch="main",
                local_check_command="python3 -c \"print('ok')\"",
            )
            env = os.environ.copy()
            env["CODEXMON_DB_PATH"] = str(db_path)
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch("codexmon.cli.build_pr_handoff_service", return_value=service):
                    output_buffer = StringIO()
                    with redirect_stdout(output_buffer):
                        exit_code = main(["handoff", run.run_id, "--residual-risk-note", "cli risk"])

        self.assertEqual(exit_code, 0)
        self.assertIn("final_state=completed", output_buffer.getvalue())
        self.assertIn("pr_reference=github#7", output_buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
