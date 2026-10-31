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
  async function preview(){try{await save();note('Готовлю пример…');const r=await fetch('/v1/voice/speak',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'Привет. Я Леший. Так будет звучать мой выбранный голос.'})});if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.error||('HTTP '+r.status))}const b=await r.blob(),u=URL.createObjectURL(b),a=new Audio(u);a.onended=()=>URL.revokeObjectURL(u);a.onerror=()=>URL.revokeObjectURL(u);await a.play();note('Воспроизвожу пример голоса')}catch(e){note('Проверка голоса не удалась: '+e.message,true)}}

  function boot(){if(!ensure()){const ob=new MutationObserver(()=>{if(ensure()){ob.disconnect();load()}});ob.observe(document.documentElement,{childList:true,subtree:true})}else load();window.addEventListener('leshy:session-changed',()=>{})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
