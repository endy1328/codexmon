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
    github_owner: str
    github_repo: str
    telegram_chat_id: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            log_level=os.getenv("CODEXMON_LOG_LEVEL", "INFO"),
            db_path=Path(os.getenv("CODEXMON_DB_PATH", ".codexmon/codexmon.db")),
            github_owner=os.getenv("CODEXMON_GITHUB_OWNER", ""),
            github_repo=os.getenv("CODEXMON_GITHUB_REPO", ""),
            telegram_chat_id=os.getenv("CODEXMON_TELEGRAM_CHAT_ID", ""),
        )
