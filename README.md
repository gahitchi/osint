# Specter

**A local-first OSINT research console for people-centric investigations.**

Specter takes a single identifier — full name, email, username (optionally
scoped to a platform), phone, location, or employer — and walks a guided
workflow over free public sources to surface, cluster, and coherence-check
findings into *people*.

Specter is built for lawful research, journalism, and authorized security
work. It refuses to scrape behind logins, honours `robots.txt`, rate-limits
per host, and ships an identifying User-Agent. No paid keys are required.

---

## Quickstart (Arch Linux)

```bash
sudo pacman -S --needed python git uv
git clone https://github.com/gahitchi/osint ~/Projects/specter
cd ~/Projects/specter
uv sync --extra dev
uv run uvicorn specter.main:app --reload
# → open http://127.0.0.1:8000
```

`uv` is in `extra/`. Python 3.11+ from the `python` package works. To run
without reload:

```bash
uv run specter
```

To wipe all locally persisted reports:

```bash
curl -sX POST http://127.0.0.1:8000/reports/purge-all
```

---

## How a search flows

1. You fill some fields and hit **Assess context**.
2. The tool POSTs `/search/preview` (no scraping yet) and shows the plan:
   - "Will auto-run" rows are locked checkboxes — they always run.
   - "You can also approve" rows have unchecked boxes with risk pills
     (low / medium / high).
3. Hit **Run with selected scopes**. The tool POSTs `/search`, then streams
   findings, person updates, the coherence pass, and any follow-up leads
   over Server-Sent Events.
4. People cards render at the top with tags and contact details. Findings
   the coherence pass flagged as contradicting their cluster move to
   **Needs review** at the bottom (nothing is silently dropped).
5. `Download JSON` / `Download CSV` give you the full report including the
   per-cluster `CoherenceReport` with reasons for every flag.

---

## Methodology

Specter is deterministic — no LLMs, no opaque scoring. Every decision the
tool makes is traceable to a rule you can read.

### Confidence

Each Finding ships with a `confidence` ∈ [0.0, 1.0] derived from
`matched_fields`: which of the query fields the source independently
corroborated. A finding that matches *name + email + employer* scores
higher than one that matches *name* alone. Confidence is multiplied
down when the match is partial (e.g. surname matched, given name
missing).

### Clustering (entity resolution)

`cluster.py` runs union-find over **strong identity signals** —
ORCID, GitHub login, email, gravatar hash, ORCID-linked publications.
Two findings sharing a strong signal merge into one Person. Findings
that only share a weak signal (name only) do *not* merge. This keeps
two real Janes from being collapsed because they share a name.

### Name matching

`names.py` implements word-boundary matching for the query name plus
its variants:

- ASCII fold (Müller → muller) via `anyascii` (ISC license).
- Nickname expansion (Robert ↔ Bob, Elizabeth ↔ Liz, …) from a
  curated public-domain table.
- Locale-aware family-first swap for East-Asian and Hungarian names.
- Jaro-Winkler fuzzy fallback (≥ 0.92) via `rapidfuzz` (MIT), skipped
  for tokens shorter than 4 characters to avoid `Li ↔ Lo` noise.

`filter.py` requires the matched tokens to land within an 80-character
**positional cluster** in the source text. This blocks the classic
substring trap where "Jane Doe" matches a page mentioning Jane Smith
in one paragraph and John Doe in another.

### Coherence

After all modules finish, each cluster is run through `cohere.py`,
which applies four rules:

| Rule | Flags a Finding when… |
|---|---|
| `name_mismatch` | The Finding's name signals disagree with the cluster's canonical name beyond fuzzy tolerance. |
| `geo_outlier` | The Finding's geographic signals are inconsistent with the cluster's modal location. |
| `century_gap` | DOB/DOD signals place the Finding more than 80 years from the cluster's modal era. |
| `domain_outlier` | The Finding's professional domain disagrees with the cluster's modal domain (academic ≠ entertainment, etc.). |

Flagged findings are not dropped — they appear in a collapsible
**Needs review** panel so the analyst can override.

---

## Expansions

| ID | Modules | Risk | Auto? |
|---|---|---|---|
| `targeted` | pivot_crawler, pgp_keys, rdap_domain, gravatar, hibp_breach | low | when `source_platform+username` or `email` given |
| `academic` | orcid, crossref, openalex | low | when `name` given |
| `archive` | wayback | low | when `username` or `email` given |
| `genealogy` | wikidata_tree | low | when `name` given |
| `code_hosts` | github_user, npm_user | medium | auto if `username` given; opt-in for name-only |
| `web_search` | search_ddg | medium | opt-in |
| `news` | news_gdelt | medium | opt-in |
| `forums` | stack_exchange | medium | opt-in |
| `public_records` | sec_edgar | medium | opt-in |
| `username_fanout` | sherlock | **high** | opt-in; *skipped entirely if `source_platform` is set* |

## Supported source platforms (pivot crawler)

`github`, `gitlab`, `reddit`, `hackernews`, `mastodon`, `dev`, `keybase`,
`lichess`, `orcid`, `telegram`, `tiktok`, `youtube`. Each fetcher reads
the platform's free public API or OpenGraph metadata, extracts contact
info (including obfuscated emails like `foo (at) bar dot com`, phone
numbers parsed via `phonenumbers`), and follows up to five user-listed
outbound URLs.

Some platforms are **deliberately refused**: Instagram, Discord,
Facebook, X/Twitter, LinkedIn, Snapchat, WhatsApp, Tinder. See
`schema.UNAVAILABLE_PLATFORMS` for the per-platform reason (usually:
auth-walled, ToS-restricted, or no free public surface).

---

## Legal, ethical, and GDPR considerations

Specter is **a tool, not a license**. You are responsible for ensuring
your use complies with the laws of your jurisdiction and the
jurisdiction of the data subject. Some specific considerations:

### What Specter does *not* do

- It does not bypass authentication, paywalls, CAPTCHAs, or any
  technical access control.
- It does not scrape platforms that prohibit automated access in their
  Terms of Service when no public, robots-compliant surface exists.
- It does not store raw HTML, page snapshots, or anything beyond the
  structured Finding objects you see in the report.
- It does not retain reports remotely — they are written to
  `./reports/` on the machine running Specter, and `DELETE
  /reports/{job_id}` / `POST /reports/purge-all` give you data-subject
  erasure on demand.

### GDPR considerations

If you research a person whose data is protected under the GDPR
(EU/EEA residents, or in some readings: any data collected within the
EU), you must have a **lawful basis** for processing under Article 6
— typically *legitimate interest* (Art. 6(1)(f)) balanced against the
data subject's rights, or *consent* (Art. 6(1)(a)).

Specter helps you discharge the *technical* side of GDPR compliance:

- **Right to erasure (Art. 17)** — `DELETE /reports/{job_id}` removes
  a subject's report immediately and irrevocably.
- **Data minimisation (Art. 5(1)(c))** — only fields you explicitly
  query for, or that surface as strong identity signals during
  clustering, are recorded.
- **Provenance / accuracy (Art. 5(1)(d))** — every Finding records
  `source_url` and `fetched_at`, so the lineage of every claim is
  auditable.
- **Purpose limitation (Art. 5(1)(b))** — the tool ships with no
  long-term persistence, no telemetry, and no shared backend. The
  data leaves your machine only if you send it elsewhere.

It does *not* discharge the *legal* side — you still need a
documented lawful basis, an evaluation against the rights and
freedoms of the data subject, and (for sensitive categories under
Art. 9) a stricter basis. **When in doubt, do not collect.**

### Other jurisdictions

- **United States (CFAA, ECPA)** — Specter only fetches surfaces the
  source has chosen to publish, with `robots.txt` consent.
- **United Kingdom (DPA 2018, UK GDPR)** — substantially mirrors EU GDPR.
- **California (CCPA/CPRA)** — the "publicly available" carve-out
  (Cal. Civ. Code § 1798.140(v)(2)) generally covers what Specter
  fetches, but verify for your specific use case.
- **Brazil (LGPD)** — analogous to GDPR; legitimate interest applies.

This is not legal advice. Consult counsel for production use.

---

## Compliance guardrails (operational)

- Every outbound request goes through one shared `HttpClient` that
  checks `robots.txt`, applies a per-host token-bucket limiter
  (default 1 req/s), and refuses URLs that redirect to login walls.
- Every `Finding` records `source_url` and `fetched_at` for full
  provenance.
- The User-Agent surfaced to remote hosts is
  `specter/0.1 (research; +your@email)` when `SPECTER_CONTACT_EMAIL`
  is set — recommended by some APIs.
- The `hibp_breach` module ships disabled (HIBP is the one source
  category that genuinely needs a paid key). Drop `HIBP_API_KEY=...`
  into `.env` to enable; the module is wired and waiting.
- Reports are written under `./reports/`. `DELETE /reports/{job_id}`
  purges one; `POST /reports/purge-all` purges everything.

---

## Architecture

For the system-level view (request lifecycle, module map, data-flow
diagram, extension points), see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Development

```bash
uv run pytest -q       # all tests
uv run ruff check .    # lint
uv run ruff format .   # format
```

## Deploy to Render

A `render.yaml` Blueprint is checked in. From the Render dashboard,
**New + → Blueprint**, point at this repo, and Render reads the file:
free-tier Python web service, `pip install -e .`, `uvicorn` on `$PORT`.

Read the caveats at the top of `render.yaml` before publishing the URL.
Two that bite first:

- Free instances **spin down after ~15 min idle** — in-flight SSE
  jobs die.
- The deployed URL is **public**; the approval gate is client-side.
  Either keep the URL to yourself or put basic-auth in front of it.
