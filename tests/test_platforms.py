"""Validation tests for the source_platform field."""

import pytest
from pydantic import ValidationError

from osint_tool.schema import SUPPORTED_PLATFORMS, UNAVAILABLE_PLATFORMS, Query


def test_supported_platform_accepted():
    q = Query(username="someone", source_platform="telegram")
    assert q.source_platform == "telegram"


def test_alias_normalized():
    q = Query(username="someone", source_platform="TG")
    assert q.source_platform == "telegram"


@pytest.mark.parametrize("plat", list(UNAVAILABLE_PLATFORMS))
def test_unavailable_platform_rejected_with_reason(plat):
    with pytest.raises(ValidationError) as ei:
        Query(username="someone", source_platform=plat)
    assert plat in str(ei.value)
    # the rejection message should include *why*, not just "invalid"
    assert "not supported" in str(ei.value).lower()


def test_new_supported_platforms_listed():
    for p in ("telegram", "tiktok", "youtube"):
        assert p in SUPPORTED_PLATFORMS
