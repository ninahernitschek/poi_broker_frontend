// Run with: node --test tests/test_pending_actions.cjs
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const helper = fs.readFileSync(path.join(root, 'poi_broker/static/js/pending_actions.js'), 'utf8');
const profile = fs.readFileSync(path.join(root, 'poi_broker/templates/profile.html'), 'utf8');

// Minimal DOM contract for the helper; the action callbacks below are the
// actual template source, executed against controlled pending requests.
class Element {
  constructor(tag = 'button', type = 'submit') {
    this.tagName = tag.toUpperCase();
    this.type = type;
    this.disabled = false;
    this.attrs = new Map();
    this.children = [];
    this.dataset = {};
    this.events = {};
  }
  getAttribute(name) { return this.attrs.get(name) ?? null; }
  setAttribute(name, value) { this.attrs.set(name, value); }
  removeAttribute(name) { this.attrs.delete(name); }
  prepend(child) { this.children.unshift(child); child.parent = this; }
  appendChild(child) { this.children.push(child); child.parent = this; }
  remove() {
    if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1);
  }
  addEventListener(type, handler) { this.events[type] = handler; }
  matches(selector) { return selector === 'form[data-single-submit]' && this.singleSubmit; }
  get elements() { return this.children; }
}

function setup() {
  let now = 0, nextTimer = 0;
  const timers = new Map();
  const document = {
    events: {},
    createElement: tag => new Element(tag),
    addEventListener(type, handler) { this.events[type] = handler; }
  };
  const window = {
    events: {},
    addEventListener(type, handler) { this.events[type] = handler; },
    setTimeout(handler, delay) {
      const id = ++nextTimer;
      timers.set(id, { handler, at: now + delay });
      return id;
    },
    clearTimeout(id) { timers.delete(id); }
  };
  function advance(ms) {
    now += ms;
    for (const [id, timer] of timers) {
      if (timer.at <= now) {
        timers.delete(id);
        timer.handler();
      }
    }
  }
  const context = vm.createContext({ window, document, console: { error() {} } });
  vm.runInContext(helper, context);
  return { window, document, context, advance, timers };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('button locks immediately, preserves its contents and allows retry', () => {
  const { window } = setup();
  const button = new Element();
  const icon = new Element('i');
  button.appendChild(icon);
  button.setAttribute('aria-busy', 'false');
  const release = window.PoiPendingActions.begin(button);
  assert.equal(button.disabled, true);
  assert.equal(button.getAttribute('aria-busy'), 'true');
  assert.equal(window.PoiPendingActions.begin(button), null);
  assert.equal(button.children.length, 2);
  release();
  assert.equal(button.disabled, false);
  assert.equal(button.getAttribute('aria-busy'), 'false');
  assert.deepEqual(button.children, [icon]);
  const nextRelease = window.PoiPendingActions.begin(button);
  release(); // An old completion must not unlock a newer request.
  assert.equal(button.disabled, true);
  nextRelease();
});

test('pre-disabled buttons stay disabled', () => {
  const { window } = setup();
  const button = new Element();
  button.disabled = true;
  assert.equal(window.PoiPendingActions.begin(button), null);
  assert.equal(button.disabled, true);
});

test('native forms block duplicate submits, preserve named submitters and reset on Back', () => {
  const { window, document } = setup();
  const form = new Element('form');
  form.singleSubmit = true;
  const button = new Element();
  button.name = 'action';
  button.value = 'save';
  const disabledButton = new Element();
  disabledButton.disabled = true;
  form.appendChild(button);
  form.appendChild(disabledButton);
  let prevented = 0;
  const event = { target: form, submitter: button, preventDefault() { prevented++; } };
  document.events.submit(event);
  assert.equal(button.disabled, true);
  const hidden = form.elements.find(control => control.type === 'hidden');
  assert.equal(hidden.name, 'action');
  assert.equal(hidden.value, 'save');
  document.events.submit(event);
  assert.equal(prevented, 1);
  window.events.pageshow();
  assert.equal(button.disabled, false);
  assert.equal(disabledButton.disabled, true);
  assert.equal(form.elements.length, 2);
  document.events.submit(event);
  assert.equal(prevented, 1);
});

test('canceled and AJAX submissions are not locked by the native form handler', () => {
  const { document } = setup();
  const form = new Element('form');
  form.singleSubmit = true;
  const button = new Element();
  form.appendChild(button);
  document.events.submit({ target: form, defaultPrevented: true });
  assert.equal(button.disabled, false);
  form.singleSubmit = false;
  document.events.submit({ target: form, defaultPrevented: false });
  assert.equal(button.disabled, false);
});

test('native submission recovers on the same document and a fresh retry gets its own lock', () => {
  const { document, advance } = setup();
  const form = new Element('form');
  form.singleSubmit = true;
  const button = new Element();
  button.name = 'action';
  button.value = 'save';
  form.appendChild(button);
  let prevented = 0;
  const event = { target: form, submitter: button, preventDefault() { prevented++; } };
  document.events.submit(event);
  advance(29999);
  document.events.submit(event);
  assert.equal(prevented, 1);
  assert.equal(button.disabled, true);
  advance(1);
  assert.equal(button.disabled, false);
  assert.equal(button.getAttribute('aria-busy'), null);
  assert.equal(form.elements.length, 1);
  document.events.submit(event);
  assert.equal(prevented, 1);
  assert.equal(button.disabled, true);
  advance(29999);
  assert.equal(button.disabled, true);
});

test('Back restoration cancels the old native recovery timer', () => {
  const { document, window, advance, timers } = setup();
  const form = new Element('form');
  form.singleSubmit = true;
  const button = new Element();
  form.appendChild(button);
  const event = { target: form, submitter: button, preventDefault() {} };
  document.events.submit(event);
  advance(10000);
  window.events.pageshow();
  assert.equal(timers.size, 0);
  document.events.submit(event);
  advance(20000);
  assert.equal(button.disabled, true);
  advance(10000);
  assert.equal(button.disabled, false);
});

test('group creation rejects double clicks and remains busy through list refresh', async () => {
  const { window, context } = setup();
  const button = new Element();
  const request = deferred(), refresh = deferred();
  let calls = 0, prompts = 0;
  Object.assign(context, {
    newGroupBtn: button, csrfToken: 'test',
    prompt() { prompts++; return 'Group'; },
    fetch() { calls++; return request.promise; },
    showToast() {}, fetchFailureMessage() {}, getActiveTabId() { return 'all'; },
    loadGroups() { return refresh.promise; }
  });
  const start = profile.indexOf("newGroupBtn.addEventListener('click'");
  vm.runInContext(profile.slice(start, profile.indexOf('function getActiveTabId()', start)), context);
  button.events.click();
  button.events.click();
  assert.equal(calls, 1);
  assert.equal(prompts, 1);
  request.resolve({ ok: true, json: async () => ({ id: 1 }) });
  await settle();
  assert.equal(button.disabled, true);
  refresh.resolve();
  await settle();
  assert.equal(button.disabled, false);
  assert.equal(button.getAttribute('aria-busy'), null);
  assert.equal(window.PoiPendingActions.begin(button) !== null, true);
});

for (const selector of ['.remove-fav-btn', '.delete-group-btn', '.remove-watchlist-btn', '.remove-filter-bookmark-btn']) {
  test(`${selector} rejects repeated clicks and unlocks after request failure`, async () => {
    const { document, context } = setup();
    const button = new Element();
    button.dataset = { locus: 'L1', id: '1', watchlistId: '1', bookmarkId: '1' };
    const request = deferred();
    let calls = 0, confirmations = 0;
    Object.assign(context, {
      csrfToken: 'test', confirm() { confirmations++; return true; },
      fetch() { calls++; return request.promise; },
      showToast() {}, fetchFailureMessage() {},
      loadGroups() {}, loadWatchlists() {}, loadFilterBookmarks() {}
    });
    const start = profile.indexOf('// Event delegation for remove and move buttons');
    vm.runInContext(profile.slice(start, profile.indexOf('// Confirm move button', start)), context);
    const event = { target: { closest: value => value === selector ? button : null } };
    document.events.click(event);
    document.events.click(event);
    assert.equal(calls, 1);
    assert.equal(button.disabled, true);
    assert.equal(confirmations, selector === '.remove-fav-btn' ? 0 : 1);
    request.reject(new Error('Network unavailable'));
    await settle();
    assert.equal(button.disabled, false);
    document.events.click(event);
    assert.equal(calls, 2);
    await settle();
  });
}

function queryBuilderSetup(options = {}) {
  const source = fs.readFileSync(path.join(root, 'poi_broker/templates/_query_builder_js.html'), 'utf8');
  class Control {
    constructor() { this.props = {}; this.events = {}; this.value = ''; }
    prop(name, value) {
      if (arguments.length === 1) return this.props[name];
      this.props[name] = value; return this;
    }
    val(value) { if (!arguments.length) return this.value; this.value = value; return this; }
    on(name, handler) { this.events[name] = handler; return this; }
    find() { return this; }
    attr() { return this; }
    text() { return this; }
    trigger() { return this; }
    off() { return this; }
    empty() { return this; }
    remove() { return this; }
    tooltip() { return this; }
    queryBuilder(method) {
      if (method === 'getSQL') return { sql: 'SELECT 1' };
      if (method === 'getRules') return { rules: [{ field: 'featuretable.num_alerts', operator: 'greater', value: 1 }] };
      return this;
    }
  }
  const controls = {};
  for (const name of ['builder', 'preview-btn', 'save-btn', 'name-input', 'output', 'results']) controls[name] = new Control();
  controls['save-btn'].prop('disabled', true);
  const rootControl = { find: selector => controls[selector.match(/"([^"]+)"/)[1]] };
  const requests = [];
  const $ = value => value === 'body > .tooltip' ? new Control() : rootControl;
  $.ajax = options => {
    const done = [], fail = [], always = [];
    let finished = false;
    const request = {
      done(callback) { done.push(callback); return this; },
      fail(callback) { fail.push(callback); return this; },
      always(callback) { always.push(callback); return this; },
      resolve(data) {
        finished = true;
        if (options.success) options.success(data);
        done.forEach(callback => callback(data));
        always.forEach(callback => callback());
      },
      reject(status = 'error') {
        finished = true;
        if (options.error) options.error({}, status, status);
        fail.forEach(callback => callback({}, status, status));
        always.forEach(callback => callback());
      },
      abort() { if (!finished) this.reject('abort'); }
    };
    requests.push(request);
    return request;
  };
  const context = vm.createContext({ $, filters: [], builderChangeEvents: ['change'], getCsrfToken: () => 'test' });
  const start = source.indexOf('function createApi(');
  vm.runInContext(source.slice(start, source.indexOf('window.PoiQueryBuilder =', start)), context);
  const api = context.createApi({}, options);
  return { controls, requests, api };
}

test('query builder keeps saves locked across rule edits and another preview', () => {
  const { controls, requests } = queryBuilderSetup();
  const preview = controls['preview-btn'], save = controls['save-btn'];
  preview.events.click();
  preview.events.click();
  assert.equal(requests.length, 1);
  requests[0].resolve({ sql: 'num_alerts > 1' });
  controls['name-input'].val('Watchlist');
  save.events.click();
  save.events.click();
  assert.equal(requests.length, 2);
  controls.builder.events.change();
  preview.events.click();
  requests[2].resolve({ sql: 'num_alerts > 2' });
  assert.equal(save.prop('disabled'), true);
  save.events.click();
  assert.equal(requests.length, 3);
  requests[1].reject();
  assert.equal(save.prop('disabled'), false);
  save.events.click();
  assert.equal(requests.length, 4);
});

test('query builder does not enable saving an invalidated preview after failure', () => {
  const { controls, requests } = queryBuilderSetup();
  controls['preview-btn'].events.click();
  requests[0].resolve({ sql: 'num_alerts > 1' });
  controls['name-input'].val('Watchlist');
  controls['save-btn'].events.click();
  controls.builder.events.change();
  requests[1].reject();
  assert.equal(controls['save-btn'].prop('disabled'), true);
});

test('query builder respects a reset performed by the successful save callback', () => {
  const { controls, requests } = queryBuilderSetup({ onSave(response, api) { api.reset(); } });
  controls['preview-btn'].events.click();
  requests[0].resolve({ sql: 'num_alerts > 1' });
  controls['name-input'].val('Watchlist');
  controls['save-btn'].events.click();
  requests[1].resolve({ name: 'Watchlist', id: 1 });
  assert.equal(controls['save-btn'].prop('disabled'), true);
});
