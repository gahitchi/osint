from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from .config import load_config
from .context import EXPANSION_CATALOG, assess
from .pipeline import Job
from .schema import Query

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

BASE = Path(__file__).parent
app = FastAPI(title="osint-name", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
INDEX_HTML = BASE / "templates" / "index.html"

_cfg = load_config()
_jobs: dict[str, Job] = {}


def _format_errors(e: ValidationError) -> list[dict]:
    """Pydantic v2 includes non-JSON-serializable objects (the underlying
    ValueError instance) in `ctx`; flatten to plain strings."""
    out: list[dict] = []
    for err in e.errors():
        out.append(
            {
                "field": ".".join(str(p) for p in err.get("loc", ())),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )
    return out


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(INDEX_HTML, media_type="text/html")


@app.post("/search/preview")
async def search_preview(payload: dict) -> JSONResponse:
    """Phase 1: cheap, no scraping. Score the query and return the expansion
    plan (what will auto-run + what needs the user's approval)."""
    try:
        q = Query.model_validate(payload)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=_format_errors(e)) from e
    return JSONResponse(assess(q).model_dump())


@app.post("/search")
async def start_search(payload: dict) -> JSONResponse:
    """Phase 2: actually run a job. Body: {query: {...}, approved_expansions: [...]}.

    For backwards compatibility, a flat body (no `query` key) is treated as
    the Query itself, and the default expansion set is the auto-run plan
    from `assess()`."""
    if "query" in payload and isinstance(payload["query"], dict):
        query_payload = payload["query"]
        approved = set(payload.get("approved_expansions") or [])
    else:
        query_payload = payload
        approved = set()
    try:
        q = Query.model_validate(query_payload)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors()) from e

    # Always merge in the auto-run set so low-risk modules don't require
    # the client to remember to include them.
    auto = assess(q)
    approved |= {e.id for e in auto.auto_run}

    # Validate ids — any unknown expansion id is a 400.
    unknown = approved - set(EXPANSION_CATALOG)
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown expansions: {sorted(unknown)}")

    job = Job(q, _cfg, approved_expansions=approved)
    _jobs[job.id] = job
    job._task = asyncio.create_task(job.run())
    return JSONResponse(
        {
            "job_id": job.id,
            "approved_expansions": sorted(approved),
        }
    )


@app.get("/stream/{job_id}")
async def stream(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job_id")

    async def gen():
        async for kind, payload in job.events():
            yield {"event": kind, "data": json.dumps(payload)}

    return EventSourceResponse(gen())


@app.get("/jobs/{job_id}")
async def job_summary(job_id: str) -> JSONResponse:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return JSONResponse(
        {
            "job_id": job.id,
            "query": job.query.model_dump(mode="json"),
            "approved_expansions": sorted(job.approved_expansions),
            "statuses": job.status_snapshot(),
            "people": [p.model_dump() for p in job.people],
            "findings": [json.loads(f.model_dump_json()) for f in job.findings],
            "coherence_reports": {
                pid: r.model_dump() for pid, r in job.coherence_reports.items()
            },
            "followups": job.followups,
            "trees": job.trees,
            "dropped_count": job.dropped_count,
            "started_at": job.started_at.isoformat(),
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }
    )


@app.get("/reports/{job_id}.json")
async def report_json(job_id: str) -> FileResponse:
    p = _cfg.reports_dir / f"{job_id}.json"
    if not p.exists():
        raise HTTPException(status_code=404)
    return FileResponse(p, media_type="application/json", filename=p.name)


@app.get("/reports/{job_id}.csv")
async def report_csv(job_id: str) -> Response:
    p = _cfg.reports_dir / f"{job_id}.json"
    if not p.exists():
        raise HTTPException(status_code=404)
    doc = json.loads(p.read_text())
    person_lookup: dict[tuple[str, str], dict] = {}
    for person in doc.get("people", []):
        for k in person.get("finding_keys", []):
            person_lookup[(k[0], k[1])] = person
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "person_id", "person_name", "tags",
        "module", "category", "type", "title", "source_url",
        "confidence", "matched_fields", "fetched_at",
    ])
    for f in doc.get("findings", []):
        person = person_lookup.get((f.get("module"), f.get("source_url"))) or {}
        w.writerow([
            person.get("id", ""),
            person.get("display_name", ""),
            ";".join(person.get("tags", [])),
            f.get("module", ""),
            f.get("category", ""),
            f.get("type", ""),
            f.get("title", ""),
            f.get("source_url", ""),
            f.get("confidence", ""),
            ";".join(f.get("matched_fields", []) or []),
            f.get("fetched_at", ""),
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.csv"'},
    )


@app.delete("/reports/{job_id}")
async def report_delete(job_id: str) -> JSONResponse:
    p = _cfg.reports_dir / f"{job_id}.json"
    if p.exists():
        p.unlink()
    _jobs.pop(job_id, None)
    return JSONResponse({"deleted": job_id})


@app.post("/reports/purge-all")
async def purge_all() -> JSONResponse:
    removed = 0
    for f in _cfg.reports_dir.glob("*.json"):
        f.unlink()
        removed += 1
    _jobs.clear()
    return JSONResponse({"removed": removed})


def run() -> None:
    """Entry point: `osint-name`."""
    import uvicorn  # noqa: PLC0415 - lazy import; only needed when run as a script

    uvicorn.run("osint_tool.main:app", host="127.0.0.1", port=8000, reload=False)
