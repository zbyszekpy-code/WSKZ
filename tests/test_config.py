"""Tests for application settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.policy import DEFAULT_SYSTEM_PROMPT
from app.core.config import Settings, get_settings


def test_settings_default_message_max_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MESSAGE_MAX_LENGTH", raising=False)
    get_settings.cache_clear()

    assert Settings(_env_file=None).message_max_length == 2000


def test_settings_reads_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGE_MAX_LENGTH", "123")

    assert Settings(_env_file=None).message_max_length == 123


def test_settings_rejects_non_positive_message_max_length() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, message_max_length=0)


def test_settings_ignores_unrelated_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("UNRELATED_KEY=value\n")

    assert Settings(_env_file=env_file).message_max_length == 2000


def test_settings_default_ollama_and_smtp_contract() -> None:
    settings = Settings(_env_file=None)

    assert settings.ollama_base_url == "http://ollama:11434"
    assert settings.ollama_model == "llama3.1:8b"
    assert settings.ollama_timeout_seconds == 30.0
    assert settings.ollama_max_retries == 2
    assert settings.smtp_host == "mailpit"
    assert settings.smtp_port == 1025
    assert settings.smtp_from == "noreply@ai-router.local"
    assert settings.smtp_timeout_seconds == 10.0
    assert settings.smtp_max_retries == 3
    assert settings.agent_timeout_seconds == 180.0


def test_settings_reads_new_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:4b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("OLLAMA_MAX_RETRIES", "5")

    settings = Settings(_env_file=None)

    assert settings.ollama_model == "qwen3:4b"
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_timeout_seconds == 12.5
    assert settings.ollama_max_retries == 5


def test_ollama_openai_base_url_appends_v1() -> None:
    settings = Settings(_env_file=None, ollama_base_url="http://localhost:11434")

    assert settings.ollama_openai_base_url == "http://localhost:11434/v1"


def test_ollama_openai_base_url_keeps_existing_v1() -> None:
    settings = Settings(_env_file=None, ollama_base_url="http://localhost:11434/v1/")

    assert settings.ollama_openai_base_url == "http://localhost:11434/v1"


def test_settings_rejects_non_positive_ollama_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ollama_timeout_seconds=0)


def test_settings_rejects_negative_retries() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ollama_max_retries=-1)


def test_settings_default_retry_and_reason_contract() -> None:
    settings = Settings(_env_file=None)

    assert settings.ollama_retry_backoff_seconds == 0.1
    assert settings.smtp_retry_backoff_seconds == 0.1
    assert settings.agent_retry_backoff_seconds == 0.1
    assert settings.agent_reason_max_length == 300
    assert settings.agent_no_send_max_attempts == 2


def test_settings_reads_new_retry_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_RETRY_BACKOFF_SECONDS", "0.5")
    monkeypatch.setenv("SMTP_RETRY_BACKOFF_SECONDS", "0.25")
    monkeypatch.setenv("AGENT_RETRY_BACKOFF_SECONDS", "0.75")
    monkeypatch.setenv("AGENT_REASON_MAX_LENGTH", "120")
    monkeypatch.setenv("AGENT_NO_SEND_MAX_ATTEMPTS", "4")

    settings = Settings(_env_file=None)

    assert settings.ollama_retry_backoff_seconds == 0.5
    assert settings.smtp_retry_backoff_seconds == 0.25
    assert settings.agent_retry_backoff_seconds == 0.75
    assert settings.agent_reason_max_length == 120
    assert settings.agent_no_send_max_attempts == 4


def test_settings_rejects_invalid_retry_and_reason_values() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, smtp_retry_backoff_seconds=-0.1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_retry_backoff_seconds=-0.1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ollama_retry_backoff_seconds=-0.1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_reason_max_length=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_no_send_max_attempts=0)


def test_settings_default_system_prompt_is_canonical() -> None:
    assert Settings(_env_file=None).agent_system_prompt == DEFAULT_SYSTEM_PROMPT


def test_settings_reads_system_prompt_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_SYSTEM_PROMPT", "Custom routing instructions.")

    assert Settings(_env_file=None).agent_system_prompt == "Custom routing instructions."


def test_settings_rejects_empty_system_prompt() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_system_prompt="")
