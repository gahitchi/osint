from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import phonenumbers
from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator, model_validator

Category = Literal["search", "social", "academic", "breach"]
FindingType = Literal[
    "profile", "article", "publication", "repo", "mention", "breach", "image", "contact"
]

SUPPORTED_PLATFORMS = (
    "github", "gitlab", "reddit", "hackernews", "mastodon",
    "dev", "keybase", "lichess", "orcid",
    "telegram", "tiktok", "youtube",
)

# Platforms we explicitly do NOT support, and the reason. These either require
# authentication / a paid key, or actively block unauthenticated scraping in a
# way that can't be reconciled with the "strictly free, public sources, no
# TOS-violation" policy.
UNAVAILABLE_PLATFORMS: dict[str, str] = {
    "instagram": (
        "Instagram blocks unauthenticated profile access and bans scraper IPs "
        "quickly. Their public JSON endpoint was removed years ago. There is "
        "no free, TOS-compliant way to pull profile data."
    ),
    "discord": (
        "Discord has no public user profile pages. Users are only reachable "
        "inside servers via an authenticated bot. There is nothing to scrape "
        "without joining a specific server."
    ),
    "facebook": (
        "Facebook requires login for almost all profile content and "
        "aggressively blocks scraping."
    ),
    "x": (
        "X (Twitter) requires authentication and a paid Developer plan for "
        "any meaningful API access since 2023."
    ),
    "twitter": (
        "Twitter requires authentication and a paid Developer plan for any "
        "meaningful API access since 2023."
    ),
    "linkedin": (
        "LinkedIn requires login for profile content and actively pursues "
        "scrapers legally. Not supported."
    ),
    "snapchat": (
        "Snapchat has no public profile API or web profile pages."
    ),
    "whatsapp": (
        "WhatsApp has no public user directory. Numbers are private by "
        "default."
    ),
    "tinder": (
        "Tinder has no public user directory."
    ),
}


class Query(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    username: str | None = None
    phone: str | None = None
    location: str | None = None
    employer: str | None = None
    source_platform: str | None = None  # e.g. "github" — anchor the pivot crawler

    @field_validator("name", "username", "location", "employer", "source_platform", mode="before")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("source_platform", mode="after")
    @classmethod
    def _normalize_platform(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.lower()
        aliases = {
            "hn": "hackernews", "dev.to": "dev", "gh": "github",
            "tg": "telegram", "yt": "youtube", "tk": "tiktok",
            "ig": "instagram", "fb": "facebook", "li": "linkedin",
        }
        s = aliases.get(s, s)
        if s in UNAVAILABLE_PLATFORMS:
            raise ValueError(
                f"source_platform '{s}' is not supported: "
                f"{UNAVAILABLE_PLATFORMS[s]}"
            )
        return s

    @field_validator("phone", mode="before")
    @classmethod
    def _normalize_phone(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = str(v).strip()
        if not v:
            return None
        try:
            parsed = phonenumbers.parse(v, None)
        except phonenumbers.NumberParseException:
            return v
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        return v

    @model_validator(mode="after")
    def _at_least_one(self) -> Query:
        fields = (self.name, self.email, self.username, self.phone, self.location, self.employer)
        if not any(fields):
            raise ValueError("At least one query field must be provided.")
        return self

    def filled_fields(self) -> list[str]:
        return [
            k for k, v in self.model_dump().items() if v is not None
        ]


SignalKey = Literal[
    "username", "email", "orcid", "github_login", "gravatar_hash",
    "openalex_id", "institution", "doi_author_pair", "wikidata_qid", "name",
]


class Finding(BaseModel):
    module: str
    category: Category
    type: FindingType
    title: str
    source_url: HttpUrl
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    matched_fields: list[str] = Field(default_factory=list)
    signals: dict[str, list[str]] = Field(default_factory=dict)

    def dedupe_key(self) -> tuple[str, str]:
        return (self.module, str(self.source_url))


class Person(BaseModel):
    id: str
    display_name: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    signals: dict[str, list[str]] = Field(default_factory=dict)
    finding_keys: list[tuple[str, str]] = Field(default_factory=list)
    coherence: float = Field(default=1.0, ge=0.0, le=1.0)
    incoherent_finding_keys: list[tuple[str, str]] = Field(default_factory=list)
    summary: str = ""  # deterministic one-line interpretation; filled by interpret.py


# ---- v2: expansion taxonomy + context assessment ----

RiskLevel = Literal["low", "medium", "high"]


class Expansion(BaseModel):
    id: str
    label: str
    risk: RiskLevel
    description: str
    modules: list[str]


class ContextAssessment(BaseModel):
    score: int
    thin: bool
    auto_run: list[Expansion]
    proposed: list[Expansion]


# ---- v2: coherence report ----

CoherenceRule = Literal["name_mismatch", "geo_outlier", "century_gap", "domain_outlier"]


class CoherenceFlag(BaseModel):
    finding_key: tuple[str, str]
    rule: CoherenceRule
    reason: str


class CoherenceReport(BaseModel):
    person_id: str
    score: float = Field(ge=0.0, le=1.0)
    flags: list[CoherenceFlag] = Field(default_factory=list)


# ---- v4: genealogy ----

# Relations are coded relative to the focal person.
# Negative generation = ancestor; positive = descendant; 0 = focal level.
TreeRelation = Literal[
    "focal", "father", "mother", "parent",
    "grandfather", "grandmother", "grandparent",
    "great-grandparent",
    "child", "grandchild",
    "sibling", "spouse",
]


class TreeNode(BaseModel):
    qid: str  # Wikidata Q-id
    name: str
    birth: str | None = None  # ISO year string, or None
    death: str | None = None
    relation: TreeRelation
    generation: int  # 0=focal, -1=parents, -2=grandparents, +1=children
    wikipedia_url: str | None = None
    image_url: str | None = None


class FamilyTree(BaseModel):
    focal_qid: str
    focal_label: str
    focal_description: str | None = None
    nodes: list[TreeNode]
    edges: list[tuple[str, str]] = Field(default_factory=list)
    # edge = (parent_qid, child_qid) — used to draw connector lines


class ModuleStatus(BaseModel):
    module: str
    category: Category
    state: Literal["pending", "running", "ok", "skipped", "error"]
    detail: str | None = None
