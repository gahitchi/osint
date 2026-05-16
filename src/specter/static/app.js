// Specter — single-page client.
// Two-phase flow: preview → user approves scopes → run.
// Session-local state for triage (rejected / confirmed Persons).

const $ = (id) => document.getElementById(id);
const form = $("q");
const submitBtn = $("submit-btn");
const keepFormChk = $("keep-form");
const errorBox = $("error");
const previewSection = $("preview");
const ctxScore = $("ctx-score");
const ctxNote = $("ctx-note");
const autoList = $("auto-list");
const proposedList = $("proposed-list");
const proposedH = $("proposed-h");
const runBtn = $("run-btn");
const cancelBtn = $("cancel-btn");
const statusBar = $("status-bar");
const peopleList = $("people-list");
const peopleCount = $("people-count");
const treesSec = $("trees");
const treesList = $("trees-list");
const followupsSec = $("followups");
const followupsList = $("followups-list");
const reviewToggle = $("review-toggle");
const reviewCount = $("review-count");
const reviewListEl = $("review-list");
const rejectedToggle = $("rejected-toggle");
const rejectedCount = $("rejected-count");
const rejectedListEl = $("rejected-list");
const downloads = $("downloads");
const dlJson = $("dl-json");
const dlCsv = $("dl-csv");
const dlPdf = $("dl-pdf");
const purgeBtn = $("purge");
const cols = {
  search: document.querySelector('.col[data-cat="search"] ul'),
  social: document.querySelector('.col[data-cat="social"] ul'),
  academic: document.querySelector('.col[data-cat="academic"] ul'),
  breach: document.querySelector('.col[data-cat="breach"] ul'),
};

const state = {
  jobId: null,
  es: null,
  findingsByKey: new Map(),
  people: [],
  followups: [],
  trees: [],
  assessment: null,
  query: null,
  rejected: new Set(),    // person_id
  confirmed: new Set(),
};

const fkey = (f) => `${f.module}|${f.source_url}`;

function setError(msg) {
  if (!msg) { errorBox.hidden = true; errorBox.textContent = ""; return; }
  errorBox.hidden = false;
  errorBox.textContent = msg;
}

function setSubmitting(on) {
  submitBtn.disabled = on;
  runBtn.disabled = on;
}

function clearResults() {
  statusBar.innerHTML = "";
  peopleList.innerHTML = "";
  peopleCount.textContent = "";
  treesList.innerHTML = "";
  treesSec.hidden = true;
  followupsList.innerHTML = "";
  followupsSec.hidden = true;
  reviewListEl.innerHTML = "";
  reviewCount.textContent = "";
  reviewToggle.hidden = true;
  rejectedListEl.innerHTML = "";
  rejectedCount.textContent = "";
  rejectedToggle.hidden = true;
  for (const ul of Object.values(cols)) ul.innerHTML = "";
  downloads.hidden = true;
  state.findingsByKey = new Map();
  state.people = [];
  state.followups = [];
  state.trees = [];
  state.rejected = new Set();
  state.confirmed = new Set();
}

function readFormQuery() {
  const data = {};
  for (const [k, v] of new FormData(form).entries()) {
    const s = (v || "").toString().trim();
    if (s) data[k] = s;
  }
  return data;
}

function setFormValues(obj) {
  // Reset everything first, then populate.
  form.reset();
  for (const [k, v] of Object.entries(obj || {})) {
    const el = form.querySelector(`[name="${k}"]`);
    if (el) el.value = v;
  }
}

// ---- Phase 1: preview ----

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  setError("");
  if (state.es) { state.es.close(); state.es = null; }
  clearResults();
  previewSection.hidden = true;

  const data = readFormQuery();
  if (Object.keys(data).length === 0) {
    setError("Enter at least one field.");
    return;
  }
  state.query = data;
  setSubmitting(true);
  try {
    const r = await fetch("/search/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!r.ok) {
      const body = await r.text();
      setError(`Preview rejected (${r.status}): ${body}`);
      return;
    }
    state.assessment = await r.json();
    renderPreview(state.assessment);
  } catch (err) {
    setError(`Network error: ${err}`);
  } finally {
    setSubmitting(false);
  }
});

function renderPreview(a) {
  ctxScore.textContent = `(score ${a.score} / 10)`;
  ctxNote.textContent = a.thin
    ? "Context is thin. The tool will auto-run safe lookups; pick anything below to broaden the search (more recall, more noise)."
    : "Context looks specific. Auto-run set is sufficient for a precise lookup; extras are optional.";
  autoList.innerHTML = "";
  proposedList.innerHTML = "";
  proposedH.hidden = a.proposed.length === 0;
  for (const e of a.auto_run) autoList.appendChild(expansionRow(e, true));
  for (const e of a.proposed) proposedList.appendChild(expansionRow(e, false));
  previewSection.hidden = false;
  if (a.proposed.length === 0 && a.auto_run.length > 0) submitRun();
}

function expansionRow(exp, locked) {
  const row = document.createElement("div");
  row.className = "expansion";
  const chk = document.createElement("input");
  chk.type = "checkbox";
  chk.className = "check";
  chk.value = exp.id;
  chk.checked = locked;
  chk.disabled = locked;
  chk.dataset.exp = exp.id;
  const body = document.createElement("div");
  body.className = "body";
  const label = document.createElement("div");
  label.className = "label";
  label.appendChild(document.createTextNode(exp.label));
  const pill = document.createElement("span");
  pill.className = `risk-pill risk-${exp.risk}`;
  pill.textContent = exp.risk;
  label.appendChild(pill);
  if (locked) {
    const auto = document.createElement("span");
    auto.className = "muted";
    auto.style.marginLeft = ".5rem";
    auto.textContent = " auto";
    label.appendChild(auto);
  }
  const desc = document.createElement("div");
  desc.className = "desc";
  desc.textContent = exp.description;
  body.append(label, desc);
  row.append(chk, body);
  return row;
}

// ---- Phase 2: run ----

runBtn.addEventListener("click", submitRun);
cancelBtn.addEventListener("click", () => {
  previewSection.hidden = true;
  state.assessment = null;
});

async function submitRun() {
  setError("");
  const checked = Array.from(document.querySelectorAll("#preview input.check:checked"))
    .map((c) => c.value);
  const approved = Array.from(new Set(checked));
  setSubmitting(true);
  let res;
  try {
    res = await fetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: state.query, approved_expansions: approved }),
    });
  } catch (err) {
    setError(`Network error: ${err}`);
    setSubmitting(false);
    return;
  }
  if (!res.ok) {
    setError(`Search rejected (${res.status}): ${await res.text()}`);
    setSubmitting(false);
    return;
  }
  const { job_id } = await res.json();
  state.jobId = job_id;
  openStream(job_id);
}

function openStream(jobId) {
  previewSection.hidden = true;
  const es = new EventSource(`/stream/${jobId}`);
  state.es = es;
  es.addEventListener("status", (ev) => {
    const s = JSON.parse(ev.data);
    chip(s.module, s.state, s.detail || "");
  });
  es.addEventListener("finding", (ev) => {
    const f = JSON.parse(ev.data);
    state.findingsByKey.set(fkey(f), f);
    renderRawFinding(f);
  });
  es.addEventListener("people", (ev) => {
    state.people = JSON.parse(ev.data).people;
    renderAll();
  });
  es.addEventListener("followups", (ev) => {
    state.followups = JSON.parse(ev.data).items || [];
    renderFollowups();
  });
  es.addEventListener("trees", (ev) => {
    state.trees = JSON.parse(ev.data).items || [];
    renderTrees();
  });
  es.addEventListener("done", () => {
    es.close();
    state.es = null;
    dlJson.href = `/reports/${jobId}.json`;
    dlCsv.href = `/reports/${jobId}.csv`;
    dlPdf.href = `/reports/${jobId}.pdf`;
    downloads.hidden = false;
    setSubmitting(false);
    if (!keepFormChk.checked) form.reset();
  });
  es.onerror = () => {
    es.close();
    state.es = null;
    setSubmitting(false);
  };
}

function chip(mod, st, detail) {
  let c = statusBar.querySelector(`[data-mod="${mod}"]`);
  if (!c) {
    c = document.createElement("span");
    c.className = "chip";
    c.dataset.mod = mod;
    statusBar.appendChild(c);
  }
  c.className = `chip ${st}`;
  c.textContent = mod + (["ok", "skipped", "error"].includes(st) ? ` · ${st}` : "");
  if (detail) c.title = detail;
}

// ---- Rendering ----

function renderAll() {
  renderPeople(state.people);
  renderReview(state.people);
}

function renderPeople(people) {
  peopleList.innerHTML = "";
  rejectedListEl.innerHTML = "";
  let liveCount = 0, rejectedDisplayed = 0;
  for (const p of people) {
    const card = personCard(p);
    if (state.rejected.has(p.id)) {
      rejectedListEl.appendChild(card);
      rejectedDisplayed++;
    } else {
      peopleList.appendChild(card);
      liveCount++;
    }
  }
  peopleCount.textContent = liveCount ? `(${liveCount})` : "";
  rejectedCount.textContent = rejectedDisplayed ? `(${rejectedDisplayed})` : "";
  rejectedToggle.hidden = rejectedDisplayed === 0;
}

function personCard(p) {
  const incoherent = new Set((p.incoherent_finding_keys || []).map((k) => `${k[0]}|${k[1]}`));
  const allFindings = (p.finding_keys || [])
    .map(([m, u]) => state.findingsByKey.get(`${m}|${u}`))
    .filter(Boolean);
  const finds = allFindings.filter((f) => !incoherent.has(fkey(f)));
  const contacts = finds.filter((f) => f.type === "contact");
  const rejected = state.rejected.has(p.id);
  const confirmed = state.confirmed.has(p.id);

  const card = document.createElement("div");
  card.className = "person" + (rejected ? " rejected" : "") + (confirmed ? " confirmed" : "");

  const head = document.createElement("header");
  const name = document.createElement("h3");
  name.className = "name";
  name.textContent = (confirmed ? "✓ " : "") + p.display_name;
  const right = document.createElement("div");
  right.style.display = "flex";
  right.style.alignItems = "center";
  const conf = document.createElement("span");
  conf.className = "conf";
  conf.textContent = `c=${(confirmed ? 1 : p.confidence).toFixed(2)}`;
  right.appendChild(conf);
  if (typeof p.coherence === "number" && p.coherence < 1) {
    const co = document.createElement("div");
    co.className = "cohere" + (p.coherence < 0.6 ? " low" : "");
    co.textContent = ` coh ${p.coherence.toFixed(2)}`;
    right.appendChild(co);
  }
  const actions = document.createElement("div");
  actions.className = "actions";
  const yes = document.createElement("button");
  yes.className = "ok";
  yes.title = "Confirm this is the right person";
  yes.textContent = "✓";
  yes.addEventListener("click", () => {
    if (confirmed) state.confirmed.delete(p.id);
    else { state.confirmed.add(p.id); state.rejected.delete(p.id); }
    renderAll();
  });
  const no = document.createElement("button");
  no.className = "no";
  no.title = "Hide — not this person";
  no.textContent = "✗";
  no.addEventListener("click", () => {
    if (rejected) state.rejected.delete(p.id);
    else { state.rejected.add(p.id); state.confirmed.delete(p.id); }
    renderAll();
  });
  actions.append(yes, no);
  right.appendChild(actions);
  head.append(name, right);
  card.appendChild(head);

  if (p.summary) {
    const sum = document.createElement("p");
    sum.className = "summary";
    sum.textContent = p.summary;
    card.appendChild(sum);
  }

  if (p.tags && p.tags.length) {
    const tagsEl = document.createElement("div");
    tagsEl.className = "tags";
    for (const t of p.tags) {
      const ch = document.createElement("span");
      let cls = "tag";
      if (t.startsWith("@")) cls += " inst";
      if (t === "has-email" || t === "has-phone" || t === "contactable") cls += " contact";
      ch.className = cls;
      ch.textContent = t;
      tagsEl.appendChild(ch);
    }
    card.appendChild(tagsEl);
  }

  if (contacts.length) {
    const block = document.createElement("div");
    block.className = "contact-block";
    const grouped = {};
    for (const c of contacts) {
      const k = c.data.kind || "other";
      (grouped[k] = grouped[k] || []).push(c);
    }
    for (const [kind, list] of Object.entries(grouped)) {
      const heading = document.createElement("div");
      heading.innerHTML = `<strong>${kind}:</strong>`;
      const ul = document.createElement("ul");
      for (const c of list) {
        const li = document.createElement("li");
        const val = c.data.value || c.title;
        const src = c.data.source || c.module;
        li.innerHTML = `${escapeHtml(val)} <span class="muted">— ${escapeHtml(src)}</span>`;
        ul.appendChild(li);
      }
      block.append(heading, ul);
    }
    card.appendChild(block);
  }

  if (finds.length) {
    const det = document.createElement("details");
    const sum = document.createElement("summary");
    sum.textContent = `${finds.length} finding${finds.length === 1 ? "" : "s"}`;
    if (incoherent.size) sum.textContent += ` (+${incoherent.size} hidden)`;
    det.appendChild(sum);
    const ul = document.createElement("ul");
    for (const f of finds.sort((a, b) => b.confidence - a.confidence)) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = f.source_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = f.title;
      li.appendChild(a);
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = `${f.module} · ${f.type} · c=${f.confidence.toFixed(2)}`;
      li.appendChild(meta);
      ul.appendChild(li);
    }
    det.appendChild(ul);
    card.appendChild(det);
  }
  return card;
}

// ---- Family trees ----

const GENERATION_LABEL = {
  "-3": "great-grandparents",
  "-2": "grandparents",
  "-1": "parents",
  "0": "focal",
  "1": "children",
  "2": "grandchildren",
};

function renderTrees() {
  treesList.innerHTML = "";
  if (!state.trees.length) {
    treesSec.hidden = true;
    return;
  }
  for (const tree of state.trees) treesList.appendChild(treeCard(tree));
  treesSec.hidden = false;
}

function treeCard(tree) {
  const card = document.createElement("div");
  card.className = "tree-card";

  const head = document.createElement("div");
  head.className = "tree-head";
  const heading = document.createElement("h3");
  heading.textContent = tree.focal_label || tree.focal_qid;
  if (tree.focal_description) {
    const desc = document.createElement("span");
    desc.className = "desc";
    desc.textContent = " — " + tree.focal_description;
    heading.appendChild(desc);
  }
  head.appendChild(heading);
  const wiki = document.createElement("a");
  wiki.href = `https://www.wikidata.org/wiki/${tree.focal_qid}`;
  wiki.target = "_blank";
  wiki.rel = "noopener noreferrer";
  wiki.textContent = tree.focal_qid;
  head.appendChild(wiki);
  card.appendChild(head);

  // Group nodes by generation. Focal-level (gen=0) shows focal + siblings + spouses.
  const byGen = new Map();
  for (const n of tree.nodes || []) {
    const key = n.generation === undefined || n.generation === null ? 0 : n.generation;
    if (!byGen.has(key)) byGen.set(key, []);
    byGen.get(key).push(n);
  }
  const gens = Array.from(byGen.keys()).sort((a, b) => a - b);

  if (gens.length === 0) {
    const empty = document.createElement("div");
    empty.className = "tree-empty";
    empty.textContent = "No family data found on Wikidata for this candidate.";
    card.appendChild(empty);
    return card;
  }

  for (let i = 0; i < gens.length; i++) {
    const g = gens[i];
    const row = document.createElement("div");
    row.className = "tree-gen";
    row.dataset.generation = String(g);
    row.dataset.genlabel = GENERATION_LABEL[String(g)] || `gen ${g}`;
    // Sort: focal first at gen 0, then spouses, then siblings; otherwise by year.
    const nodes = byGen.get(g).slice().sort((a, b) => {
      const ord = { focal: 0, spouse: 1, sibling: 2 };
      const da = ord[a.relation] ?? 9;
      const db = ord[b.relation] ?? 9;
      if (da !== db) return da - db;
      return (a.birth || "").localeCompare(b.birth || "") || a.name.localeCompare(b.name);
    });
    for (const n of nodes) row.appendChild(treeNode(n));
    card.appendChild(row);
    if (i < gens.length - 1) {
      const div = document.createElement("div");
      div.className = "tree-divider";
      card.appendChild(div);
    }
  }

  return card;
}

function treeNode(n) {
  const a = document.createElement("a");
  a.className = `tree-node ${n.relation || ""}`;
  a.href = n.wikipedia_url || `https://www.wikidata.org/wiki/${n.qid}`;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  const name = document.createElement("span");
  name.className = "node-name";
  name.textContent = n.name || n.qid;
  a.appendChild(name);
  if (n.birth || n.death) {
    const dates = document.createElement("span");
    dates.className = "node-meta";
    dates.textContent = `${n.birth || "?"}${n.death ? " – " + n.death : ""}`;
    a.appendChild(dates);
  }
  if (n.relation && n.relation !== "focal") {
    const rel = document.createElement("span");
    rel.className = "node-rel";
    rel.textContent = n.relation;
    a.appendChild(rel);
  }
  return a;
}

function renderFollowups() {
  followupsList.innerHTML = "";
  if (!state.followups.length) {
    followupsSec.hidden = true;
    return;
  }
  for (const item of state.followups) {
    const btn = document.createElement("button");
    btn.className = "followup";
    btn.type = "button";
    btn.textContent = `Search from: ${item.label}`;
    const m = document.createElement("span");
    m.className = "muted";
    m.textContent = ` via ${item.found_by}`;
    btn.appendChild(m);
    btn.addEventListener("click", () => {
      // Populate form with the anchor, clear other fields, scroll to top,
      // and re-trigger the preview flow.
      setFormValues(item.anchor);
      window.scrollTo({ top: 0, behavior: "smooth" });
      form.dispatchEvent(new Event("submit", { cancelable: true }));
    });
    followupsList.appendChild(btn);
  }
  followupsSec.hidden = false;
}

function renderRawFinding(f) {
  const ul = cols[f.category];
  if (!ul) return;
  const li = document.createElement("li");
  const a = document.createElement("a");
  a.className = "title";
  a.href = f.source_url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = f.title;
  li.appendChild(a);
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.append(spanText(f.module), spanText(f.type));
  if (f.matched_fields && f.matched_fields.length) {
    meta.append(spanText("matches: " + f.matched_fields.join(",")));
  }
  const bar = document.createElement("span");
  bar.className = "conf-bar";
  const fill = document.createElement("span");
  fill.style.width = `${Math.round(f.confidence * 100)}%`;
  bar.appendChild(fill);
  meta.append(bar);
  li.appendChild(meta);
  ul.appendChild(li);
}

function renderReview(people) {
  reviewListEl.innerHTML = "";
  let total = 0;
  for (const p of people) {
    const incoherent = new Set((p.incoherent_finding_keys || []).map((k) => `${k[0]}|${k[1]}`));
    if (!incoherent.size) continue;
    for (const k of incoherent) {
      const f = state.findingsByKey.get(k);
      if (!f) continue;
      total++;
      const div = document.createElement("div");
      div.className = "review-item";
      const a = document.createElement("a");
      a.href = f.source_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = f.title;
      div.appendChild(a);
      const sub = document.createElement("div");
      sub.className = "muted";
      sub.textContent = `cluster: ${p.display_name} · ${f.module} · ${f.type}`;
      div.appendChild(sub);
      reviewListEl.appendChild(div);
    }
  }
  reviewCount.textContent = total ? `(${total})` : "";
  reviewToggle.hidden = total === 0;
}

function spanText(t) {
  const s = document.createElement("span");
  s.textContent = t;
  return s;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
  );
}

purgeBtn.addEventListener("click", async () => {
  if (!state.jobId) return;
  await fetch(`/reports/${state.jobId}`, { method: "DELETE" });
  clearResults();
  state.jobId = null;
});
