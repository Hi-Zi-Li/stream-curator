"""Runtime configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

DEFAULT_LLM_CHAT_COMPLETIONS_URL = "https://opencode.ai/zen/go/v1/chat/completions"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"
APP_SETTINGS_FILE_NAME = "app-settings.json"


@dataclass(frozen=True)
class Settings:
    project_root: Path
    db_path: Path
    llm_chat_completions_url: str
    llm_api_key: str | None
    llm_model: str
    llm_fallback_model: str
    llm_timeout_seconds: int
    worker_poll_interval_seconds: int
    bilibili_executable: str
    zhihu_executable: str
    xiaohongshu_executable: str


def get_settings() -> Settings:
    project_root = Path(
        os.getenv("STREAM_CURATOR_PROJECT_ROOT") or Path(__file__).resolve().parents[2]
    )
    db_path = Path(
        os.getenv("STREAM_CURATOR_DB_PATH") or (project_root / "data" / "stream_curator.db")
    )
    app_settings = _load_app_settings(project_root)
    return Settings(
        project_root=project_root,
        db_path=db_path,
        llm_chat_completions_url=_first_non_empty(
            os.getenv("STREAM_CURATOR_LLM_CHAT_COMPLETIONS_URL"),
            _read_text(app_settings.get("llm_chat_completions_url")),
            DEFAULT_LLM_CHAT_COMPLETIONS_URL,
        ),
        llm_api_key=_first_non_empty(
            os.getenv("STREAM_CURATOR_LLM_API_KEY"),
            _read_text(app_settings.get("llm_api_key")),
            os.getenv("OPENCODE_API_KEY"),
        ),
        llm_model=_first_non_empty(
            os.getenv("STREAM_CURATOR_LLM_MODEL"),
            _read_text(app_settings.get("llm_model")),
            DEFAULT_LLM_MODEL,
        ),
        llm_fallback_model="",
        llm_timeout_seconds=int(os.getenv("STREAM_CURATOR_LLM_TIMEOUT_SECONDS", "35")),
        worker_poll_interval_seconds=int(os.getenv("STREAM_CURATOR_WORKER_POLL_INTERVAL_SECONDS", "30")),
        bilibili_executable=_resolve_source_executable(
            project_root=project_root,
            env_var_name="STREAM_CURATOR_BILIBILI_EXECUTABLE",
            local_relative_path=Path("third-party") / "bin" / "bili.cmd",
            fallback_path="E:\\Anaconda3\\envs\\streamcurator\\Scripts\\bili.exe",
        ),
        zhihu_executable=_resolve_source_executable(
            project_root=project_root,
            env_var_name="STREAM_CURATOR_ZHIHU_EXECUTABLE",
            local_relative_path=Path("third-party") / "bin" / "zhihu.cmd",
            fallback_path="E:\\Anaconda3\\envs\\streamcurator\\Scripts\\zhihu.exe",
        ),
        xiaohongshu_executable=_resolve_source_executable(
            project_root=project_root,
            env_var_name="STREAM_CURATOR_XIAOHONGSHU_EXECUTABLE",
            local_relative_path=Path("third-party") / "bin" / "xhs.cmd",
            fallback_path="E:\\Anaconda3\\envs\\streamcurator\\Scripts\\xhs.exe",
        ),
    )


def _app_settings_path(project_root: Path) -> Path:
    return Path(
        os.getenv("STREAM_CURATOR_APP_SETTINGS_PATH")
        or (project_root / "data" / APP_SETTINGS_FILE_NAME)
    )


def _load_app_settings(project_root: Path) -> dict[str, object]:
    path = _app_settings_path(project_root)
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_text(value: object) -> str:
    text = str(value or "").strip()
    return text


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _resolve_source_executable(
    *,
    project_root: Path,
    env_var_name: str,
    local_relative_path: Path,
    fallback_path: str,
) -> str:
    explicit_path = _read_text(os.getenv(env_var_name))
    if explicit_path:
        return explicit_path
    local_path = project_root / local_relative_path
    if local_path.exists():
        return str(local_path)
    return fallback_path
