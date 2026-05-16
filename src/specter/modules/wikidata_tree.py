"""Build a genealogical tree from Wikidata.

Wikidata is currently the only free, unauthenticated, structured source with
meaningful genealogical coverage. Coverage is biased toward notable
individuals (scientists, politicians, royalty, celebrities). For a random
person, this module returns nothing — which it surfaces honestly.

Pipeline:
1. Search Wikidata for up to 5 candidate items matching the name.
2. For each candidate, run a SPARQL query to find ancestors (3 gens up),
   descendants (2 gens down), siblings, and spouses, plus DOB/DOD/image/
   Wikipedia link per relative.
3. Keep candidates whose tree has at least one relative — they are people
   with family data on Wikidata. Limit to top 3.
4. Yield one Finding per candidate carrying the structured FamilyTree in
   `data["tree"]`.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator

from ..http import HttpClient
from ..schema import FamilyTree, Finding, Query, TreeNode
from .base import BaseModule

# Wikidata search returns this many candidate entities before filtering.
SEARCH_LIMIT = 5
# Maximum number of candidates whose trees we actually build + yield.
KEEP_CANDIDATES = 3
# How many generations of ancestors to walk (1 = parents, 3 = great-grandparents).
ANCESTORS_DEPTH = 3
# How many generations of descendants to walk.
DESCENDANTS_DEPTH = 2

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"


def _year_of(iso: str | None) -> str | None:
    if not iso:
        return None
    # Wikidata dates look like "+1938-01-10T00:00:00Z" or "-0050-00-00T00:00:00Z" (BCE)
    s = iso.lstrip("+")
    sign = "-" if s.startswith("-") else ""
    s = s.lstrip("-")
    return f"{sign}{s[:4]}" if s else None


def _relation_for(generation: int, is_focal: bool = False) -> str:  # noqa: PLR0911
    if is_focal:
        return "focal"
    if generation == -1:
        return "parent"
    if generation == -2:
        return "grandparent"
    if generation == -3:
        return "great-grandparent"
    if generation == 1:
        return "child"
    if generation == 2:
        return "grandchild"
    return "parent"  # fallback


class WikidataTreeModule(BaseModule):
    name = "wikidata_tree"
    category = "academic"
    expansions = ("genealogy",)

    def applicable(self, q: Query) -> bool:
        return bool(q.name)

    # ----- search -----

    async def _search_candidates(self, name: str, http: HttpClient) -> list[dict]:
        params = {
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "format": "json",
            "type": "item",
            "limit": str(SEARCH_LIMIT),
        }
        try:
            r = await http.get(WIKIDATA_API, params=params, check_robots=False)
        except Exception:
            return []
        if r.status_code != 200:
            return []
        try:
            data = r.json()
        except Exception:
            return []
        return data.get("search", []) or []

    # ----- SPARQL -----

    async def _sparql(self, query: str, http: HttpClient) -> list[dict]:
        try:
            r = await http.get(
                WIKIDATA_SPARQL,
                params={"query": query, "format": "json"},
                headers={"Accept": "application/sparql-results+json"},
                check_robots=False,
            )
        except Exception:
            return []
        if r.status_code != 200:
            return []
        try:
            return r.json().get("results", {}).get("bindings", []) or []
        except Exception:
            return []

    def _ancestors_query(self, qid: str) -> str:
        # Blazegraph (Wikidata's SPARQL engine) supports +/*/?  property-path
        # quantifiers, but NOT {n,m} cardinality. We use `+` and rely on the
        # fact that genealogical depth is naturally tiny; we'll clip nodes
        # past `ANCESTORS_DEPTH` in post-processing via the BFS that assigns
        # generations.
        return f"""
        SELECT DISTINCT ?p ?pLabel ?dob ?dod ?image ?wp ?father ?mother WHERE {{
          wd:{qid} (wdt:P22|wdt:P25)+ ?p .
          OPTIONAL {{ ?p wdt:P22 ?father }}
          OPTIONAL {{ ?p wdt:P25 ?mother }}
          OPTIONAL {{ ?p wdt:P569 ?dob }}
          OPTIONAL {{ ?p wdt:P570 ?dod }}
          OPTIONAL {{ ?p wdt:P18 ?image }}
          OPTIONAL {{ ?wp schema:about ?p ; schema:isPartOf <https://en.wikipedia.org/> }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }}
        LIMIT 200
        """

    def _descendants_query(self, qid: str) -> str:
        return f"""
        SELECT DISTINCT ?p ?pLabel ?dob ?dod ?image ?wp ?parent WHERE {{
          wd:{qid} wdt:P40+ ?p .
          OPTIONAL {{
            ?parent wdt:P40 ?p .
            wd:{qid} wdt:P40* ?parent .
          }}
          OPTIONAL {{ ?p wdt:P569 ?dob }}
          OPTIONAL {{ ?p wdt:P570 ?dod }}
          OPTIONAL {{ ?p wdt:P18 ?image }}
          OPTIONAL {{ ?wp schema:about ?p ; schema:isPartOf <https://en.wikipedia.org/> }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }}
        LIMIT 200
        """

    def _focal_query(self, qid: str) -> str:
        # The focal entity's own properties. Note: explicit rdfs:label lookup —
        # the wikibase:label SERVICE doesn't reliably resolve BIND-bound URIs.
        return f"""
        SELECT ?pLabel ?dob ?dod ?image ?wp ?desc WHERE {{
          OPTIONAL {{ wd:{qid} rdfs:label ?pLabel . FILTER(LANG(?pLabel) = "en") }}
          OPTIONAL {{ wd:{qid} schema:description ?desc . FILTER(LANG(?desc) = "en") }}
          OPTIONAL {{ wd:{qid} wdt:P569 ?dob }}
          OPTIONAL {{ wd:{qid} wdt:P570 ?dod }}
          OPTIONAL {{ wd:{qid} wdt:P18 ?image }}
          OPTIONAL {{ ?wp schema:about wd:{qid} ; schema:isPartOf <https://en.wikipedia.org/> }}
        }}
        LIMIT 1
        """

    def _peers_query(self, qid: str) -> str:
        # Immediate siblings and spouses.
        return f"""
        SELECT DISTINCT ?p ?pLabel ?dob ?dod ?image ?wp ?relation WHERE {{
          {{
            wd:{qid} wdt:P3373 ?p .
            BIND("sibling" AS ?relation)
          }} UNION {{
            wd:{qid} wdt:P26 ?p .
            BIND("spouse" AS ?relation)
          }}
          OPTIONAL {{ ?p wdt:P569 ?dob }}
          OPTIONAL {{ ?p wdt:P570 ?dod }}
          OPTIONAL {{ ?p wdt:P18 ?image }}
          OPTIONAL {{ ?wp schema:about ?p ; schema:isPartOf <https://en.wikipedia.org/> }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }}
        LIMIT 50
        """

    # ----- tree building -----

    @staticmethod
    def _qid(uri: str | None) -> str | None:
        if not uri:
            return None
        # Bindings are URIs like http://www.wikidata.org/entity/Q11930
        return uri.rsplit("/", 1)[-1] if uri.startswith("http") else uri

    @staticmethod
    def _binding(row: dict, key: str) -> str | None:
        v = row.get(key)
        if not isinstance(v, dict):
            return None
        val = v.get("value")
        return val if val else None

    async def _build_tree_for(  # noqa: PLR0912, PLR0915
        self, qid: str, http: HttpClient, fallback_label: str = "",
    ) -> FamilyTree | None:
        focal_rows, peer_rows, ancestor_rows, descendant_rows = await asyncio.gather(
            self._sparql(self._focal_query(qid), http),
            self._sparql(self._peers_query(qid), http),
            self._sparql(self._ancestors_query(qid), http),
            self._sparql(self._descendants_query(qid), http),
            return_exceptions=False,
        )

        has_relatives = bool(ancestor_rows) or bool(descendant_rows) or bool(peer_rows)
        if not has_relatives:
            return None

        nodes: dict[str, TreeNode] = {}
        edges: set[tuple[str, str]] = set()
        focal_label = fallback_label
        focal_desc: str | None = None

        # --- focal node ---
        if focal_rows:
            row = focal_rows[0]
            focal_label = self._binding(row, "pLabel") or focal_label or qid
            focal_desc = self._binding(row, "desc")
            nodes[qid] = TreeNode(
                qid=qid,
                name=focal_label,
                birth=_year_of(self._binding(row, "dob")),
                death=_year_of(self._binding(row, "dod")),
                relation="focal",
                generation=0,
                wikipedia_url=self._binding(row, "wp"),
                image_url=self._binding(row, "image"),
            )
        else:
            nodes[qid] = TreeNode(
                qid=qid, name=focal_label or qid, relation="focal", generation=0,
            )

        # --- siblings + spouses ---
        for row in peer_rows:
            p_qid = self._qid(self._binding(row, "p")) or ""
            if not p_qid:
                continue
            relation = self._binding(row, "relation") or "sibling"
            nodes.setdefault(
                p_qid,
                TreeNode(
                    qid=p_qid,
                    name=self._binding(row, "pLabel") or p_qid,
                    birth=_year_of(self._binding(row, "dob")),
                    death=_year_of(self._binding(row, "dod")),
                    relation=relation,  # type: ignore[arg-type]
                    generation=0,
                    wikipedia_url=self._binding(row, "wp"),
                    image_url=self._binding(row, "image"),
                ),
            )

        # --- ancestors ---
        # We don't know the generation depth per-row directly from the SPARQL
        # output. Compute via BFS over the edges (father/mother) once gathered.
        ancestors_seen: set[str] = set()
        for row in ancestor_rows:
            p_qid = self._qid(self._binding(row, "p")) or ""
            father_qid = self._qid(self._binding(row, "father"))
            mother_qid = self._qid(self._binding(row, "mother"))
            if not p_qid:
                continue
            ancestors_seen.add(p_qid)
            if p_qid not in nodes:
                nodes[p_qid] = TreeNode(
                    qid=p_qid,
                    name=self._binding(row, "pLabel") or p_qid,
                    birth=_year_of(self._binding(row, "dob")),
                    death=_year_of(self._binding(row, "dod")),
                    relation="parent",  # corrected after BFS
                    generation=-1,
                    wikipedia_url=self._binding(row, "wp"),
                    image_url=self._binding(row, "image"),
                )
            # Record father→p and mother→p edges
            if father_qid:
                edges.add((father_qid, p_qid))
                ancestors_seen.add(father_qid)
                nodes.setdefault(
                    father_qid,
                    TreeNode(qid=father_qid, name=father_qid, relation="parent", generation=-1),
                )
            if mother_qid:
                edges.add((mother_qid, p_qid))
                ancestors_seen.add(mother_qid)
                nodes.setdefault(
                    mother_qid,
                    TreeNode(qid=mother_qid, name=mother_qid, relation="parent", generation=-1),
                )

        # Add focal→parent edges so BFS sees them.
        # The ancestor query only returns ancestors, but each ancestor row
        # carries its own parents. Focal's parents appear as rows themselves;
        # the edge from focal to its parents needs to be derived from those
        # rows' "appears as parent" relationships.
        # Simplest correct way: re-query focal's direct parents and add edges.
        # We piggyback on the fact that the ancestors query returns
        # `?p (wdt:P22|wdt:P25){1,N} qid`, i.e. p is an ancestor of qid. Direct
        # parents are those for which p is reachable in ONE hop.
        # We don't have that information explicitly here, so we add edges
        # from focal to anyone whose "father"/"mother" doesn't appear in
        # the result set OR who is a known ancestor with no closer ancestor in
        # the set. A simpler heuristic: BFS from focal using the edges set,
        # assigning generation by depth. Anyone in ancestors_seen but not
        # reachable from focal via edges is a *direct* parent (1 hop) we
        # missed an edge for. So: any ancestor with no outgoing-from-it-to-
        # focal-path becomes a direct parent of focal.

        # BFS assigns generation
        children_of: dict[str, set[str]] = defaultdict(set)
        for a, b in edges:
            children_of[a].add(b)

        # Reverse edges: parents_of[child] = {parents}
        parents_of: dict[str, set[str]] = defaultdict(set)
        for a, b in edges:
            parents_of[b].add(a)

        # The ancestor SPARQL doesn't give us focal→parent edges directly
        # (it returns ancestors *of* focal, with each ancestor's own parents).
        # We resolve focal's direct parents with a tiny extra query — cheaper
        # than getting the inference wrong on edge cases.
        parents_q = f"""
        SELECT ?p ?pLabel WHERE {{
          {{ wd:{qid} wdt:P22 ?p }} UNION {{ wd:{qid} wdt:P25 ?p }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }} LIMIT 10
        """
        for row in await self._sparql(parents_q, http):
            p_qid = self._qid(self._binding(row, "p")) or ""
            if p_qid:
                edges.add((p_qid, qid))
                parents_of[qid].add(p_qid)
                children_of[p_qid].add(qid)
                if p_qid not in nodes:
                    nodes[p_qid] = TreeNode(
                        qid=p_qid,
                        name=self._binding(row, "pLabel") or p_qid,
                        relation="parent",
                        generation=-1,
                    )

        # BFS up from focal to assign generations & relations. We clip past
        # ANCESTORS_DEPTH to drop the long tail that Blazegraph's unbounded
        # property path can pull in.
        seen = {qid}
        queue: deque[tuple[str, int]] = deque([(qid, 0)])
        kept_ancestors: set[str] = set()
        while queue:
            node_qid, gen = queue.popleft()
            for parent_qid in parents_of.get(node_qid, set()):
                if parent_qid in seen:
                    continue
                seen.add(parent_qid)
                new_gen = gen - 1
                if abs(new_gen) > ANCESTORS_DEPTH:
                    continue
                kept_ancestors.add(parent_qid)
                if parent_qid in nodes:
                    nodes[parent_qid].generation = new_gen
                    nodes[parent_qid].relation = _relation_for(new_gen)  # type: ignore[assignment]
                queue.append((parent_qid, new_gen))

        # --- descendants ---
        descendants_seen: set[str] = set()
        for row in descendant_rows:
            p_qid = self._qid(self._binding(row, "p")) or ""
            parent_qid = self._qid(self._binding(row, "parent"))
            if not p_qid:
                continue
            descendants_seen.add(p_qid)
            if p_qid not in nodes:
                nodes[p_qid] = TreeNode(
                    qid=p_qid,
                    name=self._binding(row, "pLabel") or p_qid,
                    birth=_year_of(self._binding(row, "dob")),
                    death=_year_of(self._binding(row, "dod")),
                    relation="child",
                    generation=1,
                    wikipedia_url=self._binding(row, "wp"),
                    image_url=self._binding(row, "image"),
                )
            if parent_qid:
                edges.add((parent_qid, p_qid))

        # Resolve focal's direct children to anchor descendant tree.
        children_q = f"""
        SELECT ?p ?pLabel WHERE {{
          wd:{qid} wdt:P40 ?p .
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }} LIMIT 20
        """
        for row in await self._sparql(children_q, http):
            p_qid = self._qid(self._binding(row, "p")) or ""
            if p_qid:
                edges.add((qid, p_qid))
                if p_qid not in nodes:
                    nodes[p_qid] = TreeNode(
                        qid=p_qid, name=self._binding(row, "pLabel") or p_qid,
                        relation="child", generation=1,
                    )

        # BFS down to assign descendant generations (clip past DESCENDANTS_DEPTH)
        seen = {qid}
        queue = deque([(qid, 0)])
        kept_descendants: set[str] = set()
        while queue:
            node_qid, gen = queue.popleft()
            for child_qid in children_of.get(node_qid, set()):
                if child_qid in seen:
                    continue
                seen.add(child_qid)
                new_gen = gen + 1
                if new_gen > DESCENDANTS_DEPTH:
                    continue
                kept_descendants.add(child_qid)
                if child_qid in nodes:
                    nodes[child_qid].generation = new_gen
                    nodes[child_qid].relation = _relation_for(new_gen)  # type: ignore[assignment]
                queue.append((child_qid, new_gen))

        # Filter out nodes that aren't focal/sibling/spouse AND weren't
        # assigned a generation by BFS — these are noise from the unbounded
        # property path.
        kept_qids = {qid, *kept_ancestors, *kept_descendants}
        for n in nodes.values():
            if n.relation in ("sibling", "spouse"):
                kept_qids.add(n.qid)
        ordered_nodes = sorted(
            (n for n in nodes.values() if n.qid in kept_qids),
            key=lambda n: (n.generation, n.name),
        )

        if not focal_label:
            focal_label = qid

        return FamilyTree(
            focal_qid=qid,
            focal_label=focal_label,
            focal_description=focal_desc,
            nodes=ordered_nodes,
            edges=sorted(edges),
        )

    # ----- top-level run -----

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        if not q.name:
            return
        candidates = await self._search_candidates(q.name, http)
        kept = 0
        for cand in candidates:
            if kept >= KEEP_CANDIDATES:
                break
            qid = cand.get("id")
            if not qid:
                continue
            tree = await self._build_tree_for(
                qid, http, fallback_label=cand.get("label", "") or qid,
            )
            if not tree:
                continue
            kept += 1
            yield Finding(
                module=self.name,
                category="academic",
                type="profile",
                title=f"Wikidata: {tree.focal_label}",
                source_url=f"https://www.wikidata.org/wiki/{qid}",
                data={
                    "qid": qid,
                    "description": tree.focal_description,
                    "tree": json.loads(tree.model_dump_json()),
                    "candidate_label": cand.get("label"),
                    "candidate_description": cand.get("description"),
                    "match_rank": kept,
                },
                signals={
                    "wikidata_qid": [qid],
                    "name": [tree.focal_label],
                },
                confidence=0.75,
            )
