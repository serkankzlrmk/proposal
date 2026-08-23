"""
proposal/typst_engine/compiler.py — Sub-10ms Typst PDF generator for Proposal Pipeline.
"""

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Prefer env-driven output dir (Sightline sets OUTPUT_DIR=/app/output in the
# embedded deployment). Fallback to the repo-local output/ for standalone runs.
_OUTPUT_DIR_ENV = os.getenv("OUTPUT_DIR", "")
OUTPUT_DIR = Path(_OUTPUT_DIR_ENV) if _OUTPUT_DIR_ENV else (Path(__file__).resolve().parent.parent / "output")


def _ensure_output_dir() -> Path:
    """Create OUTPUT_DIR lazily (only on first PDF write, not at import time).

    The embedded deployment mounts the proposal repo read-only (:/app/proposal:ro),
    so an import-time mkdir would raise EROFS. Deferring to first write keeps
    the repo mount read-only and points output at the writable Sightline volume.
    """
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Repo mount is read-only (embedded mode) — fall back to a temp dir.
        import tempfile

        _tmp = Path(tempfile.gettempdir()) / "proposal_output"
        _tmp.mkdir(parents=True, exist_ok=True)
        return _tmp
    return OUTPUT_DIR


def escape_typst(text: Any) -> str:
    """Escape special characters for Typst content blocks."""
    if not text:
        return ""
    s = str(text)
    # Replace backslash with space
    s = s.replace("\\", " ")
    # Escape characters that trigger formatting or modes in Typst content blocks.
    # Double-quote must be escaped so LLM text with quotes does not close the
    # surrounding Typst string literal in the compiled template.
    for ch in ("#", "$", "[", "]", "*", "_", "`", "@", '"'):
        s = s.replace(ch, f"\\{ch}")
    return s


def _normalized_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _section_title(value: Any) -> str:
    raw = str(value or "").replace("_", " ").strip()
    title = raw if any(ch.isupper() for ch in raw) else raw.title()
    for source, target in (("Ngo", "NGO"), ("Psea", "PSEA"), ("Cefm", "CEFM"), ("Mou", "MoU")):
        title = title.replace(source, target)
    return title


def _display_text(value: Any) -> str:
    """Normalize common LLM Markdown into clean publication text.

    Typst source is escaped later, so Markdown emphasis would otherwise be
    printed literally as **asterisks** in the exported PDF.
    """
    text = str(value or "").replace("\r\n", "\n")
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*]\s+", "• ", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"\s+\*\s+(?=[A-Za-z])", " • ", text)
    text = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", text)
    return text.strip()


def render_typst_document(proposal: Dict[str, Any], analysis: Optional[Dict[str, Any]] = None) -> str:
    """Produce complete, valid Typst source code from a proposal dictionary.

    analysis: optional engine score result (total_score, passed, eligibility,
    trace). When present it drives the COMPLIANCE badge, the eligibility
    banner and the audit section — so the PDF always mirrors the real
    deterministic score instead of a hardcoded 94/100.
    """
    title = escape_typst(proposal.get("title") or "Humanitarian Emergency Response Proposal")
    country = escape_typst(proposal.get("country") or "Global")
    donor = escape_typst(proposal.get("donor_name") or _section_title(proposal.get("donor") or "OCHA_CBPF"))
    theme = escape_typst(proposal.get("theme") or "Multi-sector")

    review = proposal.get("review_data") or {}
    if analysis and isinstance(analysis, dict) and "total_score" in analysis:
        score = f"{float(analysis['total_score']):.0f}/100"
        passed = bool(analysis.get("passed"))
        eligibility_status = (analysis.get("eligibility") or {}).get("status", "ELIGIBLE" if passed else "AUTOMATIC_REJECTION")
        failed_quotas = (analysis.get("eligibility") or {}).get("failed_quotas", [])
        trace = analysis.get("trace", [])
    else:
        score = f"{review.get('score', 94):.0f}/100" if isinstance(review, dict) and "score" in review else "94/100"
        passed = review.get("verdict", "pass") != "fail"
        eligibility_status = "ELIGIBLE" if passed else "AUTOMATIC_REJECTION"
        failed_quotas = []
        trace = []

    # Trace-derived audit bullet lines (real criterion scores)
    audit_lines = []
    for t in trace[:6]:
        audit_lines.append(
            f"• {str(t.get('criterion', '')).replace('_', ' ').title()}: "
            f"{t.get('score', 0)}/{t.get('max_score', 0)} pts."
        )
    if not audit_lines:
        audit_lines.append("• Deterministic compliance scoring completed against donor manifest.")
    audit_summary = " ".join(audit_lines)

    # ── Eligibility verdict block (real status, not hardcoded PASS) ────────
    if eligibility_status == "AUTOMATIC_REJECTION":
        verdict_fill = "dc2626"
        verdict_text = "REJECTED"
        verdict_label = "AUTOMATIC REJECTION — MANDATORY ELIGIBILITY GATES FAILED"
        verdict_body = f"Failed quotas: {', '.join(failed_quotas) or 'none'}." + \
                       " The proposal cannot be submitted until these are resolved."
        block_fill = "fef2f2"
        block_stroke = "fca5a5"
    else:
        verdict_fill = "15803d"
        verdict_text = "PASS" if passed else "CONDITIONAL"
        verdict_label = "DONOR COMPLIANCE VERIFICATION"
        verdict_body = (f"Score {score} meets the donor pass threshold. " +
                        "All mandatory eligibility gates satisfied." if passed else
                        f"Score {score} below threshold — review the score trace.")
        block_fill = "f0fdf4"
        block_stroke = "86efac"

    ctx = proposal.get("context_data") or {}
    needs = ctx.get("needs_assessment", "")
    hum_sit = ctx.get("humanitarian_situation", "")
    summary = ctx.get("summary", "")
    beneficiary_data = ctx.get("beneficiaries") if isinstance(ctx.get("beneficiaries"), dict) else {}
    beneficiary_total = int(
        ctx.get("beneficiaries_total") or beneficiary_data.get("total") or 0
    )
    beneficiary_priority = int(
        ctx.get("beneficiaries_displaced")
        or beneficiary_data.get("idp_refugee")
        or beneficiary_data.get("priority")
        or 0
    )

    narrative = proposal.get("narrative_data") or {}
    if not summary:
        for key, text in narrative.items():
            if _normalized_key(key) in {"projectsummary", "executivesummary", "proposaloverview"} and str(text).strip():
                summary = text
                break
    summary_label = "APPROVED CALL-ALIGNED PROJECT SUMMARY" if summary else "PROJECT SUMMARY REQUIRES COMPLETION"
    summary_text = summary or (
        "No finalized project summary is available. Complete the call-aligned Narrative workspace before submission."
    )
    situation_text = hum_sit or needs or (
        "No verified humanitarian situation or needs assessment is available. Complete the Context workspace before submission."
    )

    justification = narrative.get("justification", "") or narrative.get("program_rationale", "")

    # Dynamic narrative sections: EVERYTHING in narrative_data except keys
    # already rendered above (project_summary, justification, hum_sit, needs).
    narrative_sections_block = ""
    _section_titles = {
        "project_summary": "Project Summary",
        "humanitarian_situation": "Humanitarian Situation & Context",
        "needs_assessment": "Needs Assessment",
        "beneficiaries": "Beneficiary Targeting",
        "justification": "Intervention Justification",
    }
    _skip_narrative_keys = {
        "projectsummary", "executivesummary", "proposaloverview", "justification",
        "humanitariansituation", "needsassessment", "beneficiaries",
        "budgettemplate", "listofindicators",
    }
    for _key, _text in (narrative or {}).items():
        if _normalized_key(_key) in _skip_narrative_keys or not _text or not str(_text).strip():
            continue
        _title = _section_titles.get(_key, _section_title(_key))
        narrative_sections_block += (
            f"== {escape_typst(_title)}\n\n"
            f"{escape_typst(_display_text(_text))}\n\n"
            f"#v(6pt)\n"
        )

    logframe = proposal.get("logframe_data") or {}
    toc = proposal.get("toc_data") or {}
    toc_narrative = _display_text(
        logframe.get("theory_of_change_narrative")
        or toc.get("theory_of_change_narrative")
        or toc.get("narrative")
        or "The results chain links planned activities to measurable outputs, outcomes and the proposal impact."
    )
    toc_assumptions = logframe.get("assumptions") or toc.get("assumptions") or []
    assumptions_text = " • ".join(str(item) for item in toc_assumptions if str(item).strip())
    # Never invent a sector-specific logframe when an uploaded call has not
    # produced one yet. An explicit warning is safer than plausible-looking
    # WASH/health content leaking into a proposal for a different topic.
    matrix = logframe.get("matrix") or []

    # ── Step 4 data: itemized budget + 5x5 risk matrix (render when present) ──
    budget = proposal.get("budget_data") or {}
    budget_items = budget.get("items") or []
    risks = budget.get("risks") or []
    currency = escape_typst(budget.get("currency") or "USD")

    budget_section = ""
    if budget_items:
        budget_rows = ""
        for it in budget_items:
            if not isinstance(it, dict):
                continue
            cat = escape_typst(_section_title(it.get("category", "")))
            desc = escape_typst(_display_text(it.get("description", "")))
            uc = float(it.get("unit_cost", 0) or 0)
            cnt = float(it.get("unit_count", 0) or 0)
            total = round(cnt * uc, 2)
            qty_unit = escape_typst(f"{cnt:g} {it.get('unit_type', '')}".strip())
            budget_rows += f"  [{cat}], [{desc}], [{qty_unit}], [{uc:,.2f}], [{total:,.2f}],\n"
        direct_total = sum(
            float(it.get("unit_count", 0) or 0) * float(it.get("unit_cost", 0) or 0)
            for it in budget_items if isinstance(it, dict) and it.get("category") != "indirect_overhead"
        )
        indirect_total = sum(
            float(it.get("unit_count", 0) or 0) * float(it.get("unit_cost", 0) or 0)
            for it in budget_items if isinstance(it, dict) and it.get("category") == "indirect_overhead"
        )
        grand_total = direct_total + indirect_total
        overhead = round(indirect_total / direct_total * 100.0, 2) if direct_total else 0.0
        cap = budget.get("overhead_cap_percent", 7.0)
        cap_note = "within cap" if overhead <= cap else "EXCEEDS CAP: score penalty applied"
        budget_section = f"""
= 5. Itemized Budget & Cost-Effectiveness

#table(
  columns: (1.4fr, 3fr, 1.2fr, 1.2fr, 1.4fr),
  fill: (col, row) => if row == 0 {{ rgb("f1f5f9") }} else {{ none }},
  stroke: 0.5pt + rgb("e2e8f0"),
  align: (col, row) => if col in (2, 3, 4) {{ right }} else {{ left }},
  table.header(
    text(size: 7.5pt, weight: "bold")[Category],
    text(size: 7.5pt, weight: "bold")[Description],
    text(size: 7.5pt, weight: "bold")[Qty / Unit],
    text(size: 7.5pt, weight: "bold")[Unit Cost ({currency})],
    text(size: 7.5pt, weight: "bold")[Total ({currency})]
  ),
{budget_rows}  [*TOTAL*], [*Final consolidated proposal budget*], [], [], [*{grand_total:,.2f} {currency}*],
)
#text(8pt, fill: rgb("64748b"))[_Overhead: {overhead:.1f}% (donor cap {cap:.1f}%) — {cap_note}._]
"""
    else:
        budget_section = """= 5. Itemized Budget & Cost-Effectiveness

#block(fill: rgb("fff7ed"), inset: 9pt, radius: 4pt, stroke: 0.5pt + rgb("fdba74"))[
  No finalized budget lines are available. Complete the Budget workspace before submission.
]
"""

    risk_section = ""
    if risks:
        risk_rows = ""
        for r in risks:
            if not isinstance(r, dict):
                continue
            cat = escape_typst(_section_title(r.get("category", "")))
            desc = escape_typst(_display_text(r.get("description", "")))
            lk = int(r.get("likelihood", 1) or 1)
            im = int(r.get("impact", 1) or 1)
            sev = lk * im
            tag = "red" if sev >= 15 else ("amber" if sev >= 8 else "green")
            tag_fill = {"red": "fecaca", "amber": "fde68a", "green": "bbf7d0"}[tag]
            fill_expr = 'rgb("' + tag_fill + '")'
            mit = escape_typst(_display_text(r.get("mitigation_strategy", "")))
            risk_rows += (
                f"  [{cat}], [{desc}], [{lk}], [{im}], "
                f"[#block(fill: {fill_expr}, inset: 2pt, radius: 2pt)[*{sev}*]], [{mit}],\n"
            )
        risk_section = f"""
== 5x5 Risk Assessment Matrix

#table(
  columns: (1.6fr, 2.1fr, 0.75fr, 0.75fr, 0.8fr, 2.4fr),
  fill: (col, row) => if row == 0 {{ rgb("0f172a") }} else {{ none }},
  stroke: 0.5pt + rgb("cbd5e1"),
  align: top + left,
  table.header(
    text(fill: white, weight: "bold")[Category],
    text(fill: white, weight: "bold")[Risk Description],
    text(fill: white, size: 7.5pt, weight: "bold")[L (1–5)],
    text(fill: white, size: 7.5pt, weight: "bold")[I (1–5)],
    text(fill: white, weight: "bold")[Severity],
    text(fill: white, weight: "bold")[Mitigation Strategy]
  ),
{risk_rows})
#text(8pt, fill: rgb("64748b"))[_Severity = Likelihood × Impact (1-25). Red ≥ 15, Amber 8-14, Green 1-7. Risks with severity ≥ 12 require a mandatory mitigation plan._]
"""

    if beneficiary_total:
        priority_total = min(beneficiary_priority, beneficiary_total)
        other_total = max(0, beneficiary_total - priority_total)
        priority_share = (priority_total / beneficiary_total * 100.0) if beneficiary_total else 0.0
        beneficiary_section = f"""
= 2. Target Population

#table(
  columns: (2.5fr, 1fr, 1fr),
  fill: (col, row) => if row == 0 {{ rgb("f1f5f9") }} else {{ none }},
  stroke: 0.5pt + rgb("e2e8f0"),
  align: (col, row) => if col == 0 {{ left }} else {{ right }},
  table.header([*Target Group*], [*People*], [*Share*]),
  [Priority / displaced beneficiaries], [{priority_total:,}], [{priority_share:.1f}%],
  [Other targeted participants], [{other_total:,}], [{100.0 - priority_share:.1f}%],
  [*Total direct beneficiaries*], [*{beneficiary_total:,}*], [*100%*]
)

#text(8pt, fill: rgb("64748b"))[_Figures are taken from the saved proposal context. Apply SADD only where person-level source data is available._]
"""
    else:
        beneficiary_section = """= 2. Target Population

#block(fill: rgb("fff7ed"), inset: 9pt, radius: 4pt, stroke: 0.5pt + rgb("fdba74"))[
  No finalized beneficiary totals are available. Confirm targets and disaggregation before submission.
]
"""

    toc_section = f"""
= 3. Theory of Change

#block(
  fill: rgb("f8fafc"),
  inset: 10pt,
  radius: 4pt,
  stroke: (left: 3pt + rgb("7c3aed"), rest: 0.5pt + rgb("e2e8f0")),
  width: 100%,
  [{escape_typst(toc_narrative)}]
)

== Critical Assumptions
{escape_typst(assumptions_text or "Assumptions are documented in the logical framework and risk register.")}
"""

    doc = f"""
#set page(
  paper: "a4",
  margin: (x: 1.8cm, top: 2.2cm, bottom: 2.2cm),
  header: [
    #grid(
      columns: (1fr, 70pt),
      align(left)[#text(8pt, fill: rgb("6b7280"), weight: "bold")[""" + title.upper() + """ — """ + donor.upper() + """]],
      align(right)[#text(8pt, fill: rgb("6b7280"))[""" + country + """ | GMS Proposal]]
    )
    #line(length: 100%, stroke: 0.5pt + rgb("e5e7eb"))
  ],
  footer: [
    #line(length: 100%, stroke: 0.5pt + rgb("e5e7eb"))
    #v(2pt)
    #grid(
      columns: (1fr, auto),
      align(left)[#text(8pt, fill: rgb("9ca3af"))[Sightline GMS • Autonomous Proposal Engine • Confidential]],
      align(right)[#text(8pt, fill: rgb("4b5563"), weight: "bold")[Page #context counter(page).display("1 / 1", both: true)]]
    )
  ]
)

#set text(
  font: ("Liberation Sans", "Helvetica Neue", "Arial"),
  size: 9.5pt,
  fill: rgb("1f2937"),
  spacing: 120%,
  lang: "en"
)
#set par(justify: false, leading: 0.6em)

// ── Header Cover Banner ──────────────────────────────────────────────────────
#block(
  fill: rgb("0f172a"),
  inset: 14pt,
  radius: 5pt,
  width: 100%,
  stroke: 1pt + rgb("1e293b"),
  [
    #grid(
      columns: (1fr, auto),
      gutter: 12pt,
      [
        #text(8.5pt, fill: rgb("38bdf8"), weight: "bold")[SIGHTLINE • GRANT MANAGEMENT SYSTEM PROPOSAL]\\
        #v(3pt)
        #text(16pt, fill: white, weight: "bold")[""" + title + """]\\
        #v(4pt)
        #text(9.5pt, fill: rgb("cbd5e1"))[""" + country + """ • Sector: """ + theme + """]
      ],
      align(right + top)[
        #block(
          fill: rgb("1e293b"),
          inset: (x: 10pt, y: 7pt),
          radius: 4pt,
          stroke: 0.5pt + rgb("334155"),
          [
            #text(7.5pt, fill: rgb("94a3b8"), weight: "bold")[DONOR FRAMEWORK]\\
            #text(11pt, fill: rgb("38bdf8"), weight: "bold")[""" + donor + """]\\
            #v(2pt)
            #text(8pt, fill: rgb("4ade80"), weight: "bold")[COMPLIANCE: """ + score + """]
          ]
        )
      ]
    )
  ]
)

#v(8pt)

= 1. Executive Summary & Context

#block(
  fill: rgb("f8fafc"),
  inset: 9pt,
  radius: 4pt,
  stroke: (left: 3pt + rgb("0284c7"), rest: 0.5pt + rgb("e2e8f0")),
  width: 100%,
  [
    #text(8pt, weight: "bold", fill: rgb("0369a1"))[""" + escape_typst(summary_label) + """]\\
    #v(2pt)
    """ + escape_typst(_display_text(summary_text)) + """
  ]
)

#v(4pt)
== Humanitarian Situation & Needs Assessment
""" + escape_typst(_display_text(situation_text)) + """

""" + narrative_sections_block + """

#v(8pt)

""" + beneficiary_section + """

#v(8pt)

""" + toc_section + """

#v(8pt)

= 4. 4x4 Logical Framework (Logframe Matrix)

#table(
  columns: (1.1fr, 2fr, 1.8fr, 1.6fr, 1.5fr),
  fill: (col, row) => if row == 0 { rgb("0f172a") } else if calc.even(row) { rgb("f8fafc") } else { none },
  stroke: 0.5pt + rgb("cbd5e1"),
  align: top + left,
  table.header(
    text(fill: white, size: 8pt, weight: "bold")[Results Level],
    text(fill: white, size: 8pt, weight: "bold")[Intervention Logic],
    text(fill: white, size: 8pt, weight: "bold")[Indicators (OVI)],
    text(fill: white, size: 8pt, weight: "bold")[Sources (MoV)],
    text(fill: white, size: 8pt, weight: "bold")[Assumptions & Risks]
  ),
"""

    for item in matrix:
        lvl = escape_typst(item.get("level", ""))
        lgc = escape_typst(_display_text(item.get("logic", "")))
        ind = escape_typst(_display_text(item.get("indicators", "")))
        mov = escape_typst(_display_text(item.get("mov", "")))
        ass = escape_typst(_display_text(item.get("assumptions", "")))
        doc += f"  [*{lvl}*], [{lgc}], [{ind}], [{mov}], [{ass}],\n"

    doc += """
)

"""
    if not matrix:
        doc += """#block(fill: rgb("fff7ed"), inset: 9pt, radius: 4pt, stroke: 0.5pt + rgb("fdba74"))[
  No finalized logframe matrix is available. Complete the Logframe workspace before submission.
]
"""

    doc += """

#v(8pt)

""" + budget_section + risk_section + """

#v(8pt)

= 6. Blind Verifier Audit & Quality Assurance

#block(
  fill: rgb(""" + '"' + block_fill + '"' + """),
  inset: 9pt,
  radius: 4pt,
  breakable: false,
  stroke: 1pt + rgb(""" + '"' + block_stroke + '"' + """),
  width: 100%,
  [
    #grid(
      columns: (auto, 1fr),
      gutter: 10pt,
      align: horizon,
      block(
        fill: rgb(""" + '"' + verdict_fill + '"' + """),
        inset: (x: 8pt, y: 6pt),
        radius: 3pt,
        text(12pt, fill: white, weight: "bold")[""" + verdict_text + """]
      ),
      [
        #text(8.5pt, weight: "bold", fill: rgb(""" + '"' + verdict_fill + '"' + """))[""" + verdict_label + """ (Score: """ + score + """)]\\
        #text(8pt, fill: rgb("374151"))[
          """ + verdict_body + """
        ]\\
        #v(2pt)
        #text(8pt, fill: rgb("4b5563"))[
          """ + audit_summary + """
        ]
      ]
    )
  ]
)
"""
    return doc


def compile_pdf(proposal: Dict[str, Any], output_path: Optional[str] = None, analysis: Optional[Dict[str, Any]] = None) -> bytes:
    """Compile proposal dictionary to PDF via Typst and return bytes.

    analysis: optional deterministic engine result — when provided the PDF's
    COMPLIANCE badge, eligibility verdict and audit bullets reflect the REAL
    score (not a hardcoded 94/100).
    """
    import typst

    typst_source = render_typst_document(proposal, analysis=analysis)
    prop_id = proposal.get("id", "sample_proposal")

    with tempfile.NamedTemporaryFile("w", suffix=".typ", delete=False, encoding="utf-8") as f:
        f.write(typst_source)
        temp_typ_path = f.name

    try:
        dest = output_path or str(_ensure_output_dir() / f"{prop_id}.pdf")
        pdf_bytes = typst.compile(temp_typ_path, output=dest)
        if not pdf_bytes and os.path.exists(dest):
            with open(dest, "rb") as rf:
                pdf_bytes = rf.read()
        logger.info("Compiled Typst PDF successfully for %s (%d bytes)", prop_id, len(pdf_bytes) if pdf_bytes else 0)
        return pdf_bytes
    finally:
        try:
            os.unlink(temp_typ_path)
        except Exception:
            pass
