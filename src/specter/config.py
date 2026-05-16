from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    user_agent: str
    contact_email: str | None
    host_rps: float
    max_concurrency: int
    reports_dir: Path
    hibp_api_key: str | None
    request_timeout: float = 15.0


def load_config() -> Config:
    contact = os.getenv("SPECTER_CONTACT_EMAIL") or None
    suffix = f"; +{contact}" if contact else ""
    return Config(
        user_agent=f"specter/0.1 (research{suffix})",
        contact_email=contact,
        host_rps=float(os.getenv("SPECTER_HOST_RPS", "1.0")),
        max_concurrency=int(os.getenv("SPECTER_MAX_CONCURRENCY", "20")),
        reports_dir=Path(os.getenv("SPECTER_REPORTS_DIR", "./reports")).resolve(),
        hibp_api_key=os.getenv("HIBP_API_KEY") or None,
    )
