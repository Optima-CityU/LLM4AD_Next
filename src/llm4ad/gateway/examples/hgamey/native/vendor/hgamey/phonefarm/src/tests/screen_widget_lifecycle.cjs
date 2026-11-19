// No browser, phone, server, or npm dependencies. Run: node tests/screen_widget_lifecycle.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../screen_widget.html'), 'utf8')
  .match(/<script>\s*([\s\S]*?)<\/script>/)[1];

class Element {
  constructor() {
    this.textContent = ''; this.innerHTML = ''; this.src = ''; this.hidden = false;
    this.style = {}; this.clientWidth = 300; this.clientHeight = 600;
    const names = new Set();
    this.classList = { add: n => names.add(n), remove: n => names.delete(n), contains: n => names.has(n) };
  }
  replaceChildren(...children) { this.textContent = children.filter(x => typeof x === 'string').join(''); }
  appendChild() {}
  remove() {}
}

function backend() {
  let next = 0;
  const active = new Set(), requests = [];
  let delayedStart = null, delayedFrame = null;
  function response(status, data = {}) {
    return { status, ok: status === 200, json: async () => data,
      headers: new Map([['X-Frame-Seq', '1'], ['X-Frame-Age-Ms', '10']]), blob: async () => 'fake-jpeg' };
  }
  async function fetch(url) {
    requests.push(url);
    if (url === '/watch/start') {
      const id = String(++next);
      active.add(id);
      const result = response(200, { id, after_seq: 0 });
      if (delayedStart) { const wait = delayedStart; delayedStart = null; return wait(result); }
      return result;
    }
    if (url.startsWith('/watch/heartbeat')) {
      const id = new URL(url, 'http://local').searchParams.get('id');
      return response(active.has(id) ? 200 : 410);
    }
    if (url.startsWith('/watch/stop')) {
      active.delete(new URL(url, 'http://local').searchParams.get('id'));
      return response(200);
    }
    if (url === '/state.json') return response(200, { active_viewers: active.size, width: 1080, height: 2340 });
    if (url.startsWith('/frame.jpg')) {
      const result = response(200);
      if (delayedFrame) { const wait = delayedFrame; delayedFrame = null; return wait(result); }
      return result;
    }
    return response(200, { events: [] });
  }
  return { active, requests, fetch,
    delayStart(fn) { delayedStart = fn; }, delayFrame(fn) { delayedFrame = fn; } };
}

function page(net, initialVisibility = 'visible') {
  const elements = new Map(), listeners = new Map(), intervals = [];
  let clock = 100000;
  const document = {
    visibilityState: initialVisibility,
    getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    createElement: () => new Element(), createTextNode: text => text,
    addEventListener: (name, fn) => listeners.set(name, fn),
  };
  class FakeImage { set src(value) { this.naturalWidth = 672; this.naturalHeight = 1200; queueMicrotask(() => this.onload()); } }
  class FakeDate extends Date { static now() { return clock; } }
  let blob = 0;
  const context = vm.createContext({
    document, window: {}, navigator: { sendBeacon: url => { net.fetch(url); return true; } },
    fetch: net.fetch, Image: FakeImage, Date: FakeDate, AbortController,
    AbortSignal: { timeout: () => undefined }, URL: { createObjectURL: () => `blob:${++blob}`, revokeObjectURL() {} },
    encodeURIComponent, setTimeout: () => 0, clearTimeout() {},
    setInterval: (fn, ms) => intervals.push({ fn, ms }),
    addEventListener: (name, fn) => listeners.set(name, fn),
  });
  vm.runInContext(source, context);
  return { document, elements, intervals,
    run: code => vm.runInContext(code, context),
    event: name => listeners.get(name)?.(),
    advance: ms => { clock += ms; },
    pause: () => document.getElementById('pause').onclick(),
    status: () => document.getElementById('status').textContent,
  };
}

const settle = async () => { for (let i = 0; i < 3; i++) await new Promise(setImmediate); };

(async () => {
  let cases = 0;
  {
    const net = backend(), p = page(net, 'hidden');
    await settle();
    assert.equal(net.active.size, 0, 'opening a hidden page must not subscribe');
    assert(!net.requests.some(x => x.startsWith('/frame')));
    p.document.visibilityState = 'visible'; p.event('visibilitychange'); await settle();
    assert.equal(net.active.size, 1);
    p.pause(); await settle();
    assert.equal(net.active.size, 0);
    assert.match(p.status(), /已暂停/);
    for (const { fn } of p.intervals) await fn();
    assert.equal(net.active.size, 0, 'timers must not undo pause');
    p.pause(); await settle();
    assert.equal(net.active.size, 1, 'resume creates a fresh subscription');
    p.document.visibilityState = 'hidden'; p.event('visibilitychange'); await settle();
    assert.equal(net.active.size, 0);
    cases++;
  }
  {
    const net = backend(), a = page(net), b = page(net);
    await settle(); assert.equal(net.active.size, 2);
    a.pause(); await settle(); assert.equal(net.active.size, 1, 'one viewer cannot stop another');
    b.event('pagehide'); await settle(); assert.equal(net.active.size, 0);
    b.event('pageshow'); await settle(); assert.equal(net.active.size, 1, 'bfcache resume resubscribes');
    cases++;
  }
  {
    const net = backend(); let finish;
    net.delayStart(result => new Promise(resolve => { finish = () => resolve(result); }));
    const p = page(net); await settle();
    p.pause(); assert.equal(net.active.size, 1, 'start reply still in flight');
    finish(); await settle();
    assert.equal(net.active.size, 0, 'late start reply must be explicitly released');
    assert.match(p.status(), /已暂停/);
    cases++;
  }
  {
    const net = backend(); let finish;
    net.delayFrame(result => new Promise(resolve => { finish = () => resolve(result); }));
    const p = page(net); await settle();
    p.pause(); finish(); await settle();
    assert.match(p.status(), /已暂停/, 'late frame cannot repaint paused status');
    assert.equal(p.elements.get('frame').src, '', 'late frame is discarded');
    cases++;
  }
  {
    const net = backend(), p = page(net); await settle();
    const firstId = [...net.active][0];
    net.active.clear(); // Expired at the server while the page was disconnected.
    await p.run('maintainSubscription()'); await settle();
    assert.equal(net.active.size, 0, 'expired heartbeat does not create a viewer');
    await p.run('maintainSubscription()'); await settle();
    assert.equal(net.active.size, 1);
    assert.notEqual([...net.active][0], firstId);
    cases++;
  }
  {
    const net = backend(), p = page(net); await settle();
    assert.match(p.status(), /观看中/);
    const before = p.run('frameCount');
    await p.run('tick()'); await settle();
    assert.equal(p.run('frameCount'), before, 'cached frame repeats cannot inflate FPS');
    p.advance(5000); p.run('freshness()');
    assert.match(p.status(), /过期/);
    assert(p.elements.get('screen').classList.contains('stale'));
    cases++;
  }
  console.log(`screen widget lifecycle: ${cases} cases passed (fake DOM/network, no devices)`);
})().catch(error => { console.error(error); process.exitCode = 1; });
