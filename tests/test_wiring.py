"""Wiring test: ``build_routing_service`` uses the configured SMTP and prompt."""

from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent import service as service_module
from app.core.config import Settings
from app.core.routing import RouteRequest
from test_smtp_transport import FakeSmtpServer

SENDER = "jan.nowak@example.com"
RECIPIENT = "help-desk@example.com"
SUBJECT = "Forwarded request"
BODY = "A user request needs attention."


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


def _recording_model(instructions: list[str]) -> FunctionModel:
    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        instructions.extend(_system_prompts(messages))
        if _has_tool_return(messages):
            return ModelResponse(parts=[TextPart("Routed.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "send_email",
                    {
                        "recipient": RECIPIENT,
                        "subject": SUBJECT,
                        "body": BODY,
                        "reply_to": "",
                    },
                )
            ]
        )

    return FunctionModel(function)


async def test_build_routing_service_wires_configured_transport_and_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instructions: list[str] = []
    model = _recording_model(instructions)
    monkeypatch.setattr(service_module, "build_ollama_model", lambda settings: model)

    async with FakeSmtpServer() as server:
        settings = Settings(
            _env_file=None,
            smtp_host="127.0.0.1",
            smtp_port=server.port,
            smtp_timeout_seconds=5.0,
            smtp_max_retries=0,
            smtp_retry_backoff_seconds=0.0,
            agent_system_prompt="Custom wiring policy.",
        )
        service = service_module.build_routing_service(settings)

        response = await service.route(RouteRequest(email=SENDER, message="Nie działa mi komputer"))

    assert response.recipient == RECIPIENT
    assert len(server.captured) == 1
    envelope_from, envelope_to, mime = server.captured[0]
    assert envelope_from == settings.smtp_from
    assert envelope_to == RECIPIENT
    assert str(mime["From"]) == settings.smtp_from
    assert str(mime["To"]) == RECIPIENT
    assert str(mime["Reply-To"]) == SENDER
    assert instructions
    assert all(instruction == "Custom wiring policy." for instruction in instructions)
