"""Helpers that keep sensitive tokens out of log output."""

import re

_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def redact_emails(text: str) -> str:
    """Replace email-like tokens in ``text`` with ``<email>``.

    Used only for the logged routing reason; the API response keeps the
    unredacted, length-bounded reason.
    """
    return _EMAIL_PATTERN.sub("<email>", text)
