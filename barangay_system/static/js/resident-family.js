(() => {
  const root = document.querySelector('[data-resident-family]');
  if (!root) return;
  const head = root.querySelector('#is_family_head');
  const disability = root.querySelector('#has_disability');
  const type = root.querySelector('#disability_type');
  const section = root.querySelector('#familyMembersSection');
  const rows = root.querySelector('#familyMemberRows');
  function sync() {
    const hasFamily = head.value === 'Yes';
    section.hidden = !hasFamily;
    rows.querySelectorAll('input, select').forEach(field => { field.disabled = !hasFamily; });
    const hasDisability = disability.value === 'Yes';
    root.querySelector('#residentDisabilityType').hidden = !hasDisability;
    type.disabled = !hasDisability;
    type.required = false;
    const other = hasDisability && type.value === 'Other';
    root.querySelector('#residentDisabilityOther').hidden = !other;
    root.querySelector('#disability_other').disabled = !other;
  }
  root.querySelector('#addFamilyMember').addEventListener('click', () => {
    rows.append(document.querySelector('#familyMemberTemplate').content.cloneNode(true));
    sync();
    rows.lastElementChild.querySelector('[name=family_name]').focus();
  });
  rows.addEventListener('click', event => {
    if (event.target.closest('[data-remove-family]')) event.target.closest('.family-member-row').remove();
  });
  [head, disability, type].forEach(field => field.addEventListener('change', sync));
  sync();
})();
