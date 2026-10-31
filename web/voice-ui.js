(function(){
  'use strict';

  const WAKE_PHRASES=[
    'привет леший',
    'слушай лёша',
    'слушай леша',
    'слушай леший',
    'окей лёша',
    'окей леша',
    'окей леший'
  ];
  const storageKey='leshy.voice.enabled';
  const speechReplyKey='leshy.voice.reply';
  const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  let recognition=null;
  let enabled=false;
  let armed=false;
  let restartTimer=null;
  let lastVoiceCommandAt=0;

  function $(id){return document.getElementById(id);}
  function normalize(value){return String(value||'').toLowerCase().replace(/[.,!?;:—–-]/g,' ').replace(/ё/g,'ё').replace(/\s+/g,' ').trim();}

  const style=document.createElement('style');
  style.textContent=`
    .leshy-persona-card{display:grid;grid-template-columns:58px minmax(0,1fr);gap:12px;align-items:center}
    .leshy-face{width:58px;height:58px;border-radius:50%;position:relative;background:radial-gradient(circle at 38% 34%,#dff6e8 0 7%,#75b895 8% 26%,#3e6d55 44%,#22352b 72%,#17221c 100%);box-shadow:0 0 0 1px rgba(72,112,89,.18),0 8px 24px rgba(36,62,47,.18);transition:.2s ease}
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
    #voiceToggle.voice-on{background:#e9f5ee!important;border-color:#9dcab0!important;color:#286944!important}
    .voice-live-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#9da6b4;margin-right:6px}.voice-live-dot.on{background:#4b9a69;box-shadow:0 0 0 4px rgba(75,154,105,.10)}
    .voice-head{white-space:nowrap}
    @media(max-width:1050px){.voice-head{display:none}}
  `;
  document.head.appendChild(style);

  function stateLabel(state){
    return ({idle:'Готов',listening:'Слушаю wake-word',heard:'Услышал Вас',thinking:'Думаю',speaking:'Отвечаю',acting:'Выполняю действие',error:'Ошибка'})[state]||state;
  }

  function setPersonaState(state,detail){
    const face=$('leshyPersonaFace');
    const label=$('leshyPersonaState');
    const info=$('leshyPersonaDetail');
    if(face) face.dataset.state=state;
    if(label) label.textContent=stateLabel(state);
    if(info&&detail) info.textContent=detail;
    const dot=$('voiceLiveDot');
    if(dot) dot.classList.toggle('on',enabled&&state==='listening');
  }

  function installPersona(){
    if($('leshyPersonaCard')) return;
    const context=document.querySelector('.context');
    const host=context||document.querySelector('#pane-overview')||document.body;
    const card=document.createElement('div');
    card.id='leshyPersonaCard';
    card.className='card';
    card.innerHTML=`
      <div class="leshy-persona-card">
        <div class="leshy-face" id="leshyPersonaFace" data-state="idle"><span class="mouth"></span></div>
        <div><div class="leshy-persona-title">Леший</div><div class="leshy-persona-state" id="leshyPersonaState">Готов</div><div class="leshy-persona-detail" id="leshyPersonaDetail">Голосовой режим выключен.</div></div>
      </div>
      <div class="voice-controls">
        <button class="button secondary small" id="voiceToggle" type="button">Микрофон: ВЫКЛ</button>
        <button class="button secondary small" id="voiceReplyToggle" type="button">Озвучка: ВКЛ</button>
      </div>
      <div class="hint" style="margin-top:7px">Wake-word: «Привет, Леший», «Слушай, Лёша/Леший», «Окей, Лёша/Леший».</div>
      <div class="hint" id="generationPerf" style="margin-top:5px">Последний ответ: —</div>
    `;
    if(context){
      const heading=context.querySelector('h3');
      if(heading) heading.insertAdjacentElement('afterend',card); else context.prepend(card);
    }else host.prepend(card);
  }

  function installHeaderControl(){
    if($('voiceHeadToggle')) return;
    const host=document.querySelector('.head-actions');
    if(!host) return;
    const button=document.createElement('button');
    button.id='voiceHeadToggle';
    button.type='button';
    button.className='button secondary small voice-head';
    button.innerHTML='<span class="voice-live-dot" id="voiceLiveDot"></span>Микрофон';
    button.onclick=toggleVoice;
    host.insertBefore(button,host.firstChild);
  }

  function updateButtons(){
    const b=$('voiceToggle');
    if(b){b.textContent='Микрофон: '+(enabled?'ВКЛ':'ВЫКЛ');b.classList.toggle('voice-on',enabled);}
    const reply=$('voiceReplyToggle');
    if(reply) reply.textContent='Озвучка: '+(speechReplyEnabled()?'ВКЛ':'ВЫКЛ');
    const detail=$('leshyPersonaDetail');
    if(detail) detail.textContent=enabled?'Микрофон активен. Ожидаю ключевую фразу.':'Голосовой режим выключен.';
    const dot=$('voiceLiveDot'); if(dot) dot.classList.toggle('on',enabled);
  }

  function speechReplyEnabled(){return localStorage.getItem(speechReplyKey)!=='0';}

  function beep(){
    try{
      const Ctx=window.AudioContext||window.webkitAudioContext;
      if(!Ctx) return;
      const ctx=new Ctx();
      const osc=ctx.createOscillator();const gain=ctx.createGain();
      osc.frequency.value=620;gain.gain.setValueAtTime(.0001,ctx.currentTime);gain.gain.exponentialRampToValueAtTime(.08,ctx.currentTime+.015);gain.gain.exponentialRampToValueAtTime(.0001,ctx.currentTime+.12);
      osc.connect(gain).connect(ctx.destination);osc.start();osc.stop(ctx.currentTime+.13);osc.onended=()=>ctx.close();
    }catch(_e){}
  }

  function wakeMatch(text){
    const normalized=normalize(text);
    for(const phrase of WAKE_PHRASES){
      const p=normalize(phrase);const index=normalized.indexOf(p);
      if(index>=0) return {phrase:p,command:normalized.slice(index+p.length).trim()};
    }
    return null;
  }

  function submitVoiceCommand(command){
    command=String(command||'').trim();
    if(!command) return;
    armed=false;lastVoiceCommandAt=Date.now();
    const input=$('chatInput')||$('heroInput');
    if(!input) return;
    input.value=command;
    setPersonaState('thinking','Обрабатываю голосовой запрос.');
    if(typeof window.sendFrom==='function') window.sendFrom(input);
    else {const send=$('chatSend')||$('heroSend'); if(send) send.click();}
  }

  function onTranscript(text){
    if(!enabled) return;
    if(armed){submitVoiceCommand(text);return;}
    const match=wakeMatch(text);
    if(!match) return;
    beep();armed=true;
    setPersonaState('heard','Ключевая фраза распознана.');
    setTimeout(()=>{if(armed&&enabled)setPersonaState('listening','Говорите команду после ключевой фразы.');},350);
    if(match.command) setTimeout(()=>submitVoiceCommand(match.command),180);
  }

  function makeRecognition(){
    if(!Recognition) return null;
    const r=new Recognition();
    r.lang='ru-RU';r.continuous=true;r.interimResults=false; r.maxAlternatives=1;
    r.onresult=(event)=>{
      for(let i=event.resultIndex;i<event.results.length;i++){
        if(event.results[i].isFinal) onTranscript(event.results[i][0].transcript||'');
      }
    };
    r.onerror=(event)=>{
      if(event.error==='not-allowed'||event.error==='service-not-allowed'){
        enabled=false;localStorage.setItem(storageKey,'0');updateButtons();setPersonaState('error','Браузер запретил доступ к микрофону.');return;
      }
      if(enabled)setPersonaState('error','Ошибка распознавания: '+event.error);
    };
    r.onend=()=>{
      if(enabled){clearTimeout(restartTimer);restartTimer=setTimeout(startRecognition,450);}
    };
    return r;
  }

  function startRecognition(){
    if(!enabled||!Recognition) return;
    if(!recognition) recognition=makeRecognition();
    try{recognition.start();setPersonaState('listening','Ожидаю ключевую фразу.');}catch(_e){}
  }

  function stopRecognition(){
    clearTimeout(restartTimer);armed=false;
    if(recognition){try{recognition.stop();}catch(_e){}}
    setPersonaState('idle','Голосовой режим выключен.');
  }

  async function enableVoice(){
    if(!Recognition){setPersonaState('error','Этот браузер не предоставляет SpeechRecognition. Используйте браузер с Web Speech API.');return;}
    try{
      if(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia){
        const stream=await navigator.mediaDevices.getUserMedia({audio:true});
        stream.getTracks().forEach(t=>t.stop());
      }
      enabled=true;localStorage.setItem(storageKey,'1');updateButtons();startRecognition();
    }catch(error){
      enabled=false;localStorage.setItem(storageKey,'0');updateButtons();setPersonaState('error','Не удалось получить микрофон: '+error.message);
    }
  }

  function disableVoice(){enabled=false;localStorage.setItem(storageKey,'0');updateButtons();stopRecognition();}
  function toggleVoice(){if(enabled) disableVoice(); else enableVoice();}

  function speak(text){
    if(!speechReplyEnabled()||!('speechSynthesis' in window)) return;
    if(Date.now()-lastVoiceCommandAt>120000) return;
    const clean=String(text||'').replace(/<think>[\s\S]*?<\/think>/gi,'').trim();
    if(!clean) return;
    speechSynthesis.cancel();
    const utter=new SpeechSynthesisUtterance(clean);utter.lang='ru-RU';utter.rate=1;utter.pitch=.95;
    utter.onstart=()=>setPersonaState('speaking','Озвучиваю ответ.');
    utter.onend=()=>setPersonaState(enabled?'listening':'idle',enabled?'Ожидаю ключевую фразу.':'Готов.');
    utter.onerror=()=>setPersonaState(enabled?'listening':'idle','Не удалось озвучить ответ.');
    speechSynthesis.speak(utter);
  }

  function bind(){
    const toggle=$('voiceToggle');if(toggle)toggle.onclick=toggleVoice;
    const reply=$('voiceReplyToggle');if(reply)reply.onclick=()=>{localStorage.setItem(speechReplyKey,speechReplyEnabled()?'0':'1');updateButtons();};
    window.addEventListener('leshy:state',event=>{
      const detail=event.detail||{};setPersonaState(detail.state||'idle',detail.detail||'');
    });
    window.addEventListener('leshy:assistant',event=>{
      const detail=event.detail||{};
      const perf=$('generationPerf');
      if(perf&&detail.elapsedMs){
        const seconds=detail.elapsedMs/1000;const cps=seconds>0?Math.round(String(detail.text||'').length/seconds):0;
        perf.textContent='Последний ответ: '+seconds.toFixed(1)+' с · ~'+cps+' зн/с';
      }
      speak(detail.text||'');
    });
  }

  function boot(){
    installPersona();installHeaderControl();bind();
    enabled=localStorage.getItem(storageKey)==='1';updateButtons();
    if(enabled) enableVoice();
  }

  window.LeshyPersona={setState:setPersonaState};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
