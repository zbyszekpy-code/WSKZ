"""Tests for the agent-backed routing service, driven offline by FunctionModel."""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx2
import pytest
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent import agent as agent_module
from app.agent.agent import build_agent, build_ollama_model
from app.agent.service import AgentRoutingService
from app.core.config import Settings
from app.core.errors import RoutingError
from app.core.routing import RouteRequest
from app.tools.email import EmailMessage, RecordingEmailTransport


@dataclass
class _SuspendingTransport:
    """Transport whose ``send`` yields control, exposing check-then-send races."""

    messages: list[EmailMessage] = field(default_factory=list)
    send_calls: int = 0

    async def send(self, message: EmailMessage) -> None:
        self.send_calls += 1
        await asyncio.sleep(0)
        self.messages.append(message)


def _has_tool_return(messages: list[ModelMessage]) -> bool:
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "send_email":
                return True
    return False


def _system_prompts(messages: list[ModelMessage]) -> list[str]:
    prompts: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, SystemPromptPart):
                prompts.append(part.content)
    return prompts


def _send_args(recipient: str) -> dict[str, str]:
    return {
        "recipient": recipient,
        "subject": "Forwarded request",
        "body": "A user request needs attention.",
        "reply_to": "",
    }


def _service(
    model: Model,
    *,
    no_send_attempts: int = 2,
    timeout: float = 60.0,
    reason_max_length: int = 300,
) -> tuple[AgentRoutingService, RecordingEmailTransport]:
    transport = RecordingEmailTransport()
    settings = Settings(
        _env_file=None,
        agent_no_send_max_attempts=no_send_attempts,
        agent_timeout_seconds=timeout,
        agent_reason_max_length=reason_max_length,
    )
    service = AgentRoutingService(
        settings=settings,
        agent_factory=lambda: build_agent(model, settings),
        transport_factory=lambda: transport,
    )
    return service, transport


def _tool_calling_model(recipient: str) -> FunctionModel:
    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _has_tool_return(messages):
            return ModelResponse(parts=[TextPart("Routed for the stated reason.")])
        return ModelResponse(parts=[ToolCallPart("send_email", _send_args(recipient))])

    return FunctionModel(function)


def _build_captured_model(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> tuple[Model, list[httpx2.AsyncClient]]:
    captured: list[httpx2.AsyncClient] = []
    real_builder = agent_module.build_ollama_http_client

    def spy(resolved: Settings) -> httpx2.AsyncClient:
        client = real_builder(resolved)
        captured.append(client)
        return client

    monkeypatch.setattr(agent_module, "build_ollama_http_client", spy)
    return build_ollama_model(settings), captured


async def test_routes_to_recipient_via_send_email() -> None:
    service, transport = _service(_tool_calling_model("help-desk@example.com"))

    response = await service.route(
        RouteRequest(email="jan.nowak@example.com", message="Nie działa mi komputer")
    )

    assert response.recipient == "help-desk@example.com"
    assert response.reason == "Routed for the stated reason."
    assert len(transport.messages) == 1
    sent = transport.messages[0]
    assert sent.recipient == "help-desk@example.com"
    assert sent.subject == "Forwarded request"
    assert sent.body == "A user request needs attention."
    assert sent.reply_to == "jan.nowak@example.com"


async def test_vacation_message_routes_to_kadry() -> None:
    service, transport = _service(_tool_calling_model("kadry@example.com"))

    response = await service.route(
        RouteRequest(email="a@example.com", message="Chciałbym zgłosić urlop na jutro")
    )

    assert response.recipient == "kadry@example.com"
    assert len(transport.messages) == 1


async def test_unrecognized_message_falls_back_to_other() -> None:
    service, transport = _service(_tool_calling_model("other@example.com"))

    response = await service.route(
        RouteRequest(email="a@example.com", message="Mam pytanie ogólne")
    )

    assert response.recipient == "other@example.com"
    assert len(transport.messages) == 1


async def test_no_tool_call_retries_are_bounded() -> None:
    calls: list[int] = []

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse(parts=[TextPart("I am not sure.")])

    service, transport = _service(FunctionModel(function), no_send_attempts=2)

    with pytest.raises(RoutingError, match="did not send an email"):
        await service.route(RouteRequest(email="a@example.com", message="hello"))

    assert len(calls) == 2
    assert transport.messages == []


def test_agent_uses_temperature_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)
    model, captured = _build_captured_model(monkeypatch, settings)
    agent = build_agent(model, settings)

    assert agent.model is model
    assert model.settings is not None
    assert model.settings["temperature"] == 0

    asyncio.run(captured[0].aclose())


async def test_second_send_is_ignored() -> None:
    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _has_tool_return(messages):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[
                ToolCallPart("send_email", _send_args("help-desk@example.com")),
                ToolCallPart("send_email", _send_args("it@example.com")),
            ]
        )

    service, transport = _service(FunctionModel(function))

    response = await service.route(RouteRequest(email="a@example.com", message="x"))

    assert response.recipient == "help-desk@example.com"
    assert len(transport.messages) == 1


async def test_concurrent_duplicate_send_is_serialized() -> None:
    transport = _SuspendingTransport()

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _has_tool_return(messages):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[
                ToolCallPart("send_email", _send_args("help-desk@example.com")),
                ToolCallPart("send_email", _send_args("it@example.com")),
            ]
        )

    settings = Settings(_env_file=None, agent_no_send_max_attempts=1, agent_timeout_seconds=60.0)
    service = AgentRoutingService(
        settings=settings,
        agent_factory=lambda: build_agent(FunctionModel(function), settings),
        transport_factory=lambda: transport,
    )

    response = await service.route(RouteRequest(email="a@example.com", message="x"))

    assert transport.send_calls == 1
    assert len(transport.messages) == 1
    assert response.recipient == transport.messages[0].recipient


async def test_model_api_error_is_not_retried_at_agent_level() -> None:
    calls: list[int] = []

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        raise ModelAPIError("function", "connection refused")

    service, _ = _service(FunctionModel(function), no_send_attempts=2)

    with pytest.raises(RoutingError, match="temporarily unavailable"):
        await service.route(RouteRequest(email="a@example.com", message="hello"))

    assert len(calls) == 1


async def test_transient_error_is_retried_by_transport_not_agent() -> None:
    calls: list[int] = []

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        if len(calls) == 1:
            raise ModelAPIError("function", "connection failed")
        if _has_tool_return(messages):
            return ModelResponse(parts=[TextPart("Routed after transport retry.")])
        return ModelResponse(parts=[ToolCallPart("send_email", _send_args("it@example.com"))])

    service, transport = _service(FunctionModel(function), no_send_attempts=2)

    with pytest.raises(RoutingError, match="temporarily unavailable"):
        await service.route(RouteRequest(email="a@example.com", message="VPN is down"))

    assert len(calls) == 1
    assert transport.messages == []


async def test_unexpected_model_behavior_maps_to_routing_error() -> None:
    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise UnexpectedModelBehavior("model produced an invalid tool call")

    service, transport = _service(FunctionModel(function), no_send_attempts=2)

    with pytest.raises(RoutingError, match="temporarily unavailable"):
        await service.route(RouteRequest(email="a@example.com", message="hello"))

    assert transport.messages == []


async def test_send_then_model_error_returns_without_retrying() -> None:
    calls: list[int] = []

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        if _has_tool_return(messages):
            raise ModelAPIError("function", "connection lost after send")
        return ModelResponse(
            parts=[ToolCallPart("send_email", _send_args("help-desk@example.com"))]
        )

    service, transport = _service(FunctionModel(function), no_send_attempts=2)

    response = await service.route(RouteRequest(email="a@example.com", message="x"))

    assert response.recipient == "help-desk@example.com"
    assert response.reason == "Routed to help-desk@example.com."
    assert len(transport.messages) == 1
    assert len(calls) == 2


async def test_reason_is_truncated_to_configured_length() -> None:
    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _has_tool_return(messages):
            return ModelResponse(parts=[TextPart("x" * 100)])
        return ModelResponse(parts=[ToolCallPart("send_email", _send_args("it@example.com"))])

    service, _ = _service(FunctionModel(function), reason_max_length=10)

    response = await service.route(RouteRequest(email="a@example.com", message="x"))

    assert response.reason == "x" * 10


async def test_logged_reason_is_email_redacted(caplog: pytest.LogCaptureFixture) -> None:
    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _has_tool_return(messages):
            return ModelResponse(parts=[TextPart("Escalate to admin@example.com for review.")])
        return ModelResponse(parts=[ToolCallPart("send_email", _send_args("it@example.com"))])

    service, _ = _service(FunctionModel(function))

    with caplog.at_level(logging.INFO, logger="app.agent.service"):
        response = await service.route(RouteRequest(email="a@example.com", message="x"))

    assert "admin@example.com" not in caplog.text
    assert "<email>" in caplog.text
    assert response.reason == "Escalate to admin@example.com for review."


async def test_agent_uses_configured_system_prompt() -> None:
    captured: list[str] = []

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured.extend(_system_prompts(messages))
        if _has_tool_return(messages):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart("send_email", _send_args("it@example.com"))])

    settings = Settings(_env_file=None, agent_system_prompt="Custom routing policy.")
    service = AgentRoutingService(
        settings=settings,
        agent_factory=lambda: build_agent(FunctionModel(function), settings),
        transport_factory=RecordingEmailTransport,
    )

    await service.route(RouteRequest(email="a@example.com", message="x"))

    assert captured
    assert all(prompt == "Custom routing policy." for prompt in captured)


async def test_agent_timeout_raises_routing_error() -> None:
    async def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(1)
        return ModelResponse(parts=[TextPart("too late")])

    service, _ = _service(FunctionModel(function), timeout=0.05)

    with pytest.raises(RoutingError, match="timed out"):
        await service.route(RouteRequest(email="a@example.com", message="hello"))


def test_ollama_model_uses_configured_name_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        ollama_model="llama3.1:8b",
        ollama_base_url="http://localhost:11434",
    )

    model, captured = _build_captured_model(monkeypatch, settings)

    assert model.model_name == "llama3.1:8b"
    assert model.base_url is not None
    assert model.base_url.rstrip("/") == "http://localhost:11434/v1"
    assert model.settings is not None
    assert model.settings["timeout"] == settings.ollama_timeout_seconds

    asyncio.run(captured[0].aclose())
