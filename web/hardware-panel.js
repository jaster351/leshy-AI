(function(){
  'use strict';

  async function request(path, options){
    if(typeof api==='function') return api(path, options);
    const response=await fetch(path,Object.assign({headers:{'Content-Type':'application/json'}},options||{}));
    const body=await response.json();
    if(!response.ok) throw new Error(body.error||('HTTP '+response.status));
    return body;
  }

  const style=document.createElement('style');
  style.textContent=`
    .compute-card{margin-top:12px}
    .compute-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:10px 0}
    .compute-chip{border:1px solid var(--line);border-radius:11px;padding:10px;background:rgba(255,255,255,.025)}
    .compute-chip b{display:block;font-size:13px;margin-bottom:3px}.compute-chip span{font-size:10px;color:var(--muted);word-break:break-word}
    .compute-ready b{color:var(--accent)}.compute-off b{color:var(--muted)}
    .compute-note{margin-top:8px;padding:9px;border-radius:10px;background:rgba(147,221,177,.06);font-size:11px;color:#b7cbbf;line-height:1.45}
    @media(max-width:720px){.compute-grid{grid-template-columns:1fr}}
  `;
  document.head.appendChild(style);

  function el(tag, attrs, text){
    const node=document.createElement(tag);
    if(attrs) Object.entries(attrs).forEach(([k,v])=>{if(k==='class')node.className=v;else node.setAttribute(k,v);});
    if(text!=null) node.textContent=text;
    return node;
  }

  function names(values, fallback){return Array.isArray(values)&&values.length?values.join(', '):fallback;}

  async function refresh(card){
    const status=await request('/v1/hardware');
    const caps=status.capabilities||{};
    const plan=status.plan||{};
    const cfg=status.config||{};
    const select=card.querySelector('[data-compute-mode]');
    if(select) select.value=cfg.mode||'auto';
    const chips=card.querySelector('[data-compute-chips]');
    chips.innerHTML='';
    [
      ['CPU',true,caps.cpu_name||'CPU'],
      ['GPU',!!caps.gpu,names(caps.gpu_names,'Не обнаружен')],
      ['NPU',!!caps.npu,names(caps.npu_names,'Не обнаружен')]
    ].forEach(([title,ready,detail])=>{
      const chip=el('div',{class:'compute-chip '+(ready?'compute-ready':'compute-off')});
      chip.append(el('b',null,title+(ready?' · доступен':' · нет')),el('span',null,detail));
      chips.appendChild(chip);
    });
    const summary=card.querySelector('[data-compute-summary]');
    summary.textContent='Активный план: '+(plan.effective_mode||'—')+' · основной backend: '+(plan.chat_backend||'—')+' · фон: '+((plan.background_devices||[]).join(' + ')||'—');
    const warnings=card.querySelector('[data-compute-warnings]');
    const list=[...(caps.notes||[]),...(plan.warnings||[])];
    warnings.textContent=list.length?list.join(' · '):'Леший автоматически выбирает доступные ускорители и сохраняет CPU как резерв для системных задач и fallback.';
  }

  function install(){
    const pane=document.getElementById('pane-settings');
    if(!pane||document.getElementById('computeResourceCard')) return;
    const card=el('div',{class:'card compute-card',id:'computeResourceCard'});
    card.innerHTML=`
      <h3>Производительность</h3>
      <div class="muted">Управление CPU, GPU и NPU. Режим «Все ресурсы» включает максимально доступную комбинацию ускорителей.</div>
      <div class="compute-grid" data-compute-chips></div>
      <div class="row"><span>Режим вычислений</span><select class="field" style="width:auto;min-width:230px" data-compute-mode>
        <option value="auto">Автоматически</option>
        <option value="cpu_gpu_npu">Все ресурсы: CPU + GPU + NPU</option>
        <option value="cpu_npu">CPU + NPU</option>
        <option value="cpu_gpu">CPU + GPU</option>
        <option value="gpu_npu">GPU + NPU</option>
        <option value="gpu">Только GPU</option>
        <option value="npu">Только NPU</option>
        <option value="cpu">Только CPU</option>
      </select></div>
      <div class="compute-note" data-compute-summary>Загрузка…</div>
      <div class="hint" data-compute-warnings style="margin-top:8px"></div>
      <button class="button" style="width:100%;margin-top:10px" data-compute-save>Применить режим</button>
    `;
    pane.appendChild(card);
    card.querySelector('[data-compute-save]').addEventListener('click',async function(){
      const button=this;button.disabled=true;button.textContent='Применяю…';
      try{
        await request('/v1/hardware/config',{method:'POST',body:JSON.stringify({mode:card.querySelector('[data-compute-mode]').value,prefer_npu_for_background:true})});
        await refresh(card);
        button.textContent='Сохранено · применяется после перезапуска runtime';
      }catch(e){button.textContent='Ошибка: '+e.message;}
      finally{setTimeout(()=>{button.disabled=false;button.textContent='Применить режим';},2400);}
    });
    refresh(card).catch(e=>{card.querySelector('[data-compute-summary]').textContent='Не удалось получить аппаратный статус: '+e.message;});
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',install); else install();
})();

(function(){
  'use strict';

  const WAKE_PHRASES=['привет леший','слушай лёша','слушай леша','слушай леший','окей лёша','окей леша','окей леший'];
  const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  const voiceKey='leshy.voice.enabled';
  const replyKey='leshy.voice.reply';
  let recognition=null;
  let voiceEnabled=false;
  let armed=false;
  let restartTimer=null;
  let lastVoiceCommandAt=0;
  let pendingStartedAt=0;

  function $(id){return document.getElementById(id);}
  function norm(value){return String(value||'').toLowerCase().replace(/[.,!?;:—–-]/g,' ').replace(/\s+/g,' ').trim();}
  function voiceReply(){return localStorage.getItem(replyKey)!=='0';}

  const style=document.createElement('style');
  style.textContent=`
    .leshy-persona-card{display:grid;grid-template-columns:58px minmax(0,1fr);gap:12px;align-items:center}
    .leshy-face{width:58px;height:58px;border-radius:50%;position:relative;background:radial-gradient(circle at 38% 34%,#e4f6eb 0 7%,#7abb99 8% 26%,#436f59 44%,#26382e 72%,#17221c 100%);box-shadow:0 0 0 1px rgba(72,112,89,.18),0 8px 24px rgba(36,62,47,.18);transition:.2s ease}
    .leshy-face:before,.leshy-face:after{content:'';position:absolute;top:23px;width:6px;height:4px;border-radius:50%;background:#15231b;transition:.2s}.leshy-face:before{left:17px}.leshy-face:after{right:17px}
    .leshy-face .mouth{position:absolute;left:23px;top:37px;width:12px;height:4px;border-bottom:2px solid #183126;border-radius:0 0 12px 12px;transition:.2s}
    .leshy-face[data-state='listening']{box-shadow:0 0 0 5px rgba(62,120,90,.11),0 0 28px rgba(62,120,90,.28)}
    .leshy-face[data-state='heard']{transform:scale(1.06);filter:brightness(1.12)}
    .leshy-face[data-state='thinking']{animation:leshy-think 1.2s ease-in-out infinite alternate}.leshy-face[data-state='thinking'] .mouth{width:8px;left:25px;border-radius:50%}
    .leshy-face[data-state='speaking'] .mouth{height:8px;border:2px solid #183126;border-top:0;animation:leshy-talk .38s linear infinite alternate}
    .leshy-face[data-state='error']{filter:saturate(.55) hue-rotate(300deg)}
    @keyframes leshy-think{to{transform:translateY(-2px);filter:brightness(1.08)}}@keyframes leshy-talk{to{height:3px}}
    .leshy-persona-title{font-weight:600;font-size:12px;color:#344054}.leshy-persona-state{font-size:11px;color:#667085;margin-top:3px}.leshy-persona-detail{font-size:10px;color:#8a93a3;margin-top:5px;line-height:1.35}
    .voice-controls{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px}.voice-controls button{flex:1;min-width:110px}
    #voiceToggle.voice-on{background:#e9f5ee!important;border-color:#9dcab0!important;color:#286944!important}.voice-live-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#9da6b4;margin-right:6px}.voice-live-dot.on{background:#4b9a69;box-shadow:0 0 0 4px rgba(75,154,105,.10)}
    .voice-head{white-space:nowrap}@media(max-width:1050px){.voice-head{display:none}}
  `;
  document.head.appendChild(style);

  function label(state){return ({idle:'Готов',listening:'Слушаю wake-word',heard:'Услышал Вас',thinking:'Думаю',speaking:'Отвечаю',acting:'Выполняю действие',error:'Ошибка'})[state]||state;}
  function setState(state,detail){
    const face=$('leshyPersonaFace'),title=$('leshyPersonaState'),info=$('leshyPersonaDetail');
    if(face)face.dataset.state=state;if(title)title.textContent=label(state);if(info&&detail)info.textContent=detail;
  }

  function installPersona(){
    if($('leshyPersonaCard'))return;
    const context=document.querySelector('.context');if(!context)return;
    const card=document.createElement('div');card.id='leshyPersonaCard';card.className='card';
    card.innerHTML=`<div class="leshy-persona-card"><div class="leshy-face" id="leshyPersonaFace" data-state="idle"><span class="mouth"></span></div><div><div class="leshy-persona-title">Леший</div><div class="leshy-persona-state" id="leshyPersonaState">Готов</div><div class="leshy-persona-detail" id="leshyPersonaDetail">Голосовой режим выключен.</div></div></div><div class="voice-controls"><button class="button secondary small" id="voiceToggle" type="button">Микрофон: ВЫКЛ</button><button class="button secondary small" id="voiceReplyToggle" type="button">Озвучка: ВКЛ</button></div><div class="hint" style="margin-top:7px">Wake-word: «Привет, Леший», «Слушай, Лёша/Леший», «Окей, Лёша/Леший».</div><div class="hint" id="generationPerf" style="margin-top:5px">Последний ответ: —</div>`;
    const h=context.querySelector('h3');if(h)h.insertAdjacentElement('afterend',card);else context.prepend(card);
  }

  function installHeader(){
    if($('voiceHeadToggle'))return;const host=document.querySelector('.head-actions');if(!host)return;
    const b=document.createElement('button');b.id='voiceHeadToggle';b.type='button';b.className='button secondary small voice-head';b.innerHTML='<span class="voice-live-dot" id="voiceLiveDot"></span>Микрофон';b.onclick=toggleVoice;host.insertBefore(b,host.firstChild);
  }

  function updateButtons(){
    const b=$('voiceToggle');if(b){b.textContent='Микрофон: '+(voiceEnabled?'ВКЛ':'ВЫКЛ');b.classList.toggle('voice-on',voiceEnabled);}
    const r=$('voiceReplyToggle');if(r)r.textContent='Озвучка: '+(voiceReply()?'ВКЛ':'ВЫКЛ');
    const dot=$('voiceLiveDot');if(dot)dot.classList.toggle('on',voiceEnabled);
  }

  function beep(){
    try{const C=window.AudioContext||window.webkitAudioContext;if(!C)return;const c=new C(),o=c.createOscillator(),g=c.createGain();o.frequency.value=620;g.gain.setValueAtTime(.0001,c.currentTime);g.gain.exponentialRampToValueAtTime(.08,c.currentTime+.015);g.gain.exponentialRampToValueAtTime(.0001,c.currentTime+.12);o.connect(g).connect(c.destination);o.start();o.stop(c.currentTime+.13);o.onended=()=>c.close();}catch(_e){}
  }

  function wake(text){const value=norm(text);for(const phrase of WAKE_PHRASES){const p=norm(phrase),i=value.indexOf(p);if(i>=0)return value.slice(i+p.length).trim();}return null;}
  function submitVoice(command){
    command=String(command||'').trim();if(!command)return;armed=false;lastVoiceCommandAt=Date.now();const input=$('chatInput')||$('heroInput');if(!input)return;input.value=command;setState('thinking','Обрабатываю голосовой запрос.');if(typeof window.sendFrom==='function')window.sendFrom(input);else{const send=$('chatSend')||$('heroSend');if(send)send.click();}
  }
  function transcript(text){
    if(!voiceEnabled)return;if(armed){submitVoice(text);return;}const command=wake(text);if(command===null)return;beep();armed=true;setState('heard','Ключевая фраза распознана.');setTimeout(()=>{if(armed&&voiceEnabled)setState('listening','Говорите команду.');},350);if(command)setTimeout(()=>submitVoice(command),180);
  }

  function createRecognition(){
    if(!Recognition)return null;const r=new Recognition();r.lang='ru-RU';r.continuous=true;r.interimResults=false;r.maxAlternatives=1;
    r.onresult=e=>{for(let i=e.resultIndex;i<e.results.length;i++)if(e.results[i].isFinal)transcript(e.results[i][0].transcript||'');};
    r.onerror=e=>{if(e.error==='not-allowed'||e.error==='service-not-allowed'){voiceEnabled=false;localStorage.setItem(voiceKey,'0');updateButtons();setState('error','Браузер запретил доступ к микрофону.');}else if(voiceEnabled)setState('error','Ошибка распознавания: '+e.error);};
    r.onend=()=>{if(voiceEnabled){clearTimeout(restartTimer);restartTimer=setTimeout(startRecognition,450);}};return r;
  }
  function startRecognition(){if(!voiceEnabled||!Recognition)return;if(!recognition)recognition=createRecognition();try{recognition.start();setState('listening','Ожидаю ключевую фразу.');}catch(_e){}}
  function stopRecognition(){clearTimeout(restartTimer);armed=false;if(recognition)try{recognition.stop();}catch(_e){}setState('idle','Голосовой режим выключен.');}
  async function enableVoice(){
    if(!Recognition){setState('error','В этом браузере нет Web Speech API для распознавания речи.');return;}
    try{if(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia){const s=await navigator.mediaDevices.getUserMedia({audio:true});s.getTracks().forEach(t=>t.stop());}voiceEnabled=true;localStorage.setItem(voiceKey,'1');updateButtons();startRecognition();}catch(e){voiceEnabled=false;localStorage.setItem(voiceKey,'0');updateButtons();setState('error','Не удалось получить микрофон: '+e.message);}
  }
  function disableVoice(){voiceEnabled=false;localStorage.setItem(voiceKey,'0');updateButtons();stopRecognition();}
  function toggleVoice(){if(voiceEnabled)disableVoice();else enableVoice();}

  function speak(text){
    if(!voiceReply()||!('speechSynthesis'in window)||Date.now()-lastVoiceCommandAt>120000)return;const clean=String(text||'').replace(/<think>[\s\S]*?<\/think>/gi,'').trim();if(!clean)return;speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(clean);u.lang='ru-RU';u.rate=1;u.pitch=.95;u.onstart=()=>setState('speaking','Озвучиваю ответ.');u.onend=()=>setState(voiceEnabled?'listening':'idle',voiceEnabled?'Ожидаю ключевую фразу.':'Готов.');u.onerror=()=>setState(voiceEnabled?'listening':'idle','Не удалось озвучить ответ.');speechSynthesis.speak(u);
  }

  function observeChat(){
    const state=$('chatState');if(state)new MutationObserver(()=>{const t=(state.textContent||'').toLowerCase();if(t.includes('дума'))setState('thinking','Формирую ответ.');else if(t.includes('ошиб'))setState('error',state.textContent);else if(t.includes('генер'))setState('acting',state.textContent);else if(t.includes('готов')&&!speechSynthesis.speaking)setState(voiceEnabled?'listening':'idle',voiceEnabled?'Ожидаю ключевую фразу.':'Готов.');}).observe(state,{childList:true,subtree:true,characterData:true});
    const messages=$('messages');if(!messages)return;
    new MutationObserver(mutations=>{for(const m of mutations)for(const node of m.addedNodes){if(!(node instanceof HTMLElement)||!node.classList.contains('msg'))continue;if(node.classList.contains('pending')){pendingStartedAt=Date.now();continue;}if(node.classList.contains('assistant')){const text=node.textContent.replace(/^Леший\s*/,'').trim();if(pendingStartedAt){const sec=Math.max(.01,(Date.now()-pendingStartedAt)/1000),perf=$('generationPerf');if(perf)perf.textContent='Последний ответ: '+sec.toFixed(1)+' с · ~'+Math.round(text.length/sec)+' зн/с';pendingStartedAt=0;}speak(text);}}}).observe(messages,{childList:true});
  }

  function boot(){
    installPersona();installHeader();
    const b=$('voiceToggle');if(b)b.onclick=toggleVoice;const r=$('voiceReplyToggle');if(r)r.onclick=()=>{localStorage.setItem(replyKey,voiceReply()?'0':'1');updateButtons();};
    observeChat();voiceEnabled=localStorage.getItem(voiceKey)==='1';updateButtons();if(voiceEnabled)enableVoice();
  }

  window.LeshyPersona={setState};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
