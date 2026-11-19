'use strict';

// Четыре изолированных сценария. Не запускать без отдельного разрешения владельца.
// Реальный voice-live.js исполняется в VM; сеть, часы, микрофон и звук подменены.
// Из прежнего файла берутся только функции окружения: его двенадцать тестов
// не регистрируются в node:test и не исполняются.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const helperModule = {exports: {}};
let skippedRegistrations = 0;
const helperSource = readFileSync(path.join(__dirname, 'web_voice_lifecycle.test.cjs'), 'utf8');
vm.runInNewContext(helperSource + '\nmodule.exports={environment,load,flush};', {
  __dirname, module: helperModule, AbortController, Blob, DOMException, Event,
  require(name) {
    if (name === 'node:test') return () => { skippedRegistrations += 1; };
    assert.ok(['node:assert/strict', 'node:fs', 'node:path', 'node:vm'].includes(name),
      'Разрешены только стандартные модули прежнего изолированного окружения');
    return require(name);
  },
}, {filename: 'web_voice_lifecycle.test.cjs', timeout: 1000});
assert.equal(skippedRegistrations, 12, 'Прежние сценарии должны остаться неисполненными');
const {environment, load, flush} = helperModule.exports;

const gateError = 'Локальный ASR уже обрабатывает предыдущий фрагмент. Повторите после его завершения.';
function rejection({status = 400, error = gateError, busy = true} = {}) {
  return {ok: false, status, json: async () => ({error, voice: {busy}})};
}

function fixture() {
  const env = environment();
  let now = 1000, sent = 0;
  env.context.performance.now = () => now;
  env.context.sendFrom = () => { sent += 1; throw new Error('Отправка в LLM запрещена'); };
  load(env, 'voice-live.js', 'window.__voice={state,transcribe,requestTranscription,stop,finishTurn};');
  env.voice = env.context.__voice;
  const chunk = new Blob([new Uint8Array(9000)], {type: 'audio/webm'});
  const capture = {epoch: 1, chunks: [chunk], chunkBytes: chunk.size,
    latestText: 'Сохранённый черновик', baseText: '', mime: 'audio/webm',
    recording: true, finishing: false, asrTask: null};
  Object.assign(env.voice.state, {enabled: true, epoch: 1, sessionId: 'chat-a', capture});
  env.elements.get('chatInput').value = capture.latestText;
  env.capture = capture;
  env.chunk = chunk;
  env.sent = () => sent;
  env.now = () => now;
  env.fire = (id, elapsed = 0) => {
    const timer = env.timers.get(id);
    assert.ok(timer, 'Ожидается существующий подменённый таймер');
    env.timers.delete(id);
    now += elapsed;
    timer.fn();
  };
  env.waitForTimer = async (delay, done = () => false) => {
    for (let turn = 0; turn < 40; turn += 1) {
      await flush();
      if (done()) return null;
      const timers = [...env.timers].filter(([, timer]) => timer.delay === delay && !timer.interval);
      if (timers.length) {
        assert.equal(timers.length, 1, 'Ожидание должно иметь один таймер');
        return timers[0][0];
      }
    }
    assert.fail('Не появился ожидаемый таймер ' + delay);
  };
  return env;
}

test('Явный ASR busy отклонён до обработки: повторяет тот же blob и сохраняет буфер', async () => {
  const env = fixture();
  env.context.fetch = async (...args) => {
    env.metrics.fetch.push(args);
    return env.metrics.fetch.length === 1 ? rejection() : {ok: true, json: async () => ({text: 'Подтверждённая речь'})};
  };
  const pending = env.voice.transcribe(env.capture, true);
  const wait = await env.waitForTimer(1500);
  assert.equal(env.metrics.fetch.length, 1);
  assert.equal(env.capture.chunks.length, 1);
  assert.equal(env.capture.chunks[0], env.chunk);
  assert.equal(env.elements.get('chatInput').value, 'Сохранённый черновик');
  env.fire(wait, 1500);
  assert.equal(await pending, 'Подтверждённая речь');
  assert.equal(env.metrics.fetch.length, 2);
  assert.equal(env.metrics.fetch[0][0], env.metrics.fetch[1][0]);
  assert.equal(env.metrics.fetch[0][1].body, env.metrics.fetch[1][1].body);
  assert.equal(env.metrics.fetch[0][1].body.size, 9000);
  assert.equal(env.capture.chunks[0], env.chunk);
  assert.equal(env.capture.latestText, 'Подтверждённая речь');
  assert.equal(env.elements.get('chatInput').value, 'Подтверждённая речь');
  assert.equal(env.voice.state.controllers.size, 0);
  assert.equal(env.sent(), 0);
});

test('Stop и смена session key во время busy ожидания запрещают следующий POST', async () => {
  for (const action of ['stop', 'session']) {
    const env = fixture();
    env.context.fetch = async (...args) => { env.metrics.fetch.push(args); return rejection(); };
    const pending = env.voice.requestTranscription(env.capture, env.chunk, 'ru');
    const rejected = assert.rejects(pending, action === 'stop' ? /разговор завершён/ : /Чат изменился/);
    const wait = await env.waitForTimer(1500);
    if (action === 'stop') await env.voice.stop();
    else {
      env.localStorage.setItem('leshy.session', 'chat-b');
      env.fire(wait, 1500);
    }
    await rejected;
    await flush();
    assert.equal(env.metrics.fetch.length, 1, 'После отмены не должно быть повторной передачи');
    assert.equal(env.voice.state.controllers.size, 0);
    assert.equal([...env.timers.values()].filter(timer => timer.delay === 1500).length, 0);
    assert.equal(env.elements.get('chatInput').value, 'Сохранённый черновик');
    assert.equal(env.sent(), 0);
  }
});

test('Неизвестный результат, произвольный 400, busy false и тайм-аут не повторяются', async () => {
  for (const outcome of ['network', 'other400', 'busyFalse', 'server500', 'timeout']) {
    const env = fixture();
    env.context.fetch = (...args) => {
      env.metrics.fetch.push(args);
      if (outcome === 'network') return Promise.reject(new TypeError('Результат передачи неизвестен'));
      if (outcome === 'other400') return Promise.resolve(rejection({error: 'Декодирование завершилось ошибкой'}));
      if (outcome === 'busyFalse') return Promise.resolve(rejection({busy: false}));
      if (outcome === 'server500') return Promise.resolve(rejection({status: 500}));
      return new Promise((_resolve, reject) => {
        args[1].signal.addEventListener('abort', () => reject(new DOMException('Запрос отменён', 'AbortError')), {once: true});
      });
    };
    const pending = env.voice.requestTranscription(env.capture, env.chunk, 'ru');
    const rejected = assert.rejects(pending, outcome === 'timeout' ? /не ответил вовремя/ : /неизвестен|Декодирование|Локальный ASR/);
    if (outcome === 'timeout') env.fire(await env.waitForTimer(90000), 90000);
    await rejected;
    assert.equal(env.metrics.fetch.length, 1, 'Повтор недоказанно отклонённого запроса запрещён');
    assert.equal([...env.timers.values()].filter(timer => timer.delay === 1500).length, 0);
    assert.equal(env.voice.state.controllers.size, 0);
    assert.equal(env.capture.chunks[0], env.chunk);
    assert.equal(env.elements.get('chatInput').value, 'Сохранённый черновик');
    assert.equal(env.sent(), 0);
  }
});

test('Постоянный busy ограничен сроком и числом ожиданий, без LLM и потери черновика', async () => {
  for (const clock of ['normal', 'frozen', 'delayed', 'oversleep']) {
    const env = fixture();
    env.context.fetch = async (...args) => { env.metrics.fetch.push(args); return rejection(); };
    let settled = false;
    const pending = env.voice.finishTurn(env.capture).then(() => { settled = true; });
    let waits = 0;
    for (let step = 0; step < 24; step += 1) {
      await flush();
      if (settled) break;
      const wait = await env.waitForTimer(1500, () => settled);
      if (wait === null) break;
      assert.equal(env.capture.chunks[0], env.chunk, 'До отказа буфер остаётся целым');
      assert.equal(env.elements.get('chatInput').value, 'Сохранённый черновик');
      env.fire(wait, clock === 'frozen' ? 0 : clock === 'delayed' ? 10000 : clock === 'oversleep' ? 60000 : 1500);
      waits += 1;
    }
    await flush();
    assert.equal(settled, true, 'Ожидание должно завершиться без настоящих таймеров');
    await pending;
    assert.equal(waits, clock === 'oversleep' ? 1 : clock === 'delayed' ? 3 : 20);
    assert.equal(env.metrics.fetch.length, clock === 'frozen' ? 21 : waits,
      'После истечения срока новый POST запрещён даже при задержанном таймере');
    if (clock === 'oversleep') assert.equal(env.now(), 61000);
    else assert.ok(env.now() <= 31000);
    assert.equal(env.voice.state.enabled, false);
    assert.equal(env.voice.state.phase, 'error');
    assert.match(env.voice.state.error, /всё ещё занято/);
    assert.equal(env.voice.state.capture, null);
    assert.equal(env.voice.state.controllers.size, 0);
    assert.equal(env.elements.get('chatInput').value, 'Сохранённый черновик');
    assert.equal(env.sent(), 0);
  }
});
