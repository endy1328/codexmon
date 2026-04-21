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


class CliTestCase(unittest.TestCase):
    def test_parser_exposes_expected_commands(self) -> None:
        parser = build_parser()
        help_text = parser.format_help()
        self.assertIn("version", help_text)
        self.assertIn("doctor", help_text)
        self.assertIn("start", help_text)
        self.assertIn("status", help_text)
        self.assertIn("workspace", help_text)
        self.assertIn("runner", help_text)
        self.assertIn("telegram", help_text)

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


if __name__ == "__main__":
    unittest.main()
