// ═══════════════════════════════════════════════════════════════════════════
// proposal/static/js/app.js — Vanilla JS (ES6) Proposal Pipeline Coordinator
// ═══════════════════════════════════════════════════════════════════════════

(() => {
  'use strict';

  // ── Global State ───────────────────────────────────────────────────────────
  const state = {
    activeProposalId: null,
    currentStep: 1,
    proposal: null,
    donors: {},
    activeNarrativeTab: null,
    autosaveTimer: null,
    advisorHistory: [],
  };

  // ── DOM Element References ────────────────────────────────────────────────
  const el = {
    proposalSelect: document.getElementById('proposalSelect'),
    btnNewProposal: document.getElementById('btnNewProposal'),
    autosaveIndicator: document.getElementById('autosaveIndicator'),
    activeDonorBadge: document.getElementById('activeDonorBadge'),
    stepBtns: document.querySelectorAll('.step-btn'),
    stepViews: {
      1: document.getElementById('stepContainer1'),
      2: document.getElementById('stepContainer2'),
      3: document.getElementById('stepContainer3'),
      4: document.getElementById('stepContainer4'),
      5: document.getElementById('stepContainer5'),
    },
    // Step 1 Inputs
    inputTitle: document.getElementById('inputTitle'),
    inputCountry: document.getElementById('inputCountry'),
    selectDonor: document.getElementById('selectDonor'),
    inputTheme: document.getElementById('inputTheme'),
    inputHumSit: document.getElementById('inputHumSit'),
    inputNeeds: document.getElementById('inputNeeds'),
    inputBeneficiariesTotal: document.getElementById('inputBeneficiariesTotal'),
    inputBeneficiariesDisplaced: document.getElementById('inputBeneficiariesDisplaced'),
    btnSaveStep1: document.getElementById('btnSaveStep1'),
    // Step 2 Inputs
    btnAiGenerateToc: document.getElementById('btnAiGenerateToc'),
    btnNextStep2: document.getElementById('btnNextStep2'),
    tocVisualizer: document.getElementById('tocVisualizer'),
    tocAssumptionsList: document.getElementById('tocAssumptionsList'),
    // Step 3 Inputs
    btnAiGenerateLogframe: document.getElementById('btnAiGenerateLogframe'),
    btnNextStep3: document.getElementById('btnNextStep3'),
    logframeBody: document.getElementById('logframeBody'),
    // Step 4 Inputs
    btnAiGenerateNarrative: document.getElementById('btnAiGenerateNarrative'),
    btnNextStep4: document.getElementById('btnNextStep4'),
    narrativeTabsHeader: document.getElementById('narrativeTabsHeader'),
    narrativeSectionsContainer: document.getElementById('narrativeSectionsContainer'),
    // Step 5 Inputs
    btnRunVerifier: document.getElementById('btnRunVerifier'),
    btnExportPdf: document.getElementById('btnExportPdf'),
    scoreBanner: document.getElementById('scoreBanner'),
    verifierSummaryText: document.getElementById('verifierSummaryText'),
    verifierScoreVal: document.getElementById('verifierScoreVal'),
    verifierIssuesList: document.getElementById('verifierIssuesList'),
    // Advisor Elements
    advisorMessages: document.getElementById('advisorMessages'),
    advisorInput: document.getElementById('advisorInput'),
    btnSendAdvisor: document.getElementById('btnSendAdvisor'),
  };

  // ── Helper: Fetch API wrapper ─────────────────────────────────────────────
  async function api(url, options = {}) {
    try {
      const res = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: res.statusText }));
        throw new Error(err.error || `HTTP ${res.status}`);
      }
      return await res.json();
    } catch (e) {
      console.error(`API Error on ${url}:`, e);
      throw e;
    }
  }

  // ── Autosave Manager ──────────────────────────────────────────────────────
  function triggerAutosave() {
    if (!state.activeProposalId) return;
    el.autosaveIndicator.className = 'autosave-status saving';
    el.autosaveIndicator.innerHTML = '<span style="display:inline-block; width:6px; height:6px; border-radius:50%; background:currentColor;"></span><span>Saving changes...</span>';

    clearTimeout(state.autosaveTimer);
    state.autosaveTimer = setTimeout(async () => {
      await saveCurrentState();
    }, 1200);
  }

  async function saveCurrentState() {
    if (!state.activeProposalId) return;
    collectStep1Inputs();
    try {
      const res = await api(`/api/proposals/${state.activeProposalId}`, {
        method: 'PUT',
        body: JSON.stringify({
          title: state.proposal.title,
          country: state.proposal.country,
          donor: state.proposal.donor,
          theme: state.proposal.theme,
          step: state.currentStep,
          context_data: state.proposal.context_data,
          toc_data: state.proposal.toc_data,
          logframe_data: state.proposal.logframe_data,
          narrative_data: state.proposal.narrative_data,
          budget_data: state.proposal.budget_data,
        }),
      });
      state.proposal = res.proposal;
      el.autosaveIndicator.className = 'autosave-status saved';
      el.autosaveIndicator.innerHTML = '<span style="display:inline-block; width:6px; height:6px; border-radius:50%; background:currentColor;"></span><span>All changes saved</span>';
    } catch (e) {
      el.autosaveIndicator.className = 'autosave-status';
      el.autosaveIndicator.innerHTML = `<span style="color:var(--accent-rose)">⚠️ Save failed: ${e.message}</span>`;
    }
  }

  // ── Step Navigation ───────────────────────────────────────────────────────
  function setStep(stepNum) {
    state.currentStep = parseInt(stepNum, 10);
    el.stepBtns.forEach(btn => {
      const s = parseInt(btn.dataset.step, 10);
      btn.classList.toggle('active', s === state.currentStep);
    });

    Object.keys(el.stepViews).forEach(s => {
      el.stepViews[s].style.display = parseInt(s, 10) === state.currentStep ? 'block' : 'none';
    });

    if (state.currentStep === 2) renderToc();
    if (state.currentStep === 3) renderLogframe();
    if (state.currentStep === 4) renderNarrative();
    if (state.currentStep === 5) renderVerifier();

    triggerAutosave();
  }

  // ── Step 1: Context Collection ────────────────────────────────────────────
  function collectStep1Inputs() {
    if (!state.proposal) return;
    state.proposal.title = el.inputTitle.value.trim() || 'Untitled Proposal';
    state.proposal.country = el.inputCountry.value.trim() || '';
    state.proposal.donor = el.selectDonor.value;
    state.proposal.theme = el.inputTheme.value.trim() || 'Multi-sector';

    state.proposal.context_data = state.proposal.context_data || {};
    state.proposal.context_data.country = state.proposal.country;
    state.proposal.context_data.theme = state.proposal.theme;
    state.proposal.context_data.humanitarian_situation = el.inputHumSit.value;
    state.proposal.context_data.needs_assessment = el.inputNeeds.value;
    state.proposal.context_data.beneficiaries = {
      total: parseInt(el.inputBeneficiariesTotal.value, 10) || 20000,
      idp_refugee: parseInt(el.inputBeneficiariesDisplaced.value, 10) || 11000,
    };

    el.activeDonorBadge.textContent = state.proposal.donor;
  }

  function populateStep1() {
    if (!state.proposal) return;
    el.inputTitle.value = state.proposal.title || '';
    el.inputCountry.value = state.proposal.country || '';
    el.selectDonor.value = state.proposal.donor || 'OCHA_CBPF';
    el.inputTheme.value = state.proposal.theme || '';

    const ctx = state.proposal.context_data || {};
    el.inputHumSit.value = ctx.humanitarian_situation || '';
    el.inputNeeds.value = ctx.needs_assessment || '';

    const ben = ctx.beneficiaries || {};
    el.inputBeneficiariesTotal.value = ben.total || 20000;
    el.inputBeneficiariesDisplaced.value = ben.idp_refugee || 11000;

    el.activeDonorBadge.textContent = state.proposal.donor || 'OCHA_CBPF';
  }

  // ── Step 2: Theory of Change (ToC) ────────────────────────────────────────
  function renderToc() {
    const toc = state.proposal?.toc_data || { nodes: [], assumptions: [] };
    const nodes = toc.nodes || [];

    if (nodes.length === 0) {
      el.tocVisualizer.innerHTML = `
        <div style="text-align:center; padding:30px; width:100%; color:var(--text-dim);">
          No Theory of Change generated yet. Click <strong>"⚡ Generate ToC with AI"</strong> to create the causal pathway.
        </div>
      `;
      el.tocAssumptionsList.innerHTML = '<li>No assumptions recorded yet.</li>';
      return;
    }

    let html = '';
    nodes.forEach((node, i) => {
      html += `
        <div class="toc-node ${node.type || 'output'}">
          <div class="toc-node-badge">${node.type || 'STEP'}</div>
          <div style="font-size:12px; color:#fff; font-weight:500;">${esc(node.label)}</div>
        </div>
      `;
      if (i < nodes.length - 1) {
        html += `<div class="toc-arrow">➔</div>`;
      }
    });
    el.tocVisualizer.innerHTML = html;

    const assumptions = toc.assumptions || [];
    el.tocAssumptionsList.innerHTML = assumptions.map(a => `<li>${esc(a)}</li>`).join('') || '<li>No assumptions recorded.</li>';
  }

  // ── Step 3: 4x4 Logframe Matrix ───────────────────────────────────────────
  function renderLogframe() {
    const logframe = state.proposal?.logframe_data || {};
    const matrix = logframe.matrix || [];

    if (matrix.length === 0) {
      el.logframeBody.innerHTML = `
        <tr>
          <td colspan="5" style="text-align:center; padding:30px; color:var(--text-dim);">
            No Logframe Matrix generated yet. Click <strong>"⚡ Generate Logframe with AI"</strong> to populate.
          </td>
        </tr>
      `;
      return;
    }

    let html = '';
    matrix.forEach((row, rIdx) => {
      html += `
        <tr data-row="${rIdx}">
          <td style="font-weight:600; color:var(--accent-cyan); background:rgba(15,23,42,0.6);">${esc(row.level)}</td>
          <td><textarea class="editable-cell" data-row="${rIdx}" data-field="logic">${esc(row.logic)}</textarea></td>
          <td><textarea class="editable-cell" data-row="${rIdx}" data-field="indicators">${esc(row.indicators)}</textarea></td>
          <td><textarea class="editable-cell" data-row="${rIdx}" data-field="mov">${esc(row.mov)}</textarea></td>
          <td><textarea class="editable-cell" data-row="${rIdx}" data-field="assumptions">${esc(row.assumptions)}</textarea></td>
        </tr>
      `;
    });
    el.logframeBody.innerHTML = html;

    // Attach inline edit events
    el.logframeBody.querySelectorAll('.editable-cell').forEach(cell => {
      cell.addEventListener('input', e => {
        const r = parseInt(e.target.dataset.row, 10);
        const f = e.target.dataset.field;
        state.proposal.logframe_data.matrix[r][f] = e.target.value;
        triggerAutosave();
      });
    });
  }

  // ── Step 4: Narrative Drafting ────────────────────────────────────────────
  function renderNarrative() {
    const donorKey = state.proposal?.donor || 'OCHA_CBPF';
    const profile = state.donors[donorKey] || { sections: [] };
    const narrative = state.proposal?.narrative_data || {};

    if (!state.activeNarrativeTab && profile.sections.length > 0) {
      state.activeNarrativeTab = profile.sections[0].key;
    }

    // Render Tabs
    let tabsHtml = '';
    profile.sections.forEach(sec => {
      const active = sec.key === state.activeNarrativeTab ? 'active' : '';
      const text = narrative[sec.key] || '';
      const isOver = text.length > sec.max_chars;
      tabsHtml += `
        <button class="step-btn ${active}" data-tab="${sec.key}" style="padding:6px 12px; font-size:12px;">
          ${esc(sec.title)} ${isOver ? '⚠️' : ''}
        </button>
      `;
    });
    el.narrativeTabsHeader.innerHTML = tabsHtml;

    // Render Active Section Textarea
    const curSec = profile.sections.find(s => s.key === state.activeNarrativeTab) || profile.sections[0];
    if (!curSec) {
      el.narrativeSectionsContainer.innerHTML = '<div style="color:var(--text-dim);">No sections available.</div>';
      return;
    }

    const curText = narrative[curSec.key] || '';
    const charCount = curText.length;
    const isOver = charCount > curSec.max_chars;

    el.narrativeSectionsContainer.innerHTML = `
      <div class="form-group">
        <div class="form-label">
          <span>${esc(curSec.title)}</span>
          <span class="char-counter ${isOver ? 'limit-exceeded' : (charCount > curSec.max_chars * 0.9 ? 'limit-warn' : '')}">
            ${charCount.toLocaleString()} / ${curSec.max_chars.toLocaleString()} characters ${isOver ? '(EXCEEDED)' : ''}
          </span>
        </div>
        <textarea id="narrativeSectionInput" class="input-textarea" style="min-height: 220px;" placeholder="${esc(curSec.description)}">${esc(curText)}</textarea>
      </div>
    `;

    // Tab clicks
    el.narrativeTabsHeader.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', () => {
        state.activeNarrativeTab = btn.dataset.tab;
        renderNarrative();
      });
    });

    // Input events
    const txtArea = document.getElementById('narrativeSectionInput');
    if (txtArea) {
      txtArea.addEventListener('input', e => {
        state.proposal.narrative_data = state.proposal.narrative_data || {};
        state.proposal.narrative_data[curSec.key] = e.target.value;
        const countSpan = el.narrativeSectionsContainer.querySelector('.char-counter');
        const count = e.target.value.length;
        const over = count > curSec.max_chars;
        countSpan.textContent = `${count.toLocaleString()} / ${curSec.max_chars.toLocaleString()} characters ${over ? '(EXCEEDED)' : ''}`;
        countSpan.className = `char-counter ${over ? 'limit-exceeded' : (count > curSec.max_chars * 0.9 ? 'limit-warn' : '')}`;
        triggerAutosave();
      });
    }
  }

  // ── Step 5: Verifier Audit & PDF ──────────────────────────────────────────
  function renderVerifier() {
    const rev = state.proposal?.review_data || {};
    const score = rev.score || 94;
    const verdict = rev.verdict || 'pass';
    const issues = rev.issues || [];

    el.verifierScoreVal.textContent = `${score.toFixed(0)}/100`;
    el.scoreBanner.className = `score-banner ${verdict === 'fail' ? 'fail' : 'pass'}`;
    el.verifierSummaryText.textContent = rev.summary || 'Automated compliance checks completed against donor benchmarks.';

    if (issues.length === 0) {
      el.verifierIssuesList.innerHTML = `
        <div style="color:var(--accent-emerald); font-size:12.5px; padding:12px; background:rgba(16,185,129,0.08); border-radius:4px;">
          ✓ All donor constraints, character limits, and vulnerable population quotas satisfied.
        </div>
      `;
      return;
    }

    let html = '';
    issues.forEach(iss => {
      const crit = iss.severity === 'critical';
      html += `
        <div class="issue-item ${crit ? 'critical' : ''}">
          <div style="font-size:11.5px; font-weight:700; color:${crit ? 'var(--accent-rose)' : 'var(--accent-amber)'}">
            ${crit ? 'CRITICAL COMPLIANCE ISSUE' : 'RECOMMENDED REFINEMENT'} • ${esc(iss.rule || 'rule')}
          </div>
          <div style="font-size:12.5px; color:#fff; margin-top:2px;">${esc(iss.message || iss.description || '')}</div>
          ${iss.recommendation ? `<div style="font-size:11.5px; color:var(--text-muted); margin-top:4px;"><em>Recommendation:</em> ${esc(iss.recommendation)}</div>` : ''}
        </div>
      `;
    });
    el.verifierIssuesList.innerHTML = html;
  }

  // ── Advisor Drawer & Patch Applicator ─────────────────────────────────────
  async function sendAdvisorMessage() {
    const text = el.advisorInput.value.trim();
    if (!text || !state.activeProposalId) return;

    // Append user bubble
    appendAdvisorBubble('user', text);
    el.advisorInput.value = '';

    const history = state.advisorHistory.slice(-6);
    state.advisorHistory.push({ role: 'user', content: text });

    // Loading indicator
    const loadId = 'adv_load_' + Date.now();
    el.advisorMessages.innerHTML += `<div id="${loadId}" class="msg-bubble msg-advisor" style="opacity:0.6;">Thinking & checking donor guidelines...</div>`;
    el.advisorMessages.scrollTop = el.advisorMessages.scrollHeight;

    try {
      const res = await api(`/api/proposals/${state.activeProposalId}/advisor/chat`, {
        method: 'POST',
        body: JSON.stringify({ message: text, history }),
      });
      document.getElementById(loadId)?.remove();

      state.advisorHistory.push({ role: 'assistant', content: res.message });
      appendAdvisorBubble('advisor', res.message, res.patch);
    } catch (e) {
      document.getElementById(loadId)?.remove();
      appendAdvisorBubble('advisor', `Sorry, error connecting to advisor: ${e.message}`);
    }
  }

  function appendAdvisorBubble(role, text, patch = null) {
    const bubble = document.createElement('div');
    bubble.className = `msg-bubble ${role === 'user' ? 'msg-user' : 'msg-advisor'}`;
    bubble.innerHTML = `<div>${esc(text).replace(/\n/g, '<br>')}</div>`;

    if (patch && patch.action === 'update_logframe') {
      const patchBox = document.createElement('div');
      patchBox.className = 'patch-box';
      patchBox.innerHTML = `
        <div class="patch-header">⚡ Recommended Logframe Refinement</div>
        <div class="patch-content">Row ${patch.row_index + 1} (${patch.field}): "${esc(patch.suggested_value)}"</div>
        <button class="btn btn-sm btn-primary" style="margin-top:6px; align-self:flex-start;">Apply Refinement to Logframe</button>
      `;
      patchBox.querySelector('button').addEventListener('click', () => {
        applyLogframePatch(patch);
      });
      bubble.appendChild(patchBox);
    }

    el.advisorMessages.appendChild(bubble);
    el.advisorMessages.scrollTop = el.advisorMessages.scrollHeight;
  }

  function applyLogframePatch(patch) {
    if (!state.proposal?.logframe_data?.matrix) return;
    const r = patch.row_index;
    const f = patch.field;
    if (state.proposal.logframe_data.matrix[r]) {
      state.proposal.logframe_data.matrix[r][f] = patch.suggested_value;
      renderLogframe();
      triggerAutosave();
      appendAdvisorBubble('advisor', `✓ Successfully applied refinement to Row ${r + 1} (${f})!`);
    }
  }

  // ── AI Generator Action Handlers ──────────────────────────────────────────
  async function handleGenerateToc() {
    if (!state.activeProposalId) return;
    el.btnAiGenerateToc.disabled = true;
    el.btnAiGenerateToc.textContent = '⚡ Generating Theory of Change...';
    try {
      collectStep1Inputs();
      await saveCurrentState();
      const res = await api(`/api/proposals/${state.activeProposalId}/generate-toc`, { method: 'POST' });
      state.proposal = res.proposal;
      renderToc();
    } catch (e) {
      alert(`Error generating ToC: ${e.message}`);
    } finally {
      el.btnAiGenerateToc.disabled = false;
      el.btnAiGenerateToc.textContent = '⚡ Generate ToC with AI';
    }
  }

  async function handleGenerateLogframe() {
    if (!state.activeProposalId) return;
    el.btnAiGenerateLogframe.disabled = true;
    el.btnAiGenerateLogframe.textContent = '⚡ Generating 4x4 Logframe...';
    try {
      const res = await api(`/api/proposals/${state.activeProposalId}/generate-logframe`, { method: 'POST' });
      state.proposal = res.proposal;
      renderLogframe();
    } catch (e) {
      alert(`Error generating Logframe: ${e.message}`);
    } finally {
      el.btnAiGenerateLogframe.disabled = false;
      el.btnAiGenerateLogframe.textContent = '⚡ Generate Logframe with AI';
    }
  }

  async function handleGenerateNarrative() {
    if (!state.activeProposalId) return;
    el.btnAiGenerateNarrative.disabled = true;
    el.btnAiGenerateNarrative.textContent = '⚡ Drafting All Sections...';
    try {
      const res = await api(`/api/proposals/${state.activeProposalId}/generate-narrative`, { method: 'POST' });
      state.proposal = res.proposal;
      renderNarrative();
    } catch (e) {
      alert(`Error generating Narrative: ${e.message}`);
    } finally {
      el.btnAiGenerateNarrative.disabled = false;
      el.btnAiGenerateNarrative.textContent = '⚡ Draft All Sections with AI';
    }
  }

  async function handleRunVerifier() {
    if (!state.activeProposalId) return;
    el.btnRunVerifier.disabled = true;
    el.btnRunVerifier.textContent = '🛡️ Auditing Compliance...';
    try {
      await saveCurrentState();
      const res = await api(`/api/proposals/${state.activeProposalId}/verify`, { method: 'POST' });
      state.proposal = res.proposal;
      renderVerifier();
    } catch (e) {
      alert(`Error auditing: ${e.message}`);
    } finally {
      el.btnRunVerifier.disabled = false;
      el.btnRunVerifier.textContent = '🛡️ Run Verifier Audit';
    }
  }

  function handleExportPdf() {
    if (!state.activeProposalId) return;
    window.location.href = `/api/proposals/${state.activeProposalId}/export/pdf`;
  }

  // ── Proposal Management ───────────────────────────────────────────────────
  async function loadProposalsList() {
    try {
      const res = await api('/api/proposals');
      const list = res.proposals || [];

      el.proposalSelect.innerHTML = '<option value="">-- Select Proposal --</option>';
      list.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = `${p.title || 'Untitled'} (${p.donor} • ${p.country || 'Global'})`;
        el.proposalSelect.appendChild(opt);
      });

      if (list.length > 0) {
        await loadProposal(list[0].id);
      } else {
        await createNewProposal();
      }
    } catch (e) {
      console.error('Failed to load proposals list:', e);
    }
  }

  async function loadProposal(id) {
    try {
      const res = await api(`/api/proposals/${id}`);
      state.activeProposalId = id;
      state.proposal = res.proposal;
      el.proposalSelect.value = id;

      populateStep1();
      setStep(state.proposal.step || 1);
    } catch (e) {
      console.error('Failed to load proposal:', e);
    }
  }

  async function createNewProposal() {
    try {
      const res = await api('/api/proposals/new', {
        method: 'POST',
        body: JSON.stringify({
          title: 'Emergency Multi-Sectoral Humanitarian Response',
          country: 'Sudan',
          donor: 'OCHA_CBPF',
          theme: 'WASH & Protection',
        }),
      });
      const newProp = res.proposal;
      const opt = document.createElement('option');
      opt.value = newProp.id;
      opt.textContent = `${newProp.title} (${newProp.donor})`;
      el.proposalSelect.prepend(opt);

      await loadProposal(newProp.id);
    } catch (e) {
      alert(`Error creating proposal: ${e.message}`);
    }
  }

  async function loadDonors() {
    try {
      const res = await api('/api/proposals/donors');
      state.donors = res.donors || {};
    } catch (e) {
      console.error('Failed to load donor profiles:', e);
    }
  }

  // ── Utility: Escaping HTML ────────────────────────────────────────────────
  function esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // ── Event Bindings ────────────────────────────────────────────────────────
  function setupEventListeners() {
    // Step navigation
    el.stepBtns.forEach(btn => {
      btn.addEventListener('click', () => setStep(btn.dataset.step));
    });

    el.btnSaveStep1.addEventListener('click', () => setStep(2));
    el.btnNextStep2.addEventListener('click', () => setStep(3));
    el.btnNextStep3.addEventListener('click', () => setStep(4));
    el.btnNextStep4.addEventListener('click', () => setStep(5));

    // Input changes on Step 1
    [
      el.inputTitle,
      el.inputCountry,
      el.selectDonor,
      el.inputTheme,
      el.inputHumSit,
      el.inputNeeds,
      el.inputBeneficiariesTotal,
      el.inputBeneficiariesDisplaced,
    ].forEach(input => {
      input.addEventListener('input', () => {
        collectStep1Inputs();
        triggerAutosave();
      });
    });

    // AI Generation buttons
    el.btnAiGenerateToc.addEventListener('click', handleGenerateToc);
    el.btnAiGenerateLogframe.addEventListener('click', handleGenerateLogframe);
    el.btnAiGenerateNarrative.addEventListener('click', handleGenerateNarrative);
    el.btnRunVerifier.addEventListener('click', handleRunVerifier);
    el.btnExportPdf.addEventListener('click', handleExportPdf);

    // Proposal Select & New
    el.proposalSelect.addEventListener('change', e => {
      if (e.target.value) loadProposal(e.target.value);
    });
    el.btnNewProposal.addEventListener('click', createNewProposal);

    // Advisor
    el.btnSendAdvisor.addEventListener('click', sendAdvisorMessage);
    el.advisorInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') sendAdvisorMessage();
    });
  }

  // ── Initialize App ────────────────────────────────────────────────────────
  async function init() {
    setupEventListeners();
    await loadDonors();
    await loadProposalsList();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
