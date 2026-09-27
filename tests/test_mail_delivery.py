"""End-to-end mail delivery tests against the in-process fake SMTP server."""

from __future__ import annotations

import socket
from typing import Any

from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent.agent import build_agent
from app.agent.service import AgentRoutingService
from app.core.config import Settings
from app.core.routing import RouteRequest
from app.main import create_app
from app.tools.smtp import SmtpEmailTransport
from test_smtp_transport import FakeSmtpServer

SENDER = "jan.nowak@example.com"
RECIPIENT = "help-desk@example.com"
SUBJECT = "Forwarded request"
BODY = "A user request needs attention."
PAYLOAD = {"email": SENDER, "message": "Nie działa mi komputer"}


def _has_send_return(messages: list[ModelMessage]) -> bool:
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "send_email":
                return True
    return False


def _send_email_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    if _has_send_return(messages):
        return ModelResponse(parts=[TextPart("Routed to help-desk.")])
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


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "agent_no_send_max_attempts": 1,
        "agent_timeout_seconds": 60.0,
        "smtp_max_retries": 0,
        "smtp_timeout_seconds": 1.0,
        "smtp_retry_backoff_seconds": 0.0,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _transport(settings: Settings) -> SmtpEmailTransport:
    return SmtpEmailTransport.from_settings(settings)


async def test_route_delivers_exactly_one_message_to_fake_smtp() -> None:
    async with FakeSmtpServer() as server:
        settings = _settings(smtp_host="127.0.0.1", smtp_port=server.port)
        service = AgentRoutingService(
            settings=settings,
            agent_factory=lambda: build_agent(FunctionModel(_send_email_model), settings),
            transport_factory=lambda: _transport(settings),
        )

        response = await service.route(RouteRequest(**PAYLOAD))

        assert response.recipient == RECIPIENT
        assert len(server.captured) == 1

    envelope_from, envelope_to, mime = server.captured[0]
    assert envelope_from == settings.smtp_from
    assert envelope_to == RECIPIENT
    assert str(mime["To"]) == RECIPIENT
    assert str(mime["Reply-To"]) == SENDER
    assert str(mime["Subject"]) == SUBJECT
    assert mime.get_content() == BODY


def test_smtp_failure_returns_503() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    settings = _settings(smtp_host="127.0.0.1", smtp_port=port)
    service = AgentRoutingService(
        settings=settings,
        agent_factory=lambda: build_agent(FunctionModel(_send_email_model), settings),
        transport_factory=lambda: _transport(settings),
    )
    client = TestClient(create_app(service))

    response = client.post("/api/v1/route", json=PAYLOAD)

    assert response.status_code == 503
    assert response.json()["detail"] == "The email could not be delivered; please try again later."
