'use strict';

// Пять source-only VM-сценариев. Не запускать без отдельного разрешения владельца.
// Настоящие DOM, сеть, таймеры, загрузки, ключи и доверие ОС не используются.
const assert = require('node:assert/strict');
const {createHash, webcrypto} = require('node:crypto');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = readFileSync(path.join(__dirname, '../web/network-security-panel.js'), 'utf8');
const rootBytes = new Uint8Array([1, 2, 3, 4]);
const rootHash = createHash('sha256').update(rootBytes).digest('hex');

function status() {
  return {settings: {http_enabled: true, https_enabled: true, http_port: 8765, https_port: 8766,
    hostname: '192.0.2.10'}, runtime: {}, certificate: {}, acme: {},
    connection: {can_import_certificate: true, can_issue_local_ca: true, can_download_local_ca: true},
    local_ca: {configured: true, enabled: true, auto_renew: true, state: 'issued',
      names: ['192.0.2.10'], root_fingerprint_sha256: rootHash}};
}

function environment({protocol = 'https:', hostname = '192.0.2.10'} = {}) {
  const nodes = new Map(), calls = [], timers = new Map(), downloads = [], urls = [];
  let timerId = 0;
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag; this.value = ''; this.checked = false; this.disabled = false;
      this.hidden = false; this.files = []; this.listeners = new Map(); this.textContent = '';
      const classes = new Set();
      this.classList = {contains: x => classes.has(x), toggle: (x, on) => on ? classes.add(x) : classes.delete(x)};
    }
    set innerHTML(html) {
      for (const match of html.matchAll(/<(\w+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
        const node = new Element(match[1]); node.id = match[2];
        node.hidden = /\bhidden\b/.test(match[0]); nodes.set(node.id, node);
      }
    }
    appendChild(node) { if (node.id) nodes.set(node.id, node); return node; }
    addEventListener(name, fn) { this.listeners.set(name, fn); }
    removeAttribute(name) { delete this[name]; }
    querySelectorAll() { return [...nodes.values()].filter(node => ['input', 'select', 'textarea'].includes(node.tagName)); }
    click() { downloads.push({href: this.href, filename: this.download}); }
    remove() {}
  }
  nodes.set('pane-settings', new Element());
  const document = {readyState: 'loading', hidden: false, head: new Element(), body: new Element(),
    getElementById: id => nodes.get(id) || null, createElement: tag => new Element(tag),
    addEventListener() {}};
  const env = {nodes, calls, timers, downloads, urls, current: status(), confirms: [], accept: true};
  env.reply = async (requestPath, options) => {
    if (requestPath.endsWith('root.cer')) return {ok: true, headers: {get: () => '4'},
      arrayBuffer: async () => rootBytes.slice().buffer};
    return {ok: true, json: async () => env.current};
  };
  class FakeURL extends URL {
    static createObjectURL(blob) { urls.push(blob); return 'blob:synthetic'; }
    static revokeObjectURL(value) { env.revoked = value; }
  }
  const context = {document, location: {protocol, hostname}, URL: FakeURL, Blob, AbortController,
    crypto: webcrypto, MutationObserver: class { observe() {} disconnect() {} },
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch: async (requestPath, options) => { calls.push({path: requestPath, options}); return env.reply(requestPath, options); },
    confirm: text => { env.confirms.push(text); return env.accept; }};
  context.window = context;
  vm.runInNewContext(source.replace(/\}\)\(\);\s*$/, 'window.panel={ensure,render,issueLocal,disableLocal,downloadLocalRoot,importCertificate,syncButtons};})();'),
    context, {filename: 'network-security-panel.js', timeout: 1000});
  env.panel = context.panel; env.panel.ensure(); env.panel.render(env.current);
  env.node = id => nodes.get(id);
  env.event = (id, name) => { const node = env.node(id); assert.ok(!node.disabled); node.listeners.get(name)(); };
  env.consent = () => { env.node('networkLocalTrustConsent').checked = true; env.event('networkLocalTrustConsent', 'change'); };
  return env;
}

test('Ключ и локальный CA требуют разрешения сервера и безопасного browser origin', async () => {
  for (const options of [{protocol: 'http:'}, {protocol: 'http:', hostname: 'localhost'}, {}]) {
    const env = environment(options);
    const remoteHTTP = options.protocol === 'http:' && !options.hostname;
    if (!remoteHTTP) {
      env.current.connection = {can_import_certificate: false, can_issue_local_ca: false, can_download_local_ca: false};
      env.panel.render(env.current);
    }
    env.consent();
    let fileReads = 0;
    env.node('networkKeyFile').files = [{size: 20, text: async () => { fileReads++; return 'PRIVATE'; }}];
    for (const id of ['networkImport', 'networkLocalIssue', 'networkLocalRoot']) assert.equal(env.node(id).disabled, true);
    await env.panel.importCertificate(); await env.panel.issueLocal(); await env.panel.downloadLocalRoot();
    assert.equal(env.calls.length, 0); assert.equal(fileReads, 0); assert.equal(env.downloads.length, 0);
  }
  const loopback = environment({protocol: 'http:', hostname: '127.0.0.2'});
  loopback.consent(); assert.equal(loopback.node('networkLocalIssue').disabled, false);
  // Состояние разрешения могло измениться после render: action guard должен
  // остановиться до чтения обоих файлов даже при устаревшей доступной кнопке.
  const stale = environment({protocol: 'http:', hostname: 'localhost'});
  stale.current.connection.can_import_certificate = false;
  let reads = 0;
  for (const id of ['networkKeyFile', 'networkCertificateFile']) {
    stale.node(id).files = [{size: 20, text: async () => { reads++; return 'SYNTHETIC'; }}];
  }
  stale.node('networkImport').disabled = false;
  await stale.panel.importCertificate();
  assert.equal(reads, 0); assert.equal(stale.calls.filter(call => call.options.method === 'POST').length, 0);
});

test('Согласие очищается при смене SAN, отмена не выпускает, подтверждённый scope точен', async () => {
  const env = environment(); env.consent();
  env.node('networkLocalNames').value = '192.0.2.10\n2001:db8::10'; env.event('networkLocalNames', 'input');
  assert.equal(env.node('networkLocalTrustConsent').checked, false);
  await env.panel.issueLocal(); assert.equal(env.calls.length, 0);
  env.consent(); env.accept = false; await env.panel.issueLocal(); assert.equal(env.calls.length, 0);
  env.accept = true; await env.panel.issueLocal();
  const posts = env.calls.filter(call => call.options.method === 'POST'); assert.equal(posts.length, 1);
  assert.equal(posts[0].path, '/v1/web-security/local-ca/issue');
  assert.deepEqual(JSON.parse(posts[0].options.body), {names: ['192.0.2.10', '2001:db8::10'],
    auto_renew: true, confirm: true, trust_consent: true});
  assert.match(env.confirms.at(-1), /2001:db8::10/);
  assert.equal(env.node('networkLocalTrustConsent').checked, false);
  env.consent(); env.current.local_ca.names = ['192.0.2.11']; env.panel.render(env.current);
  assert.equal(env.node('networkLocalTrustConsent').checked, false);
});

test('Незавершённый выпуск блокирует правки формы и параллельный импорт', async () => {
  const env = environment(); env.consent(); let finish;
  env.reply = (requestPath, options) => options.method === 'POST'
    ? new Promise(resolve => { finish = () => resolve({ok: true, json: async () => env.current}); })
    : Promise.resolve({ok: true, json: async () => env.current});
  const pending = env.panel.issueLocal();
  assert.equal(typeof finish, 'function');
  for (const id of ['networkLocalNames', 'networkLocalRenew', 'networkHostname', 'networkKeyFile', 'networkImport', 'networkAcmeSave']) {
    assert.equal(env.node(id).disabled, true, id);
  }
  await env.panel.importCertificate(); assert.equal(env.calls.length, 1);
  finish(); await pending;
  assert.equal(env.node('networkLocalNames').disabled, false);
  assert.equal(env.node('networkImport').disabled, false);
});

test('HTTP 200 с битым JSON или неверным контрактом не подтверждает выпуск и не повторяется', async () => {
  for (const outcome of ['parse-error', null, [], {}, {settings: {}}]) {
    const env = environment(); env.consent();
    env.reply = async (_requestPath, options) => options.method === 'POST'
      ? {ok: true, json: async () => { if (outcome === 'parse-error') throw new SyntaxError('synthetic HTML'); return outcome; }}
      : {ok: true, json: async () => env.current};
    await env.panel.issueLocal();
    assert.equal(env.calls.filter(call => call.options.method === 'POST').length, 1);
    assert.match(env.node('networkSecurityNote').textContent, /Результат действия (не подтверждён|неизвестен)/);
    assert.doesNotMatch(env.node('networkSecurityNote').textContent, /сертификат сохранён/);
    assert.equal(env.node('networkLocalNames').disabled, false);
  }
});

test('DER скачивается только при совпадении SHA-256, без установки доверия', async () => {
  for (const mismatch of [true, false]) {
    const env = environment();
    if (mismatch) { env.current.local_ca.root_fingerprint_sha256 = 'ab'.repeat(32); env.panel.render(env.current); }
    await env.panel.downloadLocalRoot();
    const downloads = env.calls.filter(call => call.path.endsWith('root.cer'));
    assert.equal(downloads.length, 1); assert.equal(downloads[0].options.headers['X-Leshy-UI'], 'network-security');
    assert.equal(downloads[0].options.credentials, 'same-origin'); assert.equal(downloads[0].options.cache, 'no-store');
    assert.equal(env.downloads.length, mismatch ? 0 : 1); assert.equal(env.urls.length, mismatch ? 0 : 1);
    if (mismatch) assert.match(env.node('networkSecurityNote').textContent, /Отпечаток.*изменился/);
    else {
      assert.equal(env.downloads[0].filename, 'Leshy-local-root.cer');
      assert.equal(env.urls[0].size, rootBytes.length);
      assert.match(env.node('networkSecurityNote').textContent, /передан браузеру/);
      assert.doesNotMatch(env.node('networkSecurityNote').textContent, /сертификат скачан|Доверие установлено/);
      const cleanup = [...env.timers.values()].find(timer => timer.delay === 30000); assert.ok(cleanup);
      cleanup.fn(); assert.equal(env.revoked, 'blob:synthetic');
    }
  }
});
