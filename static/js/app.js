import { api, esc, notify, confirmAction, beginActivity, setButtonBusy } from './modules/core.js';
import { applyStepLockState } from './modules/workspace-state.js';
import { renderCallList, renderCallDetail } from './modules/donor-intelligence.js';

// ── Base path for reverse-proxy deployment ──────────────────────────────────
// Set by Flask template: window.PROPOSAL_BASE_PATH = '/proposal' or ''
// In standalone mode (local dev), this is an empty string.
const BASE = window.PROPOSAL_BASE_PATH || '';

/**
 * Prefix an API path with the base path.
 * e.g. apiPath('/api/proposals') → '/proposal/api/proposals' in production
 */
function apiPath(path) {
  return `${BASE}${path}`;
}

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
    step4Subtab: 'narrative',
    autosaveTimer: null,
    advisorHistory: [],
    callDrafts: [],
    activeCallDraftId: null,
  };

  // ── DOM Element References ────────────────────────────────────────────────
  const el = {
    autosaveIndicator: document.getElementById('autosaveIndicator'),
    activeDonorBadge: document.getElementById('activeDonorBadge'),
    workspaceProposalTitle: document.getElementById('workspaceProposalTitle'),
    stepBtns: document.querySelectorAll('.step-nav .step-btn'),
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
    btnAiGenerateContext: document.getElementById('btnAiGenerateContext'),
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
    step4SubTabs: document.getElementById('step4SubTabs'),
    step4NarrativePane: document.getElementById('step4NarrativePane'),
    step4RiskPane: document.getElementById('step4RiskPane'),
    step4BudgetPane: document.getElementById('step4BudgetPane'),
    // Step 5 Inputs
    btnRunVerifier: document.getElementById('btnRunVerifier'),
    btnExportPdf: document.getElementById('btnExportPdf'),
    scoreBanner: document.getElementById('scoreBanner'),
    verifierSummaryText: document.getElementById('verifierSummaryText'),
    verifierScoreVal: document.getElementById('verifierScoreVal'),
    verifierIssuesList: document.getElementById('verifierIssuesList'),
    eligibilityBanner: document.getElementById('eligibilityBanner'),
    eligibilityBannerText: document.getElementById('eligibilityBannerText'),
    eligibilityChecklist: document.getElementById('eligibilityChecklist'),
    eligibilityGatesList: document.getElementById('eligibilityGatesList'),
    scoreTableContainer: document.getElementById('scoreTableContainer'),
    // Advisor Elements
    advisorMessages: document.getElementById('advisorMessages'),
    advisorInput: document.getElementById('advisorInput'),
    btnSendAdvisor: document.getElementById('btnSendAdvisor'),
    // Donor Call Ingestion
    callFileInput: document.getElementById('callFileInput'),
    callIdInput: document.getElementById('callIdInput'),
    callNameInput: document.getElementById('callNameInput'),
    btnIngestCall: document.getElementById('btnIngestCall'),
    callIngestResult: document.getElementById('callIngestResult'),
    callDraftsList: document.getElementById('callDraftsList'),
  };

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
      const res = await api(apiPath(`/api/proposals/${state.activeProposalId}`), {
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
      el.autosaveIndicator.innerHTML = `<span style="color:var(--red)">Save failed: ${e.message}</span>`;
      if (e.code === 'STEP_LOCKED') {
        applyStepLockState(state.proposal, state.currentStep);
        notify('This approved step is locked. Its saved snapshot was preserved.', 'info');
      }
    }
  }

  // ── Step Navigation ───────────────────────────────────────────────────────
  function setStep(stepNum) {
    state.currentStep = parseInt(stepNum, 10);
    el.stepBtns.forEach(btn => {
      const s = parseInt(btn.dataset.step, 10);
      btn.classList.toggle('active', s === state.currentStep);
      btn.classList.toggle('is-complete', s < state.currentStep);
      if (s === state.currentStep) btn.setAttribute('aria-current', 'step');
      else btn.removeAttribute('aria-current');
    });
    // Sub-tab buttons (Narrative|Risk|Budget) must not lose their active state
    const subTabs = document.querySelectorAll('#step4SubTabs .step-btn');
    subTabs.forEach(btn => {
      const s = btn.dataset.subtab;
      btn.classList.toggle('active', s === (state.step4Subtab || 'narrative'));
    });

    Object.keys(el.stepViews).forEach(s => {
      el.stepViews[s].style.display = parseInt(s, 10) === state.currentStep ? 'block' : 'none';
    });

    if (state.currentStep === 2) renderToc();
    if (state.currentStep === 3) renderLogframe();
    if (state.currentStep === 4) {
      renderNarrative();
      switchStep4Subtab(state.step4Subtab || 'narrative');
    }
    if (state.currentStep === 5) renderVerifier();

    applyStepLockState(state.proposal, state.currentStep);

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

    updateDonorBadge(state.proposal.donor);
    updateWorkspaceTitle(state.proposal.title);
  }

  function populateStep1() {
    if (!state.proposal) return;
    el.inputTitle.value = state.proposal.title || '';
    el.inputCountry.value = state.proposal.country || '';
    const donorId = state.proposal.donor || '';
    if (donorId && ![...el.selectDonor.options].some(option => option.value === donorId)) {
      const option = document.createElement('option');
      option.value = donorId;
      option.textContent = state.donors[donorId]?.display_name || donorId;
      el.selectDonor.appendChild(option);
    }
    el.selectDonor.value = donorId;
    el.inputTheme.value = state.proposal.theme || '';

    const ctx = state.proposal.context_data || {};
    el.inputHumSit.value = ctx.humanitarian_situation || '';
    el.inputNeeds.value = ctx.needs_assessment || '';

    const ben = ctx.beneficiaries || {};
    el.inputBeneficiariesTotal.value = ben.total || 20000;
    el.inputBeneficiariesDisplaced.value = ben.idp_refugee || 11000;

    updateDonorBadge(state.proposal.donor || 'OCHA_CBPF');
    updateWorkspaceTitle(state.proposal.title);
  }

  function updateDonorBadge(donor) {
    el.activeDonorBadge.innerHTML = `<span class="status-dot"></span>${esc(donor || 'OCHA_CBPF')}`;
  }

  function updateWorkspaceTitle(title) {
    if (el.workspaceProposalTitle) {
      el.workspaceProposalTitle.textContent = title || 'Untitled proposal';
    }
  }

  // ── Step 2: Theory of Change (ToC) ────────────────────────────────────────
  function renderToc() {
    const toc = state.proposal?.toc_data || { nodes: [], assumptions: [] };
    const nodes = toc.nodes || [];

    if (nodes.length === 0) {
      el.tocVisualizer.innerHTML = `
        <div style="text-align:center; padding:30px; width:100%; color:var(--text-secondary);">
          No Theory of Change yet. Click <strong>"Generate ToC with AI"</strong> or <strong>"+ Add Node"</strong> to build the causal pathway manually.
        </div>
      `;
      el.tocAssumptionsList.innerHTML = '<li>No assumptions recorded yet.</li>';
      return;
    }

    let html = '';
    nodes.forEach((node, i) => {
      html += `
        <div class="toc-node ${node.type || 'output'}" style="position:relative;">
          <div class="toc-node-badge">
            <select class="toc-type" data-idx="${i}" style="background:transparent; border:none; color:inherit; font-size:10px; font-weight:700; text-transform:uppercase;">
              ${['input', 'activity', 'output', 'outcome', 'impact'].map(t => `<option value="${t}" ${t === (node.type || 'output') ? 'selected' : ''}>${t}</option>`).join('')}
            </select>
          </div>
          <textarea class="toc-node-label" data-idx="${i}" style="width:100%; min-height:44px; background:transparent; border:none; color:inherit; font-size:12px; resize:vertical;">${esc(node.label)}</textarea>
          <button class="btn btn-sm toc-del" data-idx="${i}" style="position:absolute; top:2px; right:2px; color:var(--red); font-size:10px; padding:0 4px;">✕</button>
        </div>
      `;
      if (i < nodes.length - 1) {
        html += `<div class="toc-arrow">&rarr;</div>`;
      }
    });
    el.tocVisualizer.innerHTML = html;

    // Inline node label edits
    el.tocVisualizer.querySelectorAll('.toc-node-label').forEach(t => {
      t.addEventListener('input', e => {
        const idx = parseInt(e.target.dataset.idx, 10);
        state.proposal.toc_data.nodes[idx].label = e.target.value;
        triggerAutosave();
      });
    });

    // Node type change
    el.tocVisualizer.querySelectorAll('.toc-type').forEach(sel => {
      sel.addEventListener('change', e => {
        const idx = parseInt(e.target.dataset.idx, 10);
        state.proposal.toc_data.nodes[idx].type = e.target.value;
        renderToc();
        triggerAutosave();
      });
    });

    // Delete node
    el.tocVisualizer.querySelectorAll('.toc-del').forEach(btn => {
      btn.addEventListener('click', e => {
        const idx = parseInt(e.target.dataset.idx, 10);
        state.proposal.toc_data.nodes.splice(idx, 1);
        renderToc();
        triggerAutosave();
      });
    });

    const assumptions = toc.assumptions || [];
    el.tocAssumptionsList.innerHTML = assumptions.map(a => `<li>${esc(a)}</li>`).join('') || '<li>No assumptions recorded.</li>';
  }

  function addTocNode() {
    if (!state.proposal) return;
    const type = document.getElementById('tocTypeSelect')?.value || 'output';
    state.proposal.toc_data = state.proposal.toc_data || { nodes: [], assumptions: [] };
    state.proposal.toc_data.nodes = state.proposal.toc_data.nodes || [];
    state.proposal.toc_data.nodes.push({ type, label: 'New node — describe the step' });
    renderToc();
    triggerAutosave();
  }

  // ── Step 3: 4x4 Logframe Matrix ───────────────────────────────────────────
  function renderLogframe() {
    const logframe = state.proposal?.logframe_data || {};
    const matrix = logframe.matrix || [];

    if (matrix.length === 0) {
      el.logframeBody.innerHTML = `
        <tr>
          <td colspan="5" style="text-align:center; padding:30px; color:var(--text-secondary);">
            No Logframe Matrix generated yet. Click <strong>"Generate Logframe with AI"</strong> to populate.
          </td>
        </tr>
      `;
      return;
    }

    let html = '';
    matrix.forEach((row, rIdx) => {
      html += `
        <tr data-row="${rIdx}">
          <td class="level-cell">
            <select class="input-select lf-level" data-row="${rIdx}" style="font-size: 11px; width: 100%;">
              ${['GOAL', 'OUTCOME', 'OUTPUT', 'ACTIVITY'].map(lv => `<option value="${lv}" ${lv === row.level ? 'selected' : ''}>${lv}</option>`).join('')}
            </select>
          </td>
          <td><textarea class="editable-cell" data-row="${rIdx}" data-field="logic">${esc(row.logic)}</textarea></td>
          <td><textarea class="editable-cell" data-row="${rIdx}" data-field="indicators">${esc(row.indicators)}</textarea></td>
          <td><textarea class="editable-cell" data-row="${rIdx}" data-field="mov">${esc(row.mov)}</textarea></td>
          <td><textarea class="editable-cell" data-row="${rIdx}" data-field="assumptions">${esc(row.assumptions)}</textarea></td>
          <td style="width: 4%;"><button class="btn btn-sm lf-del" data-row="${rIdx}" style="color:var(--red);">✕</button></td>
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

    // Level change
    el.logframeBody.querySelectorAll('.lf-level').forEach(sel => {
      sel.addEventListener('change', e => {
        const r = parseInt(e.target.dataset.row, 10);
        state.proposal.logframe_data.matrix[r].level = e.target.value;
        triggerAutosave();
      });
    });

    // Delete row
    el.logframeBody.querySelectorAll('.lf-del').forEach(btn => {
      btn.addEventListener('click', e => {
        const r = parseInt(e.target.dataset.row, 10);
        state.proposal.logframe_data.matrix.splice(r, 1);
        renderLogframe();
        triggerAutosave();
      });
    });
  }

  function addLogframeRow() {
    if (!state.proposal) return;
    const level = document.getElementById('logframeLevelSelect')?.value || 'OUTPUT';
    state.proposal.logframe_data = state.proposal.logframe_data || {};
    state.proposal.logframe_data.matrix = state.proposal.logframe_data.matrix || [];
    state.proposal.logframe_data.matrix.push({
      level, logic: '', indicators: '', mov: '', assumptions: '',
    });
    renderLogframe();
    triggerAutosave();
  }

  // ── Step 4: Narrative Drafting ────────────────────────────────────────────
  const FALLBACK_SECTIONS = [
    { key: 'project_summary', title: 'Project Summary', max_chars: 4000, description: 'Concise overview of the proposed intervention.' },
    { key: 'humanitarian_situation', title: 'Humanitarian Situation & Context', max_chars: 4000, description: 'Describe the acute humanitarian triggers and displacement.' },
    { key: 'needs_assessment', title: 'Needs Assessment', max_chars: 4000, description: 'Detail sectoral gaps and Sphere deficits.' },
    { key: 'beneficiaries', title: 'Beneficiary Targeting', max_chars: 3000, description: 'Describe who is reached and why.' },
    { key: 'justification', title: 'Intervention Justification', max_chars: 3000, description: 'Why this intervention, why now.' },
  ];

  function renderNarrative() {
    const donorKey = state.proposal?.donor || 'OCHA_CBPF';
    const profile = state.donors[donorKey] || { sections: [] };
    const narrative = state.proposal?.narrative_data || {};
    const sections = (profile.sections && profile.sections.length ? profile.sections : FALLBACK_SECTIONS);

    if (!state.activeNarrativeTab && sections.length > 0) {
      state.activeNarrativeTab = sections[0].key;
    }

    // Render Tabs
    let tabsHtml = '';
    sections.forEach(sec => {
      const active = sec.key === state.activeNarrativeTab ? 'active' : '';
      const text = narrative[sec.key] || '';
      const isOver = text.length > sec.max_chars;
      tabsHtml += `
        <button class="step-btn ${active}" data-tab="${sec.key}" style="padding:6px 12px; font-size:12px;">
          ${esc(sec.title)} ${isOver ? '(OVER LIMIT)' : ''}
        </button>
      `;
    });
    el.narrativeTabsHeader.innerHTML = tabsHtml;

    // Render Active Section Textarea
    const curSec = sections.find(s => s.key === state.activeNarrativeTab) || sections[0];
    if (!curSec) {
      el.narrativeSectionsContainer.innerHTML = '<div style="color:var(--text-secondary);">No sections available.</div>';
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

  // ── Step 4: 5x5 Risk Matrix ────────────────────────────────────────────
  const RISK_CATEGORIES = ['security', 'safeguarding_psea', 'financial', 'operational', 'environmental'];

  function renderStep4Risk() {
    const budget = state.proposal?.budget_data || {};
    const risks = budget.risks || [];
    const container = el.step4RiskPane;

    let html = `
      <div class="glass-card" style="margin-top: 12px;">
        <div class="step-header-area" style="margin-bottom: 8px;">
          <div>
            <h3 style="font-size: 13px; font-weight: 600; color: var(--text);">5x5 Severity Risk Matrix</h3>
            <p style="font-size: 11.5px; color: var(--text-secondary);">Likelihood (1-5) x Impact (1-5) = Severity. Red ≥ 15, Amber 8-12, Green 1-6. Severity ≥ 12 requires mitigation.</p>
          </div>
          <button class="btn btn-sm btn-primary" id="btnAddRisk">+ Add Risk</button>
        </div>
        <table class="logframe-table" style="font-size: 12px;">
          <thead>
            <tr>
              <th style="width: 14%;">Category</th>
              <th style="width: 26%;">Risk Description</th>
              <th style="width: 8%;">Likelihood</th>
              <th style="width: 8%;">Impact</th>
              <th style="width: 8%;">Severity</th>
              <th style="width: 30%;">Mitigation Strategy</th>
              <th style="width: 6%;"></th>
            </tr>
          </thead>
          <tbody id="riskMatrixBody">
            ${risks.length === 0 ? `<tr><td colspan="7" style="text-align:center; padding:20px; color:var(--text-secondary);">No risks defined. Add at least Security, Safeguarding/PSEA, Financial and Operational rows.</td></tr>` : ''}
            ${risks.map((r, i) => {
              const sev = (parseInt(r.likelihood, 10) || 1) * (parseInt(r.impact, 10) || 1);
              const tagCls = sev >= 15 ? 'risk-red' : (sev >= 8 ? 'risk-amber' : 'risk-green');
              return `
                <tr data-risk="${i}">
                  <td>
                    <select class="input-select risk-cat" data-idx="${i}" style="font-size: 11px;">
                      ${RISK_CATEGORIES.map(c => `<option value="${c}" ${c === r.category ? 'selected' : ''}>${c.replace('_', ' / ')}</option>`).join('')}
                    </select>
                  </td>
                  <td><textarea class="editable-cell risk-desc" data-idx="${i}" style="min-height: 40px;">${esc(r.description || '')}</textarea></td>
                  <td><input type="number" min="1" max="5" class="input-text risk-lh" data-idx="${i}" value="${r.likelihood || 1}" style="width: 50px;"></td>
                  <td><input type="number" min="1" max="5" class="input-text risk-im" data-idx="${i}" value="${r.impact || 1}" style="width: 50px;"></td>
                  <td><span class="risk-sev ${tagCls}">${sev}</span></td>
                  <td><textarea class="editable-cell risk-mit" data-idx="${i}" style="min-height: 40px;">${esc(r.mitigation_strategy || '')}</textarea></td>
                  <td><button class="btn btn-sm risk-del" data-idx="${i}" style="color:var(--red);">✕</button></td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      </div>
    `;
    container.innerHTML = html;

    // Add risk
    const addBtn = document.getElementById('btnAddRisk');
    if (addBtn) addBtn.addEventListener('click', () => {
      state.proposal.budget_data = state.proposal.budget_data || {};
      state.proposal.budget_data.risks = state.proposal.budget_data.risks || [];
      state.proposal.budget_data.risks.push({ category: 'security', description: '', likelihood: 2, impact: 2, mitigation_strategy: '' });
      renderStep4Risk();
      triggerAutosave();
    });

    // Inline edits
    container.querySelectorAll('.editable-cell, .risk-lh, .risk-im, .risk-cat').forEach(inp => {
      const evt = inp.tagName === 'SELECT' ? 'change' : 'input';
      inp.addEventListener(evt, e => {
        const idx = parseInt(e.target.dataset.idx, 10);
        const risk = state.proposal.budget_data.risks[idx];
        if (!risk) return;
        const cls = e.target.className;
        if (cls.includes('risk-desc')) risk.description = e.target.value;
        else if (cls.includes('risk-mit')) risk.mitigation_strategy = e.target.value;
        else if (cls.includes('risk-lh')) risk.likelihood = parseInt(e.target.value, 10) || 1;
        else if (cls.includes('risk-im')) risk.impact = parseInt(e.target.value, 10) || 1;
        else if (cls.includes('risk-cat')) risk.category = e.target.value;
        // live severity refresh
        if (cls.includes('risk-lh') || cls.includes('risk-im')) {
          const sevCell = e.target.closest('tr').querySelector('.risk-sev');
          const sev = (parseInt(risk.likelihood, 10) || 1) * (parseInt(risk.impact, 10) || 1);
          sevCell.textContent = sev;
          sevCell.className = `risk-sev ${sev >= 15 ? 'risk-red' : (sev >= 8 ? 'risk-amber' : 'risk-green')}`;
        }
        triggerAutosave();
      });
    });

    // Delete risk
    container.querySelectorAll('.risk-del').forEach(btn => {
      btn.addEventListener('click', e => {
        const idx = parseInt(e.target.dataset.idx, 10);
        state.proposal.budget_data.risks.splice(idx, 1);
        renderStep4Risk();
        triggerAutosave();
      });
    });
  }

  // ── Step 4: Itemized Budget ────────────────────────────────────────────
  const BUDGET_CATEGORIES = ['personnel', 'travel_transport', 'equipment_supplies', 'contractual', 'direct_operational', 'indirect_overhead'];

  function renderStep4Budget() {
    const budget = state.proposal?.budget_data || {};
    const items = budget.items || [];
    const container = el.step4BudgetPane;

    // Compute summary client-side (mirror of backend compute_budget_summary)
    let direct = 0, indirect = 0;
    const catTotals = {};
    items.forEach(it => {
      const total = (parseFloat(it.unit_count) || 0) * (parseFloat(it.unit_cost) || 0);
      catTotals[it.category] = (catTotals[it.category] || 0) + total;
      if (it.category === 'indirect_overhead') indirect += total; else direct += total;
    });
    const overheadPct = direct ? (indirect / direct) * 100 : 0;
    const cap = budget.overhead_cap_percent || 7.0;
    const overCap = overheadPct > cap;

    let html = `
      <div class="glass-card" style="margin-top: 12px;">
        <div class="step-header-area" style="margin-bottom: 8px;">
          <div>
            <h3 style="font-size: 13px; font-weight: 600; color: var(--text);">Itemized Budget</h3>
            <p style="font-size: 11.5px; color: var(--text-secondary);">Overhead % = indirect / direct × 100. Donor cap: ${cap}% (OCHA/EU 7%, USAID 10%).</p>
          </div>
          <button class="btn btn-sm btn-primary" id="btnAddBudgetItem">+ Add Line</button>
        </div>
        <div class="budget-overhead-banner ${overCap ? 'limit-exceeded' : ''}" style="padding: 8px 12px; border-radius: 4px; margin-bottom: 10px; font-size: 12px; ${overCap ? 'background: rgba(220,38,38,.12); color: var(--red);' : 'background: rgba(30,158,79,.12); color: #1e9e4f;'}">
          Overhead: <strong>${overheadPct.toFixed(1)}%</strong> vs donor cap ${cap}% — ${overCap ? 'EXCEEDS CAP: budget_alignment penalty applied' : 'within cap'}
        </div>
        <table class="logframe-table" style="font-size: 12px;">
          <thead>
            <tr>
              <th style="width: 15%;">Category</th>
              <th style="width: 30%;">Description</th>
              <th style="width: 10%;">Unit Type</th>
              <th style="width: 9%;">Unit Count</th>
              <th style="width: 10%;">Unit Cost (USD)</th>
              <th style="width: 11%;">Total</th>
              <th style="width: 6%;"></th>
            </tr>
          </thead>
          <tbody id="budgetItemsBody">
            ${items.length === 0 ? `<tr><td colspan="7" style="text-align:center; padding:20px; color:var(--text-secondary);">No budget lines defined.</td></tr>` : ''}
            ${items.map((it, i) => {
              const total = (parseFloat(it.unit_count) || 0) * (parseFloat(it.unit_cost) || 0);
              return `
                <tr data-item="${i}">
                  <td>
                    <select class="input-select budget-cat" data-idx="${i}" style="font-size: 11px;">
                      ${BUDGET_CATEGORIES.map(c => `<option value="${c}" ${c === it.category ? 'selected' : ''}>${c.replace(/_/g, ' ')}</option>`).join('')}
                    </select>
                  </td>
                  <td><textarea class="editable-cell budget-desc" data-idx="${i}" style="min-height: 36px;">${esc(it.description || '')}</textarea></td>
                  <td><input type="text" class="input-text budget-ut" data-idx="${i}" value="${esc(it.unit_type || '')}" style="font-size: 11px;"></td>
                  <td><input type="number" min="0" class="input-text budget-cnt" data-idx="${i}" value="${it.unit_count || 0}" style="width: 60px;"></td>
                  <td><input type="number" min="0" class="input-text budget-cst" data-idx="${i}" value="${it.unit_cost || 0}" style="width: 70px;"></td>
                  <td class="budget-total">${total.toLocaleString('en-US', {style: 'currency', currency: 'USD'})}</td>
                  <td><button class="btn btn-sm budget-del" data-idx="${i}" style="color:var(--red);">✕</button></td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
        <div style="text-align:right; font-size: 12.5px; margin-top: 8px; color: var(--text);">
          <strong>Grand Total: ${(direct + indirect).toLocaleString('en-US', {style: 'currency', currency: 'USD'})}</strong>
        </div>
      </div>
    `;
    container.innerHTML = html;

    const addBtn = document.getElementById('btnAddBudgetItem');
    if (addBtn) addBtn.addEventListener('click', () => {
      state.proposal.budget_data = state.proposal.budget_data || {};
      state.proposal.budget_data.items = state.proposal.budget_data.items || [];
      state.proposal.budget_data.items.push({ category: 'personnel', description: '', unit_type: 'month', unit_count: 1, unit_cost: 0 });
      renderStep4Budget();
      triggerAutosave();
    });

    container.querySelectorAll('.editable-cell, .budget-ut, .budget-cnt, .budget-cst, .budget-cat').forEach(inp => {
      const evt = inp.tagName === 'SELECT' ? 'change' : 'input';
      inp.addEventListener(evt, e => {
        const idx = parseInt(e.target.dataset.idx, 10);
        const item = state.proposal.budget_data.items[idx];
        if (!item) return;
        const cls = e.target.className;
        if (cls.includes('budget-desc')) item.description = e.target.value;
        else if (cls.includes('budget-ut')) item.unit_type = e.target.value;
        else if (cls.includes('budget-cnt')) item.unit_count = parseFloat(e.target.value) || 0;
        else if (cls.includes('budget-cst')) item.unit_cost = parseFloat(e.target.value) || 0;
        else if (cls.includes('budget-cat')) item.category = e.target.value;
        // live total refresh
        const totalCell = e.target.closest('tr').querySelector('.budget-total');
        const total = (parseFloat(item.unit_count) || 0) * (parseFloat(item.unit_cost) || 0);
        if (totalCell) totalCell.textContent = total.toLocaleString('en-US', {style: 'currency', currency: 'USD'});
        triggerAutosave();
      });
    });

    container.querySelectorAll('.budget-del').forEach(btn => {
      btn.addEventListener('click', e => {
        const idx = parseInt(e.target.dataset.idx, 10);
        state.proposal.budget_data.items.splice(idx, 1);
        renderStep4Budget();
        triggerAutosave();
      });
    });
  }

  function switchStep4Subtab(subtab) {
    el.step4NarrativePane.style.display = subtab === 'narrative' ? 'block' : 'none';
    el.step4RiskPane.style.display = subtab === 'risk' ? 'block' : 'none';
    el.step4BudgetPane.style.display = subtab === 'budget' ? 'block' : 'none';
    // Per-subtab agent buttons (visible only for the active subtab)
    document.getElementById('btnAgentNarrative').style.display = subtab === 'narrative' ? 'inline-block' : 'none';
    document.getElementById('btnAgentRisk').style.display = subtab === 'risk' ? 'inline-block' : 'none';
    document.getElementById('btnAgentBudget').style.display = subtab === 'budget' ? 'inline-block' : 'none';
    if (subtab === 'risk') renderStep4Risk();
    if (subtab === 'budget') renderStep4Budget();
  }

  // ── Landing view: existing proposals (view/delete) ──────────────────────
  async function renderLanding() {
    document.getElementById('landingView').style.display = 'block';
    document.getElementById('workspace').style.display = 'none';
    document.getElementById('donorCallSection').style.display = 'none';
    const listEl = document.getElementById('landingProposalsList');
    try {
      const res = await api(apiPath('/api/proposals'));
      const props = res.proposals || [];
      if (!props.length) {
        listEl.innerHTML = `
          <div class="glass-card" style="text-align:center; padding:40px 20px;">
            <div style="font-size:15px; font-weight:600; color:var(--text); margin-bottom:6px;">No proposals yet</div>
            <div style="font-size:12.5px; color:var(--text-secondary); margin-bottom:16px;">Upload the donor call documents to extract the rules and start writing.</div>
            <button class="btn btn-primary" id="btnEmptyNew">+ New Proposal</button>
          </div>`;
        document.getElementById('btnEmptyNew').addEventListener('click', createNewProposal);
        return;
      }
      listEl.innerHTML = props.map(p => {
        const donor = esc(p.donor || '—');
        const step = Math.min(parseInt(p.step, 10) || 1, 5);
        const progress = Math.max(12, Math.round((step / 5) * 100));
        const updated = p.updated_at ? new Date(p.updated_at * 1000).toLocaleDateString() : '—';
        return `
          <article class="proposal-card glass-card" data-open="${esc(p.id)}" tabindex="0" aria-label="Open ${esc(p.title || 'Untitled proposal')}">
            <div class="proposal-card-top">
              <span class="proposal-donor"><span class="status-dot"></span>${donor}</span>
              <button class="icon-button proposal-menu" type="button" data-del="${esc(p.id)}" aria-label="Delete ${esc(p.title || 'proposal')}" title="Delete proposal">×</button>
            </div>
            <div class="proposal-card-body">
              <h3>${esc(p.title || 'Untitled proposal')}</h3>
              <p>${esc(p.country || 'Global')} · ${esc(p.theme || 'Multi-sector')}</p>
            </div>
            <div class="proposal-progress" aria-label="Step ${step} of 5">
              <div><span>Stage ${String(step).padStart(2, '0')} of 05</span><strong>${progress}%</strong></div>
              <span class="progress-track"><i style="width:${progress}%"></i></span>
            </div>
            <footer><span>Updated ${updated}</span><span class="open-arrow">Open <b>→</b></span></footer>
          </article>`;
      }).join('');
      listEl.querySelectorAll('[data-open]').forEach(card => {
        card.addEventListener('click', event => {
          if (!event.target.closest('[data-del]')) openProposalFromLanding(card.dataset.open);
        });
        card.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openProposalFromLanding(card.dataset.open);
          }
        });
      });
      listEl.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', async () => {
        const confirmed = await confirmAction({
          title: 'Delete proposal?',
          message: 'This permanently removes the proposal and its review history.',
          confirmLabel: 'Delete proposal',
          danger: true,
        });
        if (!confirmed) return;
        const activity = beginActivity({ title: 'Deleting proposal', detail: 'Removing the proposal and its review history…' });
        try {
          await api(apiPath(`/api/proposals/${b.dataset.del}`), { method: 'DELETE' });
          activity.success('Proposal deleted.');
          notify('Proposal deleted.', 'success');
          renderLanding();
        } catch (e) {
          activity.fail(`Proposal deletion failed: ${e.message}`);
          notify(`Delete failed: ${e.message}`, 'danger');
        }
      }));
    } catch (e) {
      listEl.innerHTML = `<div style="color:var(--red); font-size:13px;">Failed to load proposals: ${esc(e.message)}</div>`;
    }
  }

  async function openProposalFromLanding(pid) {
    document.getElementById('landingView').style.display = 'none';
    document.getElementById('workspace').style.display = 'block';
    await loadProposal(pid);
  }

  // ── Step 1: AI Context Drafting ─────────────────────────────────────────
  async function handleGenerateContext() {
    if (!state.activeProposalId) { notify('Create or select a proposal first.', 'info'); return; }
    // Push current Step 1 inputs into state and persist BEFORE drafting,
    // so the backend drafts for the country/theme the user actually typed.
    collectStep1Inputs();
    try {
      await api(apiPath(`/api/proposals/${state.activeProposalId}`), {
        method: 'PUT',
        body: JSON.stringify(state.proposal),
      });
    } catch (e) { /* continue anyway */ }
    const activity = beginActivity({
      title: 'Drafting proposal context',
      detail: 'Reading the donor rules and your project inputs…',
    });
    setButtonBusy(el.btnAiGenerateContext, true);
    el.btnAiGenerateContext.textContent = 'Drafting context...';
    try {
      const res = await api(apiPath(`/api/proposals/${state.activeProposalId}/generate-context`), { method: 'POST' });
      activity.update('Applying the generated context to your workspace…');
      const ctx = res.context_data || {};
      if (res.title) el.inputTitle.value = res.title;
      if (ctx.country) el.inputCountry.value = ctx.country;
      if (ctx.theme) el.inputTheme.value = ctx.theme;
      if (ctx.humanitarian_situation) el.inputHumSit.value = ctx.humanitarian_situation;
      if (ctx.needs_assessment) el.inputNeeds.value = ctx.needs_assessment;
      if (ctx.beneficiaries_total) el.inputBeneficiariesTotal.value = ctx.beneficiaries_total;
      if (ctx.beneficiaries_displaced) el.inputBeneficiariesDisplaced.value = ctx.beneficiaries_displaced;
      triggerAutosave();
      if (res.generation_mode === 'deterministic_fallback') {
        activity.success('Donor-rule context draft applied.');
        notify(res.notice || 'AI was unavailable, so a donor-rule context draft was applied for review.', 'info', 6500);
      } else {
        activity.success('AI context draft is ready and being autosaved.');
        notify('AI context draft generated and saved.', 'success');
      }
    } catch (e) {
      activity.fail(`Context drafting failed: ${e.message}`);
      notify(`Context draft failed: ${e.message}`, 'danger');
    } finally {
      setButtonBusy(el.btnAiGenerateContext, false);
      el.btnAiGenerateContext.textContent = 'Generate Context with AI';
    }
  }

  // ── Step 4: Per-subtab agents (Narrative | Risk | Budget) ────────────────
  async function handleAgentRisk() {
    if (!state.activeProposalId) return;
    const btn = document.getElementById('btnAgentRisk');
    const activity = beginActivity({ title: 'Drafting risk register', detail: 'Identifying risks, assumptions and mitigations…' });
    setButtonBusy(btn, true); btn.textContent = 'Drafting risks...';
    try {
      const res = await api(apiPath(`/api/proposal-v2/steps/4/generate-risk`), {
        method: 'POST',
        body: JSON.stringify({ proposal_id: state.activeProposalId }),
      });
      state.proposal.budget_data = state.proposal.budget_data || {};
      state.proposal.budget_data.risks = res.risks || [];
      renderStep4Risk();
      triggerAutosave();
      activity.success('Risk register generated and saved.');
    } catch (e) {
      activity.fail(`Risk drafting failed: ${e.message}`);
      notify(`Risk agent failed: ${e.message}`, 'danger');
    } finally {
      setButtonBusy(btn, false); btn.textContent = '🤖 Agent: Draft Risks';
    }
  }

  async function handleAgentBudget() {
    if (!state.activeProposalId) return;
    const btn = document.getElementById('btnAgentBudget');
    const activity = beginActivity({ title: 'Building proposal budget', detail: 'Aligning cost lines with the donor ceiling and rules…' });
    setButtonBusy(btn, true); btn.textContent = 'Drafting budget...';
    try {
      const res = await api(apiPath(`/api/proposal-v2/steps/4/generate-budget`), {
        method: 'POST',
        body: JSON.stringify({ proposal_id: state.activeProposalId }),
      });
      state.proposal.budget_data = state.proposal.budget_data || {};
      state.proposal.budget_data.items = res.items || [];
      state.proposal.budget_data.currency = res.currency || 'USD';
      renderStep4Budget();
      triggerAutosave();
      activity.success('Budget draft generated and saved.');
    } catch (e) {
      activity.fail(`Budget drafting failed: ${e.message}`);
      notify(`Budget agent failed: ${e.message}`, 'danger');
    } finally {
      setButtonBusy(btn, false); btn.textContent = '🤖 Agent: Draft Budget';
    }
  }

  // ── Donor Call Ingestion ────────────────────────────────────────────────
  async function handleIngestCall() {
    const files = el.callFileInput.files;
    if (!files || !files.length) { notify('Select at least one donor call document (PDF, DOCX or Markdown).', 'info'); return; }
    const callId = el.callIdInput.value.trim();
    const displayName = el.callNameInput.value.trim();

    const stopProgress = startUploadProgress(el.btnIngestCall);
    const activity = beginActivity({
      title: 'Processing donor call',
      detail: `Uploading and reading ${files.length} source document${files.length === 1 ? '' : 's'}…`,
    });
    notify('Upload started. Donor documents are being analysed.', 'info');
    try {
      const fd = new FormData();
      for (const f of files) fd.append('files', f);
      fd.append('call_id', callId);
      fd.append('display_name', displayName);
      const res = await fetch(apiPath('/api/calls/ingest'), { method: 'POST', body: fd });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      activity.update('Building the summary, requirements and donor rule preview…');
      renderCallIngestResult(body);
      await loadCallDrafts(body.draft_id);
      document.getElementById('callUploadPanel').hidden = true;
      activity.success(`${body.manifest_draft?.display_name || body.call_id} is ready for review.`);
      notify(`${body.manifest_draft?.display_name || body.call_id} uploaded successfully.`, 'success');
    } catch (e) {
      activity.fail(`Call processing failed: ${e.message}`);
      notify(`Ingest failed: ${e.message}`, 'danger');
    } finally {
      stopProgress();
    }
  }

  function startUploadProgress(button) {
    const startedAt = Date.now();
    setButtonBusy(button, true);
    const update = () => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      button.textContent = seconds < 4
        ? 'Uploading documents...'
        : `Analysing donor call · ${seconds}s`;
    };
    update();
    const timer = window.setInterval(update, 1000);
    return () => {
      window.clearInterval(timer);
      setButtonBusy(button, false);
      button.textContent = 'Extract donor intelligence';
    };
  }

  // ── Donor Intelligence (separate from the proposal wizard) ──────────────
  async function showDonorCallSection(focusCallId = '') {
    document.getElementById('landingView').style.display = 'none';
    document.getElementById('workspace').style.display = 'none';
    document.getElementById('donorCallSection').style.display = 'block';
    await loadCallDrafts('', focusCallId);
  }

  function renderCallIngestResult(body) {
    const draft = {
      id: body.draft_id,
      call_id: body.call_id,
      display_name: body.manifest_draft?.display_name || body.call_id,
      status: body.status || 'review',
      summary: body.summary,
      brief: body.brief,
      deadline: body.deadline,
      documents: body.documents || [],
      requirements: body.requirements || [],
      manifest: body.manifest_draft || {},
    };
    state.activeCallDraftId = draft.id;
    renderCallDetail(el.callIngestResult, draft, callIntelligenceHandlers());
  }

  async function publishDraft(draftId) {
    const activity = beginActivity({ title: 'Approving donor rules', detail: 'Validating the manifest before publication…' });
    try {
      const res = await api(apiPath(`/api/calls/drafts/${draftId}/publish`), { method: 'POST' });
      activity.update('Refreshing the approved source of truth…');
      notify(`Manifest published. The engine now scores against ${res.donor_id}.`, 'success');
      await loadCallDrafts(draftId);
      await viewCallIntelligence(draftId);
      activity.success('Donor rules approved and active.');
    } catch (e) {
      activity.fail(`Approval failed: ${e.message}`);
      notify(`Publish failed: ${e.message}`, 'danger');
    }
  }

  async function rejectDraft(draftId) {
    const activity = beginActivity({ title: 'Rejecting extracted rules', detail: 'Updating the call review status…' });
    try {
      await api(apiPath(`/api/calls/drafts/${draftId}/reject`), { method: 'POST' });
      notify('Call rules rejected. No proposal was created.', 'info');
      await loadCallDrafts(draftId);
      await viewCallIntelligence(draftId);
      activity.success('Call review marked as rejected.');
    } catch (e) {
      activity.fail(`Rejection failed: ${e.message}`);
      notify(`Reject failed: ${e.message}`, 'danger');
    }
  }

  async function deleteCall(draftId) {
    const draft = state.callDrafts.find(item => item.id === draftId);
    const confirmed = await confirmAction({
      title: 'Delete donor call?',
      message: `This removes “${draft?.display_name || 'this call'}” and its extracted rules. Calls already used by a proposal are protected.`,
      confirmLabel: 'Delete call',
      danger: true,
    });
    if (!confirmed) return;

    const activity = beginActivity({ title: 'Deleting donor call', detail: 'Checking proposal dependencies before removal…' });
    try {
      await api(apiPath(`/api/calls/drafts/${draftId}`), { method: 'DELETE' });
      state.activeCallDraftId = null;
      notify('Donor call deleted.', 'success');
      await loadCallDrafts();
      activity.success('Donor call removed.');
    } catch (e) {
      activity.fail(e.code === 'CALL_IN_USE' ? e.message : `Delete failed: ${e.message}`);
      notify(e.code === 'CALL_IN_USE' ? e.message : `Delete failed: ${e.message}`, 'danger');
    }
  }

  async function viewCallIntelligence(draftId) {
    el.callIngestResult.classList.add('is-refreshing');
    el.callIngestResult.setAttribute('aria-busy', 'true');
    try {
      const res = await api(apiPath(`/api/calls/drafts/${draftId}`));
      const draft = res.draft || {};
      state.activeCallDraftId = draftId;
      renderCallDetail(el.callIngestResult, draft, callIntelligenceHandlers());
      renderCallList(el.callDraftsList, state.callDrafts, draftId, callIntelligenceHandlers());
    } catch (e) {
      notify(`Failed to load donor intelligence: ${e.message}`, 'danger');
    } finally {
      el.callIngestResult.classList.remove('is-refreshing');
      el.callIngestResult.removeAttribute('aria-busy');
    }
  }

  function callIntelligenceHandlers() {
    return {
      onSelect: viewCallIntelligence,
      onPublish: publishDraft,
      onReject: rejectDraft,
      onStart: createProposalWithDonor,
      onDelete: deleteCall,
    };
  }

  async function loadCallDrafts(preferredDraftId = '', preferredCallId = '') {
    try {
      const res = await api(apiPath('/api/calls/drafts'));
      const drafts = res.drafts || [];
      state.callDrafts = drafts;
      const preferred = drafts.find(draft => draft.id === preferredDraftId)
        || drafts.find(draft => draft.call_id === preferredCallId)
        || drafts.find(draft => draft.id === state.activeCallDraftId)
        || drafts[0];
      state.activeCallDraftId = preferred?.id || null;
      renderCallList(el.callDraftsList, drafts, state.activeCallDraftId, callIntelligenceHandlers());
      if (preferred) await viewCallIntelligence(preferred.id);
    } catch (e) {
      el.callDraftsList.innerHTML = `<div class="intelligence-empty" style="min-height:220px;"><h3>Calls unavailable</h3><p>${esc(e.message)}</p></div>`;
    }
  }

  // ── Step 5: Verifier Audit & PDF ──────────────────────────────────────────
  function renderVerifier() {
    const rev = state.proposal?.review_data || {};
    const hasReview = typeof rev.score === 'number';
    const score = hasReview ? rev.score : null;
    const verdict = hasReview ? (rev.verdict || 'pending') : 'pending';
    const issues = rev.issues || [];

    el.verifierScoreVal.textContent = hasReview ? `${score.toFixed(0)}/100` : '—/100';
    el.scoreBanner.className = `score-banner ${verdict === 'fail' ? 'fail' : (verdict === 'pass' ? 'pass' : 'pending')}`;
    el.verifierSummaryText.textContent = rev.summary || 'Run the verifier to calculate a donor compliance score.';

    if (issues.length === 0) {
      el.verifierIssuesList.innerHTML = `
        <div style="color:#1e9e4f; font-size:12.5px; padding:12px; background:var(--green-light); border-radius:4px;">
          ${hasReview ? '✓ All donor constraints, character limits, and vulnerable population quotas satisfied.' : 'No audit findings yet. Run the verifier when the proposal is ready.'}
        </div>
      `;
    } else {
      let html = '';
      issues.forEach(iss => {
        const crit = iss.severity === 'critical';
        html += `
          <div class="issue-item ${crit ? 'critical' : ''}">
            <div class="issue-tag">
              ${crit ? 'CRITICAL COMPLIANCE ISSUE' : 'RECOMMENDED REFINEMENT'} • ${esc(iss.rule || 'rule')}
            </div>
            <div class="issue-msg">${esc(iss.message || iss.description || '')}</div>
            ${iss.recommendation ? `<div class="issue-rec"><em>Recommendation:</em> ${esc(iss.recommendation)}</div>` : ''}
          </div>
        `;
      });
      el.verifierIssuesList.innerHTML = html;
    }
  }

  // ── Donor Score Analysis (NotebookLM hybrid callout model) ────────────────
  async function runScoreAnalysis() {
    if (!state.activeProposalId) return;
    await saveCurrentState();
    try {
      const res = await api(apiPath(`/api/proposals/${state.activeProposalId}/analyze`), { method: 'POST' });
      renderAnalysis(res);
      return res;
    } catch (e) {
      console.error('Score analysis failed:', e);
      el.scoreTableContainer.innerHTML = `<div style="color:var(--red); font-size:12.5px;">Analysis failed: ${esc(e.message)}</div>`;
      return null;
    }
  }

  function renderAnalysis(analysis) {
    // 1) Global AUTOMATIC_REJECTION banner
    const elig = analysis.eligibility || {};
    const blocked = elig.status === 'AUTOMATIC_REJECTION';
    el.eligibilityBanner.style.display = blocked ? 'flex' : 'none';
    if (blocked) {
      const failed = (elig.failed_quotas || []).join(', ');
      el.eligibilityBannerText.textContent = `Proposal fails mandatory eligibility quotas: ${failed}. Fix these before submission.`;
    }

    // 2) Eligibility Gates checklist
    const checks = elig.checks || [];
    el.eligibilityChecklist.style.display = checks.length ? 'block' : 'none';
    if (checks.length) {
      el.eligibilityGatesList.innerHTML = checks.map(c => {
        let badge, label;
        if (!c.verifiable) { badge = 'unverified'; label = 'UNVERIFIED'; }
        else if (c.passed) { badge = 'pass'; label = 'PASS'; }
        else { badge = 'hard-fail'; label = 'HARD FAIL'; }
        return `
          <div class="gate-item">
            <span class="gate-badge ${badge}">${label}</span>
            <span class="gate-name">${esc(c.quota || '')}</span>
            <span class="gate-detail">${esc(c.details || '')}</span>
            ${badge === 'hard-fail'
              ? `<button class="gate-fix-btn" data-quota="${esc(c.quota || '')}">Ask Advisor to fix</button>`
              : ''}
          </div>
        `;
      }).join('');
      // Advisor remediation trigger on hard-fail badges
      el.eligibilityGatesList.querySelectorAll('.gate-fix-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const quota = btn.dataset.quota || '';
          const msg = `Fix the failed eligibility quota "${quota}" in this proposal. Suggest concrete content changes.`;
          el.advisorInput.value = msg;
          setStep(5);
          sendAdvisorMessage();
        });
      });
    }

    // 3) Interactive Score Breakdown Table
    const trace = analysis.trace || [];
    if (trace.length) {
      el.scoreTableContainer.innerHTML = `
        <table class="score-table">
          <thead>
            <tr><th>Criterion</th><th style="width:40%;">Score</th><th>Points</th><th></th></tr>
          </thead>
          <tbody>
            ${trace.map(t => {
              const pct = t.max_score ? (t.score / t.max_score) * 100 : 0;
              const barCls = pct >= 80 ? 'ok' : (pct >= 50 ? 'warn' : 'bad');
              return `
                <tr class="score-row" data-step="${esc(t.target_step || '')}" data-field="${esc(t.target_field || '')}"
                    data-criterion="${esc(t.criterion || '')}" title="${esc(t.details || '')}">
                  <td class="score-criterion">${esc(formatCriterion(t.criterion || ''))}</td>
                  <td>
                    <div class="score-bar-track"><div class="score-bar-fill ${barCls}" style="width:${pct}%"></div></div>
                  </td>
                  <td class="score-num-cell">${Number(t.score).toFixed(1)} / ${t.max_score}</td>
                  <td class="score-jump-hint">click to jump &rarr;</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;

      // Row click -> jump to editor field
      el.scoreTableContainer.querySelectorAll('.score-row').forEach(row => {
        row.addEventListener('click', () => {
          const step = parseInt(row.dataset.step.replace(/\D/g, ''), 10) || 2;
          const field = row.dataset.field;
          jumpToField(step, field);
        });
      });
    } else {
      el.scoreTableContainer.innerHTML = '<div style="color:var(--text-secondary); font-size:12.5px;">No score data available.</div>';
    }

    // 4) Reflect score in main banner
    if (typeof analysis.total_score === 'number') {
      el.verifierScoreVal.textContent = `${analysis.total_score.toFixed(1)}/100`;
      el.scoreBanner.className = `score-banner ${analysis.passed ? 'pass' : 'fail'}`;
      el.verifierSummaryText.textContent = blocked
        ? 'Proposal is BLOCKED by mandatory eligibility quotas.'
        : (analysis.passed ? 'Proposal passes donor compliance threshold.' : `Score below threshold (${analysis.pass_threshold}).`);
    }
  }

  function formatCriterion(value) {
    return String(value || '')
      .split('_')
      .filter(Boolean)
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  }

  // ── Jump-to-editor DOM navigation (target_step / target_field) ─────────────
  function jumpToField(step, field) {
    const contextMap = {
      title: 'inputTitle', country: 'inputCountry', humanitarian_situation: 'inputHumSit',
      needs_assessment: 'inputNeeds', beneficiaries: 'inputBeneficiariesTotal',
    };
    const narrativeFields = new Set([
      'project_summary', 'executive_summary', 'beneficiary_targeting', 'justification',
      'strategic_justification', 'context_relevance', 'program_rationale', 'risk_management',
      'sustainability_exit', 'methodology', 'capacity', 'cost_effectiveness',
      'sustainability_visibility',
    ]);

    let uiStep = Number(step) || 1;
    if (contextMap[field]) uiStep = 1;
    else if (field === 'logframe') uiStep = 3;
    else if (field === 'budget' || narrativeFields.has(field)) uiStep = 4;
    uiStep = Math.max(1, Math.min(5, uiStep));

    if (field === 'budget') state.step4Subtab = 'budget';
    if (narrativeFields.has(field)) {
      state.step4Subtab = 'narrative';
      state.activeNarrativeTab = field;
    }
    setStep(uiStep);

    setTimeout(() => {
      let targetEl = contextMap[field] ? document.getElementById(contextMap[field]) : null;
      if (field === 'logframe') targetEl = document.getElementById('logframeBody');
      if (field === 'budget') targetEl = document.getElementById('step4BudgetPane');
      if (narrativeFields.has(field)) targetEl = document.getElementById('narrativeSectionInput');

      if (!targetEl) {
        notify('The related section is not available in this donor profile.', 'info');
        return;
      }
      targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      targetEl.classList.add('field-highlight');
      if (typeof targetEl.focus === 'function') targetEl.focus({ preventScroll: true });
      setTimeout(() => targetEl.classList.remove('field-highlight'), 2200);
    }, 120);
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
    const activity = beginActivity({ title: 'Advisor is reviewing', detail: 'Checking your question against the active donor rules…' });
    setButtonBusy(el.btnSendAdvisor, true);

    try {
      const res = await api(apiPath(`/api/proposals/${state.activeProposalId}/advisor/chat`), {
        method: 'POST',
        body: JSON.stringify({ message: text, history }),
      });
      document.getElementById(loadId)?.remove();

      state.advisorHistory.push({ role: 'assistant', content: res.message });
      appendAdvisorBubble('advisor', res.message, res.patch);
      activity.success('Advisor response is ready.');
    } catch (e) {
      document.getElementById(loadId)?.remove();
      appendAdvisorBubble('advisor', `Sorry, error connecting to advisor: ${e.message}`);
      activity.fail(`Advisor failed: ${e.message}`);
    } finally {
      setButtonBusy(el.btnSendAdvisor, false);
    }
  }

  function appendAdvisorBubble(role, text, patch = null) {
    const bubble = document.createElement('div');
    bubble.className = `msg-bubble ${role === 'user' ? 'msg-user' : 'msg-advisor'}`;
    bubble.innerHTML = `<div>${esc(text).replace(/\n/g, '<br>')}</div>`;

    if (patch) {
      let patchBox = null;
      if (patch.action === 'update_logframe') {
        patchBox = document.createElement('div');
        patchBox.className = 'patch-box';
        patchBox.innerHTML = `
          <div class="patch-header">Recommended Logframe Refinement</div>
          <div class="patch-content">Row ${patch.row_index + 1} (${patch.field}): "${esc(patch.suggested_value)}"</div>
          <button class="btn btn-sm btn-primary" style="margin-top:6px; align-self:flex-start;">Apply Refinement to Logframe</button>
        `;
        patchBox.querySelector('button').addEventListener('click', () => {
          applyLogframePatch(patch);
        });
      } else if (patch.action === 'apply_suggestion' && patch.section_key && patch.suggested_text) {
        // Step B: generic remediation diff (narrative section, budget, logframe cell)
        patchBox = document.createElement('div');
        patchBox.className = 'patch-box';
        patchBox.innerHTML = `
          <div class="patch-header">Suggested ${esc(patch.rule_type || 'Refinement')}</div>
          <div class="patch-content">${esc(patch.suggested_text)}</div>
          ${patch.rationale ? `<div class="patch-rationale"><em>Why:</em> ${esc(patch.rationale)}</div>` : ''}
          <button class="btn btn-sm btn-primary" style="margin-top:6px; align-self:flex-start;">Apply Suggestion & Re-Score</button>
        `;
        patchBox.querySelector('button').addEventListener('click', () => {
          applySuggestionPatch(patch);
        });
      }
      if (patchBox) bubble.appendChild(patchBox);
    }

    el.advisorMessages.appendChild(bubble);
    el.advisorMessages.scrollTop = el.advisorMessages.scrollHeight;
  }

  // ── Step B: Apply generic remediation diff → autosave → instant re-score ──
  async function applySuggestionPatch(patch) {
    if (!patch.section_key || !patch.suggested_text) return;
    const field = patch.field || 'text';

    // Logframe cell (logframe.outcomes.N)
    const lfMatch = patch.section_key.match(/^logframe\.outcomes\.(\d+)$/);
    if (lfMatch && state.proposal?.logframe_data?.matrix) {
      const r = parseInt(lfMatch[1], 10);
      if (state.proposal.logframe_data.matrix[r]) {
        state.proposal.logframe_data.matrix[r][field] = patch.suggested_text;
        renderLogframe();
      }
    } else {
      // Narrative section (or budget note)
      state.proposal.narrative_data = state.proposal.narrative_data || {};
      state.proposal.narrative_data[patch.section_key] = patch.suggested_text;
      if (state.currentStep === 4) renderNarrative();
    }

    await saveCurrentState();
    appendAdvisorBubble('advisor', `✓ Applied suggestion to "${patch.section_key}". Re-scoring now...`);
    await runScoreAnalysis();  // instant POST /analyze re-score
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
    const activity = beginActivity({ title: 'Generating Theory of Change', detail: 'Connecting activities, outputs, outcomes and impact…' });
    setButtonBusy(el.btnAiGenerateToc, true);
    el.btnAiGenerateToc.textContent = 'Generating Theory of Change...';
    try {
      collectStep1Inputs();
      await saveCurrentState();
      activity.update('Building the causal pathway from the saved context…');
      const res = await api(apiPath(`/api/proposals/${state.activeProposalId}/generate-toc`), { method: 'POST' });
      state.proposal = res.proposal;
      renderToc();
      activity.success('Theory of Change generated and saved.');
      notify('Theory of Change generated and saved.', 'success');
    } catch (e) {
      activity.fail(`Theory of Change failed: ${e.message}`);
      notify(`Theory of Change generation failed: ${e.message}`, 'danger');
    } finally {
      setButtonBusy(el.btnAiGenerateToc, false);
      el.btnAiGenerateToc.textContent = 'Generate ToC with AI';
    }
  }

  async function handleGenerateLogframe() {
    if (!state.activeProposalId) return;
    const activity = beginActivity({ title: 'Generating logical framework', detail: 'Drafting results, indicators, verification and assumptions…' });
    setButtonBusy(el.btnAiGenerateLogframe, true);
    el.btnAiGenerateLogframe.textContent = 'Generating 4x4 Logframe...';
    try {
      const res = await api(apiPath(`/api/proposals/${state.activeProposalId}/generate-logframe`), { method: 'POST' });
      state.proposal = res.proposal;
      renderLogframe();
      activity.success('Logical framework generated and saved.');
      notify('Logframe generated and saved.', 'success');
    } catch (e) {
      activity.fail(`Logframe generation failed: ${e.message}`);
      notify(`Logframe generation failed: ${e.message}`, 'danger');
    } finally {
      setButtonBusy(el.btnAiGenerateLogframe, false);
      el.btnAiGenerateLogframe.textContent = 'Generate Logframe with AI';
    }
  }

  async function handleGenerateNarrative() {
    if (!state.activeProposalId) return;
    const activity = beginActivity({
      title: 'Preparing proposal narrative',
      detail: 'This may take several minutes. You may continue working in other sections…',
    });
    setButtonBusy(el.btnAiGenerateNarrative, true);
    el.btnAiGenerateNarrative.textContent = 'Preparing Narrative...';
    notify('Narrative preparation has started. You may continue working; the completed draft will be saved automatically.', 'info', 7600);
    try {
      const res = await api(apiPath(`/api/proposals/${state.activeProposalId}/generate-narrative`), { method: 'POST' });
      state.proposal = res.proposal;
      renderNarrative();
      activity.success('Narrative preparation is complete and the draft has been saved.');
      notify('Narrative preparation is complete. Your draft has been saved.', 'success');
    } catch (e) {
      activity.fail(`Narrative drafting failed: ${e.message}`);
      notify(`Narrative generation failed: ${e.message}`, 'danger');
    } finally {
      setButtonBusy(el.btnAiGenerateNarrative, false);
      el.btnAiGenerateNarrative.textContent = 'Draft All Sections with AI';
    }
  }

  async function handleRunVerifier() {
    if (!state.activeProposalId) return;
    const activity = beginActivity({ title: 'Running compliance audit', detail: 'Saving the latest changes before deterministic checks…' });
    setButtonBusy(el.btnRunVerifier, true);
    el.btnRunVerifier.textContent = 'Auditing Compliance...';
    try {
      await saveCurrentState();
      // Deterministic donor score analysis (YAML rules engine)
      activity.update('Checking eligibility gates, budget rules and required sections…');
      const analysis = await api(apiPath(`/api/proposals/${state.activeProposalId}/analyze`), { method: 'POST' });
      renderAnalysis(analysis);
      // LLM blind verifier (semantic layer)
      activity.update('Running the final semantic quality review…');
      const res = await api(apiPath(`/api/proposals/${state.activeProposalId}/verify`), { method: 'POST' });
      state.proposal = res.proposal;
      renderVerifier();
      activity.success('Compliance audit complete.');
      notify('Compliance audit complete.', 'success');
    } catch (e) {
      activity.fail(`Compliance audit failed: ${e.message}`);
      notify(`Compliance audit failed: ${e.message}`, 'danger');
    } finally {
      setButtonBusy(el.btnRunVerifier, false);
      el.btnRunVerifier.textContent = 'Run Verifier Audit';
    }
  }

  function handleExportPdf() {
    if (!state.activeProposalId) return;
    const activity = beginActivity({ title: 'Preparing proposal PDF', detail: 'Formatting the latest saved proposal for export…' });
    notify('PDF preparation started. Your download will begin shortly.', 'info');
    window.location.href = apiPath(`/api/proposals/${state.activeProposalId}/export/pdf`);
    window.setTimeout(() => activity.success('PDF export was sent to your downloads.'), 1400);
  }

  // ── Proposal Management ───────────────────────────────────────────────────
  async function loadProposal(id) {
    try {
      const res = await api(apiPath(`/api/proposals/${id}`));
      state.activeProposalId = id;
      state.proposal = res.proposal;
      state.activeNarrativeTab = null;
      state.step4Subtab = 'narrative';

      // Always switch to the wizard workspace when opening a proposal
      document.getElementById('landingView').style.display = 'none';
      document.getElementById('donorCallSection').style.display = 'none';
      document.getElementById('workspace').style.display = 'block';

      populateStep1();
      setStep(state.proposal.step || 1);
    } catch (e) {
      console.error('Failed to load proposal:', e);
    }
  }

  async function createNewProposal() {
    // Every new proposal begins from user-uploaded donor source documents.
    document.getElementById('newProposalModal').style.display = 'flex';
  }

  async function createProposalWithDonor(donor, displayName = '') {
    const activity = beginActivity({ title: 'Creating proposal workspace', detail: 'Connecting the approved call rules to a new proposal…' });
    try {
      const res = await api(apiPath('/api/proposals/new'), {
        method: 'POST',
        body: JSON.stringify({
          title: displayName ? `${displayName} Proposal` : 'Untitled Proposal',
          country: '',
          donor,
          theme: 'Multi-sector',
        }),
      });
      const newProp = res.proposal;
      // Switch to the wizard workspace (landing + donor section hidden)
      document.getElementById('landingView').style.display = 'none';
      document.getElementById('donorCallSection').style.display = 'none';
      document.getElementById('workspace').style.display = 'block';
      await loadProposal(newProp.id);
      activity.success('Proposal workspace is ready.');
    } catch (e) {
      activity.fail(`Proposal creation failed: ${e.message}`);
      notify(`Proposal creation failed: ${e.message}`, 'danger');
    }
  }

  async function loadDonors() {
    try {
      const res = await api(apiPath('/api/proposals/donors'));
      state.donors = res.donors || {};
      // Donor profiles remain available for generation rules, but the user does
      // not choose from a ready-made list. The uploaded call owns this field.
      const sel = el.selectDonor;
      sel.innerHTML = '<option value="">Upload a donor call to begin</option>';
    } catch (e) {
      console.error('Failed to load donor profiles:', e);
    }
  }

  async function navigateToSightline() {
    if (state.activeProposalId) await saveCurrentState();

    // CustomEvent for host SPA integration (e.g. embedded iframe)
    const navigationEvent = new CustomEvent('sightline:navigate', {
      cancelable: true,
      detail: { target: 'home', source: 'proposal-studio' },
    });
    const handledByHost = !window.dispatchEvent(navigationEvent);
    if (handledByHost) return;

    // If embedded in an iframe, postMessage to parent
    if (window.parent !== window) {
      window.parent.postMessage({ type: 'sightline:navigate', target: 'home', source: 'proposal-studio' }, '*');
      return;
    }

    // Standalone or reverse-proxy: navigate to Sightline home
    const sightlineHome = BASE ? BASE.replace('/proposal', '') || '/' : '/';
    window.location.assign(sightlineHome === '/' ? '/app' : sightlineHome);
  }

  async function goToProposalHome() {
    if (state.activeProposalId) await saveCurrentState();
    await renderLanding();
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

    // Step 4 sub-tabs (Narrative | Risk | Budget)
    if (el.step4SubTabs) {
      el.step4SubTabs.querySelectorAll('button').forEach(btn => {
        btn.addEventListener('click', () => {
          state.step4Subtab = btn.dataset.subtab || 'narrative';
          el.step4SubTabs.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === btn));
          switchStep4Subtab(state.step4Subtab);
        });
      });
    }

    // Step 1: AI Context Drafting
    el.btnAiGenerateContext.addEventListener('click', handleGenerateContext);

    // Step 2: ToC — manual node management
    document.getElementById('btnAddTocNode').addEventListener('click', addTocNode);

    // Step 3: Logframe — manual row management
    document.getElementById('btnAddLogframeRow').addEventListener('click', addLogframeRow);

    // Step 4: per-subtab agents
    document.getElementById('btnAgentRisk').addEventListener('click', handleAgentRisk);
    document.getElementById('btnAgentBudget').addEventListener('click', handleAgentBudget);
    document.getElementById('btnAgentNarrative').addEventListener('click', handleGenerateNarrative);

    // Donor Call Ingestion
    el.btnIngestCall.addEventListener('click', handleIngestCall);

    // New Proposal modal
    document.getElementById('btnCloseNewProposal').addEventListener('click', () => {
      document.getElementById('newProposalModal').style.display = 'none';
    });
    document.getElementById('btnModalIngest').addEventListener('click', async () => {
      const files = document.getElementById('modalCallFiles').files;
      if (!files || !files.length) { notify('Select at least one document.', 'info'); return; }
      const callId = document.getElementById('modalCallId').value.trim();
      const displayName = document.getElementById('modalCallName').value.trim();
      const fd = new FormData();
      for (const f of files) fd.append('files', f);
      fd.append('call_id', callId);
      fd.append('display_name', displayName);
      const button = document.getElementById('btnModalIngest');
      const stopProgress = startUploadProgress(button);
      const activity = beginActivity({
        title: 'Processing donor call',
        detail: `Uploading and reading ${files.length} source document${files.length === 1 ? '' : 's'}…`,
      });
      notify('Upload started. Donor documents are being analysed.', 'info');
      try {
        const res = await fetch(apiPath('/api/calls/ingest'), { method: 'POST', body: fd });
        const body = await res.json();
        if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
        activity.update('Building the summary, requirements and donor rule preview…');
        document.getElementById('newProposalModal').style.display = 'none';
        await showDonorCallSection(body.call_id);
        renderCallIngestResult(body);
        activity.success(`${body.manifest_draft?.display_name || body.call_id} is ready for review.`);
        notify(`${body.manifest_draft?.display_name || body.call_id} uploaded successfully.`, 'success');
      } catch (e) {
        activity.fail(`Call processing failed: ${e.message}`);
        notify(`Ingest failed: ${e.message}`, 'danger');
      } finally {
        stopProgress();
      }
    });

    // Product navigation
    document.getElementById('btnLandingNew').addEventListener('click', createNewProposal);
    document.getElementById('btnSightlineHome').addEventListener('click', navigateToSightline);
    document.getElementById('btnProposalHome').addEventListener('click', goToProposalHome);
    document.getElementById('btnDonorLibrary').addEventListener('click', () => {
      showDonorCallSection(state.proposal?.donor || '');
    });
    document.getElementById('btnBackToProposals').addEventListener('click', renderLanding);
    document.getElementById('btnToggleCallUpload').addEventListener('click', () => {
      const panel = document.getElementById('callUploadPanel');
      panel.hidden = !panel.hidden;
      if (!panel.hidden) el.callFileInput.focus();
    });

    // Advisor
    el.btnSendAdvisor.addEventListener('click', sendAdvisorMessage);
    document.getElementById('advisorFab').addEventListener('click', () => {
      const popup = document.getElementById('advisorPopup');
      const open = popup.style.display !== 'none';
      popup.style.display = open ? 'none' : 'flex';
      if (!open) el.advisorInput.focus();
    });
    document.getElementById('btnCloseAdvisor').addEventListener('click', () => {
      document.getElementById('advisorPopup').style.display = 'none';
    });
    el.advisorInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') sendAdvisorMessage();
    });
  }

  // ── Initialize App ────────────────────────────────────────────────────────
  async function init() {
    setupEventListeners();
    await loadDonors();
    // Landing view first: existing proposals (view/delete) + New CTA
    await renderLanding();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
