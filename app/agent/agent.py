"""pydantic-ai agent construction for the routing service."""

import httpx2
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.retries import AsyncHTTPX2TenacityTransport, RetryConfig
from pydantic_ai.settings import ModelSettings
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from app.agent.deps import RoutingDeps
from app.core.config import Settings
from app.tools.email import send_email

_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


def _validate_response(response: httpx2.Response) -> None:
    """Raise for transient HTTP statuses so the transport retries them.

    Non-retryable 4xx responses are returned untouched: the caller decides what
    to do with them and the transport does not retry.
    """
    if response.status_code in _RETRYABLE_STATUS_CODES:
        response.raise_for_status()


def build_ollama_http_client(settings: Settings) -> httpx2.AsyncClient:
    """Build the retrying HTTP client used by the Ollama provider.

    Transient transport errors and the retryable HTTP statuses are retried up to
    ``ollama_max_retries + 1`` times with ``ollama_retry_backoff_seconds``
    exponential backoff. The client owns its transport; the provider does not
    close a client it was handed, so the caller keeps the lifecycle.
    """
    transport = AsyncHTTPX2TenacityTransport(
        RetryConfig(
            stop=stop_after_attempt(settings.ollama_max_retries + 1),
            wait=wait_exponential(multiplier=settings.ollama_retry_backoff_seconds),
            retry=retry_if_exception_type((httpx2.TransportError, httpx2.HTTPStatusError)),
            reraise=True,
        ),
        validate_response=_validate_response,
    )
    return httpx2.AsyncClient(transport=transport, timeout=settings.ollama_timeout_seconds)


def build_ollama_model(settings: Settings) -> OllamaModel:
    """Build the Ollama-backed model from configuration.

    ``temperature=0`` and the request timeout are mandatory: with the default
    temperature the model misroutes ambiguous messages and can skip the tool
    call entirely.
    """
    return OllamaModel(
        settings.ollama_model,
        provider=OllamaProvider(
            base_url=settings.ollama_openai_base_url,
            http_client=build_ollama_http_client(settings),
        ),
        settings=ModelSettings(temperature=0, timeout=settings.ollama_timeout_seconds),
    )


def build_agent(model: Model, settings: Settings) -> Agent[RoutingDeps, str]:
    """Build the routing agent on ``model`` with the email tool and policy."""
    return Agent(
        model,
        deps_type=RoutingDeps,
        system_prompt=settings.agent_system_prompt,
        tools=[send_email],
    )
