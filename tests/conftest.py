from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOC_CHEAP_API_KEY", raising=False)
    monkeypatch.delenv("DOC_CHEAP_API_BASE", raising=False)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url="https://api.test", assert_all_called=False) as router:
        yield router
