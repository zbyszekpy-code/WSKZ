"""Tests for the API docs and OpenAPI surfaces."""

from fastapi.testclient import TestClient

from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_docs_endpoint_serves_swagger_ui() -> None:
    response = _client().get("/api/v1/docs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "swagger-ui" in response.text


def test_openapi_schema_exposes_route_path() -> None:
    response = _client().get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/route" in response.json()["paths"]


def test_default_docs_path_is_not_served() -> None:
    response = _client().get("/docs")

    assert response.status_code == 404
