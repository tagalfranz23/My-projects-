(() => {
  const residentId = document.getElementById('residentId');
  const service = document.getElementById('walkinService');
  const permit = document.getElementById('walkinPermitType');
  const related = document.getElementById('walkinRelated');
  const relatedStatus = document.getElementById('walkinRelatedStatus');
  const followup = document.getElementById('walkinFollowup');
  if (!residentId || !service) return;
  let choicesFor = null;
  let pending = null;

  async function loadTransactions() {
    if (!residentId.value || choicesFor === residentId.value) return;
    if (pending) pending.abort();
    pending = new AbortController();
    const selectedId = residentId.value;
    related.replaceChildren(new Option('Choose transaction', ''));
    relatedStatus.textContent = 'Loading this resident’s transactions…';
    try {
      const response = await fetch(`/front-desk/residents/${encodeURIComponent(selectedId)}/transactions`, {signal: pending.signal});
      if (!response.ok) throw new Error('Unable to load transactions');
      const choices = await response.json();
      if (residentId.value !== selectedId) return;
      followup.replaceChildren();
      choices.forEach(item => {
        if (item.schedulable) related.add(new Option(item.label, item.value));
        const row = document.createElement('p');
        row.textContent = item.label + ' ';
        if (item.slip_url) {
          const link = document.createElement('a');
          link.href = item.slip_url;
          link.textContent = 'Print follow-up reference';
          row.append(link);
        }
        followup.append(row);
      });
      if (!choices.length) followup.textContent = 'No requests have been recorded for this resident yet.';
      choicesFor = selectedId;
      relatedStatus.textContent = choices.some(item => item.schedulable) ? '' : 'Record a permit, event, or blotter request for this resident first.';
    } catch (error) {
      if (error.name !== 'AbortError') {
        relatedStatus.textContent = 'Could not load transactions. Try again.';
        followup.textContent = 'Could not load references. Try again.';
      }
    }
  }

  function sync() {
    document.querySelectorAll('[data-walkin-kind]').forEach(section => {
      const active = section.dataset.walkinKind === service.value;
      section.hidden = !active;
      section.querySelectorAll('input,select,textarea').forEach(field => { field.disabled = !active; });
    });
    document.querySelectorAll('.walkin-requirements').forEach(section => {
      const active = service.value === 'permit' && section.dataset.service === permit.value;
      section.hidden = !active;
      section.querySelectorAll('input,textarea').forEach(field => {
        field.disabled = !active;
        field.required = active && field.dataset.required === '1';
      });
    });
    if (service.value === 'schedule') loadTransactions();
  }
  document.querySelectorAll('[data-resident]').forEach(button => button.addEventListener('click', () => {
    window.location.assign(`/front-desk?resident_id=${encodeURIComponent(button.dataset.resident)}#walkinForm`);
  }));
  service.addEventListener('change', sync);
  document.getElementById('walkinFollowupButton').addEventListener('click', () => {
    choicesFor = null;
    loadTransactions();
  });
  permit.addEventListener('change', sync);
  sync();
})();
