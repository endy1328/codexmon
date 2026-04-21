from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
