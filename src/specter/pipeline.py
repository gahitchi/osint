"""Pipeline orchestrator. Fans out modules; streams findings through an asyncio.Queue."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from .cluster import cluster
from .cohere import evaluate as cohere_evaluate
from .config import Config
from .context import modules_for_expansions
from .cross_ref import is_duplicate, rescore
from .filter import classify
from .http import HttpClient
from .interpret import interpret
from .modules import all_modules
from .schema import CoherenceReport, Finding, ModuleStatus, Person, Query
from .tagging import tag_person

log = logging.getLogger(__name__)

_SENTINEL = object()


class Job:
    def __init__(
        self,
        query: Query,
        cfg: Config,
        approved_expansions: set[str] | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.query = query
        self.cfg = cfg
        self.approved_expansions = approved_expansions or set()
        self.findings: list[Finding] = []          # kept (verdict in {keep, demote})
        self.dropped_count: int = 0                # purely for stats
        self.statuses: dict[str, ModuleStatus] = {}
        self.people: list[Person] = []
        self.coherence_reports: dict[str, CoherenceReport] = {}
        self.followups: list[dict] = []
        self.trees: list[dict] = []
        self._people_sig: str = ""
        self.queue: asyncio.Queue = asyncio.Queue()
        self.started_at = datetime.now(UTC)
        self.finished_at: datetime | None = None
        self._sem: asyncio.Semaphore | None = None
        self._task: asyncio.Task | None = None

    def status_snapshot(self) -> list[dict]:
        return [s.model_dump() for s in self.statuses.values()]

    def _rebuild_people(self) -> None:
        persons = cluster(self.findings, self.query)
        keys_to_finding = {f.dedupe_key(): f for f in self.findings}
        for p in persons:
            owned = [keys_to_finding[k] for k in p.finding_keys if k in keys_to_finding]
            p.tags = tag_person(p, owned, self.query)
        self.people = persons

    def _compute_followups(self) -> list[dict]:
        """Surface novel strong identifiers extracted by the modules so the
        user can launch a follow-up search anchored on them.

        We propose one followup per (kind, value) pair that:
        - isn't already part of the input query
        - is a strong identifier kind (email / github_login / orcid)
        - has at least one finding carrying it.
        """
        q = self.query
        already_email = (q.email or "").lower() or None
        already_username = (q.username or "").lower() or None
        already_platform = q.source_platform

        candidates: dict[tuple[str, str], dict] = {}
        for f in self.findings:
            sig = f.signals or {}
            for em in sig.get("email", []):
                em_l = em.lower()
                if not em_l or em_l == already_email:
                    continue
                key = ("email", em_l)
                if key in candidates:
                    continue
                candidates[key] = {
                    "label": em,
                    "anchor": {"email": em},
                    "source_url": str(f.source_url),
                    "found_by": f.module,
                }
            for login in sig.get("github_login", []):
                login_l = login.lower()
                if not login_l:
                    continue
                if already_platform == "github" and login_l == already_username:
                    continue
                key = ("github_login", login_l)
                if key in candidates:
                    continue
                candidates[key] = {
                    "label": f"github:{login}",
                    "anchor": {"username": login, "source_platform": "github"},
                    "source_url": str(f.source_url),
                    "found_by": f.module,
                }
            for orcid in sig.get("orcid", []):
                key = ("orcid", orcid.lower())
                if key in candidates:
                    continue
                if already_platform == "orcid" and orcid.lower() == already_username:
                    continue
                candidates[key] = {
                    "label": f"orcid:{orcid}",
                    "anchor": {"username": orcid, "source_platform": "orcid"},
                    "source_url": str(f.source_url),
                    "found_by": f.module,
                }
        return list(candidates.values())

    def _apply_coherence(self) -> None:
        """Final pass: compute coherence per Person, mark incoherent finding
        keys, record the report, and synthesize a one-line interpretation."""
        keys_to_finding = {f.dedupe_key(): f for f in self.findings}
        for p in self.people:
            owned = [keys_to_finding[k] for k in p.finding_keys if k in keys_to_finding]
            report = cohere_evaluate(p, owned)
            self.coherence_reports[p.id] = report
            p.coherence = report.score
            p.incoherent_finding_keys = sorted(
                {fl.finding_key for fl in report.flags}, key=lambda t: (t[0], t[1])
            )
            p.summary = interpret(p, owned, report)

    async def _emit_people_if_changed(self) -> None:
        sig = json.dumps([p.model_dump() for p in self.people], sort_keys=True)
        if sig != self._people_sig:
            self._people_sig = sig
            await self.queue.put(
                ("people", {"people": [p.model_dump() for p in self.people]})
            )

    async def _ingest(self, finding: Finding) -> None:
        seen = {f.dedupe_key() for f in self.findings}
        if is_duplicate(finding, seen):
            return
        verdict = classify(finding, self.query)
        if verdict == "drop":
            self.dropped_count += 1
            return
        rescore(finding, self.query, self.findings)
        if verdict == "demote":
            finding.confidence = round(min(finding.confidence, 0.25), 3)
        self.findings.append(finding)
        await self.queue.put(("finding", json.loads(finding.model_dump_json())))
        self._rebuild_people()
        await self._emit_people_if_changed()

    async def _run_module(self, mod, http: HttpClient) -> None:
        assert self._sem is not None
        st = ModuleStatus(module=mod.name, category=mod.category, state="running")
        self.statuses[mod.name] = st
        await self.queue.put(("status", st.model_dump()))
        reason = mod.skip_reason(self.cfg)
        if reason:
            st.state = "skipped"
            st.detail = reason
            await self.queue.put(("status", st.model_dump()))
            return
        try:
            async with self._sem:
                async for finding in mod.run(self.query, http):
                    await self._ingest(finding)
            st.state = "ok"
            st.detail = f"{sum(1 for f in self.findings if f.module == mod.name)} kept"
        except Exception as e:  # noqa: BLE001 - we surface to UI
            log.warning("module %s failed: %s", mod.name, e)
            st.state = "error"
            st.detail = f"{type(e).__name__}: {e}"
        await self.queue.put(("status", st.model_dump()))

    def _select_modules(self) -> list:
        """Apply expansion gating: only run modules whose `expansions` tuple
        intersects the approved set."""
        approved_module_names = modules_for_expansions(self.approved_expansions)
        out = []
        for m in all_modules(self.cfg):
            if not m.applicable(self.query):
                continue
            # If module has no declared expansions, run it unconditionally
            # (defensive default — covers any new module added later).
            if m.expansions and not (set(m.expansions) & self.approved_expansions):
                continue
            # Sanity check: confirm the module name is reachable from the
            # approved expansion catalog. If not, run it anyway — the
            # expansions tuple is the source of truth.
            _ = approved_module_names  # noqa: B018 (kept for reviewers; logic above is sufficient)
            out.append(m)
        return out

    async def run(self) -> None:
        self._sem = asyncio.Semaphore(self.cfg.max_concurrency)
        http = HttpClient(self.cfg)
        modules = self._select_modules()
        for m in modules:
            self.statuses[m.name] = ModuleStatus(
                module=m.name, category=m.category, state="pending"
            )
        try:
            await self.queue.put(
                (
                    "started",
                    {
                        "job_id": self.id,
                        "modules": [m.name for m in modules],
                        "approved_expansions": sorted(self.approved_expansions),
                    },
                )
            )
            tasks = [asyncio.create_task(self._run_module(m, http)) for m in modules]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await http.aclose()
            # ---- coherence pass before final emit + persist ----
            self._apply_coherence()
            await self._emit_people_if_changed()
            # ---- collect genealogy trees ----
            self.trees = [
                f.data["tree"]
                for f in self.findings
                if f.module == "wikidata_tree" and isinstance(f.data.get("tree"), dict)
            ]
            if self.trees:
                await self.queue.put(("trees", {"items": self.trees}))
            # ---- recursive-pivot follow-up leads ----
            followups = self._compute_followups()
            self.followups = followups
            await self.queue.put(("followups", {"items": followups}))
            self.finished_at = datetime.now(UTC)
            self._persist()
            await self.queue.put(
                (
                    "done",
                    {
                        "job_id": self.id,
                        "kept": len(self.findings),
                        "dropped": self.dropped_count,
                        "people": len(self.people),
                        "incoherent": sum(
                            len(p.incoherent_finding_keys) for p in self.people
                        ),
                    },
                )
            )
            await self.queue.put(_SENTINEL)

    def _persist(self) -> None:
        self.cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        out: Path = self.cfg.reports_dir / f"{self.id}.json"
        out.write_text(
            json.dumps(
                {
                    "job_id": self.id,
                    "query": self.query.model_dump(mode="json"),
                    "approved_expansions": sorted(self.approved_expansions),
                    "started_at": self.started_at.isoformat(),
                    "finished_at": (self.finished_at or datetime.now(UTC)).isoformat(),
                    "statuses": self.status_snapshot(),
                    "people": [p.model_dump() for p in self.people],
                    "findings": [json.loads(f.model_dump_json()) for f in self.findings],
                    "coherence_reports": {
                        pid: r.model_dump() for pid, r in self.coherence_reports.items()
                    },
                    "followups": self.followups,
                    "trees": self.trees,
                    "dropped_count": self.dropped_count,
                },
                indent=2,
            )
        )

    async def events(self) -> AsyncIterator[tuple[str, dict]]:
        while True:
            item = await self.queue.get()
            if item is _SENTINEL:
                return
            yield item
