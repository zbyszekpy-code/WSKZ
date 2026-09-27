"""HTTP-agnostic routing contract shared by the API and agent layers."""

from typing import Protocol

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.config import get_settings


class RouteRequest(BaseModel):
    """Incoming routing request.

    FastAPI's default ``RequestValidationError`` handler produces ``422`` for
    validation failures, so no custom validation handler is registered.
    """

    email: EmailStr
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        """Strip the message and enforce non-blank input and the length limit."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        limit = get_settings().message_max_length
        if len(stripped) > limit:
            raise ValueError(f"message must be at most {limit} characters long")
        return stripped


class RouteResponse(BaseModel):
    """Routing result returned to the caller."""

    recipient: EmailStr
    reason: str


class RoutingService(Protocol):
    """Routes a validated request to a department recipient.

    The neutral seam between the HTTP layer and the agent. Implementations must
    raise :class:`app.core.errors.RoutingError` on downstream failure.
    """

    async def route(self, request: RouteRequest) -> RouteResponse:
        """Return the routing result for ``request``."""
        ...
