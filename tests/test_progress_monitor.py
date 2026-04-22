from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codexmon.ledger import RunLedger
from codexmon.progress_monitor import ProgressMonitorService


def write_monitor_assets(base_dir: Path) -> tuple[Path, Path]:
    snapshot_path = base_dir / "progress.json"
    html_path = base_dir / "progress-monitor.html"
    snapshot_path.write_text(
        json.dumps(
            {
                "meta": {
                    "projectName": "codexmon",
                    "pageTitle": "구현 진행 모니터",
                    "phaseLabel": "구현 진행 중",
                    "updatedAt": "2026-04-22T00:00:00+09:00",
                    "currentFocus": "기본 포커스",
                    "currentSummary": "기본 요약",
                    "activeMilestoneId": "M9",
                    "activePacketId": "R5",
                    "autoRefreshSeconds": 15,
                },
                "summary": {
                    "currentState": "대기",
                    "nextCheckpoint": "기본 체크포인트",
                    "completionRule": "테스트 규칙",
                    "completionText": "테스트 기준선",
                },
                "runtime": {
                    "executionStatus": "idle",
                    "summary": "기본 runtime",
                    "lastHeartbeatAt": "2026-04-22T00:00:00+09:00",
                    "heartbeatGraceSeconds": 60,
                    "activeAgents": [],
                },
                "links": [],
                "milestones": [],
                "packets": [],
                "activityLog": [],
                "blockers": [],
                "watchItems": [
                    {
                        "level": "watch",
                        "label": "progress monitor DB 미구현",
                        "detail": "이 항목은 live builder에서 제거돼야 한다.",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    html_path.write_text("<!doctype html><title>monitor</title>", encoding="utf-8")
    return snapshot_path, html_path


class ProgressMonitorServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.snapshot_path, self.html_path = write_monitor_assets(self.base_path)
        self.db_path = self.base_path / "codexmon.db"
        self.ledger = RunLedger(self.db_path)
        self.ledger.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build_service(self) -> ProgressMonitorService:
        return ProgressMonitorService(
            ledger=self.ledger,
            worker_name="codexmon-daemon",
            snapshot_path=self.snapshot_path,
            html_path=self.html_path,
        )

    def test_build_snapshot_reflects_live_run_and_daemon_state(self) -> None:
        service = self._build_service()
        task = self.ledger.create_task("live monitor run")
        run = self.ledger.create_run(task.task_id)
        self.ledger.transition_run(run.run_id, "preflight", "picked")
        self.ledger.transition_run(run.run_id, "workspace_allocated", "workspace ready")
        self.ledger.transition_run(run.run_id, "running", "runner launched")
        self.ledger.record_runtime_heartbeat(
            worker_name="codexmon-daemon",
            status="picked",
            run_id=run.run_id,
            payload={"runnable_runs": 1},
        )

        snapshot = service.build_snapshot()
        agent_names = [item["name"] for item in snapshot["runtime"]["activeAgents"]]

        self.assertEqual(snapshot["runtime"]["executionStatus"], "running")
        self.assertIn(run.run_id, snapshot["meta"]["currentFocus"])
        self.assertIn("codexmon-daemon", agent_names)
        self.assertIn(f"Codex · {run.run_id}", agent_names)
        self.assertEqual(snapshot["summary"]["currentState"], "실행 중 run 1건")
        self.assertFalse(
            any(item["label"] == "progress monitor DB 미구현" for item in snapshot["watchItems"])
        )

    def test_http_server_serves_live_json_and_html(self) -> None:
        service = self._build_service()
        task = self.ledger.create_task("server snapshot")
        run = self.ledger.create_run(task.task_id)
        self.ledger.transition_run(run.run_id, "preflight", "picked")
        self.ledger.record_runtime_heartbeat(
            worker_name="codexmon-daemon",
            status="idle",
            payload={"runnable_runs": 0},
        )
        server, info = service.create_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1.0)

        with urlopen(f"{info.url}api/progress") as response:
            payload = json.loads(response.read().decode("utf-8"))
        with urlopen(info.url) as response:
            html = response.read().decode("utf-8")

        self.assertEqual(payload["meta"]["projectName"], "codexmon")
        self.assertEqual(payload["runtime"]["executionStatus"], "running")
        self.assertIn("<title>monitor</title>", html)


if __name__ == "__main__":
    unittest.main()
