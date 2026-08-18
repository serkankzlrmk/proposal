"""
proposal/blueprints/call_ingest_api.py — Donor Call Ingestion REST API.

Human-in-the-loop contract:
  POST /api/calls/ingest     multipart: PDF + call_id + display_name
      -> extracts text, builds manifest draft, stores in SQLite call_drafts
         table with status "review". Returns {draft, summary, requirements}.
  GET  /api/calls/drafts     list all drafts (status review|published|rejected)
  PUT  /api/calls/drafts/<id>  user edits the manifest draft (JSON body)
  POST /api/calls/drafts/<id>/publish   validate + write donors/<call_id>.yaml
      -> engine picks the manifest up immediately (loader globs *.yaml)
  POST /api/calls/drafts/<id>/reject    mark rejected (no publish)

Never auto-publishes: an approved-by-machine manifest still requires the
user's explicit publish call (vision: human actively co-authors the system).
"""

import io
import json
import logging
import sqlite3
import time
import uuid
from typing import Any, Dict

from flask import Blueprint, jsonify, request

try:
    from config import DB_PATH
    from engine.call_ingest import (
        extract_document_text,
        extract_requirements,
        build_manifest_draft,
        save_manifest,
    )
    from engine.models import DonorManifest
except ImportError:
    from proposal.config import DB_PATH
    from proposal.engine.call_ingest import (
        extract_document_text,
        extract_requirements,
        build_manifest_draft,
        save_manifest,
    )
    from proposal.engine.models import DonorManifest

logger = logging.getLogger(__name__)

call_ingest_bp = Blueprint("call_ingest", __name__, url_prefix="/api/calls")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_table() -> None:
    conn = _conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS call_drafts (
                id            TEXT PRIMARY KEY,
                call_id       TEXT NOT NULL,
                display_name  TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'review',
                manifest_json TEXT NOT NULL DEFAULT '{}',
                summary       TEXT NOT NULL DEFAULT '',
                requirements_json TEXT NOT NULL DEFAULT '[]',
                deadline      TEXT NOT NULL DEFAULT '',
                documents_json TEXT NOT NULL DEFAULT '[]',
                brief         TEXT NOT NULL DEFAULT '',
                created_at    REAL NOT NULL,
                updated_at    REAL NOT NULL
            )
        """)
        # Idempotent migration: add documents_json + brief if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(call_drafts)").fetchall()]
        if "documents_json" not in cols:
            conn.execute("ALTER TABLE call_drafts ADD COLUMN documents_json TEXT NOT NULL DEFAULT '[]'")
        if "brief" not in cols:
            conn.execute("ALTER TABLE call_drafts ADD COLUMN brief TEXT NOT NULL DEFAULT ''")
    conn.close()


def _generate_brief(corpus: str, extracted: Dict[str, Any], draft: Dict[str, Any]) -> str:
    """Human-readable brief: what the call says & wants.

    LLM-generated (with the extraction summary + manifest draft as context);
    falls back to a deterministic structured brief when the LLM is unavailable.
    """
    try:
        from engine.generator import _call_llm

        reqs = "\n".join(f"- {r}" for r in (extracted.get("requirements") or [])[:12])
        gates = ", ".join(draft.get("hard_eligibility_gates") or {}) or "none"
        prompt = f"""
        Write a concise, human-friendly BRIEF of this donor call for a proposal writer.
        Structure it as:
        ## What this call is about
        (2-3 sentences: who is funding, what problem, where, how much, deadline)

        ## What the donor wants
        (bullet list: mandatory requirements, hard gates, budget rules, sections)

        ## What you must do
        (3-5 concrete next steps for the proposal writer)

        Call summary: {extracted.get('summary', '')}
        Requirements: {reqs}
        Hard gates: {gates}
        Deadline: {extracted.get('deadline', 'unknown')}
        Budget cap: {draft.get('overhead_cap_percent', 'n/a')}% overhead | Currency: {draft.get('currency', 'USD')} | Max: {draft.get('budget_max', 'n/a')}
        """
        raw = _call_llm(prompt, temperature=0.2, action="call_brief")
        if raw and raw.strip():
            return raw.strip()[:6000]
    except Exception as e:
        logger.warning("Brief generation failed: %s", e)

    # Deterministic fallback
    reqs = "\n".join(f"- {r}" for r in (extracted.get("requirements") or [])[:10]) or "- (none extracted)"
    return (
        f"## What this call is about\n{extracted.get('summary', 'No summary available.')}\n\n"
        f"## What the donor wants\n{reqs}\n"
        f"- Deadline: {extracted.get('deadline', 'unknown')}\n"
        f"- Overhead cap: {draft.get('overhead_cap_percent', 'n/a')}% | Currency: {draft.get('currency', 'USD')} | Budget max: {draft.get('budget_max', 'n/a')}\n\n"
        f"## What you must do\n- Review the extracted requirements above and publish the manifest to start writing."
    )


@call_ingest_bp.route("/ingest", methods=["POST"])
def ingest_call():
    """Upload donor call documents (pdf/docx/md, MULTIPLE) -> review draft.

    All uploaded files are concatenated (in upload order) into one text
    corpus before requirement extraction — a call often ships as
    guidelines.pdf + application_form.docx + indicators.md.
    """
    _init_table()
    files = request.files.getlist("files")
    if not files:
        # Backward compat: single 'file' field
        single = request.files.get("file")
        if single:
            files = [single]
    if not files:
        return jsonify({"error": "At least one document required (fields 'files' or 'file')"}), 400

    call_id = (request.form.get("call_id") or "").strip() or f"call_{uuid.uuid4().hex[:8]}"
    display_name = (request.form.get("display_name") or "").strip() or f"Custom Donor Call ({call_id})"

    # Concatenate all documents into one corpus
    corpus_parts = []
    accepted = 0
    documents = []
    for f in files:
        filename = (f.filename or "")
        if not filename:
            continue
        data = f.read()
        text = extract_document_text(data, filename)
        if text.strip():
            corpus_parts.append(f"===== {filename} =====\n{text}")
            documents.append({"filename": filename, "chars": len(text)})
            accepted += 1
        else:
            logger.warning("No extractable text from %s (unsupported or empty)", filename)
    if not corpus_parts:
        return jsonify({
            "error": "No extractable text in uploaded documents. Supported: PDF, DOCX, MD/TXT (scanned-image PDFs need OCR — later phase)."
        }), 422

    corpus = "\n\n".join(corpus_parts)
    extracted = extract_requirements(corpus)
    draft = build_manifest_draft(call_id, display_name, extracted)

    # ── Human-readable brief: what the call says & wants (LLM, fallback) ────
    brief = _generate_brief(corpus, extracted, draft)

    draft_id = f"draft_{uuid.uuid4().hex[:10]}"
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "INSERT INTO call_drafts (id, call_id, display_name, status, manifest_json, summary, requirements_json, deadline, documents_json, brief, created_at, updated_at) "
                "VALUES (?, ?, ?, 'review', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    draft_id, call_id, display_name,
                    json.dumps(draft, ensure_ascii=False),
                    extracted.get("summary", ""),
                    json.dumps(extracted.get("requirements") or [], ensure_ascii=False),
                    str(extracted.get("deadline", "")),
                    json.dumps(documents, ensure_ascii=False),
                    brief,
                    time.time(), time.time(),
                ),
            )
    finally:
        conn.close()

    return jsonify({
        "status": "review",
        "draft_id": draft_id,
        "call_id": call_id,
        "documents_accepted": accepted,
        "documents": documents,
        "brief": brief,
        "summary": extracted.get("summary", ""),
        "requirements": extracted.get("requirements") or [],
        "deadline": extracted.get("deadline", ""),
        "manifest_draft": draft,
    }), 201


@call_ingest_bp.route("/published", methods=["GET"])
def list_published():
    """List published donor manifests (for the new-proposal picker)."""
    _init_table()
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, call_id, display_name, deadline, created_at "
            "FROM call_drafts WHERE status = 'published' ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"published": [dict(r) for r in rows]})


@call_ingest_bp.route("/drafts", methods=["GET"])
def list_drafts():
    """List all call ingestion drafts (with documents + brief)."""
    _init_table()
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, call_id, display_name, status, summary, deadline, documents_json, brief, created_at, updated_at "
            "FROM call_drafts ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["documents"] = json.loads(d.pop("documents_json") or "[]")
        except Exception:
            d["documents"] = []
        out.append(d)
    return jsonify({"drafts": out})


@call_ingest_bp.route("/drafts/<draft_id>", methods=["GET"])
def get_draft(draft_id: str):
    _init_table()
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM call_drafts WHERE id = ?", (draft_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "Draft not found"}), 404
    d = dict(row)
    d["manifest"] = json.loads(d.pop("manifest_json") or "{}")
    d["requirements"] = json.loads(d.pop("requirements_json") or "[]")
    try:
        d["documents"] = json.loads(d.pop("documents_json") or "[]")
    except Exception:
        d["documents"] = []
    return jsonify({"draft": d})


@call_ingest_bp.route("/drafts/<draft_id>", methods=["PUT"])
def update_draft(draft_id: str):
    """Human edits the manifest draft before publishing."""
    _init_table()
    data = request.get_json(force=True, silent=True) or {}
    manifest = data.get("manifest")
    if manifest is None:
        return jsonify({"error": "Body must carry {'manifest': {...}}"}), 400

    # Validate shape early (before store) — catches broken user edits
    try:
        DonorManifest(**manifest)
    except Exception as e:
        return jsonify({"error": f"Manifest validation failed: {e}"}), 422

    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE call_drafts SET manifest_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(manifest, ensure_ascii=False), time.time(), draft_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return jsonify({"error": "Draft not found"}), 404
    finally:
        conn.close()
    return jsonify({"status": "updated", "draft_id": draft_id})


@call_ingest_bp.route("/drafts/<draft_id>/publish", methods=["POST"])
def publish_draft(draft_id: str):
    """Human-approved publish: write donors/<call_id>.yaml, engine picks it up."""
    _init_table()
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM call_drafts WHERE id = ?", (draft_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "Draft not found"}), 404

    d = dict(row)
    manifest = json.loads(d["manifest_json"] or "{}")

    try:
        path = save_manifest(d["call_id"], manifest)
    except Exception as e:
        return jsonify({"error": f"Manifest validation failed: {e}"}), 422

    conn = _conn()
    try:
        conn.execute(
            "UPDATE call_drafts SET status = 'published', updated_at = ? WHERE id = ?",
            (time.time(), draft_id),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "status": "published",
        "draft_id": draft_id,
        "manifest_path": str(path),
        "donor_id": d["call_id"],
        "note": "Engine now scores proposals against this manifest (loader globs donors/*.yaml).",
    })


@call_ingest_bp.route("/drafts/<draft_id>/reject", methods=["POST"])
def reject_draft(draft_id: str):
    """Mark a draft rejected — no manifest published."""
    _init_table()
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE call_drafts SET status = 'rejected', updated_at = ? WHERE id = ?",
            (time.time(), draft_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return jsonify({"error": "Draft not found"}), 404
    finally:
        conn.close()
    return jsonify({"status": "rejected", "draft_id": draft_id})
