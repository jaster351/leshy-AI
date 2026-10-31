(function(){
  'use strict';

  const $=id=>document.getElementById(id);
  const state={
    enabled:false,
    recording:false,
    stream:null,
    recorder:null,
    chunks:[],
    mime:'',
    baseText:'',
    latestText:'',
    inFlight:false,
    dirty:false,
    startedAt:0,
    speechSeen:false,
    lastVoiceAt:0,
    analyser:null,
    audioContext:null,
    raf:0,
    snapshotTimer:0,
    restartTimer:0,
    generation:0,
  };

  function notice(text,ms=2200){
    let n=$('chatVoiceState');
    if(!n){
      const composer=document.querySelector('#pane-chat .composer');
      if(!composer)return;
      n=document.createElement('div');n.id='chatVoiceState';composer.appendChild(n);
    }
    n.textContent=text;n.classList.add('show');clearTimeout(n._t);n._t=setTimeout(()=>n.classList.remove('show'),ms);
  }

  function chooseMime(){
    const candidates=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/webm','audio/ogg'];
    for(const value of candidates){
      try{if(window.MediaRecorder&&MediaRecorder.isTypeSupported(value))return value}catch(_e){}
    }
    return '';
  }

  function setButton(){
    const b=$('chatMicButton');if(!b)return;
    b.classList.toggle('voice-live-active',state.enabled);
    b.style.background=state.recording?'#474747':(state.enabled?'#363636':'');
    b.title=state.enabled?(state.recording?'Голосовой режим: слушаю':'Голосовой режим: ожидаю следующую реплику'):'Включить голосовой режим';
  }

  async function transcribeSnapshot(final=false,generation=state.generation){
    if(generation!==state.generation||!state.chunks.length)return '';
    if(state.inFlight&&!final){state.dirty=true;return state.latestText}
    const blob=new Blob(state.chunks,{type:state.mime||'audio/webm'});
    if(blob.size<2500)return state.latestText;
    state.inFlight=true;state.dirty=false;
    try{
      const lang=localStorage.getItem('leshy.voice.language')||'';
      const response=await fetch('/v1/voice/transcribe?language='+encodeURIComponent(lang),{
        method:'POST',
        headers:{'Content-Type':blob.type||'audio/webm'},
        body:blob,
      });
      const data=await response.json().catch(()=>({error:'Некорректный ответ ASR'}));
      if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
      if(generation!==state.generation)return '';
      const text=String(data.text||'').trim();
      if(text){
        state.latestText=text;
        const input=$('chatInput');
        if(input){input.value=(state.baseText+(state.baseText?' ':'')+text).trimStart();input.dispatchEvent(new Event('input',{bubbles:true}))}
      }
      if(final)notice(text?'Речь распознана локально':'Речь не распознана',1000);
      else notice('Слушаю · '+(text||'распознаю…'),1300);
      return text;
    }catch(error){
      if(generation===state.generation)notice('Локальный ASR: '+error.message,3500);
      return state.latestText;
    }finally{
      state.inFlight=false;
      if(state.dirty&&!final&&generation===state.generation){state.dirty=false;setTimeout(()=>transcribeSnapshot(false,generation),80)}
    }
  }

  function closeAudioGraph(){
    if(state.raf)cancelAnimationFrame(state.raf);state.raf=0;
    if(state.snapshotTimer)clearInterval(state.snapshotTimer);state.snapshotTimer=0;
    try{state.audioContext&&state.audioContext.close()}catch(_e){}
    state.audioContext=null;state.analyser=null;
  }

  function stopTracks(){
    try{for(const track of state.stream?.getTracks?.()||[])track.stop()}catch(_e){}
    state.stream=null;
  }

  async function finishTurn({send=true,keepSession=true}={}){
    if(!state.recording)return;
    state.recording=false;setButton();closeAudioGraph();
    const generation=state.generation;
    const recorder=state.recorder;
    await new Promise(resolve=>{
      if(!recorder||recorder.state==='inactive'){resolve();return}
      const old=recorder.onstop;
      recorder.onstop=e=>{try{old&&old.call(recorder,e)}catch(_e){}resolve()};
      try{recorder.requestData()}catch(_e){}
      try{recorder.stop()}catch(_e){resolve()}
      setTimeout(resolve,700);
    });
    stopTracks();
    await new Promise(r=>setTimeout(r,80));
    const text=await transcribeSnapshot(true,generation);
    state.recorder=null;
    if(send&&text&&generation===state.generation){
      const input=$('chatInput');
      if(input){input.value=(state.baseText+(state.baseText?' ':'')+text).trim();}
      notice('Отправляю…',700);
      try{if(typeof window.sendFrom==='function')await window.sendFrom(input);else $('chatSend')?.click()}catch(_e){}
    }
    state.chunks=[];state.latestText='';state.baseText='';
    if(keepSession&&state.enabled)scheduleRestartAfterAnswer();
  }

  function vadLoop(generation){
    if(!state.recording||generation!==state.generation||!state.analyser)return;
    const data=new Float32Array(state.analyser.fftSize);state.analyser.getFloatTimeDomainData(data);
    let sum=0;for(let i=0;i<data.length;i++)sum+=data[i]*data[i];const rms=Math.sqrt(sum/data.length);const now=performance.now();
    const threshold=0.022;
    if(rms>threshold){state.speechSeen=true;state.lastVoiceAt=now}
    if(state.speechSeen&&now-state.lastVoiceAt>1150&&now-state.startedAt>900){finishTurn({send:true,keepSession:true});return}
    state.raf=requestAnimationFrame(()=>vadLoop(generation));
  }

  async function startRecording(){
    if(!state.enabled||state.recording)return;
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){notice('Браузер не умеет передавать аудио с микрофона',3200);state.enabled=false;setButton();return}
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true,channelCount:1}});
      if(!state.enabled){stream.getTracks().forEach(t=>t.stop());return}
      state.generation+=1;const generation=state.generation;state.stream=stream;state.chunks=[];state.latestText='';state.baseText=$('chatInput')?.value.trim()||'';state.speechSeen=false;state.lastVoiceAt=performance.now();state.startedAt=performance.now();
      state.mime=chooseMime();state.recorder=state.mime?new MediaRecorder(stream,{mimeType:state.mime,audioBitsPerSecond:64000}):new MediaRecorder(stream);state.mime=state.recorder.mimeType||state.mime||'audio/webm';
      state.recorder.ondataavailable=e=>{if(generation===state.generation&&e.data&&e.data.size>0)state.chunks.push(e.data)};
      state.recorder.onerror=e=>notice('Запись микрофона: '+(e.error?.message||'ошибка'),3000);
      state.recorder.start(700);state.recording=true;setButton();notice('Слушаю локально…',1500);
      const AC=window.AudioContext||window.webkitAudioContext;
      if(AC){state.audioContext=new AC();const source=state.audioContext.createMediaStreamSource(stream);state.analyser=state.audioContext.createAnalyser();state.analyser.fftSize=1024;state.analyser.smoothingTimeConstant=.25;source.connect(state.analyser);state.raf=requestAnimationFrame(()=>vadLoop(generation))}
      state.snapshotTimer=setInterval(()=>{if(state.recording&&state.chunks.length)transcribeSnapshot(false,generation)},1800);
    }catch(error){
      state.enabled=false;setButton();
      const msg=error&&error.name==='NotAllowedError'?'Нет разрешения на микрофон':(error.message||String(error));notice('Микрофон: '+msg,3500);
    }
  }

  function scheduleRestartAfterAnswer(){
    clearTimeout(state.restartTimer);
    const wait=()=>{
      if(!state.enabled)return;
      const send=$('chatSend');
      if(send?.classList.contains('streaming')){state.restartTimer=setTimeout(wait,250);return}
      state.restartTimer=setTimeout(()=>{if(state.enabled&&!state.recording)startRecording()},220);
    };
    wait();
  }

  async function disableVoice(){
    state.enabled=false;clearTimeout(state.restartTimer);setButton();
    if(state.recording)await finishTurn({send:false,keepSession:false});
    closeAudioGraph();stopTracks();notice('Голосовой режим выключен',1000);
  }

  async function toggleVoice(){
    if(state.enabled){await disableVoice();return}
    const send=$('chatSend');
    if(send?.classList.contains('streaming')){
      // Barge-in: прерываем текущую генерацию и сразу начинаем слушать новую реплику.
      try{send.click()}catch(_e){}
      await new Promise(r=>setTimeout(r,120));
    }
    state.enabled=true;setButton();await startRecording();
  }

  function install(){
    const b=$('chatMicButton');if(!b||b.dataset.localVoiceBound==='1')return false;
    b.dataset.localVoiceBound='1';b.onclick=e=>{e.preventDefault();e.stopPropagation();toggleVoice()};setButton();
    const style=document.createElement('style');style.id='leshyLocalVoiceStyle';style.textContent='#chatMicButton.voice-live-active{box-shadow:0 0 0 1px #737373 inset,0 0 0 4px rgba(255,255,255,.035)}';document.head.appendChild(style);
    return true;
  }

  function boot(){
    if(!install()){
      const observer=new MutationObserver(()=>{if(install())observer.disconnect()});observer.observe(document.documentElement,{childList:true,subtree:true});
    }
    window.addEventListener('beforeunload',()=>{state.enabled=false;closeAudioGraph();stopTracks()});
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
