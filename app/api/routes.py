"""HTTP routes for the routing API."""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends

from app.agent.service import build_routing_service
from app.core.routing import RouteRequest, RouteResponse, RoutingService

router = APIRouter()


@lru_cache
def get_routing_service() -> RoutingService:
    """Return the process-wide routing service (built once, lazily)."""
    return build_routing_service()


@router.post("/route", response_model=RouteResponse)
async def route(
    payload: RouteRequest,
    service: Annotated[RoutingService, Depends(get_routing_service)],
) -> RouteResponse:
    """Route a validated request and return the chosen recipient."""
    return await service.route(payload)
