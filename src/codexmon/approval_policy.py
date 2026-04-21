"""Deterministic diff classification for approval-required changes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import subprocess

from codexmon.ledger import RunLedger


class ApprovalPolicyError(RuntimeError):
    """Raised when approval-required diff classification cannot be completed."""


@dataclass(frozen=True)
class ApprovalScanResult:
    run_id: str
    approval_required: bool
    final_state: str
    approval_request_id: str
    matched_rules: list[str]
    changed_files_count: int
    deleted_lines: int


class ApprovalPolicyService:
    """Classify risky diffs and move runs into awaiting_human when required."""

    DEPENDENCY_FILES = {
        "pyproject.toml",
        "poetry.lock",
        "requirements.txt",
        "requirements-dev.txt",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
        "Gemfile",
        "Gemfile.lock",
    }

    def __init__(self, ledger: RunLedger, default_base_branch: str = "main") -> None:
        self.ledger = ledger
        self.default_base_branch = default_base_branch

    def scan(self, run_id: str, base_branch: str = "") -> ApprovalScanResult:
        run = self.ledger.get_run(run_id)
        if run.current_state not in {"running", "analyzing_failure", "awaiting_human"}:
            raise ApprovalPolicyError(
                f"run '{run_id}' must be in 'running', 'analyzing_failure', or 'awaiting_human'"
            )
        if not run.active_worktree:
            raise ApprovalPolicyError(f"run '{run_id}' does not have an active worktree")

        workspace_path = Path(run.active_worktree)
        if not workspace_path.exists():
            raise ApprovalPolicyError(f"workspace '{workspace_path}' does not exist")

        actual_base_branch = base_branch or self.default_base_branch
        changed_files = self._changed_files(workspace_path, actual_base_branch)
        deleted_lines = self._deleted_lines(workspace_path, actual_base_branch)
        matched_rules = self._matched_rules(changed_files, deleted_lines)

        self.ledger.append_event(
            run_id,
            event_type="approval.diff_classified",
            actor_type="system",
            actor_id="codexmon",
            reason_code="approval-required diff classification completed",
            payload={
                "matched_rules": matched_rules,
                "changed_files_count": len(changed_files),
                "deleted_lines": deleted_lines,
                "base_branch": actual_base_branch,
                "changed_files": changed_files[:20],
            },
        )

        if not matched_rules:
            refreshed = self.ledger.get_run(run_id)
            return ApprovalScanResult(
                run_id=run_id,
                approval_required=False,
                final_state=refreshed.current_state,
                approval_request_id="",
                matched_rules=[],
                changed_files_count=len(changed_files),
                deleted_lines=deleted_lines,
            )

        pending = self.ledger.list_approvals(run_id, status="pending")
        if pending:
            approval_request_id = pending[0].approval_request_id
        else:
            approval_request_id = self.ledger.request_approval(
                run_id,
                requested_by="policy",
                payload={
                    "matched_rules": matched_rules,
                    "changed_files_count": len(changed_files),
                    "deleted_lines": deleted_lines,
                },
            )
        reason_code = f"approval required: {', '.join(matched_rules)}"
        if run.current_state != "awaiting_human":
            run = self.ledger.transition_run(
                run_id,
                "awaiting_human",
                reason_code,
                approval_request_id=approval_request_id,
            )
        else:
            run = self.ledger.get_run(run_id)

        return ApprovalScanResult(
            run_id=run_id,
            approval_required=True,
            final_state=run.current_state,
            approval_request_id=approval_request_id,
            matched_rules=matched_rules,
            changed_files_count=len(changed_files),
            deleted_lines=deleted_lines,
        )

    def _changed_files(self, workspace_path: Path, base_branch: str) -> list[str]:
        committed = self._git(workspace_path, "diff", "--name-only", f"{base_branch}...HEAD")
        tracked = self._git(workspace_path, "diff", "--name-only")
        staged = self._git(workspace_path, "diff", "--name-only", "--cached")
        untracked = self._git(workspace_path, "ls-files", "--others", "--exclude-standard")
        items: set[str] = set()
        for stream in (committed.stdout, tracked.stdout, staged.stdout, untracked.stdout):
            for line in stream.splitlines():
                if line.strip():
                    items.add(line.strip())
        return sorted(items)

    def _deleted_lines(self, workspace_path: Path, base_branch: str) -> int:
        total = 0
        committed = self._git(workspace_path, "diff", "--numstat", f"{base_branch}...HEAD")
        working = self._git(workspace_path, "diff", "--numstat")
        for stream in (committed.stdout, working.stdout):
            for line in stream.splitlines():
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                deleted = parts[1]
                if deleted.isdigit():
                    total += int(deleted)
        return total

    def _matched_rules(self, changed_files: list[str], deleted_lines: int) -> list[str]:
        matched: list[str] = []
        normalized = [self._normalize_path(path) for path in changed_files]
        filenames = {path.name for path in normalized}

        if any(self._is_schema_or_migration(path) for path in normalized):
            matched.append("schema-or-migration")
        if any(self._is_auth_path(path) for path in normalized):
            matched.append("auth-or-authorization")
        if any(self._is_infra_path(path) for path in normalized):
            matched.append("infrastructure-or-ci")
        if any(self._is_sensitive_config_path(path) for path in normalized):
            matched.append("secret-or-sensitive-config")
        if any(name in self.DEPENDENCY_FILES for name in filenames):
            matched.append("dependency-manifest-or-lockfile")
        if len(changed_files) > 5:
            matched.append("large-file-count")
        if deleted_lines > 300:
            matched.append("large-deletion-count")
        return matched

    def _normalize_path(self, raw_path: str) -> PurePosixPath:
        return PurePosixPath(raw_path.replace("\\", "/"))

    def _is_schema_or_migration(self, path: PurePosixPath) -> bool:
        parts = {part.lower() for part in path.parts}
        return bool({"migration", "migrations", "schema", "schemas"} & parts) or path.suffix == ".sql"

    def _is_auth_path(self, path: PurePosixPath) -> bool:
        text = "/".join(part.lower() for part in path.parts)
        tokens = ("auth", "authentication", "authorization", "rbac", "permission")
        return any(token in text for token in tokens)

    def _is_infra_path(self, path: PurePosixPath) -> bool:
        text = "/".join(part.lower() for part in path.parts)
        return any(
            token in text
            for token in (
                ".github/workflows",
                "infra",
                "deploy",
                "deployment",
                "docker",
                "k8s",
                "terraform",
            )
        ) or path.name.startswith("Dockerfile")

    def _is_sensitive_config_path(self, path: PurePosixPath) -> bool:
        text = "/".join(part.lower() for part in path.parts)
        return path.name.startswith(".env") or any(
            token in text for token in ("secret", "credential", "token")
        )

    def _git(self, workspace_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-C", str(workspace_path), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ApprovalPolicyError(
                f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result
