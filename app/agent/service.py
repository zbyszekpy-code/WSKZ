"""Agent-backed implementation of the routing service."""

import asyncio
import logging
from collections.abc import Callable

from pydantic_ai import Agent
from pydantic_ai.exceptions import AgentRunError, ModelAPIError

from app.agent.agent import build_agent, build_ollama_model
from app.agent.deps import RoutingDeps
from app.core.config import Settings, get_settings
from app.core.errors import RoutingError
from app.core.redaction import redact_emails
from app.core.routing import RouteRequest, RouteResponse
from app.tools.email import EmailDeliveryError, EmailTransport, RecordingEmailTransport
from app.tools.smtp import SmtpEmailTransport

logger = logging.getLogger(__name__)

AgentFactory = Callable[[], Agent[RoutingDeps, str]]
TransportFactory = Callable[[], EmailTransport]


class AgentRoutingService:
    """Route requests through a pydantic-ai agent and capture the send.

    The agent is built lazily on first use, so constructing the application and
    importing this module do no network setup. Each request gets its own
    :class:`RoutingDeps`, which is how the chosen recipient is read back from
    the actual ``send_email`` tool call.
    """

    def __init__(
        self,
        settings: Settings,
        agent_factory: AgentFactory,
        transport_factory: TransportFactory = RecordingEmailTransport,
    ) -> None:
        self._settings = settings
        self._agent_factory = agent_factory
        self._transport_factory = transport_factory
        self._agent: Agent[RoutingDeps, str] | None = None

    async def route(self, request: RouteRequest) -> RouteResponse:
        """Run the agent and return the recipient it actually emailed."""
        deps = RoutingDeps(sender_email=str(request.email), transport=self._transport_factory())
        try:
            async with asyncio.timeout(self._settings.agent_timeout_seconds):
                output = await self._run(request.message, deps)
        except TimeoutError as exc:
            raise RoutingError("The routing agent timed out; please try again later.") from exc
        except EmailDeliveryError as exc:
            raise RoutingError("The email could not be delivered; please try again later.") from exc
        except AgentRunError as exc:
            raise RoutingError(
                "The routing service is temporarily unavailable; please try again later."
            ) from exc

        if deps.sent is None:
            raise RoutingError("The routing agent did not send an email.")

        recipient = deps.sent.recipient
        reason = self._build_reason(output, recipient)
        logger.info("routing decision recipient=%s reason=%s", recipient, redact_emails(reason))
        return RouteResponse(recipient=recipient, reason=reason)

    def _agent_instance(self) -> Agent[RoutingDeps, str]:
        if self._agent is None:
            self._agent = self._agent_factory()
        return self._agent

    async def _run(self, message: str, deps: RoutingDeps) -> str | None:
        """Run the agent with a bounded no-send retry, returning its final text.

        Transient Ollama HTTP failures are retried by the model transport, so a
        surviving ``ModelAPIError`` is not retried here. A run that finishes
        without calling ``send_email`` is retried because nothing was sent, so
        there is no duplicate risk. Once ``deps.sent`` is set the run is never
        retried: if a later model request in the same run raises
        ``ModelAPIError`` after a successful send, the recorded send is returned
        instead of being retried or surfaced as an error.
        """
        attempts = self._settings.agent_no_send_max_attempts
        for attempt in range(attempts):
            try:
                result = await self._agent_instance().run(message, deps=deps)
            except ModelAPIError:
                if deps.sent is not None:
                    logger.warning(
                        "model API error after a send on attempt %d/%d; not retrying",
                        attempt + 1,
                        attempts,
                    )
                    return None
                logger.warning(
                    "model API error on attempt %d/%d; not retrying", attempt + 1, attempts
                )
                raise
            else:
                if deps.sent is not None:
                    return result.output
                logger.warning(
                    "agent produced no send_email call on attempt %d/%d",
                    attempt + 1,
                    attempts,
                )
                if attempt + 1 >= attempts:
                    return None
            await asyncio.sleep(self._settings.agent_retry_backoff_seconds * (2**attempt))
        return None

    def _build_reason(self, output: str | None, recipient: str) -> str:
        reason = (output or "").strip()[: self._settings.agent_reason_max_length]
        return reason or f"Routed to {recipient}."


def build_routing_service(settings: Settings | None = None) -> AgentRoutingService:
    """Build the production routing service.

    Each request gets a fresh, stateless SMTP transport built from the
    ``smtp_*`` settings; the agent, tool, and deps stay untouched.
    """
    resolved = settings or get_settings()
    return AgentRoutingService(
        settings=resolved,
        agent_factory=lambda: build_agent(build_ollama_model(resolved), resolved),
        transport_factory=lambda: SmtpEmailTransport.from_settings(resolved),
    )
