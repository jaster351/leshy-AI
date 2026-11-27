'use strict';

// Двенадцать изолированных сценариев. Запуск требует отдельного разрешения.
// В VM исполняются исходные JS с экспортом замыкания только в памяти теста.
// Системные часы, сеть, браузер, микрофон и аудиоустройства не используются.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const forbidden = () => { throw new Error('Настоящая сеть или браузерный API запрещены'); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
const flush = async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); };

function node() {
  const classes = new Set();
  return {
    value: '', disabled: false, dataset: {}, textContent: '', innerHTML: '',
    classList: {
      contains: value => classes.has(value),
      add: value => classes.add(value), remove: value => classes.delete(value),
      toggle(value, force) {
        const on = force === undefined ? !classes.has(value) : force;
        if (on) classes.add(value); else classes.delete(value);
        return on;
      },
    },
    setAttribute() {}, appendChild() {}, dispatchEvent() {},
  };
}

function environment() {
  const elements = new Map(['chatInput', 'chatSend', 'chatVoiceState', 'voiceToggle',
    'voiceReplyToggle', 'voiceLiveDot', 'leshyPersonaFace', 'leshyPersonaState',
    'leshyPersonaDetail'].map(id => [id, node()]));
  const storage = new Map([['leshy.session', 'chat-a'], ['leshy.voice.enabled', '1']]);
  const listeners = new Map();
  const timers = new Map();
  let nextTimer = 1;
  const metrics = {fetch: [], microphone: 0, trackStops: 0, sourceStarts: 0,
    sourceCreates: 0, recorderStops: 0, permissionQueries: 0};
  const track = {stop() { metrics.trackStops += 1; }, onended: null};
  const stream = {getTracks: () => [track], getAudioTracks: () => [track]};
  const recognizers = [];
  class Recognition {
    constructor() { this.starts = 0; this.aborts = 0; recognizers.push(this); }
    start() { this.starts += 1; }
    abort() { this.aborts += 1; }
    stop() {}
  }
  class MediaRecorder {
    static isTypeSupported() { return true; }
    constructor() { this.state = 'inactive'; this.mimeType = 'audio/webm'; }
    start() { this.state = 'recording'; }
    requestData() {}
    stop() { this.state = 'inactive'; metrics.recorderStops += 1; this.onstop?.(); }
  }
  class AudioContext {
    constructor() { this.state = 'running'; this.destination = {}; }
    resume() { return Promise.resolve(); }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createMediaStreamSource() { return {connect() {}, disconnect() {}}; }
    createAnalyser() {
      return {getFloatTimeDomainData(samples) { samples.fill(0); }, disconnect() {}};
    }
    createBufferSource() {
      metrics.sourceCreates += 1;
      return {connect() {}, disconnect() {}, stop() {},
        start() { metrics.sourceStarts += 1; }};
    }
    decodeAudioData() { forbidden(); }
  }
  const localStorage = {
    getItem: key => storage.has(key) ? storage.get(key) : null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: key => storage.delete(key),
  };
  const document = {readyState: 'loading', hidden: false,
    getElementById: id => elements.get(id) || null,
    querySelector: () => null, createElement: () => node(),
    head: {appendChild() {}}, addEventListener() {},
  };
  const navigator = {
    mediaDevices: {async getUserMedia() { metrics.microphone += 1; return stream; }},
    permissions: {async query() { metrics.permissionQueries += 1; return {state: 'granted'}; }},
  };
  const context = {
    document, navigator, localStorage, MediaRecorder, AudioContext, SpeechRecognition: Recognition,
    isSecureContext: true, leshyVoiceState: {enabled: false},
    AbortController, Blob, DOMException, Event,
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    performance: {now: () => 1000},
    setTimeout(fn, delay) { const id = nextTimer++; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn, delay) { const id = nextTimer++; timers.set(id, {fn, delay, interval: true}); return id; },
    clearInterval(id) { timers.delete(id); },
    requestAnimationFrame() { return nextTimer++; }, cancelAnimationFrame() {},
    MutationObserver: class { observe() {} disconnect() {} },
    Element: class {}, HTMLElement: class {},
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    dispatchEvent(event) {
      for (const fn of [...listeners.get(event.type) || []]) fn(event);
      return true;
    },
    fetch(...args) { metrics.fetch.push(args); return Promise.reject(new Error('Сеть запрещена')); },
    WebSocket: forbidden, XMLHttpRequest: forbidden, Worker: forbidden,
    setImmediate: forbidden, console: {log() {}, warn() {}, error() {}},
  };
  context.window = context;
  vm.createContext(context);
  return {context, elements, localStorage, metrics, timers, recognizers, listeners,
    AudioContext,
    emit(type, detail) { context.dispatchEvent(new context.CustomEvent(type, {detail})); },
  };
}

function load(env, filename, exportsSource) {
  const original = readFileSync(path.join(root, 'web', filename), 'utf8');
  const end = original.lastIndexOf('})();');
  assert.ok(end >= 0, 'Ожидается замыкание действующего модуля');
  const instrumented = original.slice(0, end) + '\n' + exportsSource + '\n' + original.slice(end);
  vm.runInContext(instrumented, env.context, {filename, timeout: 1000});
}

function voice() {
  const env = environment();
  load(env, 'voice-live.js', 'window.__voice={state,sendVoiceTurn,speak,startRecording};');
  env.voice = env.context.__voice;
  Object.assign(env.voice.state, {enabled: true, epoch: 1, sessionId: 'chat-a', engine: 'server'});
  return env;
}

function legacy() {
  const env = environment();
  load(env, 'hardware-panel.js', `window.__legacy={enableVoice,callStateChanged,
    snapshot:()=>({voiceEnabled,callPaused,resumeLegacy,recognitionRunning,recognitionStarting})};`);
  env.legacy = env.context.__legacy;
  env.call = detail => {
    env.context.leshyVoiceState = detail;
    env.legacy.callStateChanged({detail});
  };
  return env;
}

test('Ответ Promise отвергается после смены session key без события', async () => {
  const env = voice(), answer = deferred();
  env.context.sendFrom = () => {
    env.emit('leshy:activity', {request_id: 'own-request'});
    return answer.promise;
  };
  const pending = env.voice.sendVoiceTurn('Тестовая реплика', 1);
  const rejected = assert.rejects(pending, /Чат изменился/);
  env.localStorage.setItem('leshy.session', 'chat-b');
  answer.resolve({answer: 'Ответ от покинутого чата'});
  await rejected;
  assert.equal(env.voice.state.pendingChat, null);
  assert.equal(env.listeners.get('leshy:done').size, 0);
  assert.equal(env.timers.size, 0);
  assert.equal(env.metrics.fetch.length, 0);
});

test('Событие done отвергается после смены session key без отдельного события', async () => {
  const env = voice();
  env.context.sendFrom = () => { env.emit('leshy:activity', {request_id: 'own-request'}); };
  const pending = env.voice.sendVoiceTurn('Тестовая реплика', 1);
  const rejected = assert.rejects(pending, /Чат изменился/);
  env.localStorage.setItem('leshy.session', 'chat-b');
  env.emit('leshy:done', {request_id: 'own-request', answer: 'Чужой ответ'});
  await rejected;
  assert.equal(env.voice.state.pendingChat, null);
  assert.equal(env.listeners.get('leshy:activity').size, 0);
  assert.equal(env.timers.size, 0);
  assert.equal(env.metrics.fetch.length, 0);
});

test('Смена чата перед TTS не отправляет запрос озвучки', async () => {
  const env = voice();
  env.localStorage.setItem('leshy.session', 'chat-b');
  await assert.rejects(env.voice.speak('Устаревший ответ', 1), /Чат изменился/);
  assert.equal(env.metrics.fetch.length, 0);
  assert.equal(env.metrics.sourceStarts, 0);
});

test('Смена чата при декодировании TTS не запускает аудио', async () => {
  const env = voice(), decoding = deferred();
  const audio = new env.AudioContext();
  audio.decodeAudioData = (_buffer, success) => { decoding.promise.then(success); };
  env.voice.state.outputContext = audio;
  env.context.fetch = async (...args) => {
    env.metrics.fetch.push(args);
    return {ok: true, blob: async () => ({size: 5, arrayBuffer: async () => new ArrayBuffer(5)})};
  };
  const speaking = env.voice.speak('Тестовый ответ', 1);
  const rejected = assert.rejects(speaking, /Чат изменился/);
  await flush();
  assert.equal(typeof env.voice.state.outputDecode, 'function', 'Декодирование должно уже ожидаться');
  env.localStorage.setItem('leshy.session', 'chat-b');
  decoding.resolve({duration: 1});
  await rejected;
  assert.equal(env.metrics.fetch.length, 1);
  assert.equal(env.metrics.sourceCreates, 0);
  assert.equal(env.metrics.sourceStarts, 0);
  assert.equal(env.voice.state.outputDecode, null);
  assert.equal(env.timers.size, 0);
});

for (const speechSeen of [false, true]) {
  test(speechSeen ? 'Предел длительной речи останавливает запись без автоотправки'
    : 'Предел ожидания речи останавливает запись без автоотправки', async () => {
    const env = voice();
    let sent = 0;
    env.context.sendFrom = () => { sent += 1; };
    env.voice.state.outputContext = new env.AudioContext();
    await env.voice.startRecording(1);
    const capture = env.voice.state.capture;
    assert.ok(capture?.recording, 'Синтетическая запись должна начаться');
    capture.speechSeen = speechSeen;
    capture.latestText = 'Непроверенный промежуточный текст';
    env.elements.get('chatInput').value = capture.latestText;
    const max = env.timers.get(capture.maxTimer);
    assert.equal(max?.delay, 240000);
    max.fn();
    await flush();
    assert.equal(sent, 0);
    assert.equal(env.metrics.fetch.length, 0);
    assert.equal(env.voice.state.enabled, false);
    assert.equal(env.voice.state.phase, 'error');
    assert.match(env.voice.state.error, speechSeen ? /не отправлена/ : /без отправки/);
    assert.equal(env.voice.state.capture, null);
    assert.equal(env.voice.state.recording, false);
    assert.equal(env.metrics.trackStops, 1);
    assert.equal(env.metrics.recorderStops, 1);
    assert.equal(env.elements.get('chatInput').value, 'Непроверенный промежуточный текст');
  });
}

test('Ошибка разговора выключает legacy микрофон и допускает ручной повтор', async () => {
  const env = legacy();
  await env.legacy.enableVoice();
  const recognition = env.recognizers[0];
  recognition.onstart();
  env.call({enabled: true, phase: 'listening'});
  recognition.onend();
  env.call({enabled: false, phase: 'error', error: 'Синтетическая ошибка ASR/TTS'});
  recognition.onend();
  await flush();
  assert.equal(env.legacy.snapshot().callPaused, false);
  assert.equal(env.legacy.snapshot().voiceEnabled, false);
  assert.equal(env.elements.get('voiceToggle').textContent, 'Микрофон: ВЫКЛ');
  assert.equal(env.localStorage.getItem('leshy.voice.enabled'), '1');
  assert.equal(recognition.starts, 1, 'Ошибка не должна сама заново открывать микрофон');
  assert.equal(env.metrics.microphone, 1);
  await env.legacy.enableVoice();
  recognition.onstart();
  assert.equal(recognition.starts, 2);
  assert.equal(env.metrics.microphone, 2);
  assert.equal(env.legacy.snapshot().voiceEnabled, true);
  assert.equal(env.legacy.snapshot().callPaused, false);
  assert.equal(env.elements.get('voiceToggle').textContent, 'Микрофон: ВКЛ');
});

test('Звонок во время recognitionStarting сохраняет намерение безопасного восстановления', async () => {
  const env = legacy();
  await env.legacy.enableVoice();
  const recognition = env.recognizers[0];
  assert.equal(env.legacy.snapshot().recognitionStarting, true);
  env.call({enabled: true, phase: 'listening'});
  assert.equal(env.legacy.snapshot().resumeLegacy, true);
  recognition.onend();
  env.call({enabled: false, phase: 'off', reason: 'stopped'});
  await flush();
  assert.equal(recognition.starts, 2);
  assert.equal(env.metrics.permissionQueries, 1);
  assert.equal(env.metrics.microphone, 1, 'Автовосстановление не должно вызывать getUserMedia');
  recognition.onstart();
  assert.equal(env.legacy.snapshot().recognitionRunning, true);
  assert.equal(env.legacy.snapshot().callPaused, false);
  assert.equal(env.elements.get('voiceToggle').textContent, 'Микрофон: ВКЛ');
});

for (const permission of ['missing', 'prompt', 'denied', 'reject']) {
  test('Недоступное разрешение ' + permission + ' переводит legacy микрофон в ВЫКЛ', async () => {
    const env = legacy();
    await env.legacy.enableVoice();
    const recognition = env.recognizers[0];
    recognition.onstart();
    env.call({enabled: true, phase: 'listening'});
    recognition.onend();
    if (permission === 'missing') env.context.navigator.permissions = undefined;
    else env.context.navigator.permissions.query = async () => {
      env.metrics.permissionQueries += 1;
      if (permission === 'reject') throw new Error('Тестовый отказ API разрешений');
      return {state: permission};
    };
    env.call({enabled: false, phase: 'off', reason: 'stopped'});
    await flush();
    assert.equal(recognition.starts, 1);
    assert.equal(env.metrics.microphone, 1);
    assert.equal(env.legacy.snapshot().callPaused, false);
    assert.equal(env.legacy.snapshot().voiceEnabled, false);
    assert.equal(env.elements.get('voiceToggle').textContent, 'Микрофон: ВЫКЛ');
    assert.equal(env.localStorage.getItem('leshy.voice.enabled'), '1');
    assert.equal(env.metrics.fetch.length, 0);
  });
}
