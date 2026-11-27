(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  let state=null;

  async function api(path,options={}){
    const init=Object.assign({},options||{});init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const r=await fetch(path,init);const type=r.headers.get('content-type')||'';
    if(type.includes('application/json')){const d=await r.json().catch(()=>({error:'Некорректный ответ'}));if(!r.ok)throw new Error(d.error||('HTTP '+r.status));return d}
    if(!r.ok)throw new Error('HTTP '+r.status);return r;
  }
  function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
  function note(text,bad=false){const n=$('voiceSettingsStatus');if(!n)return;n.textContent=text||'';n.style.color=bad?'#ff9e9e':'#91a89a'}

  function ensure(){
    if($('voiceSettingsCard'))return true;const pane=$('pane-settings');if(!pane)return false;
    const card=document.createElement('div');card.className='card';card.id='voiceSettingsCard';card.style.marginTop='12px';
    card.innerHTML=`
      <h3>Голос Лешего</h3>
      <div class="hint" style="margin-bottom:10px">Выбор сохраняется локально в LeshyBrain и используется для всех голосовых ответов.</div>
      <div class="grid" style="gap:10px">
        <label class="item"><div class="item-title">Пол персонажа</div><select class="field" id="voiceGender"><option value="male">Мужской</option><option value="female">Женский</option></select></label>
        <label class="item"><div class="item-title">Голосовой движок</div><select class="field" id="voiceEngine"></select></label>
        <label class="item"><div class="item-title">Голос</div><select class="field" id="voiceId"></select></label>
        <label class="item"><div class="item-title">Характер голоса</div><select class="field" id="voiceProfile"></select></label>
      </div>
      <div class="item" style="margin-top:10px"><div class="item-title">Темп речи <span id="voiceRateValue" class="muted"></span></div><input id="voiceRate" type="range" min="0.70" max="1.30" step="0.05" value="1" style="width:100%"></div>
      <div class="toolbar" style="margin:10px 0 0"><button class="button" id="voiceSave">Сохранить</button><button class="button secondary" id="voiceInstall">Скачать / подключить</button><button class="button secondary" id="voicePreview">Проверить голос</button></div>
      <div class="hint" id="voiceEngineInfo" style="margin-top:8px"></div>
      <div class="hint" id="voiceSettingsStatus" style="margin-top:6px"></div>
      <div class="hint" style="margin-top:10px">Киношные профили задают характер подачи (герой, нуар, андроид, злодей, рассказчик), но не копируют голос конкретного актёра или персонажа.</div>`;
    pane.appendChild(card);
    $('voiceGender').onchange=()=>renderVoices();$('voiceEngine').onchange=()=>{renderVoices();renderEngineInfo()};$('voiceRate').oninput=()=>updateRate();
    $('voiceSave').onclick=save;$('voiceInstall').onclick=install;$('voicePreview').onclick=preview;
    return true;
  }

  function updateRate(){const v=Number($('voiceRate')?.value||1);if($('voiceRateValue'))$('voiceRateValue').textContent=v.toFixed(2)+'×'}
  function selectedEngine(){return $('voiceEngine')?.value||'auto'}
  function renderEngineInfo(){const e=(state?.engines||[]).find(x=>x.id===selectedEngine());if(!$('voiceEngineInfo'))return;$('voiceEngineInfo').textContent=e?`${e.description||''}${e.license?' · Лицензия: '+e.license:''}${e.available?' · доступен':' · ещё не подключён'}`:'';$('voiceInstall').disabled=!e||(!e.installable&&e.id!=='rhvoice')}
  function renderVoices(){
    const sel=$('voiceId');if(!sel||!state)return;const engine=selectedEngine(),gender=$('voiceGender')?.value||'male',pref=state.preference||{};sel.innerHTML='';
    const auto=document.createElement('option');auto.value='';auto.textContent='Автоматически';sel.appendChild(auto);
    let voices=(state.voices||[]).filter(v=>engine==='auto'||v.engine===engine||(engine==='rhvoice'&&v.engine==='windows'));
    voices=voices.filter(v=>!v.gender||v.gender===gender||String(v.gender).toLowerCase()==='notset');
    for(const v of voices){const o=document.createElement('option');o.value=v.id;o.textContent=`${v.name||v.id}${v.installed===false?' · скачать':''}`;sel.appendChild(o)}
    if(pref.voice_id&&[...sel.options].some(o=>o.value===pref.voice_id))sel.value=pref.voice_id;
  }
  function render(data){
    state=data;if(!ensure())return;const pref=data.preference||{};
    $('voiceGender').value=pref.selected_gender||'male';
    const eng=$('voiceEngine');eng.innerHTML='';for(const e of data.engines||[]){const o=document.createElement('option');o.value=e.id;o.textContent=e.name+(e.available?'':' · не установлен');eng.appendChild(o)}eng.value=pref.engine||'auto';
    const prof=$('voiceProfile');prof.innerHTML='';for(const p of data.profiles||[]){const o=document.createElement('option');o.value=p.id;o.textContent=p.name;prof.appendChild(o)}prof.value=pref.profile||'natural';
    $('voiceRate').value=String(pref.rate||1);updateRate();renderVoices();renderEngineInfo();note('Настройки загружены');
  }
  async function load(){try{render(await api('/v1/voice/settings'))}catch(e){note('Не удалось загрузить голосовые настройки: '+e.message,true)}}
  function payload(){return {selected_gender:$('voiceGender').value,engine:$('voiceEngine').value,voice_id:$('voiceId').value,profile:$('voiceProfile').value,rate:Number($('voiceRate').value||1)}}
  async function save(){try{note('Сохраняю…');render(await api('/v1/voice/settings',{method:'POST',body:JSON.stringify(payload())}));note('Голос сохранён и будет использоваться в следующих ответах')}catch(e){note('Ошибка сохранения: '+e.message,true)}}
  async function install(){try{const p=payload();note('Скачиваю и подключаю движок/голос…');const d=await api('/v1/voice/install',{method:'POST',body:JSON.stringify({engine:p.engine,voice_id:p.voice_id,selected_gender:p.selected_gender})});render(d);note(d.message||'Движок и голос готовы')}catch(e){note('Не удалось подключить: '+e.message,true)}}
  async function preview(){try{await save();note('Готовлю пример…');const r=await fetch('/v1/voice/speak',{method:'POST',headers:{'Content-Type':'application/json','X-Leshy-UI':'voice'},body:JSON.stringify({text:'Привет. Я Леший. Так будет звучать мой выбранный голос.'})});if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.error||('HTTP '+r.status))}const b=await r.blob(),u=URL.createObjectURL(b),a=new Audio(u);a.onended=()=>URL.revokeObjectURL(u);a.onerror=()=>URL.revokeObjectURL(u);await a.play();note('Воспроизвожу пример голоса')}catch(e){note('Проверка голоса не удалась: '+e.message,true)}}

  let callState={enabled:false,phase:'off',level:0,mode:'conversation'};
  let callFocus=null;
  const callLabels={off:'Готов к разговору',requesting:'Подключаю микрофон…',downloading:'Скачивание модели распознавания речи…',listening:'Слушаю Вас',transcribing:'Распознаю речь…',thinking:'Леший думает…',speaking:'Леший говорит',error:'Разговор прерван'};
  const callIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3H4a1 1 0 0 0-1 1c0 9.4 7.6 17 17 17a1 1 0 0 0 1-1v-3l-5-2-2 2a15 15 0 0 1-7-7l2-2Z"/></svg>';

  function callStyle(){
    if($('leshyCallStyle'))return;
    const style=document.createElement('style');style.id='leshyCallStyle';
    style.textContent=`
      #leshyCallDialog{--call-height:100vh;--voice-level:0;position:fixed;inset:0 auto auto 0;top:var(--leshy-viewport-offset-top,0px);width:100vw;height:var(--leshy-viewport-height,var(--call-height));max-width:none;max-height:none;margin:0;border:0;padding:24px max(18px,env(safe-area-inset-right,0px)) max(24px,env(safe-area-inset-bottom,0px)) max(18px,env(safe-area-inset-left,0px));box-sizing:border-box;overflow:auto;background:radial-gradient(ellipse at 50% 30%,#183a37,#101c20 58%,#11171c);color:#edf5f2;font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;z-index:4000}
      #leshyCallDialog:not([open]){display:none}#leshyCallDialog[open]{display:flex;flex-direction:column;align-items:center}#leshyCallDialog::backdrop{background:#090f12dd}
      #leshyCallDialog .call-header{width:min(640px,100%);display:flex;align-items:center;justify-content:space-between;gap:12px;padding-top:env(safe-area-inset-top,0px)}
      #leshyCallDialog h2{font-size:20px;font-weight:600;margin:0}#leshyCallDialog .call-body{width:min(580px,100%);min-height:0;margin:auto;display:flex;flex-direction:column;align-items:center;text-align:center;padding:30px 0;gap:20px}
      #leshyCallDialog button{font:inherit;cursor:pointer;border:1px solid #54746c;background:#24443d;color:#f1faf7;border-radius:999px;min-height:44px;padding:10px 20px}#leshyCallDialog button:focus-visible{outline:2px solid #b8f3db;outline-offset:4px}#leshyCallDialog button:disabled{opacity:.45;cursor:default}
      #leshyCallClose{width:44px;min-width:44px;padding:0!important;background:transparent!important;font-size:23px!important}
      #leshyCallDialog .call-orb{width:clamp(116px,30vw,220px);aspect-ratio:1;flex:none;position:relative;display:grid;place-items:center;margin:16px;border-radius:50%;background:radial-gradient(circle at 30% 27%,#d1fff0,#67d5b2 27%,#2e9081 58%,#1e5159);box-shadow:0 0 54px #58d2b333,inset -12px -16px 35px #153e5066;transform:scale(calc(1 + var(--voice-level)*.08));transition:transform .12s ease-out,filter .25s}
      #leshyCallDialog .call-orb:before{content:'';position:absolute;inset:-12px;border:1px solid #7ee1c366;border-radius:50%;box-shadow:0 0 0 10px #5fd3af0d}
      #leshyCallDialog[data-phase="listening"] .call-orb:before{animation:leshyCallListen 2.5s ease-in-out infinite}#leshyCallDialog[data-phase="thinking"] .call-orb:before,#leshyCallDialog[data-phase="transcribing"] .call-orb:before{border-width:3px;border-color:#91e5cc22;border-top-color:#bdffe8;animation:leshyCallThink 1.4s linear infinite}
      #leshyCallDialog[data-phase="speaking"] .call-orb{animation:leshyCallSpeak 1.2s ease-in-out infinite}#leshyCallDialog[data-phase="error"] .call-orb{filter:hue-rotate(155deg) saturate(.65)}#leshyCallDialog[data-phase="off"] .call-orb{filter:saturate(.35)}
      #leshyCallStatus{font-size:clamp(18px,4vw,24px);line-height:1.35;min-height:1.4em;margin:0}#leshyCallError{color:#ffd0c8;font-size:14px;line-height:1.5;overflow-wrap:anywhere;margin:0}#leshyCallError[hidden]{display:none}
      #leshyCallDialog .call-hint{color:#aac2bb;font-size:13px;line-height:1.5;max-width:430px;margin:0}#leshyCallDialog .call-controls{display:flex;flex-wrap:wrap;justify-content:center;gap:10px}#leshyCallEnd{background:#713d3b!important;border-color:#a2625b!important}#leshyCallDialog button[hidden]{display:none}
      #leshyTopActions #leshyCallLaunch{min-height:40px;gap:6px}#leshyCallLaunch svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linejoin:round}
      @supports(height:100dvh){#leshyCallDialog{--call-height:100dvh}}@media(max-width:480px){#leshyCallLaunch .call-label{display:none}#leshyCallLaunch{width:40px;padding:0!important}#leshyCallDialog{padding-top:12px}#leshyCallDialog .call-body{padding:20px 0;gap:16px}}
      @keyframes leshyCallListen{50%{opacity:.35;transform:scale(1.06)}}@keyframes leshyCallThink{to{transform:rotate(360deg)}}@keyframes leshyCallSpeak{50%{box-shadow:0 0 70px #7befc666,inset -12px -16px 35px #153e5066;filter:brightness(1.12)}}
      #leshyCallDialog{position:relative;inset:auto;top:auto;width:100%;height:auto;max-height:none;margin:8px 0;padding:14px 18px;border:1px solid #51535b;border-radius:26px;background:#30323a;overflow:visible;z-index:auto}
      #leshyCallDialog .call-header{width:100%;padding:0}#leshyCallDialog h2{font-size:13px;font-weight:500;color:#d4d7e3}#leshyCallDialog .call-body{width:100%;padding:0;margin:0;gap:8px}
      #leshyCallDialog .call-wave{height:64px;width:100%;display:flex;align-items:center;justify-content:center;gap:4px;overflow:hidden}
      #leshyCallDialog .call-wave span{flex:1;max-width:15px;min-width:2px;height:7px;border-radius:999px;background:linear-gradient(180deg,#b2a0ff,#397fff);opacity:.55;transition:height .12s,opacity .12s}
      #leshyCallDialog .call-controls{width:100%;justify-content:flex-end;align-items:center}#leshyCallStatus{font-size:13px;min-height:0}#leshyCallDialog .call-hint{max-width:none;font-size:12px}
      #leshyCallSend{background:#3068ed!important;border-color:#3068ed!important;font-size:20px!important;min-width:52px;padding:8px!important}#leshyCallEnd{background:#535660!important;border-color:#535660!important;font-size:18px!important;min-width:52px;padding:8px!important}
      @media(prefers-reduced-motion:reduce){#leshyCallDialog .call-wave span{transition:none!important}}
    `;
    document.head.appendChild(style);
  }

  function ensureCallDialog(){
    callStyle();let dialog=$('leshyCallDialog');if(dialog)return dialog;
    dialog=document.createElement('dialog');dialog.id='leshyCallDialog';dialog.setAttribute('aria-labelledby','leshyCallTitle');dialog.setAttribute('aria-modal','false');
    dialog.innerHTML='<div class="call-header"><h2 id="leshyCallTitle">Разговор с Лешим</h2><button id="leshyCallClose" type="button" aria-label="Закрыть и завершить разговор">×</button></div><div class="call-body"><div class="call-wave" aria-hidden="true"></div><p id="leshyCallStatus" role="status" aria-live="polite"></p><p id="leshyCallError" hidden></p><p class="call-hint">Говорите обычными фразами. После ответа Леший снова начнёт слушать. Ответ можно прервать кнопкой.</p><div class="call-controls"><button id="leshyCallStart" type="button">Позвонить</button><button id="leshyCallInterrupt" type="button">Прервать ответ</button><button id="leshyCallServer" type="button">Распознавать на сервере</button><button id="leshyCallEnd" type="button" aria-label="Завершить разговор">■</button><button id="leshyCallSend" type="button" aria-label="Отправить реплику">↑</button></div></div>';
    const composer=document.querySelector('#pane-chat .composer');
    (composer?.parentElement||document.body).appendChild(dialog);
    const wave=dialog.querySelector('.call-wave');
    for(let i=0;i<42;i++)wave.appendChild(document.createElement('span'));
    $('leshyCallStart').onclick=()=>callAction('start');
    $('leshyCallInterrupt').onclick=()=>callAction('interrupt');
    $('leshyCallServer').onclick=()=>callAction('startServer');
    $('leshyCallSend').onclick=()=>callAction('send');
    $('leshyCallEnd').onclick=()=>endCall();$('leshyCallClose').onclick=()=>endCall();
    dialog.addEventListener('cancel',event=>{event.preventDefault();endCall()});
    return dialog;
  }

  function closeCallDialog(){
    const dialog=$('leshyCallDialog');if(!dialog)return;
    if(typeof dialog.close==='function'&&dialog.open)dialog.close();else dialog.removeAttribute('open');
    if(callFocus&&callFocus.isConnected)callFocus.focus();callFocus=null;
  }

  function showCallDialog(){
    const dialog=ensureCallDialog();if(dialog.open)return;
    callFocus=document.activeElement;
    if(typeof dialog.show==='function')dialog.show();else dialog.setAttribute('open','');
    renderCall(callState);
  }

  function renderCall(detail){
    callState=Object.assign({},callState,detail||{});
    const active=!!callState.enabled&&callState.mode!=='dictation';
    const dialog=ensureCallDialog(),phase=callState.mode==='dictation'?'off':(callLabels[callState.phase]?callState.phase:(active?'listening':'off'));
    if((active||(callState.phase==='error'&&callState.mode==='conversation'))&&!dialog.open)showCallDialog();
    dialog.dataset.phase=phase;
    const level=Math.max(0,Math.min(1,Number(callState.level)||0));dialog.style.setProperty('--voice-level',String(level));
    const label=callLabels[phase];if($('leshyCallStatus').textContent!==label)$('leshyCallStatus').textContent=label;
    const error=phase==='error'?String(callState.error||''):'';$('leshyCallError').textContent=error;$('leshyCallError').hidden=!error;
    $('leshyCallStart').hidden=active;$('leshyCallEnd').hidden=!active;
    $('leshyCallServer').hidden=active;
    $('leshyCallSend').hidden=!active;$('leshyCallSend').disabled=phase!=='listening';
    const bars=Array.from(dialog.querySelectorAll('.call-wave span'));
    if(detail?.reason==='level'&&phase==='listening'){
      for(let i=0;i<bars.length-1;i++){bars[i].style.height=bars[i+1].style.height||'7px';bars[i].style.opacity=bars[i+1].style.opacity||'.4'}
      if(bars.length){bars.at(-1).style.height=(7+level*49)+'px';bars.at(-1).style.opacity=String(.4+level*.6)}
    }else if(phase!=='listening'){for(const bar of bars){bar.style.height='7px';bar.style.opacity='.4'}}
    $('leshyCallInterrupt').hidden=!active;$('leshyCallInterrupt').disabled=!['thinking','speaking'].includes(phase);
    const button=$('leshyCallLaunch');if(button){button.setAttribute('aria-pressed',active?'true':'false');button.title=active?'Открыть текущий разговор':'Позвонить Лешему'}
  }

  async function callAction(name){
    showCallDialog();const api=window.leshyVoiceCall;
    if(!api||typeof api[name]!=='function'){renderCall({phase:'error',error:'Голосовой модуль ещё не готов. Попробуйте ещё раз.'});return}
    try{await api[name]()}catch(error){renderCall({phase:'error',error:error?.message||String(error)})}
  }

  async function endCall(){
    try{if(window.leshyVoiceCall?.stop)await window.leshyVoiceCall.stop();else if(callState.enabled)throw new Error('Голосовой модуль недоступен');closeCallDialog()}
    catch(error){renderCall({phase:'error',error:'Не удалось завершить разговор: '+(error?.message||String(error))})}
  }

  function attachCallLaunch(){
    if($('leshyCallLaunch'))return true;const actions=$('leshyTopActions');if(!actions)return false;
    const button=document.createElement('button');button.id='leshyCallLaunch';button.type='button';button.innerHTML=callIcon+'<span class="call-label">Позвонить</span>';button.setAttribute('aria-label','Позвонить Лешему');button.setAttribute('aria-pressed','false');
    button.onclick=()=>{showCallDialog();if(!callState.enabled||callState.mode==='dictation')callAction('start')};actions.insertBefore(button,actions.firstChild);return true;
  }

  function installCallUi(){
    ensureCallDialog();renderCall(window.leshyVoiceState||callState);
    window.addEventListener('leshy:voice-state',event=>renderCall(event.detail));
    if(!attachCallLaunch()){const observer=new MutationObserver(()=>{if(attachCallLaunch())observer.disconnect()});observer.observe(document.documentElement,{childList:true,subtree:true})}
  }

  function boot(){installCallUi();if(!ensure()){const ob=new MutationObserver(()=>{if(ensure()){ob.disconnect();load()}});ob.observe(document.documentElement,{childList:true,subtree:true})}else load();window.addEventListener('leshy:session-changed',()=>{})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
