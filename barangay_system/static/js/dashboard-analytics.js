/* Refresh only the admin analytics fragment; retain data tables and page position. */
(() => {
  const root = document.getElementById('barangay-analytics');
  if (!root) return;
  const button = root.querySelector('[data-analytics-refresh]');
  const state = root.querySelector('[data-analytics-state]');
  let busy = false;
  let lastContent = root.querySelector('[data-analytics-content]').outerHTML;
  async function refresh() {
    if (busy || document.hidden) return;
    busy = true; button.disabled = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(root.dataset.refreshUrl, {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal
      });
      if (!response.ok || response.redirected) throw new Error('Refresh unavailable');
      const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
      const next = parsed.querySelector('[data-analytics-content]');
      if (!next) throw new Error('Unexpected response');
      const html = next.outerHTML;
      if (html !== lastContent) {
        const current = root.querySelector('[data-analytics-content]');
        const open = [...current.querySelectorAll('details[open]')].map(item => item.id);
        const active = document.activeElement;
        const focusId = active?.closest('details')?.id;
        // Avoid interrupting keyboard navigation through a module link.
        const focusHref = active?.closest('.analytics-overview-card')?.getAttribute('href');
        current.replaceWith(next);
        open.forEach(id => { const detail = document.getElementById(id); if (detail) detail.open = true; });
        if (focusId) document.getElementById(focusId)?.querySelector('summary')?.focus({preventScroll:true});
        if (focusHref) [...next.querySelectorAll('.analytics-overview-card')].find(a => a.getAttribute('href') === focusHref)?.focus({preventScroll:true});
        document.dispatchEvent(new CustomEvent('charts:refresh', {detail: next}));
        lastContent = html;
      }
      state.textContent = 'Updated ' + new Date().toLocaleTimeString('en-PH', {hour:'2-digit', minute:'2-digit'}) + ' · Auto-refresh: 30s';
    } catch (_) {
      state.textContent = 'Could not refresh. Showing the last loaded data; retrying automatically.';
    } finally {
      clearTimeout(timeout); busy = false; button.disabled = false;
    }
  }
  button.addEventListener('click', refresh);
  setInterval(refresh, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
