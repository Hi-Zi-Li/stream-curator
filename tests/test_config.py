from pathlib import Path
import json

from stream_curator.config import get_settings


def test_settings_respect_project_root_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STREAM_CURATOR_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("STREAM_CURATOR_DB_PATH", raising=False)

    settings = get_settings()

    assert settings.project_root == tmp_path
    assert settings.db_path == tmp_path / "data" / "stream_curator.db"


def test_settings_respect_db_path_override(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "custom" / "push.db"
    monkeypatch.setenv("STREAM_CURATOR_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("STREAM_CURATOR_DB_PATH", str(db_path))

    settings = get_settings()

    assert settings.project_root == tmp_path
    assert settings.db_path == db_path


def test_settings_load_llm_values_from_app_settings_file(monkeypatch, tmp_path: Path) -> None:
    settings_path = tmp_path / "data" / "app-settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "llm_chat_completions_url": "https://example.com/v1/chat/completions",
                "llm_api_key": "saved-key",
                "llm_model": "deepseek-v4-pro",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STREAM_CURATOR_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("STREAM_CURATOR_LLM_CHAT_COMPLETIONS_URL", raising=False)
    monkeypatch.delenv("STREAM_CURATOR_LLM_API_KEY", raising=False)
    monkeypatch.delenv("STREAM_CURATOR_LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)

    settings = get_settings()

    assert settings.llm_chat_completions_url == "https://example.com/v1/chat/completions"
    assert settings.llm_api_key == "saved-key"
    assert settings.llm_model == "deepseek-v4-pro"


def test_saved_api_key_overrides_opencode_env(monkeypatch, tmp_path: Path) -> None:
    settings_path = tmp_path / "data" / "app-settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({"llm_api_key": "saved-key"}), encoding="utf-8")
    monkeypatch.setenv("STREAM_CURATOR_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("STREAM_CURATOR_LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENCODE_API_KEY", "env-key")

    settings = get_settings()

    assert settings.llm_api_key == "saved-key"
