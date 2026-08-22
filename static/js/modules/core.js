/** Shared browser primitives. This module has no proposal-domain knowledge. */

// ── Auth: Bearer token from Sightline's shared localStorage ──────────────────
// Same domain (sightlinehumanitarian.com) → same localStorage.
// Sightline's auth.js stores the Firebase ID token as window.__idToken.
// Proposal reads it and injects into every API call as Authorization: Bearer.

function _getIdToken() {
  // 1. Direct — Sightline's auth.js sets window.__idToken after sign-in
  if (window.__idToken) return window.__idToken;
  // 2. localStorage — Sightline persists the token as 'id_token'
  const stored = localStorage.getItem('id_token');
  if (stored) return stored;
  return '';
}

export async function api(url, options = {}) {
  const token = _getIdToken();
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(url, { ...options, headers });

  if (!res.ok) {
    // Auth failure: show overlay instead of hard redirect
    if (res.status === 401) {
      const overlay = document.getElementById('auth-overlay');
      if (overlay) overlay.classList.remove('hidden');
      throw new Error('Authentication required.');
    }
    const err = await res.json().catch(() => ({ error: res.statusText }));
    const error = new Error(err.error || `HTTP ${res.status}`);
    error.code = err.code || '';
    error.status = res.status;
    error.details = err;
    throw error;
  }

  return res.json();
}

export function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

export function notify(message, tone = 'info', duration = 4200) {
  const region = document.getElementById('toastRegion');
  if (!region) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${tone}`;
  toast.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
  toast.innerHTML = `
    <span class="toast-mark" aria-hidden="true">${tone === 'danger' ? '!' : tone === 'success' ? '✓' : '•'}</span>
    <span>${esc(message)}</span>
    <button type="button" aria-label="Dismiss notification">×</button>
  `;

  const dismiss = () => {
    toast.classList.add('toast-leaving');
    setTimeout(() => toast.remove(), 180);
  };
  toast.querySelector('button').addEventListener('click', dismiss);
  region.appendChild(toast);
  setTimeout(dismiss, duration);
}

let activitySequence = 0;

function activityRegion() {
  let region = document.getElementById('activityRegion');
  if (region) return region;
  region = document.createElement('div');
  region.id = 'activityRegion';
  region.className = 'activity-region';
  region.setAttribute('aria-live', 'polite');
  document.body.appendChild(region);
  return region;
}

export function beginActivity({ title, detail = 'Working in the background…' }) {
  const id = `activity-${Date.now()}-${activitySequence += 1}`;
  const card = document.createElement('section');
  card.id = id;
  card.className = 'activity-card is-running';
  card.setAttribute('role', 'status');
  card.innerHTML = `
    <span class="activity-orbit" aria-hidden="true"><i></i></span>
    <span class="activity-copy">
      <strong>${esc(title)}</strong>
      <small>${esc(detail)}</small>
    </span>
    <span class="activity-time">0s</span>
    <span class="activity-progress" aria-hidden="true"><i></i></span>
  `;
  activityRegion().appendChild(card);

  const startedAt = Date.now();
  const timer = window.setInterval(() => {
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    const time = card.querySelector('.activity-time');
    if (time) time.textContent = `${seconds}s`;
  }, 1000);

  const update = message => {
    const detailNode = card.querySelector('small');
    if (detailNode) detailNode.textContent = message;
  };
  const settle = (status, message, keepFor) => {
    window.clearInterval(timer);
    card.classList.remove('is-running');
    card.classList.add(`is-${status}`);
    update(message);
    const time = card.querySelector('.activity-time');
    if (time) time.textContent = status === 'success' ? 'Done' : 'Check';
    window.setTimeout(() => {
      card.classList.add('activity-leaving');
      window.setTimeout(() => card.remove(), 220);
    }, keepFor);
  };

  return {
    id,
    update,
    success: message => settle('success', message || 'Completed successfully.', 2200),
    fail: message => settle('danger', message || 'The action could not be completed.', 5200),
  };
}

export function setButtonBusy(button, busy) {
  if (!button) return;
  button.disabled = Boolean(busy);
  if (busy) button.setAttribute('aria-busy', 'true');
  else button.removeAttribute('aria-busy');
}

export function confirmAction({ title, message, confirmLabel = 'Confirm', danger = false }) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay confirmation-overlay';
    overlay.innerHTML = `
      <div class="modal-card confirmation-card" role="alertdialog" aria-modal="true" aria-labelledby="confirmTitle" aria-describedby="confirmMessage">
        <span class="confirmation-icon ${danger ? 'danger' : ''}" aria-hidden="true">${danger ? '!' : '✓'}</span>
        <h2 id="confirmTitle">${esc(title)}</h2>
        <p id="confirmMessage">${esc(message)}</p>
        <div class="confirmation-actions">
          <button type="button" class="btn" data-cancel>Cancel</button>
          <button type="button" class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-confirm>${esc(confirmLabel)}</button>
        </div>
      </div>
    `;

    const close = result => {
      overlay.remove();
      resolve(result);
    };
    overlay.querySelector('[data-cancel]').addEventListener('click', () => close(false));
    overlay.querySelector('[data-confirm]').addEventListener('click', () => close(true));
    overlay.addEventListener('click', event => {
      if (event.target === overlay) close(false);
    });
    overlay.addEventListener('keydown', event => {
      if (event.key === 'Escape') close(false);
    });
    document.body.appendChild(overlay);
    overlay.querySelector('[data-cancel]').focus();
  });
}
