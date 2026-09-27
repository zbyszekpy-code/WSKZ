"""Shared test fixtures."""

from collections.abc import Iterator

import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _pin_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the message limit and reset the settings cache around each test."""
    monkeypatch.setenv("MESSAGE_MAX_LENGTH", "2000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
