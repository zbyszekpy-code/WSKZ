"""Application settings loaded from the environment and an optional `.env` file."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.agent.policy import DEFAULT_SYSTEM_PROMPT


class Settings(BaseSettings):
    """Cross-cutting application settings.

    Field names match the `.env.example` contract. ``extra="ignore"`` keeps a
    local ``.env`` containing keys that are not fields yet from failing
    validation. The default system prompt is the pinned policy from
    :mod:`app.agent.policy`, so the routing behaviour is unchanged unless
    ``AGENT_SYSTEM_PROMPT`` explicitly overrides it.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    message_max_length: int = Field(default=2000, gt=0)
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_timeout_seconds: float = Field(default=30.0, gt=0)
    ollama_max_retries: int = Field(default=2, ge=0)
    ollama_retry_backoff_seconds: float = Field(default=0.1, ge=0)
    smtp_host: str = "mailpit"
    smtp_port: int = Field(default=1025, gt=0, le=65535)
    smtp_from: str = "noreply@ai-router.local"
    smtp_timeout_seconds: float = Field(default=10.0, gt=0)
    smtp_max_retries: int = Field(default=3, ge=0)
    smtp_retry_backoff_seconds: float = Field(default=0.1, ge=0)
    agent_timeout_seconds: float = Field(default=180.0, gt=0)
    agent_retry_backoff_seconds: float = Field(default=0.1, ge=0)
    agent_reason_max_length: int = Field(default=300, gt=0)
    agent_no_send_max_attempts: int = Field(default=2, ge=1)
    agent_system_prompt: str = Field(default=DEFAULT_SYSTEM_PROMPT, min_length=1)

    @property
    def ollama_openai_base_url(self) -> str:
        """Return ``ollama_base_url`` normalized to Ollama's OpenAI-compatible API.

        ``OllamaModel`` talks to Ollama's OpenAI-compatible endpoint, which needs
        the ``/v1`` suffix. The normalization is idempotent, so a base URL that
        already ends in ``/v1`` is returned unchanged.
        """
        base_url = self.ollama_base_url.rstrip("/")
        if base_url.endswith("/v1"):
            return base_url
        return f"{base_url}/v1"


@lru_cache
def get_settings() -> Settings:
    """Return the cached process-wide settings instance."""
    return Settings()
