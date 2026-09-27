"""SMTP transport that delivers the agent's mail to Mailpit.

Provider-specific code stays isolated here. The transport delivers with
``aiosmtplib`` so an in-flight send is cancellable, and it retries transient
failures with ``tenacity`` before raising :class:`EmailDeliveryError`.
"""

import logging
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage

import aiosmtplib
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.tools.email import EmailDeliveryError, EmailMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmtpEmailTransport:
    """Deliver :class:`EmailMessage` over plain SMTP with bounded retries.

    Mailpit is plaintext in the PoC, so there is no STARTTLS or AUTH. Delivery
    is fully async: ``send`` builds the MIME message once and retries the
    delivery with exponential backoff, raising :class:`EmailDeliveryError` once
    the budget is exhausted. A cancelled send closes its socket synchronously
    and propagates :class:`asyncio.CancelledError` without retrying.
    """

    host: str
    port: int
    from_address: str
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> SmtpEmailTransport:
        """Build a transport from the ``smtp_*`` application settings."""
        return cls(
            host=settings.smtp_host,
            port=settings.smtp_port,
            from_address=settings.smtp_from,
            timeout_seconds=settings.smtp_timeout_seconds,
            max_retries=settings.smtp_max_retries,
            retry_backoff_seconds=settings.smtp_retry_backoff_seconds,
        )

    async def send(self, message: EmailMessage) -> None:
        """Deliver ``message`` or raise :class:`EmailDeliveryError`."""
        mime = self._build_mime(message)
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.max_retries + 1),
                wait=wait_exponential(multiplier=self.retry_backoff_seconds),
                retry=retry_if_exception_type((aiosmtplib.SMTPException, OSError)),
                reraise=True,
            ):
                with attempt:
                    await self._deliver_once(mime)
        except (aiosmtplib.SMTPException, OSError) as exc:
            raise EmailDeliveryError(
                "The email could not be delivered; please try again later."
            ) from exc

    async def _deliver_once(self, message: MimeMessage) -> None:
        """Open one connection, send ``message``, and always close the socket."""
        smtp = aiosmtplib.SMTP(hostname=self.host, port=self.port, timeout=self.timeout_seconds)
        await smtp.connect()
        try:
            await smtp.send_message(message)
        finally:
            smtp.close()

    def _build_mime(self, message: EmailMessage) -> MimeMessage:
        """Build the stdlib MIME message for ``message``.

        ``From`` is the service address; ``Reply-To`` is trusted from the
        contract, where ``send_email`` pinned it to the request sender.
        """
        mime = MimeMessage()
        mime["From"] = self.from_address
        mime["To"] = message.recipient
        mime["Subject"] = message.subject
        mime["Reply-To"] = message.reply_to
        mime.set_content(message.body)
        return mime
