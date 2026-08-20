import { esc } from './core.js';

function label(value) {
  return String(value || '')
    .split('_')
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function valueOrDash(value) {
  return value === null || value === undefined || value === '' ? '—' : value;
}

function formatMoney(value, currency) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return 'Not specified';
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: currency || 'USD', maximumFractionDigits: 0,
  }).format(numeric);
}

function briefMarkup(brief) {
  if (!brief) return '<p class="intelligence-summary">No additional brief is available.</p>';
  const sections = [];
  let current = { title: 'Call brief', lines: [] };
  String(brief).split('\n').forEach(rawLine => {
    const line = rawLine.trim();
    if (line.startsWith('## ')) {
      if (current.lines.length) sections.push(current);
      current = { title: line.slice(3), lines: [] };
    } else if (line) {
      current.lines.push(line);
    }
  });
  if (current.lines.length || current.title) sections.push(current);

  return sections.map(section => {
    const bullets = section.lines.filter(line => line.startsWith('- '));
    const paragraphs = section.lines.filter(line => !line.startsWith('- '));
    return `<section class="brief-section">
      <h3>${esc(section.title)}</h3>
      ${paragraphs.map(text => `<p>${esc(text)}</p>`).join('')}
      ${bullets.length ? `<ul>${bullets.map(text => `<li>${esc(text.slice(2))}</li>`).join('')}</ul>` : ''}
    </section>`;
  }).join('');
}

export function renderCallList(container, drafts, activeId, handlers) {
  if (!drafts.length) {
    container.innerHTML = '<div class="intelligence-empty" style="min-height:220px;"><span class="intelligence-empty-mark">↥</span><h3>No calls uploaded</h3><p>Upload source documents to create donor intelligence.</p></div>';
    return;
  }

  container.innerHTML = drafts.map(draft => `
    <div class="call-library-entry">
      <button type="button" class="call-library-item ${draft.id === activeId ? 'active' : ''}" data-view-call="${esc(draft.id)}">
        <div class="call-library-item-head">
          <h3>${esc(draft.display_name || draft.call_id)}</h3>
          <span class="call-status ${esc(draft.status || 'review')}">${esc(draft.status || 'review')}</span>
        </div>
        <p>${esc(draft.deadline || 'No deadline extracted')} · ${(draft.documents || []).length} source document${(draft.documents || []).length === 1 ? '' : 's'}</p>
      </button>
      <button type="button" class="call-delete-button" data-delete-call="${esc(draft.id)}" aria-label="Delete ${esc(draft.display_name || 'call')}" title="Delete call">×</button>
    </div>
  `).join('');

  container.querySelectorAll('[data-view-call]').forEach(button => {
    button.addEventListener('click', () => handlers.onSelect(button.dataset.viewCall));
  });
  container.querySelectorAll('[data-delete-call]').forEach(button => {
    button.addEventListener('click', () => handlers.onDelete(button.dataset.deleteCall));
  });
}

export function renderCallDetail(container, draft, handlers) {
  const manifest = draft.manifest || {};
  const requirements = draft.requirements || manifest.meta?.requirements || [];
  const sections = manifest.sections?.mandatory || [];
  const keywords = manifest.mandatory_keywords || [];
  const gates = Object.entries(manifest.hard_eligibility_gates || {});
  const weights = Object.entries(manifest.scoring_weights || {});
  const limits = Object.entries(manifest.max_char_limits || {});
  const documents = draft.documents || [];
  const dimensions = manifest.smart_indicators?.required_dimensions || [];

  const workflowActions = draft.status === 'review'
    ? `<button type="button" class="btn" data-reject-call>Reject</button><button type="button" class="btn btn-primary" data-publish-call>Approve donor rules</button>`
    : draft.status === 'published'
      ? '<button type="button" class="btn btn-primary" data-start-proposal>Start proposal from this call →</button>'
      : '';
  const actions = `<button type="button" class="btn" data-delete-call>Delete call</button>${workflowActions}`;

  container.innerHTML = `
    <div class="intelligence-head">
      <div>
        <div class="section-kicker"><span></span> ${draft.status === 'published' ? 'Approved source of truth' : 'Human review required'}</div>
        <h1>${esc(draft.display_name || draft.call_id || 'Donor call')}</h1>
        <div class="intelligence-head-meta"><span>${esc(draft.call_id || manifest.donor_id || '')}</span><span>·</span><span class="call-status ${esc(draft.status || 'review')}">${esc(draft.status || 'review')}</span></div>
      </div>
      <div class="intelligence-actions">${actions}</div>
    </div>

    <div class="intelligence-metrics">
      <div class="intelligence-metric"><span>Deadline</span><strong>${esc(valueOrDash(draft.deadline || manifest.deadline))}</strong></div>
      <div class="intelligence-metric"><span>Maximum budget</span><strong>${esc(formatMoney(manifest.budget_max, manifest.currency))}</strong></div>
      <div class="intelligence-metric"><span>Duration</span><strong>${manifest.max_duration_months ? `${esc(manifest.max_duration_months)} months` : 'Not specified'}</strong></div>
      <div class="intelligence-metric"><span>Overhead cap</span><strong>${esc(valueOrDash(manifest.overhead_cap_percent))}%</strong></div>
    </div>

    <div class="intelligence-grid">
      <div class="intelligence-column">
        <section class="intelligence-card">
          <div class="intelligence-card-title"><h2>Call summary</h2><span>What it is about</span></div>
          <p class="intelligence-summary">${esc(draft.summary || 'No summary was extracted.')}</p>
        </section>

        <section class="intelligence-card">
          <div class="intelligence-card-title"><h2>Writer brief</h2><span>What the donor wants</span></div>
          ${briefMarkup(draft.brief)}
        </section>

        <section class="intelligence-card">
          <div class="intelligence-card-title"><h2>Extracted requirements</h2><span>${requirements.length} requirements</span></div>
          <div class="rule-list">
            ${requirements.length ? requirements.map((requirement, index) => `<div class="rule-item"><span>${esc(requirement)}</span><strong>${String(index + 1).padStart(2, '0')}</strong></div>`).join('') : '<p class="intelligence-summary">No explicit requirements were extracted.</p>'}
          </div>
        </section>
      </div>

      <div class="intelligence-column">
        <section class="intelligence-card">
          <div class="intelligence-card-title"><h2>Eligibility gates</h2><span>Submission blockers</span></div>
          <div class="intelligence-tags">${gates.length ? gates.map(([key, value]) => `<span class="intelligence-tag gate">${esc(label(key))}${value !== true ? ` · ${esc(value)}` : ''}</span>`).join('') : '<span class="intelligence-tag">None extracted</span>'}</div>
        </section>

        <section class="intelligence-card">
          <div class="intelligence-card-title"><h2>Mandatory content</h2><span>Sections & language</span></div>
          <div class="intelligence-tags">${sections.map(item => `<span class="intelligence-tag">${esc(label(item))}</span>`).join('') || '<span class="intelligence-tag">No sections extracted</span>'}</div>
          ${keywords.length ? `<div class="intelligence-tags" style="margin-top:8px;">${keywords.map(item => `<span class="intelligence-tag"># ${esc(item)}</span>`).join('')}</div>` : ''}
        </section>

        <section class="intelligence-card">
          <div class="intelligence-card-title"><h2>Scoring model</h2><span>Deterministic</span></div>
          <div class="rule-list">
            ${weights.map(([key, value]) => `<div class="rule-item"><span>${esc(label(key))}</span><strong>${esc(value)} pts</strong></div>`).join('')}
            <div class="rule-item"><span>Pass threshold</span><strong>${esc(valueOrDash(manifest.pass_threshold))}</strong></div>
            <div class="rule-item"><span>Minimum source ratio</span><strong>${esc(valueOrDash(manifest.min_source_ratio))}</strong></div>
          </div>
        </section>

        ${limits.length || dimensions.length ? `<section class="intelligence-card">
          <div class="intelligence-card-title"><h2>Technical constraints</h2><span>Field rules</span></div>
          <div class="rule-list">
            ${limits.map(([key, value]) => `<div class="rule-item"><span>${esc(label(key))}</span><strong>${esc(value)} chars</strong></div>`).join('')}
            ${dimensions.length ? `<div class="rule-item"><span>SMART dimensions</span><strong>${esc(dimensions.length)} required</strong></div>` : ''}
          </div>
        </section>` : ''}

        <section class="intelligence-card">
          <div class="intelligence-card-title"><h2>Source documents</h2><span>${documents.length} files</span></div>
          <div class="document-list">${documents.map(doc => `<div class="document-item"><i>↥</i><span>${esc(doc.filename)} · ${Number(doc.chars || 0).toLocaleString()} characters</span></div>`).join('') || '<p class="intelligence-summary">No document metadata available.</p>'}</div>
        </section>
      </div>
    </div>
  `;

  container.querySelector('[data-publish-call]')?.addEventListener('click', () => handlers.onPublish(draft.id));
  container.querySelector('[data-reject-call]')?.addEventListener('click', () => handlers.onReject(draft.id));
  container.querySelector('[data-start-proposal]')?.addEventListener('click', () => handlers.onStart(draft.call_id, draft.display_name));
  container.querySelector('[data-delete-call]')?.addEventListener('click', () => handlers.onDelete(draft.id));
}
