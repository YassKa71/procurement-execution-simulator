(() => {
  const root = document.querySelector('.case-workspace');
  if (!root) return;
  const tabs = [...root.querySelectorAll('.workspace-tabs [role="tab"]')];
  const menus = [...root.querySelectorAll('.action-menu')];
  const closeMenus = (except) => menus.forEach(menu => { if (menu !== except) menu.open = false; });
  function selectTab(name, updateURL = true) {
    if (!['overview', 'requirements', 'goods', 'history'].includes(name)) name = 'overview';
    root.querySelectorAll('.workspace-panel').forEach(panel => { panel.hidden = panel.id !== `panel-${name}`; });
    tabs.forEach(tab => { const selected = tab.dataset.tabLink === name; tab.setAttribute('aria-selected', selected); tab.tabIndex = selected ? 0 : -1; });
    if (updateURL) { const url = new URL(location.href); url.searchParams.set('tab', name); history.pushState({}, '', url); }
    closeMenus();
  }
  selectTab(tabs.find(tab => tab.getAttribute('aria-selected') === 'true')?.dataset.tabLink, false);
  root.querySelectorAll('[data-tab-link]').forEach(link => link.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); selectTab(link.dataset.tabLink);
  }));
  tabs.forEach((tab, index) => tab.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = tabs.length - 1;
    if (next !== undefined) { event.preventDefault(); selectTab(tabs[next].dataset.tabLink); tabs[next].focus(); }
  }));
  window.addEventListener('popstate', () => selectTab(new URL(location.href).searchParams.get('tab') || 'overview', false));
  menus.forEach(menu => menu.addEventListener('toggle', () => {
    if (!menu.open) return;
    closeMenus(menu);
    const list = menu.querySelector('.menu-items');
    const rect = menu.querySelector('summary').getBoundingClientRect();
    list.style.position = 'fixed';
    list.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - list.offsetWidth - 8))}px`;
    const below = innerHeight - rect.bottom - 12;
    if (below >= Math.min(list.scrollHeight, 200)) { list.style.top = `${rect.bottom + 6}px`; list.style.bottom = 'auto'; list.style.maxHeight = `${below}px`; }
    else { list.style.top = 'auto'; list.style.bottom = `${innerHeight - rect.top + 6}px`; list.style.maxHeight = `${Math.max(100, rect.top - 12)}px`; }
  }));
  document.addEventListener('click', event => { if (!event.target.closest('.action-menu')) closeMenus(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') { const opened = menus.find(menu => menu.open); closeMenus(); opened?.querySelector('summary').focus(); } });
  document.addEventListener('scroll', event => { if (!event.target.closest?.('.menu-items')) closeMenus(); }, true);
  window.addEventListener('resize', () => { closeMenus(); syncResources(); });
  const resourcePanel = root.querySelector('.workspace-resources');
  const resourceToggle = root.querySelector('.resource-toggle');
  function syncResources() { resourceToggle.setAttribute('aria-expanded', getComputedStyle(resourcePanel).display !== 'none'); }
  resourceToggle.addEventListener('click', () => { root.dataset.resources = getComputedStyle(resourcePanel).display === 'none' ? 'open' : 'closed'; syncResources(); });
  root.querySelector('.resource-close').addEventListener('click', () => { root.dataset.resources = 'closed'; syncResources(); resourceToggle.focus(); });
  syncResources();
  const drawer = document.querySelector('#action-drawer');
  const frame = document.querySelector('#action-frame');
  const loading = drawer.querySelector('.drawer-loading');
  let dirty = false;
  let submitting = false;
  let trigger = null;
  let fullPageURL = '';
  function closeDrawer() {
    if (submitting) return;
    if (dirty && !confirm('Discard the changes in this form?')) return;
    drawer.close(); frame.src = 'about:blank'; dirty = false; trigger?.focus();
  }
  root.querySelectorAll('[data-drawer]').forEach(link => link.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || typeof drawer.showModal !== 'function') return;
    event.preventDefault(); closeMenus(); trigger = link; dirty = false; submitting = false; fullPageURL = link.href;
    drawer.querySelector('#drawer-title').textContent = link.textContent.trim();
    loading.hidden = false; loading.textContent = 'Loading form...';
    const fallback = document.createElement('a'); fallback.href = fullPageURL; fallback.textContent = ' Open full page'; loading.append(fallback);
    const url = new URL(fullPageURL); url.searchParams.set('panel', '1'); frame.src = url;
    drawer.showModal();
  }));
  frame.addEventListener('load', () => {
    if (!drawer.open || frame.getAttribute('src') === 'about:blank') return;
    submitting = false;
    const doc = frame.contentDocument;
    if (!doc || !doc.body || !doc.body.childElementCount) return;
    if (doc.querySelector('[data-action-saved]')) { dirty = false; location.reload(); return; }
    loading.hidden = true;
    if (doc.title) drawer.querySelector('#drawer-title').textContent = doc.title;
    const form = doc.querySelector('form');
    if (form) {
      form.addEventListener('input', () => { dirty = true; });
      form.addEventListener('change', () => { dirty = true; });
      form.addEventListener('submit', () => { submitting = true; form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; button.textContent = 'Saving...'; }); });
      const invalid = doc.querySelector('.errorlist');
      if (invalid) invalid.setAttribute('role', 'alert');
      (doc.querySelector('input:not([type="hidden"]), textarea, select') || form).focus();
    }
    doc.querySelectorAll('a').forEach(link => {
      if (new URL(link.href).pathname === root.dataset.caseUrl) link.addEventListener('click', event => { event.preventDefault(); closeDrawer(); });
    });
  });
  drawer.querySelector('[data-close-drawer]').addEventListener('click', closeDrawer);
  drawer.addEventListener('cancel', event => { event.preventDefault(); closeDrawer(); });
  const preview = document.querySelector('#preview-dialog');
  const content = preview.querySelector('.preview-content');
  let blobURL = null;
  let previewAbort = null;
  let previewTrigger = null;
  function cleanPreview() { previewAbort?.abort(); if (blobURL) URL.revokeObjectURL(blobURL); blobURL = null; content.replaceChildren(); previewTrigger?.focus(); }
  preview.querySelector('[data-close-preview]').addEventListener('click', () => preview.close());
  preview.addEventListener('close', cleanPreview);
  root.querySelectorAll('[data-resource-name]').forEach(link => {
    const extension = link.dataset.resourceName.split('.').pop().toLowerCase();
    const types = {pdf:'application/pdf', png:'image/png', jpg:'image/jpeg', jpeg:'image/jpeg', gif:'image/gif', webp:'image/webp'};
    if (!types[extension]) return;
    const button = document.createElement('button'); button.type = 'button'; button.className = 'resource-preview'; button.textContent = 'Preview';
    link.after(button);
    button.addEventListener('click', async () => {
      previewTrigger = button; preview.querySelector('#preview-download').href = link.href;
      preview.querySelector('#preview-title').textContent = link.textContent.replace(/^Download /, 'Preview ');
      content.textContent = 'Loading preview...'; preview.showModal(); previewAbort = new AbortController();
      try {
        const response = await fetch(link.href, {credentials:'same-origin', signal:previewAbort.signal});
        if (!response.ok || !response.headers.get('Content-Disposition')?.includes('attachment')) throw new Error('Preview unavailable. Use the download link.');
        if (Number(response.headers.get('Content-Length')) > 20 * 1024 * 1024) throw new Error('This file is too large to preview. Download it to view.');
        const blob = await response.blob();
        if (blob.size > 20 * 1024 * 1024) throw new Error('This file is too large to preview. Download it to view.');
        blobURL = URL.createObjectURL(new Blob([blob], {type:types[extension]}));
        const element = document.createElement(extension === 'pdf' ? 'iframe' : 'img');
        if (extension === 'pdf') { element.title = 'PDF document preview'; element.setAttribute('sandbox', ''); }
        else element.alt = 'Attached resource preview';
        element.src = blobURL; content.replaceChildren(element);
      } catch (error) { if (error.name !== 'AbortError') content.textContent = error.message; }
    });
  });
})();
