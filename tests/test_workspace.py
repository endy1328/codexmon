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
from codexmon.ledger import RepositoryLockHeldError, RunLedger
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


class WorktreeAllocatorTestCase(unittest.TestCase):
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

    def test_allocate_creates_workspace_and_transitions_run(self) -> None:
        task = self.ledger.create_task("workspace allocate")
        run = self.ledger.create_run(task.task_id)

        result = self.allocator.allocate(run.run_id)
        updated = self.ledger.get_run(run.run_id)

        self.assertEqual(updated.current_state, "workspace_allocated")
        self.assertEqual(updated.active_branch, f"codexmon/{run.run_id}")
        self.assertEqual(result.branch_name, updated.active_branch)
        self.assertTrue(Path(result.workspace_path).exists())

        branch_output = subprocess.run(
            ["git", "-C", str(self.repo_path), "branch", "--list", updated.active_branch],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn(updated.active_branch, branch_output)

    def test_lock_conflict_halts_second_run_until_release(self) -> None:
        first_task = self.ledger.create_task("first run")
        first_run = self.ledger.create_run(first_task.task_id)
        self.allocator.allocate(first_run.run_id)

        second_task = self.ledger.create_task("second run")
        second_run = self.ledger.create_run(second_task.task_id)

        with self.assertRaises(RepositoryLockHeldError):
            self.allocator.allocate(second_run.run_id)

        halted = self.ledger.get_run(second_run.run_id)
        self.assertEqual(halted.current_state, "halted")
        self.assertIn("repository lock held", halted.state_reason)

        released = self.allocator.release(first_run.run_id, cleanup=True)
        self.assertTrue(released.lock_released)
        self.assertTrue(released.workspace_removed)
        self.assertEqual(len(self.ledger.list_repository_locks()), 0)

        third_task = self.ledger.create_task("third run")
        third_run = self.ledger.create_run(third_task.task_id)
        third_result = self.allocator.allocate(third_run.run_id)
        self.assertTrue(Path(third_result.workspace_path).exists())

    def test_diagnose_reports_lock_and_workspace_lifecycle(self) -> None:
        task = self.ledger.create_task("diagnose run")
        run = self.ledger.create_run(task.task_id)
        allocated = self.allocator.allocate(run.run_id)

        diagnostic = self.allocator.diagnose()
        self.assertEqual(len(diagnostic["locks"]), 1)
        self.assertEqual(diagnostic["locks"][0]["run_id"], run.run_id)
        assignment = diagnostic["workspace_assignments"][0]
        self.assertEqual(assignment["run_id"], run.run_id)
        self.assertTrue(assignment["exists_on_disk"])
        self.assertTrue(assignment["registered_in_git"])

        self.allocator.release(run.run_id, cleanup=True)
        diagnostic_after = self.allocator.diagnose()
        self.assertEqual(len(diagnostic_after["locks"]), 0)
        released_assignment = next(
            item
            for item in diagnostic_after["workspace_assignments"]
            if item["run_id"] == run.run_id
        )
        self.assertEqual(released_assignment["workspace_path"], allocated.workspace_path)
        self.assertIsNotNone(released_assignment["released_at"])
        self.assertFalse(released_assignment["exists_on_disk"])


class WorkspaceCliTestCase(unittest.TestCase):
    def test_workspace_cli_allocate_and_diagnose(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            repo_path = init_temp_repo(base_path)
            db_path = base_path / "codexmon.db"
            env = os.environ.copy()
            env["CODEXMON_DB_PATH"] = str(db_path)
            env["CODEXMON_REPO_PATH"] = str(repo_path)
            env["CODEXMON_WORKTREE_ROOT"] = str(repo_path / ".codexmon" / "worktrees")
            with mock.patch.dict(os.environ, env, clear=True):
                start_buffer = StringIO()
                with redirect_stdout(start_buffer):
                    start_exit_code = main(["start", "workspace cli run"])
                self.assertEqual(start_exit_code, 0)
                run_id = next(
                    line.split("=", 1)[1]
                    for line in start_buffer.getvalue().splitlines()
                    if line.startswith("run_id=")
                )

                allocate_buffer = StringIO()
                with redirect_stdout(allocate_buffer):
                    allocate_exit_code = main(["workspace", "allocate", run_id])
                self.assertEqual(allocate_exit_code, 0)
                allocate_output = allocate_buffer.getvalue()
                self.assertIn("current_state=workspace_allocated", allocate_output)
                self.assertIn(f"branch_name=codexmon/{run_id}", allocate_output)

                diagnose_buffer = StringIO()
                with redirect_stdout(diagnose_buffer):
                    diagnose_exit_code = main(["workspace", "diagnose", "--json"])
                self.assertEqual(diagnose_exit_code, 0)
                self.assertIn("\"locks\"", diagnose_buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
