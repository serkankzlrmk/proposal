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
      el.autosaveIndicator.innerHTML = `<span style="color:var(--red)">Save failed: ${e.message}</span>`;
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
    if (state.currentStep === 4) {
      renderNarrative();
      switchStep4Subtab(state.step4Subtab || 'narrative');
    }
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
        <div style="text-align:center; padding:30px; width:100%; color:var(--text-secondary);">
          No Theory of Change generated yet. Click <strong>"Generate ToC with AI"</strong> to create the causal pathway.
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
          <div class="toc-node-label">${esc(node.label)}</div>
        </div>
      `;
      if (i < nodes.length - 1) {
        html += `<div class="toc-arrow">&rarr;</div>`;
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
          <td class="level-cell">${esc(row.level)}</td>
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
          ${esc(sec.title)} ${isOver ? '(OVER LIMIT)' : ''}
        </button>
      `;
    });
    el.narrativeTabsHeader.innerHTML = tabsHtml;

    // Render Active Section Textarea
    const curSec = profile.sections.find(s => s.key === state.activeNarrativeTab) || profile.sections[0];
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
    if (subtab === 'risk') renderStep4Risk();
    if (subtab === 'budget') renderStep4Budget();
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
        <div style="color:#1e9e4f; font-size:12.5px; padding:12px; background:var(--green-light); border-radius:4px;">
          ✓ All donor constraints, character limits, and vulnerable population quotas satisfied.
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
      const res = await api(`/api/proposals/${state.activeProposalId}/analyze`, { method: 'POST' });
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
                  <td class="score-criterion">${esc(t.criterion || '')}</td>
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

  // ── Jump-to-editor DOM navigation (target_step / target_field) ─────────────
  function jumpToField(step, field) {
    if (!step || step < 1 || step > 5) step = 2;
    setStep(step);

    setTimeout(() => {
      let targetEl = null;
      const map = {
        1: {
          title: 'inputTitle', country: 'inputCountry', humanitarian_situation: 'inputHumSit',
          needs_assessment: 'inputNeeds', beneficiaries: 'inputBeneficiariesTotal',
          project_summary: 'inputTitle', strategic_justification: 'inputNeeds',
        },
        3: { logframe: 'logframeBody' },
        4: { budget: 'narrativeSectionInput' },
      };
      // Trace target_step is a pipeline stage, not a UI step. Map stage names
      // to the correct UI step for narrative/section fields.
      if (['humanitarian_situation', 'needs_assessment', 'project_summary',
           'executive_summary', 'beneficiaries', 'beneficiary_targeting',
           'justification', 'strategic_justification', 'context_relevance',
           'program_rationale', 'risk_management', 'sustainability_exit',
           'methodology', 'capacity', 'cost_effectiveness',
           'sustainability_visibility', 'title', 'country'].includes(field)) {
        step = 1;
      }
      const id = (map[step] || {})[field] || (step === 2 ? 'inputTitle' : '');
      if (id) targetEl = document.getElementById(id);
      if (!targetEl && field && ['project_summary', 'executive_summary',
           'humanitarian_situation', 'needs_assessment', 'beneficiaries',
           'beneficiary_targeting', 'justification', 'strategic_justification',
           'risk_management', 'sustainability_exit', 'methodology', 'capacity',
           'cost_effectiveness', 'sustainability_visibility'].includes(field)) {
        // narrative field -> Step 4 tab
        step = 4;
        targetEl = document.getElementById('narrativeSectionInput');
      }
      if (!targetEl && step === 4) {
        // narrative: find matching section tab
        const tabs = el.narrativeTabsHeader?.querySelectorAll('button');
        if (tabs) {
          for (const t of tabs) {
            if (t.dataset.tab === field) { t.click(); break; }
          }
        }
        targetEl = document.getElementById('narrativeSectionInput');
      }
      if (targetEl) {
        targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        targetEl.style.outline = '2px solid var(--primary, #3b82f6)';
        targetEl.style.outlineOffset = '2px';
        targetEl.focus({ preventScroll: true });
        setTimeout(() => { targetEl.style.outline = ''; }, 2500);
      }
    }, 100);
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
    el.btnAiGenerateToc.disabled = true;
    el.btnAiGenerateToc.textContent = 'Generating Theory of Change...';
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
      el.btnAiGenerateToc.textContent = 'Generate ToC with AI';
    }
  }

  async function handleGenerateLogframe() {
    if (!state.activeProposalId) return;
    el.btnAiGenerateLogframe.disabled = true;
    el.btnAiGenerateLogframe.textContent = 'Generating 4x4 Logframe...';
    try {
      const res = await api(`/api/proposals/${state.activeProposalId}/generate-logframe`, { method: 'POST' });
      state.proposal = res.proposal;
      renderLogframe();
    } catch (e) {
      alert(`Error generating Logframe: ${e.message}`);
    } finally {
      el.btnAiGenerateLogframe.disabled = false;
      el.btnAiGenerateLogframe.textContent = 'Generate Logframe with AI';
    }
  }

  async function handleGenerateNarrative() {
    if (!state.activeProposalId) return;
    el.btnAiGenerateNarrative.disabled = true;
    el.btnAiGenerateNarrative.textContent = 'Drafting All Sections...';
    try {
      const res = await api(`/api/proposals/${state.activeProposalId}/generate-narrative`, { method: 'POST' });
      state.proposal = res.proposal;
      renderNarrative();
    } catch (e) {
      alert(`Error generating Narrative: ${e.message}`);
    } finally {
      el.btnAiGenerateNarrative.disabled = false;
      el.btnAiGenerateNarrative.textContent = 'Draft All Sections with AI';
    }
  }

  async function handleRunVerifier() {
    if (!state.activeProposalId) return;
    el.btnRunVerifier.disabled = true;
    el.btnRunVerifier.textContent = 'Auditing Compliance...';
    try {
      await saveCurrentState();
      // Deterministic donor score analysis (YAML rules engine)
      const analysis = await api(`/api/proposals/${state.activeProposalId}/analyze`, { method: 'POST' });
      renderAnalysis(analysis);
      // LLM blind verifier (semantic layer)
      const res = await api(`/api/proposals/${state.activeProposalId}/verify`, { method: 'POST' });
      state.proposal = res.proposal;
      renderVerifier();
    } catch (e) {
      alert(`Error auditing: ${e.message}`);
    } finally {
      el.btnRunVerifier.disabled = false;
      el.btnRunVerifier.textContent = 'Run Verifier Audit';
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
