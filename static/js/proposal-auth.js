/**
 * proposal-auth.js — standalone Firebase auth for Proposal Studio.
 *
 * Proposal runs standalone at /proposal (same origin as Sightline — they
 * share the same Firebase project). This module:
 *   1. Initializes Firebase with window.FIREBASE_CONFIG (firebase-config.js).
 *   2. Persists the ID token to localStorage['id_token'] (same key Sightline
 *      uses, so a user who signed in on Sightline is already authed here).
 *   3. Provides window.getIdToken(), window.refreshIdToken() and
 *      window.signInWithGooglePopup() — used by app.js checkAuth() and the
 *      auth overlay's "Sign in with Google" button.
 */

(function () {
  const CONFIG = window.FIREBASE_CONFIG;

  if (!CONFIG) {
    console.warn('proposal-auth: FIREBASE_CONFIG missing — auth disabled');
    return;
  }

  // Firebase SDK (compat build — same CDN as Sightline).
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error('Failed to load ' + src));
      document.head.appendChild(s);
    });
  }

  async function init() {
    try {
      await loadScript(
        'https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js'
      );
      await loadScript(
        'https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js'
      );
      window.firebase.initializeApp(CONFIG);
    } catch (e) {
      console.error('proposal-auth: Firebase init failed', e);
      return;
    }
    const auth = window.firebase.auth();

    // ── Token persistence (shared key with Sightline) ─────────────────────
    auth.onAuthStateChanged(async (user) => {
      if (user) {
        try {
          const tok = await user.getIdToken(true);
          localStorage.setItem('id_token', tok);
          window.__idToken = tok;
          window.__currentUser = {
            uid: user.uid,
            email: user.email,
            name: user.displayName || '',
          };
        } catch (e) {
          console.warn('proposal-auth: token refresh failed', e);
        }
      } else {
        localStorage.removeItem('id_token');
        window.__idToken = '';
      }
      window.dispatchEvent(new Event('auth-state-changed'));
    });

    // ── API surface used by app.js ────────────────────────────────────────
    window.getIdToken = function () {
      const cur = auth.currentUser;
      if (!cur) return localStorage.getItem('id_token') || '';
      return cur.getIdToken(false).then((t) => {
        localStorage.setItem('id_token', t);
        window.__idToken = t;
        return t;
      });
    };

    window.refreshIdToken = async function () {
      const cur = auth.currentUser;
      if (!cur) {
        // Not signed in here — rely on Sightline's persisted token, if any.
        return localStorage.getItem('id_token') || '';
      }
      const t = await cur.getIdToken(true);
      localStorage.setItem('id_token', t);
      window.__idToken = t;
      return t;
    };

    window.signInWithGooglePopup = async function () {
      const provider = new window.firebase.auth.GoogleAuthProvider();
      const result = await auth.signInWithPopup(provider);
      const tok = await result.user.getIdToken(true);
      localStorage.setItem('id_token', tok);
      window.__idToken = tok;
      window.__currentUser = {
        uid: result.user.uid,
        email: result.user.email,
        name: result.user.displayName || '',
      };
      window.dispatchEvent(new Event('auth-signed-in'));
      return tok;
    };

    window.__firebaseAuthReady = true;
  }

  init();
})();