/** Visual state projection for immutable proposal steps. */

const NAVIGATION_IDS = new Set([
  'btnSaveStep1', 'btnNextStep2', 'btnNextStep3', 'btnNextStep4',
]);

export function applyStepLockState(proposal, activeStep) {
  const container = document.getElementById(`stepContainer${activeStep}`);
  if (!container) return;

  const locked = new Set((proposal && proposal.locked_steps) || []);
  const isLocked = locked.has(Number(activeStep));
  let banner = container.querySelector('.step-lock-banner');

  if (isLocked && !banner) {
    banner = document.createElement('div');
    banner.className = 'step-lock-banner';
    banner.innerHTML = '<span class="lock-symbol" aria-hidden="true">◆</span><span><strong>Approved snapshot</strong><small>This step is locked and preserved as an immutable record.</small></span>';
    container.prepend(banner);
  } else if (!isLocked && banner) {
    banner.remove();
  }

  container.querySelectorAll('input, textarea, select, button').forEach(control => {
    if (NAVIGATION_IDS.has(control.id)) return;
    if (control.closest('.step-lock-banner')) return;
    if (isLocked) {
      if (!control.disabled) control.dataset.lockDisabled = 'true';
      control.disabled = true;
    } else if (control.dataset.lockDisabled === 'true') {
      control.disabled = false;
      delete control.dataset.lockDisabled;
    }
  });

  container.classList.toggle('is-locked', isLocked);
}
