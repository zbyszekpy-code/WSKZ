"""Per-request dependencies for the routing agent."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.tools.email import EmailMessage, EmailTransport


@dataclass
class RoutingDeps:
    """Dependencies handed to the agent for a single routing request.

    One instance is created per request, so :attr:`sent` records exactly what
    the agent emailed during that request and is the source of truth for the
    response. :attr:`send_lock` serializes concurrent ``send_email`` calls in
    the same request so the exactly-once guard is race-free; because deps are
    per request, the lock never serializes separate requests. Type-only imports
    keep this module free of a runtime cycle with the email tool.
    """

    sender_email: str
    transport: EmailTransport
    sent: EmailMessage | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
