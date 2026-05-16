import httpx
import pytest
import respx

from specter.config import Config
from specter.http import HttpClient, RobotsDenied


def _cfg(tmp_path):
    return Config(
        user_agent="specter/0.1 (research)",
        contact_email=None,
        host_rps=100.0,  # high rps so the limiter doesn't slow tests
        max_concurrency=20,
        reports_dir=tmp_path,
        hibp_api_key=None,
    )


@pytest.mark.asyncio
@respx.mock
async def test_robots_blocks(tmp_path):
    cfg = _cfg(tmp_path)
    respx.get("https://blocked.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private/")
    )
    respx.get("https://blocked.test/private/x").mock(
        return_value=httpx.Response(200, text="should not be reached")
    )
    client = HttpClient(cfg)
    try:
        with pytest.raises(RobotsDenied):
            await client.get("https://blocked.test/private/x")
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_robots_allows(tmp_path):
    cfg = _cfg(tmp_path)
    respx.get("https://open.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /")
    )
    route = respx.get("https://open.test/page").mock(
        return_value=httpx.Response(200, text="ok")
    )
    client = HttpClient(cfg)
    try:
        r = await client.get("https://open.test/page")
        assert r.status_code == 200
        assert route.called
    finally:
        await client.aclose()
