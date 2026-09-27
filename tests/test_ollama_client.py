"""Tests for the retrying Ollama HTTP client (Change A)."""

import httpx2
import pytest
from pydantic_ai.retries import AsyncHTTPX2TenacityTransport
from tenacity import stop_after_attempt

from app.agent import agent as agent_module
from app.agent.agent import build_ollama_http_client, build_ollama_model
from app.core.config import Settings


class _StubTransport(httpx2.AsyncBaseTransport):
    """Return queued responses or raise queued exceptions, counting calls."""

    def __init__(self, responses: list[httpx2.Response | Exception]) -> None:
        self._responses = responses
        self.calls = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        item.request = request
        return item


def _response(status: int) -> httpx2.Response:
    return httpx2.Response(status_code=status, content=b"{}")


def _retrying_settings(*, max_retries: int = 1) -> Settings:
    return Settings(
        _env_file=None,
        ollama_max_retries=max_retries,
        ollama_retry_backoff_seconds=0.0,
    )


async def _client_with_stub(stub: _StubTransport, settings: Settings) -> httpx2.AsyncClient:
    client = build_ollama_http_client(settings)
    transport = client._transport
    assert isinstance(transport, AsyncHTTPX2TenacityTransport)
    transport.wrapped = stub
    return client


async def test_build_ollama_http_client_uses_tenacity_transport() -> None:
    settings = _retrying_settings(max_retries=4)
    client = build_ollama_http_client(settings)
    try:
        transport = client._transport
        assert isinstance(transport, AsyncHTTPX2TenacityTransport)
        assert transport.config["reraise"] is True
        stop = transport.config["stop"]
        assert isinstance(stop, stop_after_attempt)
        assert stop.max_attempt_number == settings.ollama_max_retries + 1
    finally:
        await client.aclose()


async def test_build_ollama_model_wires_retrying_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[httpx2.AsyncClient] = []
    real_builder = agent_module.build_ollama_http_client

    def spy(settings: Settings) -> httpx2.AsyncClient:
        client = real_builder(settings)
        captured.append(client)
        return client

    monkeypatch.setattr(agent_module, "build_ollama_http_client", spy)

    model = build_ollama_model(Settings(_env_file=None))
    provider = model.provider
    try:
        assert provider is not None
        assert len(captured) == 1
        assert isinstance(captured[0]._transport, AsyncHTTPX2TenacityTransport)
        assert provider.client._client is captured[0]
    finally:
        await captured[0].aclose()


async def test_retry_transport_retries_retryable_status() -> None:
    stub = _StubTransport([_response(503), _response(200)])
    client = await _client_with_stub(stub, _retrying_settings())
    try:
        response = await client.get("http://ollama.test/v1/models")
    finally:
        await client.aclose()

    assert stub.calls == 2
    assert response.status_code == 200


async def test_retry_transport_retries_transport_error() -> None:
    stub = _StubTransport([httpx2.ConnectError("boom"), _response(200)])
    client = await _client_with_stub(stub, _retrying_settings())
    try:
        response = await client.get("http://ollama.test/v1/models")
    finally:
        await client.aclose()

    assert stub.calls == 2
    assert response.status_code == 200


async def test_retry_transport_does_not_retry_non_retryable_status() -> None:
    stub = _StubTransport([_response(400)])
    client = await _client_with_stub(stub, _retrying_settings())
    try:
        response = await client.get("http://ollama.test/v1/models")
    finally:
        await client.aclose()

    assert stub.calls == 1
    assert response.status_code == 400


async def test_retrying_client_is_closed() -> None:
    client = build_ollama_http_client(_retrying_settings())

    await client.aclose()

    assert client.is_closed
