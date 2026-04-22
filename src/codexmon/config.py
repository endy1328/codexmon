"""Configuration helpers for codexmon."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    """Minimal runtime settings used by the baseline CLI."""

    log_level: str
    db_path: Path
    repo_path: Path
    worktree_root: Path
    codex_command: str
    codex_model: str
    codex_sandbox: str
    automatic_retry_budget: int
    idle_timeout_seconds: int
    wall_clock_timeout_seconds: int
    github_owner: str
    github_repo: str
    github_token: str
    github_api_base: str
    github_base_branch: str
    local_check_command: str
    daemon_worker_name: str
    daemon_poll_interval_seconds: int
    telegram_bot_token: str
    telegram_api_base: str
    telegram_chat_id: str

    @classmethod
    def from_env(cls) -> "Settings":
        repo_path = Path(os.getenv("CODEXMON_REPO_PATH", "."))
        worktree_root = Path(os.getenv("CODEXMON_WORKTREE_ROOT", ".codexmon/worktrees"))
        return cls(
            log_level=os.getenv("CODEXMON_LOG_LEVEL", "INFO"),
            db_path=Path(os.getenv("CODEXMON_DB_PATH", ".codexmon/codexmon.db")),
            repo_path=repo_path,
            worktree_root=worktree_root,
            codex_command=os.getenv("CODEXMON_CODEX_COMMAND", "codex"),
            codex_model=os.getenv("CODEXMON_CODEX_MODEL", ""),
            codex_sandbox=os.getenv("CODEXMON_CODEX_SANDBOX", "workspace-write"),
            automatic_retry_budget=int(os.getenv("CODEXMON_AUTOMATIC_RETRY_BUDGET", "1")),
            idle_timeout_seconds=int(os.getenv("CODEXMON_IDLE_TIMEOUT_SECONDS", "900")),
            wall_clock_timeout_seconds=int(os.getenv("CODEXMON_WALL_CLOCK_TIMEOUT_SECONDS", "7200")),
            github_owner=os.getenv("CODEXMON_GITHUB_OWNER", ""),
            github_repo=os.getenv("CODEXMON_GITHUB_REPO", ""),
            github_token=os.getenv("CODEXMON_GITHUB_TOKEN", ""),
            github_api_base=os.getenv("CODEXMON_GITHUB_API_BASE", "https://api.github.com"),
            github_base_branch=os.getenv("CODEXMON_GITHUB_BASE_BRANCH", "main"),
            local_check_command=os.getenv("CODEXMON_LOCAL_CHECK_COMMAND", ""),
            daemon_worker_name=os.getenv("CODEXMON_DAEMON_WORKER_NAME", "codexmon-daemon"),
            daemon_poll_interval_seconds=int(os.getenv("CODEXMON_DAEMON_POLL_INTERVAL_SECONDS", "15")),
            telegram_bot_token=os.getenv("CODEXMON_TELEGRAM_BOT_TOKEN", ""),
            telegram_api_base=os.getenv("CODEXMON_TELEGRAM_API_BASE", "https://api.telegram.org"),
            telegram_chat_id=os.getenv("CODEXMON_TELEGRAM_CHAT_ID", ""),
        )
