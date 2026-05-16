"""PDF report rendering.

Reads a job document (the same dict shape written to reports/{id}.json)
and produces a single multi-page PDF suitable for printing or handing
to a reader who doesn't want to parse JSON.

Layout:
    Page 1: cover (title, query summary, methodology, legal box).
    Page 2+: one section per Person — heading, tags, summary line,
             identity signals, findings table, coherence flags.

reportlab (BSD, free) is the only new dep.
"""

from __future__ import annotations

import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# --- styles -----------------------------------------------------------

_BASE = getSampleStyleSheet()
_TITLE = ParagraphStyle(
    "Title",
    parent=_BASE["Title"],
    fontName="Helvetica-Bold",
    fontSize=22,
    leading=26,
    textColor=colors.HexColor("#1a1a1a"),
    spaceAfter=8,
)
_H1 = ParagraphStyle(
    "H1",
    parent=_BASE["Heading1"],
    fontName="Helvetica-Bold",
    fontSize=16,
    leading=20,
    textColor=colors.HexColor("#1a1a1a"),
    spaceBefore=12,
    spaceAfter=6,
)
_H2 = ParagraphStyle(
    "H2",
    parent=_BASE["Heading2"],
    fontName="Helvetica-Bold",
    fontSize=11,
    leading=14,
    textColor=colors.HexColor("#444"),
    spaceBefore=8,
    spaceAfter=2,
)
_BODY = ParagraphStyle(
    "Body",
    parent=_BASE["BodyText"],
    fontName="Helvetica",
    fontSize=9.5,
    leading=13,
    textColor=colors.HexColor("#222"),
)
_SUMMARY = ParagraphStyle(
    "Summary",
    parent=_BODY,
    fontName="Helvetica-Bold",
    fontSize=10.5,
    leading=14,
    textColor=colors.HexColor("#0b3d91"),
    spaceBefore=4,
    spaceAfter=6,
)
_SMALL = ParagraphStyle(
    "Small",
    parent=_BODY,
    fontSize=8,
    leading=10,
    textColor=colors.HexColor("#666"),
)
_LEGAL = ParagraphStyle(
    "Legal",
    parent=_SMALL,
    backColor=colors.HexColor("#f4f4f4"),
    borderColor=colors.HexColor("#ddd"),
    borderWidth=0.5,
    borderPadding=6,
)


# --- helpers ----------------------------------------------------------

def _esc(s: Any) -> str:
    """reportlab Paragraph uses an HTML-ish mini-language. Escape user data
    so that things like <https://x> don't get parsed as tags."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#888"))
    canvas.drawString(
        15 * mm, 10 * mm,
        "Specter v0.1 — public sources only — lawful research, journalism, "
        "and authorised security work",
    )
    canvas.drawRightString(
        A4[0] - 15 * mm, 10 * mm,
        f"Page {doc.page}",
    )
    canvas.restoreState()


# --- per-section builders --------------------------------------------

def _cover(doc: dict) -> list:
    query = doc.get("query", {})
    query_rows = [(k, v) for k, v in query.items() if v]
    q_lines = "<br/>".join(
        f"<b>{_esc(k)}</b>: {_esc(v)}" for k, v in query_rows
    ) or "<i>(no query fields)</i>"

    methodology = (
        "Specter is a public-source-only OSINT aggregator. Each finding ships "
        "with a confidence score, the URL it was fetched from, and a "
        "timestamp. Findings are clustered into Persons via union-find over "
        "strong identity signals (ORCID, GitHub login, email, gravatar hash). "
        "Each cluster is then coherence-checked against four rules — "
        "name_mismatch, geo_outlier, century_gap, domain_outlier — and "
        "flagged findings are surfaced in the per-Person section rather than "
        "silently dropped."
    )
    legal = (
        "This report contains data gathered from publicly accessible sources "
        "under the principles of robots.txt compliance, per-host rate "
        "limiting, and refusal of authentication-walled surfaces. The reader "
        "is responsible for processing the data lawfully under applicable "
        "law (GDPR Art. 6, CCPA, CFAA, etc.). Specter does not assert the "
        "correctness of upstream sources."
    )

    flow: list = [
        Paragraph("Specter — Investigation Report", _TITLE),
        Paragraph(
            f"Job <font face='Courier'>{_esc(doc.get('job_id', ''))}</font> · "
            f"generated {_esc(doc.get('finished_at') or doc.get('started_at', ''))}",
            _SMALL,
        ),
        Spacer(1, 8),
        Paragraph("Query", _H2),
        Paragraph(q_lines, _BODY),
        Paragraph("Approved expansions", _H2),
        Paragraph(
            _esc(", ".join(doc.get("approved_expansions", []) or [])) or "<i>none</i>",
            _BODY,
        ),
        Paragraph("Result counts", _H2),
        Paragraph(
            f"People: {len(doc.get('people', []))} &nbsp;·&nbsp; "
            f"Findings: {len(doc.get('findings', []))} &nbsp;·&nbsp; "
            f"Dropped: {doc.get('dropped_count', 0)}",
            _BODY,
        ),
        Spacer(1, 12),
        Paragraph("Methodology", _H2),
        Paragraph(methodology, _BODY),
        Spacer(1, 8),
        Paragraph("Legal", _H2),
        Paragraph(legal, _LEGAL),
    ]
    return flow


def _person_section(person: dict, findings: list[dict], coherence: dict | None) -> list:
    flow: list = [
        PageBreak(),
        Paragraph(_esc(person.get("display_name") or "(unnamed)"), _H1),
        Paragraph(
            f"id <font face='Courier'>{_esc(person.get('id', ''))}</font> · "
            f"confidence {person.get('confidence', 0):.2f} · "
            f"coherence {person.get('coherence', 1.0):.2f}",
            _SMALL,
        ),
    ]

    summary = person.get("summary") or ""
    if summary:
        flow.append(Paragraph(_esc(summary), _SUMMARY))

    tags = person.get("tags") or []
    if tags:
        flow.append(Paragraph("Tags", _H2))
        flow.append(Paragraph(_esc(", ".join(tags)), _BODY))

    signals = person.get("signals") or {}
    if signals:
        flow.append(Paragraph("Identity signals", _H2))
        sig_rows = [
            [Paragraph(f"<b>{_esc(k)}</b>", _BODY), Paragraph(_esc(", ".join(v)), _BODY)]
            for k, v in sorted(signals.items())
            if v
        ]
        if sig_rows:
            t = Table(sig_rows, colWidths=[45 * mm, 130 * mm])
            t.setStyle(_TABLE_STYLE)
            flow.append(t)

    if findings:
        flow.append(Paragraph(f"Findings ({len(findings)})", _H2))
        header = [
            Paragraph("<b>Module</b>", _SMALL),
            Paragraph("<b>Type</b>", _SMALL),
            Paragraph("<b>Title</b>", _SMALL),
            Paragraph("<b>Conf.</b>", _SMALL),
        ]
        rows = [header]
        for f in findings:
            rows.append([
                Paragraph(_esc(f.get("module", "")), _SMALL),
                Paragraph(_esc(f.get("type", "")), _SMALL),
                Paragraph(
                    f"{_esc(f.get('title', ''))}<br/>"
                    f"<font color='#888'>{_esc(f.get('source_url', ''))}</font>",
                    _SMALL,
                ),
                Paragraph(f"{float(f.get('confidence', 0)):.2f}", _SMALL),
            ])
        t = Table(rows, colWidths=[28 * mm, 22 * mm, 110 * mm, 15 * mm], repeatRows=1)
        t.setStyle(_TABLE_STYLE_STRIPED)
        flow.append(t)

    if coherence and coherence.get("flags"):
        flow.append(Paragraph("Coherence flags", _H2))
        for fl in coherence["flags"]:
            flow.append(Paragraph(
                f"<b>{_esc(fl.get('rule', ''))}</b> — {_esc(fl.get('reason', ''))}",
                _BODY,
            ))

    return flow


_TABLE_STYLE = TableStyle([
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
])

_TABLE_STYLE_STRIPED = TableStyle([
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0f4")),
    ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#bbb")),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
])


# --- public API -------------------------------------------------------

def render_pdf(doc: dict) -> bytes:
    """Render a job document into a PDF, returning the raw bytes."""
    buf = io.BytesIO()
    pdf = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Specter Report — {doc.get('job_id', '')}",
        author="Specter",
    )

    # Build a findings lookup keyed by the (module, source_url) tuple the
    # Person.finding_keys reference.
    f_lookup: dict[tuple[str, str], dict] = {
        (f.get("module", ""), f.get("source_url", "")): f
        for f in doc.get("findings", [])
    }

    flow: list = []
    flow.extend(_cover(doc))

    people = doc.get("people", []) or []
    coherence = doc.get("coherence_reports", {}) or {}
    if not people:
        flow.append(PageBreak())
        flow.append(Paragraph("No people clusters were resolved.", _H2))
    for person in people:
        owned = []
        for k in person.get("finding_keys", []):
            # JSON deserialises tuples as lists.
            key = (k[0], k[1]) if isinstance(k, (list, tuple)) and len(k) >= 2 else None
            if key and key in f_lookup:
                owned.append(f_lookup[key])
        flow.extend(_person_section(person, owned, coherence.get(person.get("id", ""))))

    pdf.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
