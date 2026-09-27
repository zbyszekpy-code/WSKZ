"""Tests for the async SMTP transport, driven by an in-process fake server."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from email import message_from_bytes, policy
from email.message import EmailMessage as MimeMessage
from types import TracebackType

import pytest

from app.core.config import Settings
from app.tools.email import EmailDeliveryError, EmailMessage
from app.tools.smtp import SmtpEmailTransport

SMTP_FROM = "noreply@ai-router.local"
RECIPIENT = "help-desk@example.com"
SENDER = "jan.nowak@example.com"

CapturedMessage = tuple[str, str, MimeMessage]


class FakeSmtpServer:
    """Minimal SMTP server speaking just enough of the dialog for ``aiosmtplib``.

    Listens on ``127.0.0.1:0`` (an ephemeral port) and records each delivered
    message as ``(envelope_from, envelope_to, mime)``. ``fail_connections``
    makes the first N connections answer ``421`` so the client raises a connect
    error, exercising the transport's retry path. ``stall_after_greeting``
    consumes the ``MAIL FROM`` command and then stops answering, so a test can
    cancel an in-flight send and observe the client disconnect.
    """

    def __init__(self, *, fail_connections: int = 0, stall_after_greeting: bool = False) -> None:
        self.captured: list[CapturedMessage] = []
        self.connections = 0
        self.disconnects = 0
        self.stall_reached = asyncio.Event()
        self.stalled_disconnect = asyncio.Event()
        self._fail_connections = fail_connections
        self._stall_after_greeting = stall_after_greeting
        self._server: asyncio.Server | None = None
        self._port = 0

    @property
    def port(self) -> int:
        """Return the bound ephemeral port."""
        return self._port

    async def __aenter__(self) -> FakeSmtpServer:
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        sockets = self._server.sockets
        assert sockets is not None
        self._port = sockets[0].getsockname()[1]
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        try:
            if self.connections <= self._fail_connections:
                await _reply(writer, b"421 Service not available\r\n")
                return
            await self._converse(reader, writer)
        except ConnectionError, asyncio.IncompleteReadError:
            pass
        finally:
            self.disconnects += 1
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _converse(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _reply(writer, b"220 fake-smtp ESMTP\r\n")
        envelope_from = ""
        envelope_to = ""
        while True:
            line = await reader.readline()
            if not line:
                return
            command = line.decode("ascii", errors="replace").strip()
            verb = command.split(" ", 1)[0].upper()
            if verb == "EHLO":
                await _reply(writer, b"250-fake-smtp\r\n250-8BITMIME\r\n250 OK\r\n")
                if self._stall_after_greeting:
                    await reader.readline()
                    self.stall_reached.set()
                    await reader.read()
                    self.stalled_disconnect.set()
                    return
            elif verb == "HELO":
                await _reply(writer, b"250 fake-smtp\r\n")
            elif verb == "MAIL":
                envelope_from = _extract_address(command)
                await _reply(writer, b"250 OK\r\n")
            elif verb == "RCPT":
                envelope_to = _extract_address(command)
                await _reply(writer, b"250 OK\r\n")
            elif verb == "DATA":
                await _reply(writer, b"354 End data with <CR><LF>.<CR><LF>\r\n")
                data = await reader.readuntil(b"\r\n.\r\n")
                parsed = message_from_bytes(data[:-5], policy=policy.default)
                self.captured.append((envelope_from, envelope_to, parsed))
                await _reply(writer, b"250 OK\r\n")
            elif verb == "QUIT":
                await _reply(writer, b"221 Bye\r\n")
                return
            else:
                await _reply(writer, b"250 OK\r\n")


async def _reply(writer: asyncio.StreamWriter, payload: bytes) -> None:
    writer.write(payload)
    await writer.drain()


def _extract_address(command: str) -> str:
    start = command.find("<")
    end = command.find(">", start + 1)
    if start == -1 or end == -1:
        return command.split(" ", 1)[-1].strip()
    return command[start + 1 : end]


def _transport(
    server: FakeSmtpServer,
    *,
    max_retries: int = 0,
    from_address: str = SMTP_FROM,
    timeout_seconds: float = 5.0,
) -> SmtpEmailTransport:
    return SmtpEmailTransport(
        host="127.0.0.1",
        port=server.port,
        from_address=from_address,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=0.0,
    )


def _message(body: str = "A user request needs attention.") -> EmailMessage:
    return EmailMessage(
        recipient=RECIPIENT,
        subject="Forwarded request",
        body=body,
        reply_to=SENDER,
    )


def test_from_settings_maps_all_fields() -> None:
    settings = Settings(
        _env_file=None,
        smtp_host="smtp.example.com",
        smtp_port=2525,
        smtp_from="router@example.com",
        smtp_timeout_seconds=7.5,
        smtp_max_retries=4,
        smtp_retry_backoff_seconds=0.25,
    )

    transport = SmtpEmailTransport.from_settings(settings)

    assert transport.host == "smtp.example.com"
    assert transport.port == 2525
    assert transport.from_address == "router@example.com"
    assert transport.timeout_seconds == 7.5
    assert transport.max_retries == 4
    assert transport.retry_backoff_seconds == 0.25


async def test_transport_sends_mime_with_expected_headers() -> None:
    async with FakeSmtpServer() as server:
        await _transport(server).send(_message())

    assert len(server.captured) == 1
    envelope_from, envelope_to, mime = server.captured[0]
    assert envelope_from == SMTP_FROM
    assert envelope_to == RECIPIENT
    assert str(mime["From"]) == SMTP_FROM
    assert str(mime["To"]) == RECIPIENT
    assert str(mime["Reply-To"]) == SENDER
    assert str(mime["Subject"]) == "Forwarded request"
    assert mime.get_content() == "A user request needs attention."


async def test_transport_round_trips_utf8_body() -> None:
    body = "Nie działa mi komputer"

    async with FakeSmtpServer() as server:
        await _transport(server).send(_message(body=body))

    assert len(server.captured) == 1
    _, _, mime = server.captured[0]
    assert mime.get_content() == body


async def test_transport_retries_then_succeeds() -> None:
    async with FakeSmtpServer(fail_connections=1) as server:
        await _transport(server, max_retries=2).send(_message())

    assert server.connections == 2
    assert len(server.captured) == 1


async def test_transport_raises_after_exhausting_retries() -> None:
    async with FakeSmtpServer(fail_connections=999) as server:
        with pytest.raises(EmailDeliveryError):
            await _transport(server, max_retries=2).send(_message())

    assert server.connections == 3
    assert server.captured == []


async def test_transport_raises_on_closed_port() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    transport = SmtpEmailTransport(
        host="127.0.0.1",
        port=port,
        from_address=SMTP_FROM,
        timeout_seconds=1.0,
        max_retries=0,
        retry_backoff_seconds=0.0,
    )

    with pytest.raises(EmailDeliveryError):
        await transport.send(_message())


async def test_transport_cancellation_closes_connection() -> None:
    async with FakeSmtpServer(stall_after_greeting=True) as server:
        task = asyncio.create_task(_transport(server).send(_message()))
        await asyncio.wait_for(server.stall_reached.wait(), timeout=2)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        await asyncio.wait_for(server.stalled_disconnect.wait(), timeout=2)

    assert server.disconnects == 1
    assert server.captured == []
