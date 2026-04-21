"""PR handoff orchestration for the v1 success path."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import subprocess
from typing import Protocol
from urllib import error, request

from codexmon import __version__
from codexmon.ledger import RunLedger


class PRHandoffError(RuntimeError):
    """Raised when PR handoff fails or cannot start."""


@dataclass(frozen=True)
class LocalCheckResult:
    command: list[str]
    exit_code: int
    succeeded: bool
    summary: str


@dataclass(frozen=True)
class PullRequestRecord:
    number: int
    url: str


@dataclass(frozen=True)
class PRHandoffResult:
    run_id: str
    final_state: str
    head_branch: str
    base_branch: str
    changed_files_summary: str
    check_summary: str
    pr_reference: str
    pr_url: str
    ci_status: str


class GitHubClient(Protocol):
    """Transport protocol for GitHub pull request creation and CI lookup."""

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
    ) -> PullRequestRecord:
        """Create a pull request."""

    def get_ci_visibility(self, owner: str, repo: str, ref: str) -> str:
        """Read CI visibility for the given ref."""


class GitHubApiClient:
    """Minimal GitHub REST client using the standard library only."""

    def __init__(self, token: str, api_base: str = "https://api.github.com") -> None:
        if not token:
            raise PRHandoffError("GitHub token is required")
        self.token = token
        self.api_base = api_base.rstrip("/")

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
    ) -> PullRequestRecord:
        response = self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            {
                "title": title,
                "body": body,
                "head": head_branch,
                "base": base_branch,
            },
        )
        number = int(response.get("number", 0))
        url = str(response.get("html_url", ""))
        if not number or not url:
            raise PRHandoffError("GitHub PR response is missing number or html_url")
        return PullRequestRecord(number=number, url=url)

    def get_ci_visibility(self, owner: str, repo: str, ref: str) -> str:
        response = self._request("GET", f"/repos/{owner}/{repo}/commits/{ref}/status")
        state = str(response.get("state", "")).strip()
        return state or "unknown"

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"codexmon/{__version__}",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        req = request.Request(
            f"{self.api_base}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=15) as resp:
                raw_body = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise PRHandoffError(f"GitHub API request failed with HTTP {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise PRHandoffError(f"GitHub API request failed: {exc.reason}") from exc

        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise PRHandoffError("GitHub API returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise PRHandoffError("GitHub API response is not an object")
        return parsed


class PRHandoffService:
    """Create a pushable branch, open a PR, and persist handoff metadata."""

    def __init__(
        self,
        ledger: RunLedger,
        github_client: GitHubClient | None,
        default_repo_owner: str = "",
        default_repo_name: str = "",
        default_base_branch: str = "main",
        local_check_command: str = "",
    ) -> None:
        self.ledger = ledger
        self.github_client = github_client
        self.default_repo_owner = default_repo_owner
        self.default_repo_name = default_repo_name
        self.default_base_branch = default_base_branch
        self.local_check_command = local_check_command

    def execute(
        self,
        run_id: str,
        title: str = "",
        base_branch: str = "",
        residual_risk_note: str = "",
    ) -> PRHandoffResult:
        run = self.ledger.get_run(run_id)
        if run.current_state != "pr_handoff":
            raise PRHandoffError(f"run '{run_id}' must be in 'pr_handoff' before PR handoff")
        if run.approval_status == "pending":
            return self._halt(run_id, "PR handoff blocked: pending approval remains")
        if not run.active_worktree or not run.active_branch:
            return self._halt(run_id, "PR handoff blocked: workspace or branch is missing")
        if self.github_client is None:
            return self._halt(run_id, "PR handoff blocked: GitHub client is not configured")

        task = self.ledger.get_task(run.task_id)
        repo_owner = task.repo_owner or self.default_repo_owner
        repo_name = task.repo_name or self.default_repo_name
        if not repo_owner or not repo_name:
            return self._halt(run_id, "PR handoff blocked: repository owner/name is missing")

        actual_base_branch = base_branch or self.default_base_branch
        workspace_path = Path(run.active_worktree)
        if not workspace_path.exists():
            return self._halt(run_id, f"PR handoff blocked: workspace '{workspace_path}' does not exist")

        check_result = self._run_local_check(run_id, workspace_path)
        if not check_result.succeeded:
            return self._halt(
                run_id,
                "PR handoff blocked: local check bundle failed",
                check_summary=check_result.summary,
            )

        commit_summary = task.instruction_summary or run.instruction_summary
        self._ensure_commit(workspace_path, actual_base_branch, commit_summary)
        changed_files = self._changed_files(workspace_path, actual_base_branch)
        if not changed_files:
            return self._halt(
                run_id,
                "PR handoff blocked: no changes are available to hand off",
                check_summary=check_result.summary,
            )
        changed_files_summary = ", ".join(changed_files)
        pr_title = title or self._default_pr_title(commit_summary)
        pr_body = self._build_pr_body(
            task_summary=task.instruction_summary,
            changed_files=changed_files,
            check_summary=check_result.summary,
            residual_risk_note=residual_risk_note,
        )

        self._push_branch(run_id, workspace_path, run.active_branch)
        pr = self.github_client.create_pull_request(
            owner=repo_owner,
            repo=repo_name,
            title=pr_title,
            body=pr_body,
            head_branch=run.active_branch,
            base_branch=actual_base_branch,
        )
        ci_status = self.github_client.get_ci_visibility(
            owner=repo_owner,
            repo=repo_name,
            ref=run.active_branch,
        )

        run = self.ledger.set_pr_reference(
            run_id=run_id,
            provider="github",
            pr_number=pr.number,
            pr_url=pr.url,
            head_branch=run.active_branch,
            base_branch=actual_base_branch,
            ci_status=ci_status,
        )
        self.ledger.append_event(
            run_id,
            event_type="github.pr.created",
            actor_type="system",
            actor_id="codexmon",
            reason_code="github pull request created",
            payload={
                "pr_number": pr.number,
                "pr_url": pr.url,
                "base_branch": actual_base_branch,
                "title": pr_title,
                "body": pr_body,
            },
        )
        self.ledger.append_event(
            run_id,
            event_type="github.ci.visibility",
            actor_type="system",
            actor_id="codexmon",
            reason_code="github ci visibility recorded",
            payload={"ci_status": ci_status, "ref": run.active_branch},
        )
        run = self.ledger.transition_run(
            run_id,
            "completed",
            "PR opened successfully",
            changed_files_summary=changed_files_summary,
            check_summary=check_result.summary,
            pr_reference=run.pr_reference,
        )
        return PRHandoffResult(
            run_id=run_id,
            final_state=run.current_state,
            head_branch=run.active_branch,
            base_branch=actual_base_branch,
            changed_files_summary=changed_files_summary,
            check_summary=check_result.summary,
            pr_reference=run.pr_reference,
            pr_url=pr.url,
            ci_status=ci_status,
        )

    def _run_local_check(self, run_id: str, workspace_path: Path) -> LocalCheckResult:
        if not self.local_check_command.strip():
            return LocalCheckResult(
                command=[],
                exit_code=1,
                succeeded=False,
                summary="required local check bundle is not configured",
            )

        command = shlex.split(self.local_check_command)
        self.ledger.append_event(
            run_id,
            event_type="handoff.local_check.started",
            actor_type="system",
            actor_id="codexmon",
            reason_code="local check bundle started",
            payload={"command": command},
        )
        completed = subprocess.run(
            command,
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=False,
        )
        output = self._summarize_output(completed.stdout, completed.stderr)
        status_label = "passed" if completed.returncode == 0 else f"failed (exit {completed.returncode})"
        summary = f"{' '.join(command)} -> {status_label}"
        if output:
            summary = f"{summary} / {output}"
        self.ledger.append_event(
            run_id,
            event_type="handoff.local_check.finished",
            actor_type="system",
            actor_id="codexmon",
            reason_code="local check bundle finished",
            payload={
                "command": command,
                "exit_code": completed.returncode,
                "summary": summary,
            },
        )
        return LocalCheckResult(
            command=command,
            exit_code=completed.returncode,
            succeeded=completed.returncode == 0,
            summary=summary,
        )

    def _ensure_commit(self, workspace_path: Path, base_branch: str, instruction_summary: str) -> None:
        if self._has_worktree_changes(workspace_path):
            self._git(workspace_path, "add", "-A")
            self._git(
                workspace_path,
                "commit",
                "-m",
                self._default_commit_message(instruction_summary),
            )
            return

        counts = self._git(workspace_path, "rev-list", "--left-right", "--count", f"{base_branch}...HEAD")
        ahead_count = int(counts.stdout.strip().split()[1])
        if ahead_count <= 0:
            raise PRHandoffError("no local changes or branch commits are available for PR handoff")

    def _push_branch(self, run_id: str, workspace_path: Path, branch_name: str) -> None:
        try:
            self._git(workspace_path, "push", "-u", "origin", branch_name)
        except PRHandoffError as exc:
            self.ledger.append_event(
                run_id,
                event_type="handoff.branch.push_failed",
                actor_type="system",
                actor_id="codexmon",
                reason_code="git push failed",
                payload={"branch_name": branch_name, "error": str(exc)},
            )
            raise
        self.ledger.append_event(
            run_id,
            event_type="handoff.branch.pushed",
            actor_type="system",
            actor_id="codexmon",
            reason_code="git push succeeded",
            payload={"branch_name": branch_name},
        )

    def _changed_files(self, workspace_path: Path, base_branch: str) -> list[str]:
        result = self._git(workspace_path, "diff", "--name-only", f"{base_branch}...HEAD")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def _git(self, workspace_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", "-C", str(workspace_path), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            details = completed.stderr.strip() or completed.stdout.strip()
            raise PRHandoffError(f"git {' '.join(args)} failed: {details}")
        return completed

    def _has_worktree_changes(self, workspace_path: Path) -> bool:
        status = self._git(workspace_path, "status", "--porcelain")
        return bool(status.stdout.strip())

    def _build_pr_body(
        self,
        task_summary: str,
        changed_files: list[str],
        check_summary: str,
        residual_risk_note: str,
    ) -> str:
        changed_files_lines = "\n".join(f"- {item}" for item in changed_files)
        risk_note = residual_risk_note or "추가 검토가 필요할 수 있는 리스크를 아직 명시하지 않았다."
        return "\n".join(
            [
                "## Task Summary",
                task_summary or "요약 없음",
                "",
                "## Changed Files",
                changed_files_lines or "- 변경 파일 없음",
                "",
                "## Local Check Result",
                f"- {check_summary}",
                "",
                "## Residual Risk",
                risk_note,
            ]
        )

    def _summarize_output(self, stdout: str, stderr: str) -> str:
        merged = [line.strip() for line in (stdout + "\n" + stderr).splitlines() if line.strip()]
        if not merged:
            return ""
        return " | ".join(merged[-3:])[:240]

    def _default_pr_title(self, task_summary: str) -> str:
        summary = " ".join(task_summary.strip().split()) if task_summary.strip() else "codexmon handoff"
        return summary[:72]

    def _default_commit_message(self, task_summary: str) -> str:
        summary = " ".join(task_summary.strip().split()) if task_summary.strip() else "codexmon handoff"
        return f"codexmon: {summary[:60]}"

    def _halt(
        self,
        run_id: str,
        reason_code: str,
        check_summary: str = "",
    ) -> PRHandoffResult:
        run = self.ledger.transition_run(
            run_id,
            "halted",
            reason_code,
            check_summary=check_summary,
        )
        self.ledger.append_event(
            run_id,
            event_type="pr.handoff.failed",
            actor_type="system",
            actor_id="codexmon",
            reason_code=reason_code,
            payload={"check_summary": check_summary},
        )
        return PRHandoffResult(
            run_id=run_id,
            final_state=run.current_state,
            head_branch=run.active_branch,
            base_branch=self.default_base_branch,
            changed_files_summary="",
            check_summary=check_summary,
            pr_reference=run.pr_reference,
            pr_url="",
            ci_status="",
        )
