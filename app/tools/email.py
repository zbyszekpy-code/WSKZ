"""Email transport seam and the ``send_email`` agent tool.

This module defines the transport contract, the delivery failure type, and an
in-memory recording implementation used by tests. The real SMTP transport lives
in :mod:`app.tools.smtp`; ``send_email`` remains the only sender path.
"""

from dataclasses import dataclass, field
from typing import Protocol

from pydantic_ai import RunContext

from app.agent.deps import RoutingDeps
from app.agent.policy import RECIPIENTS, Recipient


@dataclass(frozen=True)
class EmailMessage:
    """A fully resolved email ready for a transport to deliver."""

    recipient: str
    subject: str
    body: str
    reply_to: str


class EmailTransport(Protocol):
    """Delivery backend for :class:`EmailMessage`."""

    async def send(self, message: EmailMessage) -> None:
        """Deliver ``message``; raise on failure."""
        ...


class EmailDeliveryError(Exception):
    """Raised when an email transport cannot deliver a message.

    Carries a user-safe ``detail`` mirroring
    :class:`app.core.errors.RoutingError`; the routing service maps it to an
    HTTP 503 instead of dropping the message silently.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass
class RecordingEmailTransport:
    """In-memory transport that records delivered messages (task 03)."""

    messages: list[EmailMessage] = field(default_factory=list)

    async def send(self, message: EmailMessage) -> None:
        """Record ``message`` without any network I/O."""
        self.messages.append(message)


async def send_email(
    ctx: RunContext[RoutingDeps],
    recipient: Recipient,
    subject: str,
    body: str,
    reply_to: str = "",
) -> str:
    """Send the routed email exactly once and record it on the request deps.

    ``reply_to`` is part of the tool contract but the server pins it to the
    request sender, so the model cannot override it. A second call in the same
    run is a no-op, which keeps delivery exactly-once even when the model emits
    several ``send_email`` calls in one response: pydantic-ai may execute those
    calls concurrently, so the check-then-send sequence runs under
    :attr:`RoutingDeps.send_lock` and the guard is re-checked while holding it.
    """
    if ctx.deps.sent is not None:
        return _already_sent(ctx.deps.sent.recipient)

    if recipient not in RECIPIENTS:
        raise ValueError(f"Unsupported recipient: {recipient}")

    normalized_subject = " ".join(subject.split())
    message = EmailMessage(
        recipient=recipient,
        subject=normalized_subject,
        body=body,
        reply_to=ctx.deps.sender_email,
    )
    async with ctx.deps.send_lock:
        if ctx.deps.sent is not None:
            return _already_sent(ctx.deps.sent.recipient)
        await ctx.deps.transport.send(message)
        ctx.deps.sent = message
    return f"Email sent to {message.recipient}."


def _already_sent(recipient: str) -> str:
    return f"Email to {recipient} was already sent; ignoring duplicate request."
