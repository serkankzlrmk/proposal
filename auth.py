"""
proposal/auth.py — Firebase token verification and role-based access control.

Shares the same Firebase project as Sightline (sightlinehumanitarian.com).
Verifies Bearer tokens via Firebase Admin SDK.
Stores user_id as Firebase UID — enables per-user proposal screens.

Role hierarchy: admin > premium > free
  - free:    Read-only access (view proposals, donor templates)
  - premium: Full access (create, edit, generate, export)
  - admin:   Premium + call ingest, donor rule management, user admin

Architecture:
  - Same Firebase project as Sightline → same user pool, same UIDs
  - Users sign in via Sightline (same domain → shared localStorage)
  - Proposal JS reads idToken from localStorage → sends as Bearer
  - Backend verifies token → extracts UID as user_id
  - Future: per-user proposal dashboards (like Sightline's chat system)
"""

import functools
import hmac
import logging
import os
import threading
import time

from flask import g, jsonify, request

_log = logging.getLogger(__name__)

# ── Role hierarchy ────────────────────────────────────────────────────────────
ROLE_HIERARCHY = ["free", "premium", "admin"]


def _get_app_config():
    from config import config

    return config


# ── Firebase Admin SDK (lazy init) ───────────────────────────────────────────

_firebase_lock = threading.Lock()
_fb_app = None


def _firebase_app():
    """Lazy-init Firebase Admin SDK. Thread-safe."""
    global _fb_app
    if _fb_app is not None:
        return _fb_app

    with _firebase_lock:
        if _fb_app is not None:
            return _fb_app

        _env_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
        _sa_paths = [
            _env_path,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "firebase-service-account.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "firebase-service-account.json"),
            "/opt/sightline/data/firebase-service-account.json",
            "/app/data/firebase-service-account.json",
        ]
        sa_path = next((p for p in _sa_paths if p and os.path.exists(p)), None)
        if not sa_path:
            _log.warning("No Firebase service account found — auth will fall back to dev mode")
            return None

        import firebase_admin
        from firebase_admin import credentials

        try:
            _fb_app = firebase_admin.get_app()
        except ValueError:
            cred = credentials.Certificate(sa_path)
            _fb_app = firebase_admin.initialize_app(cred)
        _log.info("Firebase Admin SDK initialized from %s", sa_path)
        return _fb_app


# ── Admin UIDs from env ──────────────────────────────────────────────────────

def _admins() -> set:
    """Return set of admin UIDs from env."""
    raw = os.getenv("ADMIN_UIDS", "").strip()
    if not raw:
        return set()
    return {uid.strip() for uid in raw.split(",") if uid.strip()}


# ── Dev mode bypass ──────────────────────────────────────────────────────────

def _dev_mode() -> bool:
    """Return True if running in dev mode with auth bypass enabled.

    Auth is bypassed when DESKTOP_MODE=true OR DEV_AUTH_BYPASS=true.
    Only allowed on loopback interfaces for safety.
    """
    bypass = os.getenv("DESKTOP_MODE", "").lower() == "true" or os.getenv("DEV_AUTH_BYPASS", "").lower() == "true"
    if not bypass:
        return False

    # Safety: only allow bypass on loopback
    host = os.getenv("SERVER_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        _log.warning(
            "DESKTOP_MODE/DEV_AUTH_BYPASS=true but SERVER_HOST=%s is not loopback — "
            "dev bypass disabled for safety.",
            host,
        )
        return False
    return True


# ── Token verification ───────────────────────────────────────────────────────

def verify_firebase_token(token: str) -> dict:
    """Verify a Firebase ID token and return decoded claims with role."""
    app = _firebase_app()
    if app is None:
        raise ValueError("Firebase not configured — cannot verify tokens.")

    from firebase_admin import auth as firebase_auth

    try:
        decoded = firebase_auth.verify_id_token(token, check_revoked=False)
        return decoded
    except firebase_auth.InvalidIdTokenError as exc:
        err_msg = str(exc)
        if "too early" in err_msg.lower():
            _log.warning("Clock skew detected — retrying token verify after short delay")
            time.sleep(2)
            decoded = firebase_auth.verify_id_token(token, check_revoked=False)
            return decoded
        raise ValueError("Invalid or expired Firebase token.")
    except Exception as exc:
        _log.warning("Firebase token verification failed: %s", exc)
        raise ValueError("Invalid or expired Firebase token.")


def _resolve_role(decoded: dict) -> str:
    """Resolve user role from Firebase custom claims + ADMIN_UIDS fallback."""
    # Custom claims role (set by Sightline admin panel)
    claims = decoded.get("claims", {}) or {}
    role = claims.get("role", "")
    if role in ROLE_HIERARCHY:
        return role

    # ADMIN_UIDS env fallback
    uid = decoded.get("uid", "")
    if uid in _admins():
        return "admin"

    return "free"


# ── Helper functions ─────────────────────────────────────────────────────────

def current_uid() -> str:
    """Return the authenticated user's UID from g.current_user, or ''."""
    user = getattr(g, "current_user", None)
    if user:
        return str(user.get("uid", ""))
    return ""


def current_role() -> str:
    """Return the authenticated user's role from g.current_user, or 'free'."""
    user = getattr(g, "current_user", None)
    if user:
        return user.get("role", "free")
    return "free"


def _api_key() -> str:
    """Return SERVER_API_KEY env var if set, else empty string."""
    return os.getenv("SERVER_API_KEY", "").strip()


# ── Decorators ───────────────────────────────────────────────────────────────

def require_auth(f):
    """Require authentication via Firebase Bearer token OR legacy API key.

    - If SERVER_API_KEY is set: require X-API-Key header matching it.
    - Otherwise: require Authorization: Bearer <idToken>.
    - Dev mode: bypassed as admin (DESKTOP_MODE/DEV_AUTH_BYPASS + loopback).

    Sets g.current_user = decoded Firebase claims with 'role' field.
    """

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # Dev mode bypass
        if _dev_mode():
            g.current_user = {
                "uid": "dev-local",
                "email": "dev@localhost",
                "name": "Dev User",
                "admin": True,
                "role": "premium",
            }
            return f(*args, **kwargs)

        api_key = _api_key()
        if api_key:
            provided = request.headers.get("X-API-Key", "")
            if not provided or not hmac.compare_digest(provided, api_key):
                return jsonify({"error": "Invalid API key"}), 403
            g.current_user = {"uid": "api-key", "role": "admin", "admin": True}
            return f(*args, **kwargs)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing Authorization: Bearer <token>"}), 401
        token = auth_header[len("Bearer "):].strip()
        if not token:
            return jsonify({"error": "Empty token"}), 401
        try:
            decoded = verify_firebase_token(token)
            decoded["role"] = _resolve_role(decoded)
            g.current_user = decoded
        except ValueError as exc:
            _log.warning("require_auth: token verify failed for %s: %s", request.path, exc)
            return jsonify({"error": "Authentication failed."}), 401

        return f(*args, **kwargs)

    return decorated


def optional_auth(f):
    """Optional authentication — authenticates if a valid token is present,
    but proceeds as anonymous (g.current_user = None) if not.

    Used for: public endpoints like donor templates, proposal listing.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # Dev mode bypass
        if _dev_mode():
            g.current_user = {
                "uid": "dev-local",
                "email": "dev@localhost",
                "name": "Dev User",
                "admin": True,
                "role": "premium",
            }
            return f(*args, **kwargs)

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):].strip()
            if token:
                try:
                    decoded = verify_firebase_token(token)
                    decoded["role"] = _resolve_role(decoded)
                    g.current_user = decoded
                except ValueError:
                    g.current_user = None
            else:
                g.current_user = None
        else:
            g.current_user = None

        return f(*args, **kwargs)

    return decorated


def require_role(minimum: str):
    """Decorator factory: require minimum role level.

    Usage: @require_role("premium")
    Role hierarchy: free < premium < admin
    """

    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if not user:
                return jsonify({"error": "Authentication required."}), 401

            user_role = user.get("role", "free")
            min_idx = ROLE_HIERARCHY.index(minimum) if minimum in ROLE_HIERARCHY else 999
            user_idx = ROLE_HIERARCHY.index(user_role) if user_role in ROLE_HIERARCHY else 0

            if user_idx < min_idx:
                return jsonify({"error": f"Requires {minimum} role or above.", "required": minimum, "current": user_role}), 403

            return f(*args, **kwargs)

        return decorated

    return decorator