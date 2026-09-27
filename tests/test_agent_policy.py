"""Tests for the pinned routing policy and system prompt."""

from app.agent.policy import DEFAULT_SYSTEM_PROMPT, RECIPIENTS

EXPECTED_RECIPIENTS = {
    "kadry@example.com",
    "human-resources@example.com",
    "help-desk@example.com",
    "it@example.com",
    "other@example.com",
}


def test_recipient_list_is_exactly_five() -> None:
    assert set(RECIPIENTS) == EXPECTED_RECIPIENTS
    assert len(RECIPIENTS) == 5


def test_system_prompt_mentions_every_recipient() -> None:
    for address in EXPECTED_RECIPIENTS:
        assert address in DEFAULT_SYSTEM_PROMPT


def test_system_prompt_pins_disambiguation_rules() -> None:
    normalized = " ".join(DEFAULT_SYSTEM_PROMPT.split())
    assert "leave" in normalized.lower()
    assert "kadry@example.com" in normalized
    assert "help-desk@example.com" in normalized
    assert (
        "If the message is a general greeting, small talk, an unrelated or random question, "
        "or does not clearly belong to a department, you MUST choose other@example.com."
    ) in normalized


def test_system_prompt_treats_polish_and_english_equally() -> None:
    lowered = DEFAULT_SYSTEM_PROMPT.lower()
    assert "polish" in lowered
    assert "english" in lowered
