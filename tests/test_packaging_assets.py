from __future__ import annotations

import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PackagingAssetsTestCase(unittest.TestCase):
    def test_systemd_assets_reference_daemon_wrapper(self) -> None:
        service_path = ROOT / "ops" / "systemd" / "codexmon-daemon.service"
        env_example_path = ROOT / "ops" / "systemd" / "codexmon-daemon.env.example"
        wrapper_path = ROOT / "scripts" / "run-codexmon-daemon.sh"

        self.assertTrue(service_path.exists())
        self.assertTrue(env_example_path.exists())
        self.assertTrue(wrapper_path.exists())
        self.assertTrue(os.access(wrapper_path, os.X_OK))

        service_text = service_path.read_text(encoding="utf-8")
        env_text = env_example_path.read_text(encoding="utf-8")

        self.assertIn("ExecStart=%h/projects/codexmon/scripts/run-codexmon-daemon.sh", service_text)
        self.assertIn("KillSignal=SIGTERM", service_text)
        self.assertIn("Restart=on-failure", service_text)
        self.assertIn("CODEXMON_ENV_FILE=", env_text)
        self.assertIn("CODEXMON_DAEMON_POLL_INTERVAL_SECONDS=", env_text)


if __name__ == "__main__":
    unittest.main()
