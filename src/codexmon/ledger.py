"""SQLite-backed durable run ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from codexmon.state_machine import InvalidStateTransitionError, outcome_for_state, validate_transition

_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      applied_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tasks (
      task_id TEXT PRIMARY KEY,
      instruction_summary TEXT NOT NULL,
      repo_owner TEXT NOT NULL DEFAULT '',
      repo_name TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS runs (
      run_id TEXT PRIMARY KEY,
      task_id TEXT NOT NULL,
      current_state TEXT NOT NULL,
      state_reason TEXT NOT NULL,
      outcome TEXT NOT NULL DEFAULT '',
      attempt_number INTEGER NOT NULL DEFAULT 0,
      active_worktree TEXT NOT NULL DEFAULT '',
      active_branch TEXT NOT NULL DEFAULT '',
      last_failure_fingerprint TEXT NOT NULL DEFAULT '',
      approval_status TEXT NOT NULL DEFAULT 'not_required',
      pr_reference TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY(task_id) REFERENCES tasks(task_id)
    );

    CREATE TABLE IF NOT EXISTS attempts (
      run_id TEXT NOT NULL,
      attempt_number INTEGER NOT NULL,
      status TEXT NOT NULL,
      started_at TEXT NOT NULL,
      ended_at TEXT,
      failure_fingerprint TEXT NOT NULL DEFAULT '',
      PRIMARY KEY (run_id, attempt_number),
      FOREIGN KEY(run_id) REFERENCES runs(run_id)
    );

    CREATE TABLE IF NOT EXISTS state_transitions (
      transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL,
      attempt_number INTEGER NOT NULL,
      event_time TEXT NOT NULL,
      actor_type TEXT NOT NULL,
      actor_id TEXT NOT NULL,
      state_from TEXT,
      state_to TEXT NOT NULL,
      reason_code TEXT NOT NULL,
      instruction_summary TEXT NOT NULL DEFAULT '',
      workspace_path TEXT NOT NULL DEFAULT '',
      branch_name TEXT NOT NULL DEFAULT '',
      runner_signal TEXT NOT NULL DEFAULT '',
      failure_fingerprint TEXT NOT NULL DEFAULT '',
      approval_request_id TEXT NOT NULL DEFAULT '',
      approval_result TEXT NOT NULL DEFAULT '',
      changed_files_summary TEXT NOT NULL DEFAULT '',
      check_summary TEXT NOT NULL DEFAULT '',
      telegram_message_ref TEXT NOT NULL DEFAULT '',
      pr_reference TEXT NOT NULL DEFAULT '',
      FOREIGN KEY(run_id) REFERENCES runs(run_id)
    );

    CREATE TABLE IF NOT EXISTS events (
      event_id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL,
      attempt_number INTEGER NOT NULL,
      event_time TEXT NOT NULL,
      event_type TEXT NOT NULL,
      payload_json TEXT NOT NULL DEFAULT '{}',
      actor_type TEXT NOT NULL DEFAULT 'system',
      actor_id TEXT NOT NULL DEFAULT 'codexmon',
      reason_code TEXT NOT NULL DEFAULT '',
      FOREIGN KEY(run_id) REFERENCES runs(run_id)
    );

    CREATE TABLE IF NOT EXISTS failure_fingerprints (
      failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL,
      attempt_number INTEGER NOT NULL,
      fingerprint TEXT NOT NULL,
      command_name TEXT NOT NULL DEFAULT '',
      failure_class TEXT NOT NULL DEFAULT '',
      dominant_token TEXT NOT NULL DEFAULT '',
      event_time TEXT NOT NULL,
      source_event_id INTEGER,
      FOREIGN KEY(run_id) REFERENCES runs(run_id),
      FOREIGN KEY(source_event_id) REFERENCES events(event_id)
    );

    CREATE TABLE IF NOT EXISTS approvals (
      approval_request_id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      status TEXT NOT NULL,
      requested_at TEXT NOT NULL,
      resolved_at TEXT,
      requested_by TEXT NOT NULL DEFAULT '',
      resolved_by TEXT NOT NULL DEFAULT '',
      decision_note TEXT NOT NULL DEFAULT '',
      payload_json TEXT NOT NULL DEFAULT '{}',
      FOREIGN KEY(run_id) REFERENCES runs(run_id)
    );

    CREATE TABLE IF NOT EXISTS workspace_assignments (
      run_id TEXT PRIMARY KEY,
      workspace_path TEXT NOT NULL,
      branch_name TEXT NOT NULL,
      assigned_at TEXT NOT NULL,
      released_at TEXT,
      FOREIGN KEY(run_id) REFERENCES runs(run_id)
    );

    CREATE TABLE IF NOT EXISTS pr_references (
      run_id TEXT PRIMARY KEY,
      provider TEXT NOT NULL,
      pr_number INTEGER,
      pr_url TEXT NOT NULL DEFAULT '',
      head_branch TEXT NOT NULL DEFAULT '',
      base_branch TEXT NOT NULL DEFAULT '',
      ci_status TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY(run_id) REFERENCES runs(run_id)
    );

    CREATE INDEX IF NOT EXISTS idx_runs_updated_at ON runs(updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_transitions_run_time
      ON state_transitions(run_id, event_time DESC);
    CREATE INDEX IF NOT EXISTS idx_events_run_time ON events(run_id, event_time DESC);
    CREATE INDEX IF NOT EXISTS idx_attempts_run ON attempts(run_id, attempt_number DESC);
    CREATE INDEX IF NOT EXISTS idx_failures_run ON failure_fingerprints(run_id, event_time DESC);
    CREATE INDEX IF NOT EXISTS idx_approvals_run ON approvals(run_id, requested_at DESC);
    """,
    2: """
    CREATE TABLE IF NOT EXISTS repository_locks (
      repo_key TEXT PRIMARY KEY,
      run_id TEXT NOT NULL UNIQUE,
      acquired_at TEXT NOT NULL,
      FOREIGN KEY(run_id) REFERENCES runs(run_id)
    );

    CREATE INDEX IF NOT EXISTS idx_repository_locks_run_id ON repository_locks(run_id);
    """,
}
SCHEMA_VERSION = max(_MIGRATIONS)


class LedgerError(RuntimeError):
    """Base error for ledger operations."""


class RecordNotFoundError(LedgerError):
    """Raised when a referenced ledger record does not exist."""


class RepositoryLockHeldError(LedgerError):
    """Raised when a repository-wide execution lock is already held by another run."""

    def __init__(self, repo_key: str, holder_run_id: str) -> None:
        self.repo_key = repo_key
        self.holder_run_id = holder_run_id
        super().__init__(f"repository lock for '{repo_key}' is already held by '{holder_run_id}'")


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    instruction_summary: str
    repo_owner: str
    repo_name: str
    created_at: str


@dataclass(frozen=True)
class RunProjection:
    run_id: str
    task_id: str
    instruction_summary: str
    current_state: str
    state_reason: str
    outcome: str
    attempt_number: int
    active_worktree: str
    active_branch: str
    last_failure_fingerprint: str
    approval_status: str
    pr_reference: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RepositoryLockRecord:
    repo_key: str
    run_id: str
    acquired_at: str


@dataclass(frozen=True)
class WorkspaceAssignmentRecord:
    run_id: str
    workspace_path: str
    branch_name: str
    assigned_at: str
    released_at: str | None


@dataclass(frozen=True)
class EventRecord:
    event_id: int
    run_id: str
    attempt_number: int
    event_time: str
    event_type: str
    payload: dict[str, Any]
    actor_type: str
    actor_id: str
    reason_code: str


@dataclass(frozen=True)
class FailureFingerprintRecord:
    failure_id: int
    run_id: str
    attempt_number: int
    fingerprint: str
    command_name: str
    failure_class: str
    dominant_token: str
    event_time: str
    source_event_id: int | None


@dataclass(frozen=True)
class ApprovalRecord:
    approval_request_id: str
    run_id: str
    status: str
    requested_at: str
    resolved_at: str | None
    requested_by: str
    resolved_by: str
    decision_note: str
    payload: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class RunLedger:
    """Durable store for tasks, runs, transitions, and audit events."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version INTEGER PRIMARY KEY,
                  applied_at TEXT NOT NULL
                )
                """
            )
            applied_versions = {
                row["version"] for row in conn.execute("SELECT version FROM schema_migrations")
            }
            for version, sql in sorted(_MIGRATIONS.items()):
                if version in applied_versions:
                    continue
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, _utc_now()),
                )

    def schema_version(self) -> int:
        if not self.db_path.exists():
            return 0
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
            return int(row["version"] or 0)

    def create_task(
        self,
        instruction_summary: str,
        task_id: str | None = None,
        repo_owner: str = "",
        repo_name: str = "",
    ) -> TaskRecord:
        self.initialize()
        now = _utc_now()
        actual_task_id = task_id or _make_id("task")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks(task_id, instruction_summary, repo_owner, repo_name, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (actual_task_id, instruction_summary, repo_owner, repo_name, now),
            )
            row = self._fetch_task_row(conn, actual_task_id)
        return self._row_to_task(row)

    def get_task(self, task_id: str) -> TaskRecord:
        self.initialize()
        with self._connect() as conn:
            row = self._fetch_task_row(conn, task_id)
        return self._row_to_task(row)

    def create_run(
        self,
        task_id: str,
        run_id: str | None = None,
        actor_type: str = "system",
        actor_id: str = "codexmon",
        reason_code: str = "task accepted",
        instruction_summary: str = "",
    ) -> RunProjection:
        self.initialize()
        now = _utc_now()
        actual_run_id = run_id or _make_id("run")
        with self._connect() as conn:
            task_row = self._fetch_task_row(conn, task_id)
            summary = instruction_summary or task_row["instruction_summary"]
            conn.execute(
                """
                INSERT INTO runs(
                  run_id, task_id, current_state, state_reason, outcome, attempt_number,
                  active_worktree, active_branch, last_failure_fingerprint, approval_status,
                  pr_reference, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actual_run_id,
                    task_id,
                    "queued",
                    reason_code,
                    outcome_for_state("queued"),
                    0,
                    "",
                    "",
                    "",
                    "not_required",
                    "",
                    now,
                    now,
                ),
            )
            self._insert_transition(
                conn=conn,
                run_id=actual_run_id,
                attempt_number=0,
                actor_type=actor_type,
                actor_id=actor_id,
                state_from=None,
                state_to="queued",
                reason_code=reason_code,
                instruction_summary=summary,
            )
            self._insert_event(
                conn=conn,
                run_id=actual_run_id,
                attempt_number=0,
                event_type="run.created",
                actor_type=actor_type,
                actor_id=actor_id,
                reason_code=reason_code,
                payload={"task_id": task_id, "state_to": "queued"},
            )
            row = self._fetch_run_projection_row(conn, actual_run_id)
        return self._row_to_projection(row)

    def transition_run(
        self,
        run_id: str,
        to_state: str,
        reason_code: str,
        actor_type: str = "system",
        actor_id: str = "codexmon",
        instruction_summary: str = "",
        workspace_path: str = "",
        branch_name: str = "",
        runner_signal: str = "",
        failure_fingerprint: str = "",
        approval_request_id: str = "",
        approval_result: str = "",
        changed_files_summary: str = "",
        check_summary: str = "",
        telegram_message_ref: str = "",
        pr_reference: str = "",
    ) -> RunProjection:
        self.initialize()
        with self._connect() as conn:
            run_row = self._fetch_run_row(conn, run_id)
            task_row = self._fetch_task_row(conn, run_row["task_id"])
            current_state = run_row["current_state"]
        try:
            validate_transition(current_state, to_state)
        except InvalidStateTransitionError:
            with self._connect() as conn:
                self._insert_event(
                    conn=conn,
                    run_id=run_id,
                    attempt_number=int(run_row["attempt_number"]),
                    event_type="state.transition.rejected",
                    actor_type=actor_type,
                    actor_id=actor_id,
                    reason_code=f"invalid state transition: {current_state!r} -> {to_state!r}",
                    payload={"state_from": current_state, "state_to": to_state},
                )
            raise

        with self._connect() as conn:
            now = _utc_now()
            transition_attempt_number = int(run_row["attempt_number"])

            if to_state == "running":
                transition_attempt_number += 1
                conn.execute(
                    """
                    INSERT INTO attempts(
                      run_id, attempt_number, status, started_at, ended_at, failure_fingerprint
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, transition_attempt_number, "active", now, None, ""),
                )
            elif current_state == "running" and transition_attempt_number > 0:
                conn.execute(
                    """
                    UPDATE attempts
                    SET status = ?, ended_at = COALESCE(ended_at, ?),
                        failure_fingerprint = CASE
                          WHEN ? != '' THEN ?
                          ELSE failure_fingerprint
                        END
                    WHERE run_id = ? AND attempt_number = ?
                    """,
                    (
                        self._attempt_status_for_state(to_state),
                        now,
                        failure_fingerprint,
                        failure_fingerprint,
                        run_id,
                        transition_attempt_number,
                    ),
                )

            summary = instruction_summary or task_row["instruction_summary"]
            updated_worktree = workspace_path or run_row["active_worktree"]
            updated_branch = branch_name or run_row["active_branch"]
            updated_fingerprint = failure_fingerprint or run_row["last_failure_fingerprint"]
            updated_approval_status = self._next_approval_status(
                current_status=run_row["approval_status"],
                to_state=to_state,
                approval_result=approval_result,
            )
            updated_pr_reference = pr_reference or run_row["pr_reference"]

            conn.execute(
                """
                UPDATE runs
                SET current_state = ?, state_reason = ?, outcome = ?, attempt_number = ?,
                    active_worktree = ?, active_branch = ?, last_failure_fingerprint = ?,
                    approval_status = ?, pr_reference = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    to_state,
                    reason_code,
                    outcome_for_state(to_state),
                    transition_attempt_number,
                    updated_worktree,
                    updated_branch,
                    updated_fingerprint,
                    updated_approval_status,
                    updated_pr_reference,
                    now,
                    run_id,
                ),
            )
            self._insert_transition(
                conn=conn,
                run_id=run_id,
                attempt_number=transition_attempt_number,
                actor_type=actor_type,
                actor_id=actor_id,
                state_from=current_state,
                state_to=to_state,
                reason_code=reason_code,
                instruction_summary=summary,
                workspace_path=updated_worktree,
                branch_name=updated_branch,
                runner_signal=runner_signal,
                failure_fingerprint=updated_fingerprint,
                approval_request_id=approval_request_id,
                approval_result=approval_result,
                changed_files_summary=changed_files_summary,
                check_summary=check_summary,
                telegram_message_ref=telegram_message_ref,
                pr_reference=updated_pr_reference,
            )
            self._insert_event(
                conn=conn,
                run_id=run_id,
                attempt_number=transition_attempt_number,
                event_type="state.transition",
                actor_type=actor_type,
                actor_id=actor_id,
                reason_code=reason_code,
                payload={
                    "state_from": current_state,
                    "state_to": to_state,
                    "runner_signal": runner_signal,
                    "failure_fingerprint": updated_fingerprint,
                },
            )
            row = self._fetch_run_projection_row(conn, run_id)
        return self._row_to_projection(row)

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        actor_type: str = "system",
        actor_id: str = "codexmon",
        reason_code: str = "",
        attempt_number: int | None = None,
    ) -> int:
        self.initialize()
        with self._connect() as conn:
            run_row = self._fetch_run_row(conn, run_id)
            actual_attempt_number = attempt_number
            if actual_attempt_number is None:
                actual_attempt_number = int(run_row["attempt_number"])
            return self._insert_event(
                conn=conn,
                run_id=run_id,
                attempt_number=actual_attempt_number,
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id,
                reason_code=reason_code,
                payload=payload or {},
            )

    def record_failure_fingerprint(
        self,
        run_id: str,
        fingerprint: str,
        command_name: str = "",
        failure_class: str = "",
        dominant_token: str = "",
        source_event_id: int | None = None,
    ) -> RunProjection:
        self.initialize()
        with self._connect() as conn:
            run_row = self._fetch_run_row(conn, run_id)
            now = _utc_now()
            attempt_number = int(run_row["attempt_number"])
            conn.execute(
                """
                INSERT INTO failure_fingerprints(
                  run_id, attempt_number, fingerprint, command_name, failure_class,
                  dominant_token, event_time, source_event_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    attempt_number,
                    fingerprint,
                    command_name,
                    failure_class,
                    dominant_token,
                    now,
                    source_event_id,
                ),
            )
            conn.execute(
                """
                UPDATE runs
                SET last_failure_fingerprint = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (fingerprint, now, run_id),
            )
            conn.execute(
                """
                UPDATE attempts
                SET failure_fingerprint = ?
                WHERE run_id = ? AND attempt_number = ?
                """,
                (fingerprint, run_id, attempt_number),
            )
            self._insert_event(
                conn=conn,
                run_id=run_id,
                attempt_number=attempt_number,
                event_type="failure.fingerprint",
                actor_type="system",
                actor_id="codexmon",
                reason_code="failure fingerprint recorded",
                payload={
                    "fingerprint": fingerprint,
                    "command_name": command_name,
                    "failure_class": failure_class,
                    "dominant_token": dominant_token,
                },
            )
            row = self._fetch_run_projection_row(conn, run_id)
        return self._row_to_projection(row)

    def request_approval(
        self,
        run_id: str,
        requested_by: str,
        payload: dict[str, Any] | None = None,
        approval_request_id: str | None = None,
    ) -> str:
        self.initialize()
        actual_request_id = approval_request_id or _make_id("approval")
        with self._connect() as conn:
            run_row = self._fetch_run_row(conn, run_id)
            now = _utc_now()
            conn.execute(
                """
                INSERT INTO approvals(
                  approval_request_id, run_id, status, requested_at, resolved_at, requested_by,
                  resolved_by, decision_note, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actual_request_id,
                    run_id,
                    "pending",
                    now,
                    None,
                    requested_by,
                    "",
                    "",
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            conn.execute(
                """
                UPDATE runs
                SET approval_status = ?, updated_at = ?
                WHERE run_id = ?
                """,
                ("pending", now, run_id),
            )
            self._insert_event(
                conn=conn,
                run_id=run_id,
                attempt_number=int(run_row["attempt_number"]),
                event_type="approval.requested",
                actor_type="operator",
                actor_id=requested_by,
                reason_code="approval requested",
                payload={"approval_request_id": actual_request_id},
            )
        return actual_request_id

    def resolve_approval(
        self,
        approval_request_id: str,
        status: str,
        resolved_by: str,
        decision_note: str = "",
    ) -> RunProjection:
        self.initialize()
        with self._connect() as conn:
            approval_row = conn.execute(
                "SELECT * FROM approvals WHERE approval_request_id = ?",
                (approval_request_id,),
            ).fetchone()
            if approval_row is None:
                raise RecordNotFoundError(f"approval '{approval_request_id}' not found")

            now = _utc_now()
            run_row = self._fetch_run_row(conn, approval_row["run_id"])
            conn.execute(
                """
                UPDATE approvals
                SET status = ?, resolved_at = ?, resolved_by = ?, decision_note = ?
                WHERE approval_request_id = ?
                """,
                (status, now, resolved_by, decision_note, approval_request_id),
            )
            conn.execute(
                """
                UPDATE runs
                SET approval_status = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (status, now, approval_row["run_id"]),
            )
            self._insert_event(
                conn=conn,
                run_id=approval_row["run_id"],
                attempt_number=int(run_row["attempt_number"]),
                event_type="approval.resolved",
                actor_type="operator",
                actor_id=resolved_by,
                reason_code="approval resolved",
                payload={"approval_request_id": approval_request_id, "status": status},
            )
            row = self._fetch_run_projection_row(conn, approval_row["run_id"])
        return self._row_to_projection(row)

    def assign_workspace(self, run_id: str, workspace_path: str, branch_name: str) -> RunProjection:
        self.initialize()
        with self._connect() as conn:
            run_row = self._fetch_run_row(conn, run_id)
            now = _utc_now()
            conn.execute(
                """
                INSERT INTO workspace_assignments(
                  run_id, workspace_path, branch_name, assigned_at, released_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                  workspace_path = excluded.workspace_path,
                  branch_name = excluded.branch_name,
                  assigned_at = excluded.assigned_at
                """,
                (run_id, workspace_path, branch_name, now, None),
            )
            conn.execute(
                """
                UPDATE runs
                SET active_worktree = ?, active_branch = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (workspace_path, branch_name, now, run_id),
            )
            self._insert_event(
                conn=conn,
                run_id=run_id,
                attempt_number=int(run_row["attempt_number"]),
                event_type="workspace.assigned",
                actor_type="system",
                actor_id="codexmon",
                reason_code="workspace assigned",
                payload={"workspace_path": workspace_path, "branch_name": branch_name},
            )
            row = self._fetch_run_projection_row(conn, run_id)
        return self._row_to_projection(row)

    def release_workspace_assignment(self, run_id: str) -> WorkspaceAssignmentRecord | None:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspace_assignments WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            if row["released_at"]:
                return self._row_to_workspace_assignment(row)

            released_at = _utc_now()
            conn.execute(
                """
                UPDATE workspace_assignments
                SET released_at = ?
                WHERE run_id = ?
                """,
                (released_at, run_id),
            )
            run_row = self._fetch_run_row(conn, run_id)
            self._insert_event(
                conn=conn,
                run_id=run_id,
                attempt_number=int(run_row["attempt_number"]),
                event_type="workspace.released",
                actor_type="system",
                actor_id="codexmon",
                reason_code="workspace released",
                payload={"workspace_path": row["workspace_path"], "branch_name": row["branch_name"]},
            )
            updated_row = conn.execute(
                "SELECT * FROM workspace_assignments WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return self._row_to_workspace_assignment(updated_row)

    def get_workspace_assignment(self, run_id: str) -> WorkspaceAssignmentRecord | None:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspace_assignments WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return None if row is None else self._row_to_workspace_assignment(row)

    def list_workspace_assignments(self) -> list[WorkspaceAssignmentRecord]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM workspace_assignments
                ORDER BY assigned_at DESC
                """
            ).fetchall()
        return [self._row_to_workspace_assignment(row) for row in rows]

    def acquire_repository_lock(
        self,
        repo_key: str,
        run_id: str,
        actor_type: str = "system",
        actor_id: str = "codexmon",
    ) -> RepositoryLockRecord:
        self.initialize()
        with self._connect() as conn:
            run_row = self._fetch_run_row(conn, run_id)
            existing = conn.execute(
                "SELECT * FROM repository_locks WHERE repo_key = ?",
                (repo_key,),
            ).fetchone()
            if existing is not None:
                if existing["run_id"] == run_id:
                    return self._row_to_repository_lock(existing)
                self._insert_event(
                    conn=conn,
                    run_id=run_id,
                    attempt_number=int(run_row["attempt_number"]),
                    event_type="repository.lock.rejected",
                    actor_type=actor_type,
                    actor_id=actor_id,
                    reason_code="repository lock held",
                    payload={"repo_key": repo_key, "holder_run_id": existing["run_id"]},
                )
                raise RepositoryLockHeldError(repo_key, existing["run_id"])

            acquired_at = _utc_now()
            conn.execute(
                """
                INSERT INTO repository_locks(repo_key, run_id, acquired_at)
                VALUES (?, ?, ?)
                """,
                (repo_key, run_id, acquired_at),
            )
            self._insert_event(
                conn=conn,
                run_id=run_id,
                attempt_number=int(run_row["attempt_number"]),
                event_type="repository.lock.acquired",
                actor_type=actor_type,
                actor_id=actor_id,
                reason_code="repository lock acquired",
                payload={"repo_key": repo_key},
            )
            row = conn.execute(
                "SELECT * FROM repository_locks WHERE repo_key = ?",
                (repo_key,),
            ).fetchone()
        return self._row_to_repository_lock(row)

    def release_repository_lock(
        self,
        run_id: str,
        actor_type: str = "system",
        actor_id: str = "codexmon",
    ) -> bool:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM repository_locks WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return False
            run_row = self._fetch_run_row(conn, run_id)
            conn.execute("DELETE FROM repository_locks WHERE run_id = ?", (run_id,))
            self._insert_event(
                conn=conn,
                run_id=run_id,
                attempt_number=int(run_row["attempt_number"]),
                event_type="repository.lock.released",
                actor_type=actor_type,
                actor_id=actor_id,
                reason_code="repository lock released",
                payload={"repo_key": row["repo_key"]},
            )
        return True

    def get_repository_lock(self, repo_key: str) -> RepositoryLockRecord | None:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM repository_locks WHERE repo_key = ?",
                (repo_key,),
            ).fetchone()
        return None if row is None else self._row_to_repository_lock(row)

    def list_repository_locks(self) -> list[RepositoryLockRecord]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM repository_locks
                ORDER BY acquired_at DESC
                """
            ).fetchall()
        return [self._row_to_repository_lock(row) for row in rows]

    def set_pr_reference(
        self,
        run_id: str,
        provider: str,
        pr_number: int | None,
        pr_url: str,
        head_branch: str,
        base_branch: str,
        ci_status: str = "",
    ) -> RunProjection:
        self.initialize()
        with self._connect() as conn:
            run_row = self._fetch_run_row(conn, run_id)
            now = _utc_now()
            human_reference = f"{provider}#{pr_number}" if pr_number is not None else pr_url
            conn.execute(
                """
                INSERT INTO pr_references(
                  run_id, provider, pr_number, pr_url, head_branch, base_branch,
                  ci_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                  provider = excluded.provider,
                  pr_number = excluded.pr_number,
                  pr_url = excluded.pr_url,
                  head_branch = excluded.head_branch,
                  base_branch = excluded.base_branch,
                  ci_status = excluded.ci_status,
                  updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    provider,
                    pr_number,
                    pr_url,
                    head_branch,
                    base_branch,
                    ci_status,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE runs
                SET pr_reference = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (human_reference, now, run_id),
            )
            self._insert_event(
                conn=conn,
                run_id=run_id,
                attempt_number=int(run_row["attempt_number"]),
                event_type="pr.reference.updated",
                actor_type="system",
                actor_id="codexmon",
                reason_code="pr reference updated",
                payload={"pr_reference": human_reference, "ci_status": ci_status},
            )
            row = self._fetch_run_projection_row(conn, run_id)
        return self._row_to_projection(row)

    def get_run(self, run_id: str) -> RunProjection:
        self.initialize()
        with self._connect() as conn:
            row = self._fetch_run_projection_row(conn, run_id)
        return self._row_to_projection(row)

    def list_runs(self, limit: int = 10) -> list[RunProjection]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT runs.*, tasks.instruction_summary
                FROM runs
                JOIN tasks ON tasks.task_id = runs.task_id
                ORDER BY runs.updated_at DESC, runs.created_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [self._row_to_projection(row) for row in rows]

    def list_events(self, run_id: str, limit: int = 200) -> list[EventRecord]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM events
                WHERE run_id = ?
                ORDER BY event_id ASC
                LIMIT ?
                """,
                (run_id, max(1, limit)),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_failure_fingerprints(self, run_id: str) -> list[FailureFingerprintRecord]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM failure_fingerprints
                WHERE run_id = ?
                ORDER BY failure_id ASC
                """,
                (run_id,),
            ).fetchall()
        return [self._row_to_failure_fingerprint(row) for row in rows]

    def get_approval(self, approval_request_id: str) -> ApprovalRecord:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_request_id = ?",
                (approval_request_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"approval '{approval_request_id}' not found")
        return self._row_to_approval(row)

    def list_approvals(self, run_id: str, status: str | None = None) -> list[ApprovalRecord]:
        self.initialize()
        with self._connect() as conn:
            if status is None:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM approvals
                    WHERE run_id = ?
                    ORDER BY requested_at DESC, approval_request_id DESC
                    """,
                    (run_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM approvals
                    WHERE run_id = ? AND status = ?
                    ORDER BY requested_at DESC, approval_request_id DESC
                    """,
                    (run_id, status),
                ).fetchall()
        return [self._row_to_approval(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _fetch_task_row(self, conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise RecordNotFoundError(f"task '{task_id}' not found")
        return row

    def _fetch_run_row(self, conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RecordNotFoundError(f"run '{run_id}' not found")
        return row

    def _fetch_run_projection_row(self, conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT runs.*, tasks.instruction_summary
            FROM runs
            JOIN tasks ON tasks.task_id = runs.task_id
            WHERE runs.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"run '{run_id}' not found")
        return row

    def _insert_transition(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        attempt_number: int,
        actor_type: str,
        actor_id: str,
        state_from: str | None,
        state_to: str,
        reason_code: str,
        instruction_summary: str,
        workspace_path: str = "",
        branch_name: str = "",
        runner_signal: str = "",
        failure_fingerprint: str = "",
        approval_request_id: str = "",
        approval_result: str = "",
        changed_files_summary: str = "",
        check_summary: str = "",
        telegram_message_ref: str = "",
        pr_reference: str = "",
    ) -> None:
        conn.execute(
            """
            INSERT INTO state_transitions(
              run_id, attempt_number, event_time, actor_type, actor_id, state_from, state_to,
              reason_code, instruction_summary, workspace_path, branch_name, runner_signal,
              failure_fingerprint, approval_request_id, approval_result, changed_files_summary,
              check_summary, telegram_message_ref, pr_reference
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                attempt_number,
                _utc_now(),
                actor_type,
                actor_id,
                state_from,
                state_to,
                reason_code,
                instruction_summary,
                workspace_path,
                branch_name,
                runner_signal,
                failure_fingerprint,
                approval_request_id,
                approval_result,
                changed_files_summary,
                check_summary,
                telegram_message_ref,
                pr_reference,
            ),
        )

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        attempt_number: int,
        event_type: str,
        actor_type: str,
        actor_id: str,
        reason_code: str,
        payload: dict[str, Any],
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO events(
              run_id, attempt_number, event_time, event_type, payload_json, actor_type, actor_id,
              reason_code
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                attempt_number,
                _utc_now(),
                event_type,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                actor_type,
                actor_id,
                reason_code,
            ),
        )
        return int(cursor.lastrowid)

    def _next_approval_status(
        self, current_status: str, to_state: str, approval_result: str
    ) -> str:
        if approval_result:
            return approval_result
        if to_state == "awaiting_human":
            return "pending"
        if current_status == "pending" and to_state in {"retry_pending", "cancelled", "halted"}:
            return current_status
        return current_status

    def _attempt_status_for_state(self, to_state: str) -> str:
        return {
            "pr_handoff": "succeeded",
            "analyzing_failure": "failed",
            "awaiting_human": "approval_pending",
            "halted": "halted",
            "cancelled": "cancelled",
        }.get(to_state, "finished")

    def _row_to_task(self, row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            instruction_summary=row["instruction_summary"],
            repo_owner=row["repo_owner"],
            repo_name=row["repo_name"],
            created_at=row["created_at"],
        )

    def _row_to_projection(self, row: sqlite3.Row) -> RunProjection:
        return RunProjection(
            run_id=row["run_id"],
            task_id=row["task_id"],
            instruction_summary=row["instruction_summary"],
            current_state=row["current_state"],
            state_reason=row["state_reason"],
            outcome=row["outcome"] or outcome_for_state(row["current_state"]),
            attempt_number=int(row["attempt_number"]),
            active_worktree=row["active_worktree"],
            active_branch=row["active_branch"],
            last_failure_fingerprint=row["last_failure_fingerprint"],
            approval_status=row["approval_status"],
            pr_reference=row["pr_reference"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_repository_lock(self, row: sqlite3.Row) -> RepositoryLockRecord:
        return RepositoryLockRecord(
            repo_key=row["repo_key"],
            run_id=row["run_id"],
            acquired_at=row["acquired_at"],
        )

    def _row_to_workspace_assignment(self, row: sqlite3.Row) -> WorkspaceAssignmentRecord:
        return WorkspaceAssignmentRecord(
            run_id=row["run_id"],
            workspace_path=row["workspace_path"],
            branch_name=row["branch_name"],
            assigned_at=row["assigned_at"],
            released_at=row["released_at"],
        )

    def _row_to_event(self, row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            event_id=int(row["event_id"]),
            run_id=row["run_id"],
            attempt_number=int(row["attempt_number"]),
            event_time=row["event_time"],
            event_type=row["event_type"],
            payload=json.loads(row["payload_json"]),
            actor_type=row["actor_type"],
            actor_id=row["actor_id"],
            reason_code=row["reason_code"],
        )

    def _row_to_failure_fingerprint(self, row: sqlite3.Row) -> FailureFingerprintRecord:
        return FailureFingerprintRecord(
            failure_id=int(row["failure_id"]),
            run_id=row["run_id"],
            attempt_number=int(row["attempt_number"]),
            fingerprint=row["fingerprint"],
            command_name=row["command_name"],
            failure_class=row["failure_class"],
            dominant_token=row["dominant_token"],
            event_time=row["event_time"],
            source_event_id=row["source_event_id"],
        )

    def _row_to_approval(self, row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            approval_request_id=row["approval_request_id"],
            run_id=row["run_id"],
            status=row["status"],
            requested_at=row["requested_at"],
            resolved_at=row["resolved_at"],
            requested_by=row["requested_by"],
            resolved_by=row["resolved_by"],
            decision_note=row["decision_note"],
            payload=json.loads(row["payload_json"] or "{}"),
        )
