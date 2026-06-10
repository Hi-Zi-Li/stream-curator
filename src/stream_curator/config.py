"""Runtime configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


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
    project_root = Path(__file__).resolve().parents[2]
    return Settings(
        project_root=project_root,
        db_path=project_root / "data" / "stream_curator.db",
        llm_chat_completions_url=os.getenv(
            "STREAM_CURATOR_LLM_CHAT_COMPLETIONS_URL",
            "https://opencode.ai/zen/go/v1/chat/completions",
        ),
        llm_api_key=os.getenv("STREAM_CURATOR_LLM_API_KEY") or os.getenv("OPENCODE_API_KEY"),
        llm_model="deepseek-v4-flash",
        llm_fallback_model="",
        llm_timeout_seconds=int(os.getenv("STREAM_CURATOR_LLM_TIMEOUT_SECONDS", "35")),
        worker_poll_interval_seconds=int(os.getenv("STREAM_CURATOR_WORKER_POLL_INTERVAL_SECONDS", "30")),
        bilibili_executable=os.getenv("STREAM_CURATOR_BILIBILI_EXECUTABLE", "E:\\Anaconda3\\envs\\streamcurator\\Scripts\\bili.exe"),
        zhihu_executable=os.getenv("STREAM_CURATOR_ZHIHU_EXECUTABLE", "E:\\Anaconda3\\envs\\streamcurator\\Scripts\\zhihu.exe"),
        xiaohongshu_executable=os.getenv("STREAM_CURATOR_XIAOHONGSHU_EXECUTABLE", "E:\\Anaconda3\\envs\\streamcurator\\Scripts\\xhs.exe"),
    )
