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

from codexmon.approval_policy import ApprovalPolicyService
from codexmon.cli import main
from codexmon.ledger import RunLedger
from codexmon.workspace import WorktreeAllocator


def init_temp_repo(base_dir: Path) -> Path:
    repo_path = base_dir / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_path), "config", "user.name", "codexmon-test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.email", "codexmon@example.com"],
        check=True,
    )
    (repo_path / "README.md").write_text("temp repo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo_path), "commit", "-m", "init"], check=True, capture_output=True)
    return repo_path


class ApprovalPolicyServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.repo_path = init_temp_repo(self.base_path)
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

    def prepare_running_run(self, summary: str = "approval scan") -> tuple[str, Path]:
        task = self.ledger.create_task(summary)
        run = self.ledger.create_run(task.task_id)
        allocation = self.allocator.allocate(run.run_id)
        workspace_path = Path(allocation.workspace_path)
        self.ledger.transition_run(run.run_id, "running", "runner launched")
        return run.run_id, workspace_path

    def test_scan_moves_risky_diff_to_awaiting_human(self) -> None:
        run_id, workspace_path = self.prepare_running_run("dependency change")
        (workspace_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        service = ApprovalPolicyService(self.ledger)

        result = service.scan(run_id)
        run = self.ledger.get_run(run_id)
        approvals = self.ledger.list_approvals(run_id, status="pending")

        self.assertTrue(result.approval_required)
        self.assertEqual(result.final_state, "awaiting_human")
        self.assertIn("dependency-manifest-or-lockfile", result.matched_rules)
        self.assertEqual(run.current_state, "awaiting_human")
        self.assertEqual(run.approval_status, "pending")
        self.assertEqual(len(approvals), 1)

    def test_scan_accepts_harmless_diff_without_transition(self) -> None:
        run_id, workspace_path = self.prepare_running_run("harmless change")
        (workspace_path / "feature.txt").write_text("safe\n", encoding="utf-8")
        service = ApprovalPolicyService(self.ledger)

        result = service.scan(run_id)

        self.assertFalse(result.approval_required)
        self.assertEqual(result.final_state, "running")
        self.assertEqual(self.ledger.get_run(run_id).current_state, "running")


class ApprovalPolicyCliTestCase(unittest.TestCase):
    def test_approvals_scan_cli_reports_pending_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            repo_path = init_temp_repo(base_path)
            db_path = base_path / "codexmon.db"
            ledger = RunLedger(db_path)
            ledger.initialize()
            allocator = WorktreeAllocator(
                ledger=ledger,
                repo_path=repo_path,
                worktree_root=repo_path / ".codexmon" / "worktrees",
            )
            task = ledger.create_task("approval cli")
            run = ledger.create_run(task.task_id)
            allocation = allocator.allocate(run.run_id)
            workspace_path = Path(allocation.workspace_path)
            ledger.transition_run(run.run_id, "running", "runner launched")
            (workspace_path / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
            (workspace_path / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
            env = os.environ.copy()
            env["CODEXMON_DB_PATH"] = str(db_path)
            env["CODEXMON_GITHUB_BASE_BRANCH"] = "main"
            with mock.patch.dict(os.environ, env, clear=True):
                output_buffer = StringIO()
                with redirect_stdout(output_buffer):
                    exit_code = main(["approvals", "scan", run.run_id])

        self.assertEqual(exit_code, 0)
        self.assertIn("approval_required=True", output_buffer.getvalue())
        self.assertIn("final_state=awaiting_human", output_buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
