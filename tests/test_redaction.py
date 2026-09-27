"""Tests for the log-safe email redaction helper."""

from app.core.redaction import redact_emails


def test_redact_single_address() -> None:
    assert redact_emails("Escalate to jan.nowak@example.com.") == "Escalate to <email>."


def test_redact_multiple_addresses() -> None:
    text = "Cc a@b.co and john.doe+tag@sub.example.org for review."

    assert redact_emails(text) == "Cc <email> and <email> for review."


def test_redact_leaves_plain_text_unchanged() -> None:
    text = "No personal data here."

    assert redact_emails(text) == text
