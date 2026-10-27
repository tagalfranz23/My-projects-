(() => {
  const form = document.querySelector('[data-event-status-form]');
  if (!form) return;
  const button = form.querySelector('button[type="submit"]');
  const status = form.querySelector('[name="status"]');
  const label = button.textContent;
  const sync = () => {
    button.disabled = form.dataset.submitting === 'true' || !status.value || status.selectedOptions[0]?.disabled;
  };
  form.addEventListener('submit', event => {
    if (form.dataset.submitting === 'true') {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = 'true';
    button.textContent = 'Updating…';
    sync();
  });
  status.addEventListener('change', sync);
  window.addEventListener('pageshow', () => {
    delete form.dataset.submitting;
    button.textContent = label;
    sync();
  });
  sync();
})();
