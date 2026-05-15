# osint-name

A local-first OSINT research tool. Give it any single identifier — full name,
email, username (optionally tied to a specific platform), phone, location,
employer — and it:

1. **Assesses your context** and tells you what it can do for free.
2. **Asks before broad scraping**: a per-expansion approval checklist with risk
   labels. Low-risk lookups auto-run; medium/high-risk ones wait for your tick.
3. **Pivot-crawls** the platform you anchored on, extracting public emails,
   phones and outbound links (and following one hop of those links).
4. **Clusters findings into people** via union-find over identity signals
   (ORCID, GitHub login, email, gravatar hash, etc.).
5. **Coherence-checks** each cluster (name / geo / temporal / domain agreement)
   and hides contradictory findings in a collapsible *Needs review* panel.
6. **Tags every person** (`academic`, `prolific-author`, `developer`,
   `@Stanford`, `has-email`, `verified:github`, `ai-ml`, …).

**For lawful research, journalism, and authorized security work only.** You
are responsible for complying with laws in your jurisdiction (GDPR, CCPA,
CFAA, local privacy statutes). The tool refuses to scrape behind logins,
honors `robots.txt`, rate-limits per host, and ships an identifying
User-Agent. No paid keys required.

## Quickstart (Arch Linux)

```bash
sudo pacman -S --needed python git uv
git clone <your fork> ~/Projects/osint_name   # or: cp -r the source
cd ~/Projects/osint_name
uv sync --extra dev
uv run uvicorn osint_tool.main:app --reload
# → open http://127.0.0.1:8000
```

`uv` is in `extra/`. Python 3.11+ from the `python` package works.

To run without reload (a tiny bit faster):

```bash
uv run osint-name        # uses the script entry point
```

To remove all locally persisted reports:

```bash
curl -sX POST http://127.0.0.1:8000/reports/purge-all
```

## How a search flows

1. You fill some fields and hit **Assess context**.
2. The tool POSTs `/search/preview` (no scraping yet) and shows the plan:
   - "Will auto-run" rows are locked checkboxes — they always run.
   - "You can also approve" rows have unchecked boxes with risk pills.
3. Hit **Run with selected scopes**. The tool POSTs `/search` with your picks
   plus the auto set, then streams findings, person updates, and the final
   coherence pass over SSE.
4. People cards render at the top with tags + contact details. Findings the
   coherence pass flagged as contradicting their cluster move to **Needs
   review** at the bottom (nothing is silently dropped).
5. `Download JSON` gives you the full report including the per-cluster
   `CoherenceReport` with reasons for every flag.

## Expansions

| ID | Modules | Risk | Auto? |
|---|---|---|---|
| `targeted` | pivot_crawler, gravatar, hibp_breach | low | when `source_platform+username` or `email` given |
| `academic` | orcid, crossref, openalex | low | when `name` given |
| `archive` | wayback | low | when `username` or `email` given |
| `code_hosts` | github_user | medium | auto if `username` given; opt-in for name-only |
| `web_search` | search_ddg | medium | opt-in |
| `news` | news_gdelt | medium | opt-in |
| `username_fanout` | sherlock | **high** | opt-in; *skipped entirely if `source_platform` is set* |

## Supported source platforms (pivot crawler)

`github`, `gitlab`, `reddit`, `hackernews`, `mastodon`, `dev`, `keybase`,
`lichess`, `orcid`. Each fetcher reads the platform's free public API,
extracts contact info (incl. obfuscated emails like `foo (at) bar dot com`,
phone numbers parsed via `phonenumbers`), and follows up to five
user-listed outbound URLs.

## Compliance guardrails

- Every outbound request goes through one shared `HttpClient` that checks
  `robots.txt`, applies a per-host token-bucket limiter, and refuses URLs
  that redirect to login walls.
- Every `Finding` records `source_url` and `fetched_at` for full provenance.
- The `hibp_breach` module ships disabled (HIBP is the one source category
  that genuinely needs a paid key). Drop `HIBP_API_KEY=...` into `.env` to
  enable; the module is wired and waiting.
- Reports are written under `./reports/`. `DELETE /reports/{job_id}` purges
  one; `POST /reports/purge-all` purges everything.

## Development

```bash
uv run pytest -q       # all tests
uv run ruff check .    # lint
uv run ruff format .   # format
```
