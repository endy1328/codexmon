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
from codexmon.failure_policy import FailurePolicySettings, FailureSignalController
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


def make_script(base_dir: Path, name: str, body: str) -> Path:
    script_path = base_dir / name
    script_path.write_text("#!/bin/sh\n" + textwrap.dedent(body), encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    return script_path


class FailurePolicyTestCase(unittest.TestCase):
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

    def test_duplicate_failure_fingerprint_halts_after_single_retry(self) -> None:
        failing_script = make_script(
            self.base_path,
            "always-fail.sh",
            """
            echo "top-level failing test target" 1>&2
            exit 7
            """,
        )
        task = self.ledger.create_task("duplicate fingerprint halt")
        run = self.ledger.create_run(task.task_id)
        self.allocator.allocate(run.run_id)
        controller = FailureSignalController(
            ledger=self.ledger,
            adapter=CodexAdapter(self.ledger, codex_command=str(failing_script)),
            settings=FailurePolicySettings(
                automatic_retry_budget=1,
                idle_timeout_seconds=5.0,
                wall_clock_timeout_seconds=5.0,
            ),
        )

        result = controller.execute(run.run_id, "Fail twice")
        updated = self.ledger.get_run(run.run_id)
        fingerprints = self.ledger.list_failure_fingerprints(run.run_id)

        self.assertEqual(result.final_state, "halted")
        self.assertEqual(result.retries_used, 1)
        self.assertEqual(updated.attempt_number, 2)
        self.assertEqual(len(fingerprints), 2)
        self.assertEqual(fingerprints[0].fingerprint, fingerprints[1].fingerprint)
        self.assertIn("duplicate failure fingerprint", result.reason_code)

    def test_idle_timeout_is_recorded(self) -> None:
        idle_script = make_script(
            self.base_path,
            "idle-timeout.sh",
            """
            sleep 2
            exit 0
            """,
        )
        task = self.ledger.create_task("idle timeout")
        run = self.ledger.create_run(task.task_id)
        self.allocator.allocate(run.run_id)
        controller = FailureSignalController(
            ledger=self.ledger,
            adapter=CodexAdapter(self.ledger, codex_command=str(idle_script)),
            settings=FailurePolicySettings(
                automatic_retry_budget=0,
                idle_timeout_seconds=0.2,
                wall_clock_timeout_seconds=5.0,
            ),
        )

        result = controller.execute(run.run_id, "Wait quietly")
        event_types = [event.event_type for event in self.ledger.list_events(run.run_id)]

        self.assertEqual(result.final_state, "halted")
        self.assertIn("runner.timeout", event_types)
        self.assertEqual(self.ledger.get_run(run.run_id).current_state, "halted")

    def test_wall_clock_timeout_is_recorded(self) -> None:
        noisy_script = make_script(
            self.base_path,
            "wall-timeout.sh",
            """
            i=0
            while [ "$i" -lt 20 ]; do
              echo "heartbeat $i"
              sleep 0.1
              i=$((i + 1))
            done
            exit 0
            """,
        )
        task = self.ledger.create_task("wall timeout")
        run = self.ledger.create_run(task.task_id)
        self.allocator.allocate(run.run_id)
        controller = FailureSignalController(
            ledger=self.ledger,
            adapter=CodexAdapter(self.ledger, codex_command=str(noisy_script)),
            settings=FailurePolicySettings(
                automatic_retry_budget=0,
                idle_timeout_seconds=5.0,
                wall_clock_timeout_seconds=0.35,
            ),
        )

        result = controller.execute(run.run_id, "Keep printing")
        timeout_events = [
            event for event in self.ledger.list_events(run.run_id) if event.event_type == "runner.timeout"
        ]

        self.assertEqual(result.final_state, "halted")
        self.assertEqual(timeout_events[-1].payload["timeout_type"], "wall_clock_timeout")

    def test_recover_orphaned_run_reuses_retry_policy(self) -> None:
        retry_script = make_script(
            self.base_path,
            "recover-success.sh",
            """
            echo "recovered attempt"
            exit 0
            """,
        )
        task = self.ledger.create_task("recovery retry")
        run = self.ledger.create_run(task.task_id)
        self.allocator.allocate(run.run_id)
        run = self.ledger.transition_run(run.run_id, "running", "runner launched")
        self.ledger.append_event(
            run.run_id,
            event_type="runner.output",
            reason_code="stdout output",
            payload={"stream": "stdout", "line": "orphan recovery token"},
            attempt_number=run.attempt_number,
        )
        controller = FailureSignalController(
            ledger=self.ledger,
            adapter=CodexAdapter(self.ledger, codex_command=str(retry_script)),
            settings=FailurePolicySettings(
                automatic_retry_budget=1,
                idle_timeout_seconds=5.0,
                wall_clock_timeout_seconds=5.0,
            ),
        )

        result = controller.recover_orphaned_run(
            run_id=run.run_id,
            failure_class="recovery_missing_process",
            reason_code="orphaned running state recovered",
        )

        self.assertEqual(result.final_state, "retry_pending")
        self.assertEqual(result.retries_used, 1)
        self.assertIn("recovery_missing_process", result.last_failure_fingerprint)
        self.assertEqual(self.ledger.get_run(run.run_id).current_state, "retry_pending")


class FailurePolicyCliTestCase(unittest.TestCase):
    def test_runner_supervise_cli_applies_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            repo_path = init_temp_repo(base_path)
            failing_script = make_script(
                base_path,
                "cli-fail.sh",
                """
                echo "cli failure token" 1>&2
                exit 9
                """,
            )
            db_path = base_path / "codexmon.db"
            env = os.environ.copy()
            env["CODEXMON_DB_PATH"] = str(db_path)
            env["CODEXMON_REPO_PATH"] = str(repo_path)
            env["CODEXMON_WORKTREE_ROOT"] = str(repo_path / ".codexmon" / "worktrees")
            env["CODEXMON_CODEX_COMMAND"] = str(failing_script)
            env["CODEXMON_AUTOMATIC_RETRY_BUDGET"] = "1"
            env["CODEXMON_IDLE_TIMEOUT_SECONDS"] = "5"
            env["CODEXMON_WALL_CLOCK_TIMEOUT_SECONDS"] = "5"
            with mock.patch.dict(os.environ, env, clear=True):
                start_buffer = StringIO()
                with redirect_stdout(start_buffer):
                    main(["start", "policy cli run"])
                run_id = next(
                    line.split("=", 1)[1]
                    for line in start_buffer.getvalue().splitlines()
                    if line.startswith("run_id=")
                )
                with redirect_stdout(StringIO()):
                    main(["workspace", "allocate", run_id])

                supervise_buffer = StringIO()
                with redirect_stdout(supervise_buffer):
                    supervise_exit_code = main(["runner", "supervise", run_id, "apply policy"])

                self.assertEqual(supervise_exit_code, 0)
                output = supervise_buffer.getvalue()
                self.assertIn("final_state=halted", output)
                self.assertIn("retries_used=1", output)


if __name__ == "__main__":
    unittest.main()
