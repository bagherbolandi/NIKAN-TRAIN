/* Progressive enhancement for reference lists. Native selects remain the form controls. */
(() => {
  'use strict';
  const states = new WeakMap();
  const live = new Set();
  const MAX_RESULTS = 60;
  let sequence = 0;
  const referenceNames = new Set(['skill_id', 'employee_id', 'course_id', 'company_id', 'job', 'department', 'instructor', 'category']);
  const referenceIds = /^(company|person|approved-job|participant-company|job-department|person-department)$/;
  const normalize = value => String(value ?? '').normalize('NFKC').toLowerCase()
    .replace(/[يى]/g, 'ی').replace(/ك/g, 'ک').replace(/[أإٱآ]/g, 'ا').replace(/ة/g, 'ه')
    .replace(/[\u064b-\u065f\u0670\u0640]/g, '').replace(/[\u200c\u200d]/g, ' ')
    .replace(/[۰-۹]/g, digit => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(digit)))
    .replace(/[٠-٩]/g, digit => String('٠١٢٣٤٥٦٧٨٩'.indexOf(digit)))
    .replace(/\s+/g, ' ').trim();
  const number = value => Number(value).toLocaleString('fa-IR');

  function eligible(select) {
    if (select.multiple || select.size > 1 || select.hasAttribute('data-level') || select.dataset.searchable === 'false') return false;
    return select.dataset.searchable === 'true' || referenceNames.has(select.name) || referenceIds.test(select.id) || select.options.length > 6;
  }

  function caption(select) {
    const labelledBy = (select.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => document.getElementById(id)?.textContent.trim()).filter(Boolean).join(' ');
    if (labelledBy) return labelledBy;
    if (select.getAttribute('aria-label')) return select.getAttribute('aria-label');
    const labels = select.labels?.length ? select.labels : states.get(select)?.labels;
    if (labels?.length) {
      const label = labels[0].cloneNode(true);
      label.querySelectorAll('select,input,button,textarea,.select-search').forEach(node => node.remove());
      if (label.textContent.trim()) return label.textContent.trim();
    }
    return select.title || 'انتخاب از فهرست';
  }

  function internal(state, action) {
    state.internal = true;
    try { action(); } finally { state.internal = false; }
  }

  function validity(state) {
    const missing = state.select.required && (!state.select.value || state.select.selectedIndex < 0);
    const invalid = missing || (state.editing && state.input.value.trim() !== '');
    const message = invalid ? 'یکی از موارد فهرست را انتخاب کنید.' : '';
    state.input.setCustomValidity(message);
    state.select.setCustomValidity(message);
    if (invalid && state.showInvalid) state.input.setAttribute('aria-invalid', 'true');
    else state.input.removeAttribute('aria-invalid');
    return !invalid;
  }

  function sync(state, rebuild = false, preserveEditing = false) {
    if (!state.select.isConnected) return;
    const editing = preserveEditing && state.editing;
    // Adding options can make the browser select the first item implicitly.
    // Keep a typed query uncommitted until a person actually chooses a result.
    if (editing) internal(state, () => { state.select.selectedIndex = -1; });
    if (rebuild) {
      state.items = Array.from(state.select.options, (option, index) => ({
        option, index, value: option.value, label: option.label || option.textContent,
        disabled: option.disabled || (option.parentElement?.tagName === 'OPTGROUP' && option.parentElement.disabled),
        search: normalize(`${option.label || option.textContent} ${option.value}`),
      }));
    }
    const selected = state.items.find(item => item.index === state.select.selectedIndex);
    if (!editing) {
      state.committed = selected?.option || null;
      state.editing = false;
      state.showInvalid = false;
      state.input.value = selected?.label || '';
    }
    state.input.disabled = state.select.matches(':disabled');
    state.readonly = state.select.hasAttribute('readonly') || state.select.dataset.readonly === 'true';
    state.input.readOnly = state.readonly;
    state.input.required = state.select.required;
    state.input.setAttribute('aria-required', String(state.select.required));
    state.input.setAttribute('aria-label', caption(state.select));
    state.list.setAttribute('aria-label', caption(state.select));
    if (state.select.hasAttribute('form')) state.input.setAttribute('form', state.select.getAttribute('form'));
    else state.input.removeAttribute('form');
    state.input.dataset.selectSearchFor = state.select.id || state.select.name;
    state.toggle.disabled = state.input.disabled || state.readonly;
    state.clear.hidden = state.select.required || !state.items.some(item => item.value === '') || !selected?.value;
    state.clear.disabled = state.input.disabled || state.readonly;
    state.wrapper.classList.toggle('is-disabled', state.input.disabled);
    validity(state);
    if (state.input.disabled || state.readonly) close(state);
    else if (state.open) render(state, editing ? state.input.value : '');
  }

  function queueSync(state) {
    if (state.syncPending) return;
    state.syncPending = true;
    queueMicrotask(() => {
      state.syncPending = false;
      if (states.get(state.select) === state) sync(state);
    });
  }

  function position(state) {
    if (!state.open) return;
    const rect = state.wrapper.getBoundingClientRect();
    const viewport = window.visualViewport;
    const topEdge = viewport?.offsetTop || 0;
    const leftEdge = viewport?.offsetLeft || 0;
    const viewportWidth = viewport?.width || window.innerWidth;
    const bottomEdge = topEdge + (viewport?.height || window.innerHeight);
    const spaceBelow = bottomEdge - rect.bottom - 10;
    const spaceAbove = rect.top - topEdge - 10;
    const above = spaceBelow < 180 && spaceAbove > spaceBelow;
    const available = Math.max(80, Math.min(320, above ? spaceAbove : spaceBelow));
    const width = Math.min(rect.width, viewportWidth - 16);
    state.popup.style.width = `${width}px`;
    state.popup.style.left = `${Math.max(leftEdge + 8, Math.min(rect.left, leftEdge + viewportWidth - width - 8))}px`;
    state.popup.style.maxHeight = `${available}px`;
    state.list.style.maxHeight = `${Math.max(40, available - 48)}px`;
    state.popup.style.top = above ? `${rect.top - Math.min(state.popup.scrollHeight, available) - 5}px` : `${rect.bottom + 5}px`;
  }

  function activate(state, index, scroll = false) {
    state.active = index;
    Array.from(state.list.children).forEach((node, i) => node.classList.toggle('is-active', i === index));
    const active = state.list.children[index];
    if (active) {
      state.input.setAttribute('aria-activedescendant', active.id);
      if (scroll) active.scrollIntoView({block: 'nearest'});
    } else state.input.removeAttribute('aria-activedescendant');
  }

  function render(state, query) {
    const tokens = normalize(query).split(' ').filter(Boolean);
    const matched = [];
    let count = 0;
    for (const item of state.items) {
      if (tokens.every(token => item.search.includes(token))) {
        count += 1;
        if (matched.length < MAX_RESULTS) matched.push(item);
      }
    }
    state.visible = matched;
    const fragment = document.createDocumentFragment();
    for (let i = 0; i < matched.length; i += 1) {
      const item = matched[i];
      const node = document.createElement('div');
      node.id = `${state.list.id}-option-${i}`;
      node.className = 'select-search-option';
      node.setAttribute('role', 'option');
      node.setAttribute('aria-selected', String(item.index === state.select.selectedIndex));
      node.setAttribute('aria-disabled', String(item.disabled));
      node.dataset.result = String(i);
      node.textContent = item.label;
      fragment.append(node);
    }
    state.list.replaceChildren(fragment);
    state.status.textContent = !count ? 'موردی یافت نشد؛ عبارت دیگری بنویسید.'
      : count > MAX_RESULTS ? `نمایش ${number(MAX_RESULTS)} مورد از ${number(count)}؛ برای نتیجه دقیق‌تر تایپ کنید.`
      : `${number(count)} مورد؛ با کلیدهای جهت و Enter انتخاب کنید.`;
    let index = matched.findIndex(item => item.index === state.select.selectedIndex && !item.disabled);
    if (index < 0) index = matched.findIndex(item => !item.disabled);
    activate(state, index);
    position(state);
  }

  function open(state) {
    if (state.input.disabled || state.readonly) return;
    for (const other of live) if (other !== state && other.open) close(other);
    state.open = true;
    state.popup.hidden = false;
    // A top-layer popover stays visible above modal dialogs and scrolling panels.
    if (typeof state.popup.showPopover === 'function' && !state.popup.matches(':popover-open')) state.popup.showPopover();
    state.input.setAttribute('aria-expanded', 'true');
    render(state, state.editing ? state.input.value : '');
  }

  function close(state) {
    state.open = false;
    if (typeof state.popup.hidePopover === 'function' && state.popup.matches(':popover-open')) state.popup.hidePopover();
    state.popup.hidden = true;
    state.input.setAttribute('aria-expanded', 'false');
    state.input.removeAttribute('aria-activedescendant');
  }

  function choose(state, item, emit = true) {
    if (!item || item.disabled || state.input.disabled || state.readonly) return;
    const changed = state.select.selectedIndex !== item.index || state.editing;
    internal(state, () => { state.select.selectedIndex = item.index; });
    sync(state);
    close(state);
    if (emit && changed) {
      state.select.dispatchEvent(new Event('input', {bubbles: true}));
      state.select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    // A change handler may replace this control or reject the selection.
    if (state.input.isConnected) {
      sync(state);
      state.input.focus({preventScroll: true});
      state.input.select();
    }
  }

  function restore(state) {
    const previous = state.items.find(item => item.option === state.committed);
    internal(state, () => { state.select.selectedIndex = previous?.index ?? -1; });
    sync(state);
    close(state);
  }

  function enhance(select) {
    if (states.has(select) || !eligible(select)) return;
    const wrapper = document.createElement('span');
    wrapper.className = 'select-search';
    const input = document.createElement('input');
    input.className = 'select-search-input';
    input.type = 'text';
    input.id = `select-search-${++sequence}`;
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-haspopup', 'listbox');
    input.placeholder = 'برای یافتن، تایپ کنید…';
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'select-search-toggle';
    toggle.tabIndex = -1;
    toggle.setAttribute('aria-label', 'نمایش فهرست انتخاب');
    toggle.textContent = '⌄';
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'select-search-clear';
    clear.tabIndex = -1;
    clear.setAttribute('aria-label', 'پاک کردن انتخاب');
    clear.textContent = '×';
    const popup = document.createElement('span');
    popup.className = 'select-search-popup';
    if ('popover' in HTMLElement.prototype) popup.setAttribute('popover', 'manual');
    popup.hidden = true;
    const status = document.createElement('span');
    status.className = 'select-search-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const list = document.createElement('span');
    list.id = `${input.id}-list`;
    list.className = 'select-search-list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', caption(select));
    input.setAttribute('aria-controls', list.id);
    popup.append(status, list);
    const state = {select, wrapper, input, toggle, clear, popup, status, list,
      labels: Array.from(select.labels || []), items: [], visible: [], active: -1, open: false, internal: false};
    states.set(select, state);
    live.add(state);
    // Observe native programmatic assignments as well as standard change events.
    state.properties = [];
    for (const key of ['value', 'selectedIndex']) {
      const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, key);
      if (!Object.hasOwn(select, key) && descriptor?.get && descriptor?.set) {
        Object.defineProperty(select, key, {configurable: true,
          get() { return descriptor.get.call(this); },
          set(value) { descriptor.set.call(this, value); if (!state.internal) queueSync(state); },
        });
        state.properties.push(key);
      }
    }
    const label = caption(select);
    select.before(wrapper);
    wrapper.append(input, toggle, clear, select, popup);
    select.classList.add('select-search-native');
    state.originalTabIndex = select.getAttribute('tabindex');
    state.originalAriaHidden = select.getAttribute('aria-hidden');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');
    input.setAttribute('aria-label', label);
    const controller = new AbortController();
    state.controller = controller;
    const on = (node, event, handler, options = {}) => node.addEventListener(event, handler, {...options, signal: controller.signal});
    on(input, 'focus', () => { input.select(); open(state); });
    on(input, 'click', () => { if (!state.open) open(state); });
    on(input, 'input', () => {
      if (state.readonly || input.disabled) return;
      state.editing = true;
      state.showInvalid = false;
      internal(state, () => { select.selectedIndex = -1; });
      const empty = state.items.find(item => item.value === '' && !item.disabled);
      if (!input.value && !select.required && empty) {
        internal(state, () => { select.selectedIndex = empty.index; });
        state.editing = false;
        state.committed = empty.option;
        select.dispatchEvent(new Event('input', {bubbles: true}));
        select.dispatchEvent(new Event('change', {bubbles: true}));
      }
      validity(state);
      open(state);
      render(state, input.value);
    });
    on(input, 'blur', () => { state.showInvalid = true; validity(state); });
    on(input, 'invalid', () => { state.showInvalid = true; validity(state); });
    on(input, 'keydown', event => {
      if (state.readonly || input.disabled || event.isComposing) return;
      if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
        event.preventDefault();
        if (!state.open) { open(state); return; }
        const step = event.key === 'ArrowDown' ? 1 : -1;
        let index = state.active;
        for (let attempts = 0; attempts < state.visible.length; attempts += 1) {
          index = (index + step + state.visible.length) % state.visible.length;
          if (!state.visible[index].disabled) { activate(state, index, true); break; }
        }
      } else if (event.key === 'Enter' && state.open) {
        event.preventDefault();
        if (state.active >= 0) choose(state, state.visible[state.active]);
      } else if (event.key === 'Escape' && (state.open || state.editing)) {
        event.preventDefault();
        event.stopPropagation();
        restore(state);
      } else if (event.key === 'Tab') close(state);
    });
    on(toggle, 'click', () => { const wasOpen = state.open; input.focus({preventScroll: true}); if (wasOpen) close(state); else open(state); });
    on(clear, 'click', () => choose(state, state.items.find(item => item.value === '' && !item.disabled)));
    on(list, 'pointerdown', event => { if (event.pointerType === 'mouse') event.preventDefault(); });
    on(list, 'click', event => {
      const node = event.target.closest('[data-result]');
      if (node) choose(state, state.visible[Number(node.dataset.result)]);
    });
    on(select, 'input', () => queueSync(state));
    on(select, 'change', () => queueSync(state));
    on(select, 'focus', () => { if (!input.disabled) input.focus({preventScroll: true}); });
    on(select, 'invalid', event => {
      event.preventDefault();
      state.showInvalid = true;
      validity(state);
      input.focus({preventScroll: false});
    });
    sync(state, true);
  }

  function dispose(state, unwrap = false) {
    close(state);
    state.controller.abort();
    for (const key of state.properties) delete state.select[key];
    state.select.classList.remove('select-search-native');
    if (state.originalTabIndex === null) state.select.removeAttribute('tabindex');
    else state.select.setAttribute('tabindex', state.originalTabIndex);
    if (state.originalAriaHidden === null) state.select.removeAttribute('aria-hidden');
    else state.select.setAttribute('aria-hidden', state.originalAriaHidden);
    state.select.setCustomValidity('');
    if (unwrap && state.wrapper.isConnected) state.wrapper.replaceWith(state.select);
    states.delete(state.select);
    live.delete(state);
  }

  function refresh(root = document) {
    const selects = root.matches?.('select') ? [root] : Array.from(root.querySelectorAll?.('select') || []);
    for (const select of selects) {
      const state = states.get(select);
      if (!eligible(select)) { if (state) dispose(state, true); }
      else if (state) sync(state, true, true);
      else enhance(select);
    }
  }

  function boot() {
    refresh();
    const observer = new MutationObserver(records => {
      const roots = new Set();
      const dirty = new Set();
      for (const record of records) {
        const parent = record.target.nodeType === Node.ELEMENT_NODE ? record.target : record.target.parentElement;
        const select = parent?.closest('select');
        if (select) { dirty.add(select); continue; }
        if (record.type === 'attributes' && parent?.matches('fieldset')) roots.add(parent);
        // Rendering this component's own results must never trigger another scan.
        if (parent?.closest('.select-search')) continue;
        if (record.type === 'childList') for (const node of record.addedNodes) {
          if (node.nodeType === Node.ELEMENT_NODE && !node.closest('.select-search')) roots.add(node);
        }
      }
      for (const state of live) if (!state.select.isConnected) dispose(state);
      for (const select of dirty) if (select.isConnected) refresh(select);
      for (const root of roots) if (root.isConnected) refresh(root);
    });
    observer.observe(document.documentElement, {subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ['disabled', 'required', 'multiple', 'size', 'name', 'id', 'label', 'value', 'selected', 'aria-label', 'data-searchable', 'readonly', 'data-readonly', 'form']});
    document.addEventListener('pointerdown', event => {
      for (const state of live) if (state.open && !state.wrapper.contains(event.target)) close(state);
    }, true);
    document.addEventListener('focusin', event => {
      for (const state of live) if (state.open && !state.wrapper.contains(event.target)) close(state);
    });
    document.addEventListener('reset', event => {
      // Native reset applies its values after the event has finished.
      setTimeout(() => { for (const state of live) if (state.select.form === event.target) sync(state, true); }, 0);
    }, true);
    document.addEventListener('close', event => {
      if (event.target.matches?.('dialog')) for (const state of live) {
        if (event.target.contains(state.wrapper)) close(state);
      }
    }, true);
    const reposition = () => { for (const state of live) if (state.open) position(state); };
    window.addEventListener('resize', reposition, {passive: true});
    window.addEventListener('scroll', reposition, {passive: true, capture: true});
    window.visualViewport?.addEventListener('resize', reposition, {passive: true});
  }

  window.NikanSelectSearch = Object.freeze({refresh, normalize});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
