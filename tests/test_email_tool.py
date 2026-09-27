"""Tests for the email message, recording transport, and send_email tool."""

from typing import cast

import pytest
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from app.agent.deps import RoutingDeps
from app.agent.policy import Recipient
from app.tools.email import EmailMessage, RecordingEmailTransport, send_email


def _context(deps: RoutingDeps) -> RunContext[RoutingDeps]:
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


def _deps(transport: RecordingEmailTransport) -> RoutingDeps:
    return RoutingDeps(sender_email="jan@example.com", transport=transport)


async def test_email_message_roundtrip() -> None:
    transport = RecordingEmailTransport()
    message = EmailMessage(
        recipient="help-desk@example.com",
        subject="Laptop",
        body="Laptop does not start.",
        reply_to="jan@example.com",
    )

    await transport.send(message)

    assert transport.messages == [message]


async def test_send_email_records_and_pins_reply_to() -> None:
    transport = RecordingEmailTransport()
    deps = _deps(transport)

    confirmation = await send_email(
        _context(deps),
        Recipient.HELP_DESK,
        "Laptop",
        "Laptop does not start.",
        "attacker@example.com",
    )

    assert "help-desk@example.com" in confirmation
    assert deps.sent is not None
    assert deps.sent.reply_to == "jan@example.com"
    assert transport.messages == [deps.sent]


async def test_send_email_normalizes_subject_whitespace() -> None:
    transport = RecordingEmailTransport()
    deps = _deps(transport)

    await send_email(
        _context(deps),
        Recipient.HELP_DESK,
        "  Laptop\r\nfails\tto start  ",
        "Laptop does not start.",
        "",
    )

    assert deps.sent is not None
    assert deps.sent.subject == "Laptop fails to start"
    assert transport.messages == [deps.sent]


async def test_send_email_ignores_second_call() -> None:
    transport = RecordingEmailTransport()
    deps = _deps(transport)
    context = _context(deps)

    await send_email(context, Recipient.IT, "VPN", "VPN is down.", "")
    await send_email(context, Recipient.KADRY, "Leave", "Leave request.", "")

    assert len(transport.messages) == 1
    assert deps.sent is not None
    assert deps.sent.recipient == "it@example.com"


async def test_send_email_rejects_unknown_recipient() -> None:
    deps = _deps(RecordingEmailTransport())

    with pytest.raises(ValueError, match="Unsupported recipient"):
        await send_email(
            _context(deps),
            cast(Recipient, "unknown@example.com"),
            "Subject",
            "Body",
        )

    assert deps.sent is None
