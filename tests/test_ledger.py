from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codexmon.ledger import SCHEMA_VERSION, RunLedger
from codexmon.state_machine import InvalidStateTransitionError


class RunLedgerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "codexmon.db"
        self.ledger = RunLedger(self.db_path)
        self.ledger.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initialize_sets_schema_version(self) -> None:
        self.assertEqual(self.ledger.schema_version(), SCHEMA_VERSION)

    def test_create_run_records_initial_projection(self) -> None:
        task = self.ledger.create_task("B2 synthetic task")
        run = self.ledger.create_run(task.task_id)

        self.assertEqual(run.task_id, task.task_id)
        self.assertEqual(run.current_state, "queued")
        self.assertEqual(run.attempt_number, 0)
        self.assertEqual(run.approval_status, "not_required")
        self.assertEqual(self.ledger.list_runs(limit=1)[0].run_id, run.run_id)

    def test_valid_transition_path_updates_attempts(self) -> None:
        task = self.ledger.create_task("B2 transition path")
        run = self.ledger.create_run(task.task_id)
        run = self.ledger.transition_run(run.run_id, "preflight", "task accepted")
        run = self.ledger.transition_run(run.run_id, "workspace_allocated", "preflight passed")
        run = self.ledger.assign_workspace(run.run_id, "/tmp/codexmon/run-1", "codexmon/run-1")
        run = self.ledger.transition_run(run.run_id, "running", "runner launched")
        self.assertEqual(run.attempt_number, 1)
        run = self.ledger.record_failure_fingerprint(
            run.run_id,
            fingerprint="pytest|exit-1|tests/test_app.py::test_failure",
            command_name="pytest",
            failure_class="exit-1",
            dominant_token="tests/test_app.py::test_failure",
        )
        self.assertEqual(
            run.last_failure_fingerprint, "pytest|exit-1|tests/test_app.py::test_failure"
        )
        run = self.ledger.transition_run(
            run.run_id,
            "analyzing_failure",
            "failure, timeout, or loop signal",
            runner_signal="exit=1",
            failure_fingerprint=run.last_failure_fingerprint,
        )
        run = self.ledger.transition_run(run.run_id, "retry_pending", "retry allowed")
        run = self.ledger.transition_run(run.run_id, "running", "runner relaunched")
        self.assertEqual(run.attempt_number, 2)
        run = self.ledger.transition_run(run.run_id, "pr_handoff", "success path reached")
        run = self.ledger.set_pr_reference(
            run.run_id,
            provider="github",
            pr_number=7,
            pr_url="https://example.com/pull/7",
            head_branch="codexmon/run-1",
            base_branch="main",
            ci_status="pending",
        )
        run = self.ledger.transition_run(
            run.run_id,
            "completed",
            "PR opened successfully",
            pr_reference=run.pr_reference,
        )
        self.assertEqual(run.current_state, "completed")
        self.assertEqual(run.outcome, "PR opened")
        self.assertEqual(run.pr_reference, "github#7")

    def test_invalid_transition_is_rejected(self) -> None:
        task = self.ledger.create_task("invalid transition")
        run = self.ledger.create_run(task.task_id)

        with self.assertRaises(InvalidStateTransitionError):
            self.ledger.transition_run(run.run_id, "running", "runner launched")

        events = self.ledger.list_events(run.run_id)
        self.assertIn("state.transition.rejected", [event.event_type for event in events])

    def test_auxiliary_records_are_persisted(self) -> None:
        task = self.ledger.create_task("auxiliary records")
        run = self.ledger.create_run(task.task_id)
        event_id = self.ledger.append_event(
            run.run_id,
            event_type="preflight.check",
            payload={"check": "db", "status": "ok"},
            reason_code="preflight check",
        )
        self.assertGreater(event_id, 0)
        run = self.ledger.record_failure_fingerprint(
            run.run_id,
            fingerprint="preflight|config|missing",
            command_name="config-check",
            failure_class="preflight",
            dominant_token="missing-config",
            source_event_id=event_id,
        )
        approval_request_id = self.ledger.request_approval(
            run.run_id,
            requested_by="operator",
            payload={"change_class": "dependency-manifest"},
        )
        run = self.ledger.resolve_approval(
            approval_request_id=approval_request_id,
            status="approved",
            resolved_by="operator",
            decision_note="B2 synthetic approval",
        )
        run = self.ledger.assign_workspace(run.run_id, "/tmp/codexmon/run-2", "codexmon/run-2")
        run = self.ledger.set_pr_reference(
            run.run_id,
            provider="github",
            pr_number=42,
            pr_url="https://example.com/pull/42",
            head_branch="codexmon/run-2",
            base_branch="main",
            ci_status="success",
        )

        self.assertEqual(run.approval_status, "approved")
        self.assertEqual(run.active_branch, "codexmon/run-2")
        self.assertEqual(run.pr_reference, "github#42")
        self.assertEqual(run.last_failure_fingerprint, "preflight|config|missing")

        with sqlite3.connect(self.db_path) as conn:
            events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            approvals_count = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
            pr_count = conn.execute("SELECT COUNT(*) FROM pr_references").fetchone()[0]

        self.assertGreaterEqual(events_count, 5)
        self.assertEqual(approvals_count, 1)
        self.assertEqual(pr_count, 1)

    def test_runtime_heartbeat_and_runnable_run_queries_are_persisted(self) -> None:
        task = self.ledger.create_task("daemon baseline")
        queued_run = self.ledger.create_run(task.task_id)
        second_run = self.ledger.create_run(task.task_id)
        self.ledger.transition_run(second_run.run_id, "preflight", "accepted")

        heartbeat = self.ledger.record_runtime_heartbeat(
            worker_name="codexmon-daemon",
            status="idle",
            payload={"runnable_runs": 2},
        )
        runnable = self.ledger.list_runnable_runs(limit=5)
        heartbeats = self.ledger.list_runtime_heartbeats(limit=5, worker_name="codexmon-daemon")

        self.assertEqual(heartbeat.status, "idle")
        self.assertEqual([item.run_id for item in runnable], [second_run.run_id, queued_run.run_id])
        self.assertEqual(len(heartbeats), 1)
        self.assertEqual(heartbeats[0].payload["runnable_runs"], 2)


if __name__ == "__main__":
    unittest.main()
