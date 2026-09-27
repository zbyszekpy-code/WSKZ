"""Tests for the POST /api/v1/route contract."""

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import get_settings
from app.core.errors import RoutingError
from app.core.routing import RouteRequest, RouteResponse, RoutingService
from app.main import create_app

VALID_PAYLOAD = {"email": "jan.nowak@example.com", "message": "Nie działa mi komputer"}


class FakeRoutingService:
    """Deterministic routing service returning a fixed recipient."""

    async def route(self, request: RouteRequest) -> RouteResponse:
        """Return a fixed routing result without touching Ollama."""
        return RouteResponse(
            recipient="help-desk@example.com",
            reason=f"Routed request from {request.email}.",
        )


class FailingRoutingService:
    """Routing service that always fails, used to exercise 503 mapping."""

    async def route(self, request: RouteRequest) -> RouteResponse:
        """Raise a downstream routing error."""
        raise RoutingError("ollama unavailable")


def _client() -> TestClient:
    return TestClient(create_app(FakeRoutingService()))


def test_route_valid_request_returns_200() -> None:
    response = _client().post("/api/v1/route", json=VALID_PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"recipient", "reason"}
    assert body["recipient"] == "help-desk@example.com"
    assert body["reason"]


def test_route_invalid_email_returns_422() -> None:
    response = _client().post("/api/v1/route", json={"email": "not-an-email", "message": "hello"})

    assert response.status_code == 422


def test_route_empty_message_returns_422() -> None:
    response = _client().post("/api/v1/route", json={"email": "a@example.com", "message": ""})

    assert response.status_code == 422


def test_route_whitespace_message_returns_422() -> None:
    response = _client().post("/api/v1/route", json={"email": "a@example.com", "message": "   "})

    assert response.status_code == 422


def test_route_request_strips_message() -> None:
    request = RouteRequest(email="a@example.com", message="  hi  ")

    assert request.message == "hi"


def test_route_message_over_limit_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGE_MAX_LENGTH", "10")
    get_settings.cache_clear()

    response = _client().post("/api/v1/route", json={"email": "a@example.com", "message": "a" * 11})

    assert response.status_code == 422


def test_route_message_at_limit_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGE_MAX_LENGTH", "10")
    get_settings.cache_clear()

    response = _client().post("/api/v1/route", json={"email": "a@example.com", "message": "a" * 10})

    assert response.status_code == 200


def test_route_missing_fields_returns_422() -> None:
    client = _client()

    assert client.post("/api/v1/route", json={}).status_code == 422
    assert client.post("/api/v1/route", json={"email": "a@example.com"}).status_code == 422


def test_route_downstream_failure_returns_503() -> None:
    client = TestClient(create_app(FailingRoutingService()))

    response = client.post("/api/v1/route", json=VALID_PAYLOAD)

    assert response.status_code == 503
    assert response.json() == {"detail": "ollama unavailable"}


def test_get_routing_service_is_cached_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = FakeRoutingService()
    calls = 0

    def fake_build() -> RoutingService:
        nonlocal calls
        calls += 1
        return sentinel

    monkeypatch.setattr(routes, "build_routing_service", fake_build)
    try:
        routes.get_routing_service.cache_clear()
        first = routes.get_routing_service()
        second = routes.get_routing_service()
    finally:
        routes.get_routing_service.cache_clear()

    assert first is sentinel
    assert second is sentinel
    assert calls == 1


def test_create_app_registers_dependency_override() -> None:
    fake = FakeRoutingService()

    app = create_app(fake)

    assert routes.get_routing_service in app.dependency_overrides
    assert app.dependency_overrides[routes.get_routing_service]() is fake


def test_create_app_without_service_does_not_override() -> None:
    app = create_app()

    assert routes.get_routing_service not in app.dependency_overrides


def test_app_overrides_do_not_leak_between_apps() -> None:
    first = FakeRoutingService()
    second = FakeRoutingService()

    app_one = create_app(first)
    app_two = create_app(second)

    assert app_one.dependency_overrides[routes.get_routing_service]() is first
    assert app_two.dependency_overrides[routes.get_routing_service]() is second
