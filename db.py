"""
proposal/db.py — SQLite database manager and CRUD operations for Proposal Pipeline.
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    from config import DB_PATH
except ImportError:
    from proposal.config import DB_PATH

logger = logging.getLogger(__name__)
_db_lock = threading.Lock()


class ProposalLockedError(Exception):
    """Raised when a caller attempts to modify a locked step's content."""


def get_db_connection() -> sqlite3.Connection:
    """Return a thread-safe connection to proposal.db with WAL mode."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    """Initialize database schema with idempotent table creation."""
    conn = get_db_connection()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS proposals (
                id            TEXT PRIMARY KEY,
                user_id       TEXT NOT NULL DEFAULT 'default_user',
                title         TEXT NOT NULL DEFAULT 'Untitled Proposal',
                country       TEXT NOT NULL DEFAULT '',
                donor         TEXT NOT NULL DEFAULT 'OCHA_CBPF',
                theme         TEXT NOT NULL DEFAULT 'Multi-sector',
                status        TEXT NOT NULL DEFAULT 'draft',
                step          INTEGER NOT NULL DEFAULT 1,
                context_data  TEXT NOT NULL DEFAULT '{}',
                toc_data      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"assumptions":[]}',
                logframe_data TEXT NOT NULL DEFAULT '{"matrix":[]}',
                narrative_data TEXT NOT NULL DEFAULT '{}',
                budget_data   TEXT NOT NULL DEFAULT '{"items":[],"total":0}',
                review_data   TEXT NOT NULL DEFAULT '{}',
                references_data TEXT NOT NULL DEFAULT '[]',
                locked_steps  TEXT NOT NULL DEFAULT '[]',
                created_at    REAL NOT NULL,
                updated_at    REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_prop_user ON proposals(user_id);
            CREATE INDEX IF NOT EXISTS idx_prop_updated ON proposals(updated_at);

            CREATE TABLE IF NOT EXISTS proposal_reviews (
                id            TEXT PRIMARY KEY,
                proposal_id   TEXT NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
                turn_index    INTEGER NOT NULL DEFAULT 1,
                verdict       TEXT NOT NULL DEFAULT 'pending',
                score         REAL NOT NULL DEFAULT 0.0,
                issues_json   TEXT NOT NULL DEFAULT '[]',
                created_at    REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rev_prop ON proposal_reviews(proposal_id);
        """)
    conn.close()
    # Idempotent migrations for existing databases
    conn = get_db_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(proposals)").fetchall()]
    if "references_data" not in cols:
        conn.execute("ALTER TABLE proposals ADD COLUMN references_data TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
        logger.info("Migration: added references_data column")
    if "locked_steps" not in cols:
        conn.execute("ALTER TABLE proposals ADD COLUMN locked_steps TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
        logger.info("Migration: added locked_steps column")
    conn.close()
    logger.info("Proposal SQLite DB initialized at %s", DB_PATH)


def _row_to_proposal_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert SQLite row to Python dict with JSON deserialization."""
    d = dict(row)
    for json_col in ("context_data", "toc_data", "logframe_data", "narrative_data", "budget_data", "review_data"):
        if json_col in d and isinstance(d[json_col], str):
            try:
                d[json_col] = json.loads(d[json_col])
            except Exception:
                d[json_col] = {}
    if "references_data" in d and isinstance(d["references_data"], str):
        try:
            d["references"] = json.loads(d["references_data"])
        except Exception:
            d["references"] = []
        del d["references_data"]
    if "locked_steps" in d and isinstance(d["locked_steps"], str):
        try:
            d["locked_steps"] = json.loads(d["locked_steps"])
        except Exception:
            d["locked_steps"] = []
    return d


def list_proposals(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all proposals, ordered by last updated."""
    init_db()
    conn = get_db_connection()
    try:
        if user_id:
            rows = conn.execute(
                "SELECT id, user_id, title, country, donor, theme, status, step, created_at, updated_at "
                "FROM proposals WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, user_id, title, country, donor, theme, status, step, created_at, updated_at "
                "FROM proposals ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_proposal(proposal_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve full proposal record by ID."""
    init_db()
    conn = get_db_connection()
    try:
        if user_id:
            row = conn.execute("SELECT * FROM proposals WHERE id = ? AND user_id = ?", (proposal_id, user_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if not row:
            return None
        return _row_to_proposal_dict(row)
    finally:
        conn.close()


def create_proposal(
    user_id: str = "default_user",
    title: str = "Untitled Proposal",
    country: str = "",
    donor: str = "OCHA_CBPF",
    theme: str = "Multi-sector",
    context_data: Optional[Dict] = None,
    toc_data: Optional[Dict] = None,
    logframe_data: Optional[Dict] = None,
    narrative_data: Optional[Dict] = None,
    budget_data: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Create a new proposal in the database."""
    init_db()
    prop_id = f"prop_{uuid.uuid4().hex[:10]}"
    now = time.time()

    ctx_str = json.dumps(context_data or {})
    toc_str = json.dumps(toc_data or {"nodes": [], "edges": [], "assumptions": []})
    logframe_str = json.dumps(logframe_data or {"matrix": []})
    narrative_str = json.dumps(narrative_data or {})
    budget_str = json.dumps(budget_data or {"items": [], "total": 0})
    review_str = json.dumps({})

    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO proposals (
                    id, user_id, title, country, donor, theme, status, step,
                    context_data, toc_data, logframe_data, narrative_data, budget_data, review_data,
                    references_data, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prop_id,
                    user_id,
                    title,
                    country,
                    donor,
                    theme,
                    ctx_str,
                    toc_str,
                    logframe_str,
                    narrative_str,
                    budget_str,
                    review_str,
                    "[]",
                    now,
                    now,
                ),
            )
        return get_proposal(prop_id, user_id)
    finally:
        conn.close()


def update_proposal(proposal_id: str, fields: Dict[str, Any], user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Update fields of an existing proposal with automatic timestamping.

    FSM guard (Master Spec invariant #1): locked steps are immutable. If the
    incoming payload attempts to CHANGE the content of a locked step, a
    ProposalLockedError is raised (API layer maps it to 409 Conflict).
    Writes that keep the locked value byte-identical are allowed.
    """
    init_db()
    allowed_cols = {
        "title",
        "country",
        "donor",
        "theme",
        "status",
        "step",
        "context_data",
        "toc_data",
        "logframe_data",
        "narrative_data",
        "budget_data",
        "review_data",
        "references",
        "locked_steps",
    }
    updates = {}
    for k, v in fields.items():
        if k in allowed_cols:
            if k in ("context_data", "toc_data", "logframe_data", "narrative_data", "budget_data", "review_data"):
                updates[k] = json.dumps(v) if not isinstance(v, str) else v
            elif k == "references":
                updates["references_data"] = json.dumps(v) if not isinstance(v, str) else v
            elif k == "locked_steps":
                updates["locked_steps"] = json.dumps(v) if not isinstance(v, str) else v
            else:
                updates[k] = v

    if not updates:
        return get_proposal(proposal_id, user_id)

    # ── FSM lock guard: step content is immutable once locked ─────────────
    current = get_proposal(proposal_id, user_id)
    if current:
        locked = set(current.get("locked_steps") or [])
        if locked:
            step_to_cols = {
                1: ["context_data"],
                2: ["toc_data"],
                3: ["logframe_data"],
                4: ["narrative_data"],
                5: ["budget_data", "review_data"],
            }
            for step_num, cols in step_to_cols.items():
                if step_num not in locked:
                    continue
                for col in cols:
                    if col not in updates:
                        continue
                    try:
                        incoming = json.loads(updates[col]) if isinstance(updates[col], str) else updates[col]
                    except Exception:
                        continue
                    existing = current.get(col)
                    if isinstance(existing, str):
                        try:
                            existing = json.loads(existing)
                        except Exception:
                            pass
                    if incoming != existing:
                        raise ProposalLockedError(
                            f"Step {step_num} is locked; content is immutable. "
                            f"Rejecting write to '{col}'."
                        )
                    # Identical value -> drop from updates (no-op write)
                    updates.pop(col, None)

    if not updates:
        return get_proposal(proposal_id, user_id)

    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{col} = ?" for col in updates.keys())
    params = list(updates.values())
    params.append(proposal_id)

    conn = get_db_connection()
    try:
        with conn:
            if user_id:
                params.append(user_id)
                conn.execute(f"UPDATE proposals SET {set_clause} WHERE id = ? AND user_id = ?", params)
            else:
                conn.execute(f"UPDATE proposals SET {set_clause} WHERE id = ?", params)
        return get_proposal(proposal_id, user_id)
    finally:
        conn.close()


def lock_step(proposal_id: str, step_num: int, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Lock a step (draft -> analyzed -> locked). Frozen snapshot contract.

    Returns the updated proposal, or None if the proposal is not found.
    """
    init_db()
    prop = get_proposal(proposal_id, user_id)
    if not prop:
        return None

    locked = set(prop.get("locked_steps") or [])
    if step_num not in locked:
        locked.add(step_num)
        update_proposal(proposal_id, {"locked_steps": sorted(locked)}, user_id=user_id)
    return get_proposal(proposal_id, user_id)


def delete_proposal(proposal_id: str, user_id: Optional[str] = None) -> bool:
    """Delete a proposal and its cascade review history."""
    init_db()
    conn = get_db_connection()
    try:
        with conn:
            if user_id:
                cur = conn.execute("DELETE FROM proposals WHERE id = ? AND user_id = ?", (proposal_id, user_id))
            else:
                cur = conn.execute("DELETE FROM proposals WHERE id = ?", (proposal_id,))
            deleted = cur.rowcount > 0
            if deleted:
                conn.execute("DELETE FROM proposal_reviews WHERE proposal_id = ?", (proposal_id,))
        return deleted
    finally:
        conn.close()


def save_review(proposal_id: str, verdict: str, score: float, issues: List[Dict]) -> str:
    """Record an audit review pass."""
    init_db()
    rev_id = f"rev_{uuid.uuid4().hex[:10]}"
    now = time.time()
    conn = get_db_connection()
    try:
        with conn:
            count_row = conn.execute(
                "SELECT COUNT(*) as c FROM proposal_reviews WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
            turn_index = (count_row["c"] if count_row else 0) + 1
            conn.execute(
                "INSERT INTO proposal_reviews (id, proposal_id, turn_index, verdict, score, issues_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rev_id, proposal_id, turn_index, verdict, score, json.dumps(issues), now),
            )
            # Update proposal review_data column
            rev_summary = {"verdict": verdict, "score": score, "issues": issues, "last_reviewed": now}
            conn.execute(
                "UPDATE proposals SET review_data = ?, status = ?, updated_at = ? WHERE id = ?",
                (json.dumps(rev_summary), "verified" if verdict == "pass" else "in_review", now, proposal_id),
            )
        return rev_id
    finally:
        conn.close()


def get_reviews(proposal_id: str) -> List[Dict[str, Any]]:
    """Retrieve audit history for a proposal."""
    init_db()
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM proposal_reviews WHERE proposal_id = ? ORDER BY turn_index ASC", (proposal_id,)
        ).fetchall()
        res = []
        for r in rows:
            d = dict(r)
            try:
                d["issues"] = json.loads(d["issues_json"])
            except Exception:
                d["issues"] = []
            res.append(d)
        return res
    finally:
        conn.close()
