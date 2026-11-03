(function (window, document) {
  'use strict';

  const pendingButtons = new Map();
  const pendingForms = new Map();
  const nativeSubmitRecoveryMs = 30000;

  // Return an idempotent release function, or null if this action is busy.
  function begin(button) {
    if (!button || button.disabled || pendingButtons.has(button)) return null;
    const previousBusy = button.getAttribute('aria-busy');
    const spinner = document.createElement('span');
    spinner.className = 'spinner-border spinner-border-sm mr-1';
    spinner.setAttribute('aria-hidden', 'true');
    if (button.tagName === 'BUTTON') button.prepend(spinner);
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');

    function release() {
      if (pendingButtons.get(button) !== release) return;
      pendingButtons.delete(button);
      spinner.remove();
      button.disabled = false;
      if (previousBusy === null) button.removeAttribute('aria-busy');
      else button.setAttribute('aria-busy', previousBusy);
    }
    pendingButtons.set(button, release);
    return release;
  }

  function releaseForm(form, state) {
    if (pendingForms.get(form) !== state) return;
    window.clearTimeout(state.timer);
    state.releases.forEach(release => release());
    if (state.submittedValue) state.submittedValue.remove();
    pendingForms.delete(form);
  }

  document.addEventListener('submit', function (event) {
    const form = event.target;
    if (!form.matches('form[data-single-submit]') || event.defaultPrevented) return;
    if (pendingForms.has(form)) {
      event.preventDefault();
      return;
    }

    // Disabled submit buttons are omitted from native form serialization.
    const submitter = event.submitter;
    let submittedValue = null;
    if (submitter && submitter.name && !submitter.disabled) {
      submittedValue = document.createElement('input');
      submittedValue.type = 'hidden';
      submittedValue.name = submitter.name;
      submittedValue.value = submitter.value;
      form.appendChild(submittedValue);
    }
    const releases = Array.from(form.elements)
      .filter(control => control.type === 'submit')
      .map(begin)
      .filter(Boolean);
    const state = { releases: releases, submittedValue: submittedValue, timer: null };
    pendingForms.set(form, state);
    // Native navigation has no reliable failure event. Bound the UI lock if
    // loading is stopped or fails while this document remains visible.
    // This permits retry; it does not cancel or undo the submitted request.
    state.timer = window.setTimeout(function () {
      releaseForm(form, state);
    }, nativeSubmitRecoveryMs);
  });

  // A browser back/forward-cache restore retains the old disabled controls.
  window.addEventListener('pageshow', function () {
    pendingForms.forEach(function (state, form) {
      releaseForm(form, state);
    });
  });

  window.PoiPendingActions = { begin: begin };
})(window, document);
