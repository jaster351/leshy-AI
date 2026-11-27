'use strict';

// Изолированные проверки адаптера; не запускать без отдельного разрешения.
const assert=require('node:assert/strict');
const test=require('node:test');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'..','web','voice-on-device.js'),'utf8');
function env(status='available'){
  let recognition,installed=0,localOptions=[],available=status;
  class R{
    static async available(opts){localOptions.push(opts);return available}
    static async install(opts){localOptions.push(opts);installed++;available='available';return true}
    constructor(){recognition=this}
    start(track){this.track=track}
    stop(){this.onend?.()}
    abort(){this.aborted=true}
  }
  R.prototype.processLocally=false;
  const window={isSecureContext:true,SpeechRecognition:R};
  vm.runInNewContext(source,{window,Intl,AbortController,setTimeout,clearTimeout,Promise});
  return {api:window.leshyOnDeviceVoice,window,get recognition(){return recognition},get installed(){return installed},localOptions};
}
test('Проверка и загрузка строго локального пакета',async()=>{
  const e=env('downloadable'),statuses=[];
  const result=await e.api.prepare({language:'ru-RU',onStatus:s=>statuses.push(s.status)});
  assert.equal(result.status,'ready');assert.equal(e.installed,1);
  assert.deepEqual(statuses,['checking','downloading','ready']);
  assert.ok(e.localOptions.every(o=>o.processLocally===true));
});
test('Удалённый HTTP и неподдерживаемый язык не запускают распознавание',async()=>{
  const e=env('unavailable');e.window.isSecureContext=false;
  assert.equal((await e.api.prepare()).status,'insecure');
  e.window.isSecureContext=true;
  assert.equal((await e.api.prepare()).status,'unavailable');
  assert.equal(e.installed,0);assert.equal(e.recognition,undefined);
});
test('Финальный текст отделён от промежуточного, остановка возвращает только финальный',async()=>{
  const e=env(),track={},updates=[];
  const r=e.api.start({track,onText:s=>updates.push(s)});
  assert.equal(e.recognition.processLocally,true);assert.equal(e.recognition.track,track);
  e.recognition.onresult({results:[Object.assign([{transcript:'Привет'}],{isFinal:true}),Object.assign([{transcript:'черновик'}],{isFinal:false})]});
  assert.equal(updates[0].finalText,'Привет');assert.equal(updates[0].interimText,'черновик');
  assert.equal(await r.stop(),'Привет');
});
test('Отмена не возвращает финальный текст',async()=>{
  const e=env(),controller=new AbortController();
  const r=e.api.start({signal:controller.signal});
  e.recognition.onresult({results:[Object.assign([{transcript:'не отправлять'}],{isFinal:true})]});
  controller.abort();
  await assert.rejects(r.done,/отменён/);assert.equal(e.recognition.aborted,true);
});
