"""Repository lock and git worktree allocation helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess

from codexmon.ledger import RepositoryLockHeldError, RunLedger


class WorkspaceError(RuntimeError):
    """Base error for workspace allocation failures."""


class GitCommandError(WorkspaceError):
    """Raised when a required git command fails."""

    def __init__(self, args: list[str], stderr: str) -> None:
        self.args = args
        self.stderr = stderr
        message = stderr.strip() or "git command failed"
        super().__init__(f"{' '.join(args)}: {message}")


@dataclass(frozen=True)
class AllocationResult:
    run_id: str
    repo_key: str
    branch_name: str
    workspace_path: str
    current_state: str
    state_reason: str
    lock_acquired: bool


@dataclass(frozen=True)
class ReleaseResult:
    run_id: str
    repo_key: str
    lock_released: bool
    workspace_removed: bool
    workspace_path: str
    branch_name: str


class WorktreeAllocator:
    """Allocate and diagnose run-isolated worktrees under a repository lock."""

    def __init__(
        self,
        ledger: RunLedger,
        repo_path: Path,
        worktree_root: Path | None = None,
        branch_prefix: str = "codexmon",
    ) -> None:
        self.ledger = ledger
        self.repo_root = self._resolve_repo_root(Path(repo_path))
        self.worktree_root = self._resolve_worktree_root(worktree_root)
        self.branch_prefix = branch_prefix.strip("/") or "codexmon"

    def derive_branch_name(self, run_id: str) -> str:
        return f"{self.branch_prefix}/{run_id}"

    def derive_worktree_path(self, run_id: str) -> Path:
        return self.worktree_root / run_id

    def repo_key(self) -> str:
        return str(self.repo_root)

    def allocate(self, run_id: str) -> AllocationResult:
        run = self.ledger.get_run(run_id)
        existing_assignment = self.ledger.get_workspace_assignment(run_id)
        if run.current_state == "workspace_allocated" and existing_assignment is not None:
            return AllocationResult(
                run_id=run.run_id,
                repo_key=self.repo_key(),
                branch_name=existing_assignment.branch_name,
                workspace_path=existing_assignment.workspace_path,
                current_state=run.current_state,
                state_reason=run.state_reason,
                lock_acquired=self.ledger.get_repository_lock(self.repo_key()) is not None,
            )

        if run.current_state == "queued":
            run = self.ledger.transition_run(run_id, "preflight", "task accepted")
        elif run.current_state != "preflight":
            raise WorkspaceError(
                f"run '{run_id}' must be in 'queued' or 'preflight' before workspace allocation"
            )

        branch_name = self.derive_branch_name(run_id)
        workspace_path = self.derive_worktree_path(run_id)
        lock = None
        try:
            lock = self.ledger.acquire_repository_lock(self.repo_key(), run_id)
            self._ensure_worktree(branch_name, workspace_path)
            self.ledger.assign_workspace(run_id, str(workspace_path), branch_name)
            run = self.ledger.transition_run(
                run_id,
                "workspace_allocated",
                "preflight passed",
                workspace_path=str(workspace_path),
                branch_name=branch_name,
            )
        except RepositoryLockHeldError as exc:
            self.ledger.transition_run(
                run_id,
                "halted",
                f"preflight failed: repository lock held by {exc.holder_run_id}",
            )
            raise
        except Exception:
            self._best_effort_remove_worktree(workspace_path)
            if lock is not None:
                self.ledger.release_repository_lock(run_id)
            self.ledger.transition_run(run_id, "halted", "preflight failed: workspace allocation error")
            raise

        return AllocationResult(
            run_id=run.run_id,
            repo_key=self.repo_key(),
            branch_name=branch_name,
            workspace_path=str(workspace_path),
            current_state=run.current_state,
            state_reason=run.state_reason,
            lock_acquired=lock is not None,
        )

    def release(self, run_id: str, cleanup: bool = False) -> ReleaseResult:
        assignment = self.ledger.get_workspace_assignment(run_id)
        workspace_path = assignment.workspace_path if assignment is not None else ""
        branch_name = assignment.branch_name if assignment is not None else ""

        workspace_removed = False
        if cleanup and assignment is not None:
            workspace_removed = self._remove_worktree(Path(assignment.workspace_path))
            self.ledger.release_workspace_assignment(run_id)

        lock_released = self.ledger.release_repository_lock(run_id)
        return ReleaseResult(
            run_id=run_id,
            repo_key=self.repo_key(),
            lock_released=lock_released,
            workspace_removed=workspace_removed,
            workspace_path=workspace_path,
            branch_name=branch_name,
        )

    def diagnose(self) -> dict[str, object]:
        registered_worktrees = {item["path"]: item for item in self._list_git_worktrees()}
        assignments = []
        for item in self.ledger.list_workspace_assignments():
            worktree_path = Path(item.workspace_path)
            assignments.append(
                {
                    "run_id": item.run_id,
                    "workspace_path": item.workspace_path,
                    "branch_name": item.branch_name,
                    "assigned_at": item.assigned_at,
                    "released_at": item.released_at,
                    "exists_on_disk": worktree_path.exists(),
                    "registered_in_git": item.workspace_path in registered_worktrees,
                }
            )

        locks = [
            {
                "repo_key": item.repo_key,
                "run_id": item.run_id,
                "acquired_at": item.acquired_at,
            }
            for item in self.ledger.list_repository_locks()
        ]
        return {
            "repo_root": str(self.repo_root),
            "worktree_root": str(self.worktree_root),
            "locks": locks,
            "workspace_assignments": assignments,
            "git_worktrees": self._list_git_worktrees(),
        }

    def _resolve_repo_root(self, repo_path: Path) -> Path:
        resolved = repo_path.resolve()
        result = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise GitCommandError(["git", "-C", str(resolved), "rev-parse", "--show-toplevel"], result.stderr)
        return Path(result.stdout.strip()).resolve()

    def _resolve_worktree_root(self, worktree_root: Path | None) -> Path:
        if worktree_root is None:
            candidate = Path(".codexmon/worktrees")
        else:
            candidate = worktree_root
        if not candidate.is_absolute():
            candidate = self.repo_root / candidate
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate.resolve()

    def _ensure_worktree(self, branch_name: str, workspace_path: Path) -> None:
        existing = self._find_git_worktree(workspace_path)
        if existing is not None:
            return

        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        if workspace_path.exists() and any(workspace_path.iterdir()):
            raise WorkspaceError(f"workspace path '{workspace_path}' already exists and is not empty")

        if self._branch_exists(branch_name):
            self._run_git(["worktree", "add", str(workspace_path), branch_name])
            return

        self._run_git(["worktree", "add", str(workspace_path), "-b", branch_name, "HEAD"])

    def _remove_worktree(self, workspace_path: Path) -> bool:
        existing = self._find_git_worktree(workspace_path)
        if existing is not None:
            self._run_git(["worktree", "remove", "--force", str(workspace_path)])
        elif workspace_path.exists():
            shutil.rmtree(workspace_path)
            return True
        return workspace_path.exists() is False

    def _best_effort_remove_worktree(self, workspace_path: Path) -> None:
        try:
            self._remove_worktree(workspace_path)
        except Exception:
            return

    def _branch_exists(self, branch_name: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(self.repo_root), "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _run_git(self, args: list[str]) -> str:
        command = ["git", "-C", str(self.repo_root), *args]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise GitCommandError(command, result.stderr)
        return result.stdout.strip()

    def _find_git_worktree(self, workspace_path: Path) -> dict[str, str] | None:
        normalized = str(workspace_path.resolve())
        for item in self._list_git_worktrees():
            if item["path"] == normalized:
                return item
        return None

    def _list_git_worktrees(self) -> list[dict[str, str]]:
        output = self._run_git(["worktree", "list", "--porcelain"])
        if not output:
            return []

        items: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in output.splitlines():
            if not line.strip():
                if current:
                    items.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            if key == "worktree":
                current["path"] = str(Path(value).resolve())
            elif key == "branch":
                current["branch"] = value.removeprefix("refs/heads/")
            elif key == "HEAD":
                current["head"] = value
            else:
                current[key] = value
        if current:
            items.append(current)
        return items


def dumps_diagnostic(payload: dict[str, object]) -> str:
    """Serialize diagnose output with stable formatting."""

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
