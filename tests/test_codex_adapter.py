from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codexmon.cli import main
from codexmon.codex_adapter import CodexAdapter
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


def make_fake_codex(base_dir: Path, exit_code: int) -> Path:
    script_path = base_dir / f"fake-codex-{exit_code}.sh"
    script_path.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            echo '{{"type":"session.started","cwd":"'$PWD'"}}'
            echo 'heartbeat: working'
            echo 'stderr: simulated warning' 1>&2
            exit {exit_code}
            """
        ),
        encoding="utf-8",
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    return script_path


class CodexAdapterTestCase(unittest.TestCase):
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

    def test_execute_run_persists_launch_output_and_success_exit(self) -> None:
        task = self.ledger.create_task("codex adapter success")
        run = self.ledger.create_run(task.task_id)
        self.allocator.allocate(run.run_id)
        fake_codex = make_fake_codex(self.base_path, exit_code=0)
        adapter = CodexAdapter(self.ledger, codex_command=str(fake_codex))

        result = adapter.execute_run(run.run_id, "Implement something")
        updated = self.ledger.get_run(run.run_id)
        events = self.ledger.list_events(run.run_id)

        self.assertTrue(result.launched)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(updated.current_state, "pr_handoff")
        self.assertGreaterEqual(result.stdout_line_count, 2)
        self.assertGreaterEqual(result.stderr_line_count, 1)
        event_types = [event.event_type for event in events]
        self.assertIn("runner.launched", event_types)
        self.assertIn("runner.output", event_types)
        self.assertIn("runner.exit", event_types)

    def test_execute_run_persists_failure_exit(self) -> None:
        task = self.ledger.create_task("codex adapter failure")
        run = self.ledger.create_run(task.task_id)
        self.allocator.allocate(run.run_id)
        fake_codex = make_fake_codex(self.base_path, exit_code=7)
        adapter = CodexAdapter(self.ledger, codex_command=str(fake_codex))

        result = adapter.execute_run(run.run_id, "Break something")
        updated = self.ledger.get_run(run.run_id)

        self.assertTrue(result.launched)
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(updated.current_state, "analyzing_failure")

    def test_launch_failure_is_persisted_without_transition(self) -> None:
        task = self.ledger.create_task("codex launch failure")
        run = self.ledger.create_run(task.task_id)
        self.allocator.allocate(run.run_id)
        adapter = CodexAdapter(self.ledger, codex_command=str(self.base_path / "missing-codex"))

        result = adapter.execute_run(run.run_id, "This will fail to launch")
        updated = self.ledger.get_run(run.run_id)
        events = self.ledger.list_events(run.run_id)

        self.assertFalse(result.launched)
        self.assertIsNone(result.exit_code)
        self.assertEqual(updated.current_state, "workspace_allocated")
        self.assertIn("runner.launch_failed", [event.event_type for event in events])


class CodexAdapterCliTestCase(unittest.TestCase):
    def test_runner_cli_executes_allocated_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            repo_path = init_temp_repo(base_path)
            fake_codex = make_fake_codex(base_path, exit_code=0)
            db_path = base_path / "codexmon.db"
            env = os.environ.copy()
            env["CODEXMON_DB_PATH"] = str(db_path)
            env["CODEXMON_REPO_PATH"] = str(repo_path)
            env["CODEXMON_WORKTREE_ROOT"] = str(repo_path / ".codexmon" / "worktrees")
            env["CODEXMON_CODEX_COMMAND"] = str(fake_codex)
            with mock.patch.dict(os.environ, env, clear=True):
                start_buffer = StringIO()
                with redirect_stdout(start_buffer):
                    start_exit_code = main(["start", "runner cli run"])
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

                runner_buffer = StringIO()
                with redirect_stdout(runner_buffer):
                    runner_exit_code = main(["runner", "run", run_id, "Do the work"])
                self.assertEqual(runner_exit_code, 0)
                runner_output = runner_buffer.getvalue()
                self.assertIn("launched=True", runner_output)
                self.assertIn("exit_code=0", runner_output)
                self.assertIn("final_state=pr_handoff", runner_output)


if __name__ == "__main__":
    unittest.main()
