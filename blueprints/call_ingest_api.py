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
                created_at    REAL NOT NULL,
                updated_at    REAL NOT NULL
            )
        """)
    conn.close()


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
    for f in files:
        filename = (f.filename or "")
        if not filename:
            continue
        data = f.read()
        text = extract_document_text(data, filename)
        if text.strip():
            corpus_parts.append(f"===== {filename} =====\n{text}")
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

    draft_id = f"draft_{uuid.uuid4().hex[:10]}"
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "INSERT INTO call_drafts (id, call_id, display_name, status, manifest_json, summary, requirements_json, deadline, created_at, updated_at) "
                "VALUES (?, ?, ?, 'review', ?, ?, ?, ?, ?, ?)",
                (
                    draft_id, call_id, display_name,
                    json.dumps(draft, ensure_ascii=False),
                    extracted.get("summary", ""),
                    json.dumps(extracted.get("requirements") or [], ensure_ascii=False),
                    str(extracted.get("deadline", "")),
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
    """List all call ingestion drafts."""
    _init_table()
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, call_id, display_name, status, summary, deadline, created_at, updated_at "
            "FROM call_drafts ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"drafts": [dict(r) for r in rows]})


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
