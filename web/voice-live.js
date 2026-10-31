(function(){
  'use strict';

  const $=id=>document.getElementById(id);
  const MAX_RECORDING_MS=90*1000;
  const MAX_BUFFER_BYTES=8*1024*1024;
  const SNAPSHOT_INTERVAL_MS=2500;
  const state={
    enabled:false,recording:false,speaking:false,stream:null,recorder:null,chunks:[],chunkBytes:0,mime:'',baseText:'',latestText:'',
    inFlight:false,startedAt:0,speechSeen:false,lastVoiceAt:0,analyser:null,audioContext:null,raf:0,
    snapshotTimer:0,restartTimer:0,generation:0,playback:null,playbackUrl:'',ttsUnavailable:false,asrFailed:false,
  };

  function notice(text,ms=2200){
    let n=$('chatVoiceState');
    if(!n){const composer=document.querySelector('#pane-chat .composer');if(!composer)return;n=document.createElement('div');n.id='chatVoiceState';composer.appendChild(n)}
    n.textContent=text;n.classList.add('show');clearTimeout(n._t);n._t=setTimeout(()=>n.classList.remove('show'),ms);
  }

  function chooseMime(){
    const candidates=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/webm','audio/ogg'];
    for(const value of candidates){try{if(window.MediaRecorder&&MediaRecorder.isTypeSupported(value))return value}catch(_e){}}
    return '';
  }

  function setButton(){
    const b=$('chatMicButton');if(!b)return;
    b.classList.toggle('voice-live-active',state.enabled);
    b.classList.toggle('voice-live-speaking',state.speaking);
    b.style.background=state.recording?'#474747':(state.speaking?'#3f3f3f':(state.enabled?'#363636':''));
    b.title=!state.enabled?'Включить локальный голосовой режим':state.speaking?'Леший говорит · нажмите, чтобы перебить':state.recording?'Голосовой режим: слушаю':'Голосовой режим: ожидаю следующую реплику';
  }

  function clearSnapshotTimer(){if(state.snapshotTimer)clearInterval(state.snapshotTimer);state.snapshotTimer=0}
  function closeAudioGraph(){if(state.raf)cancelAnimationFrame(state.raf);state.raf=0;clearSnapshotTimer();try{state.audioContext&&state.audioContext.close()}catch(_e){}state.audioContext=null;state.analyser=null}
  function stopTracks(){try{for(const track of state.stream?.getTracks?.()||[])track.stop()}catch(_e){}state.stream=null}
  function clearRecordedAudio(){state.chunks=[];state.chunkBytes=0;state.latestText='';state.baseText=''}

  function abortVoiceAfterAsrError(error){
    if(state.asrFailed)return;
    state.asrFailed=true;state.enabled=false;state.recording=false;state.generation+=1;
    clearTimeout(state.restartTimer);closeAudioGraph();
    try{if(state.recorder&&state.recorder.state!=='inactive')state.recorder.stop()}catch(_e){}
    state.recorder=null;stopTracks();clearRecordedAudio();setButton();
    notice('Голосовой режим остановлен: '+(error?.message||String(error))+'. Повторные запросы не отправляются.',6500);
  }

  async function transcribeSnapshot(final=false,generation=state.generation){
    if(generation!==state.generation||state.asrFailed||!state.chunks.length)return '';
    // Никогда не создаём очередь ASR-запросов. Один запрос должен закончиться до следующего.
    if(state.inFlight)return state.latestText;
    const blob=new Blob(state.chunks,{type:state.mime||'audio/webm'});if(blob.size<2500)return state.latestText;
    if(blob.size>MAX_BUFFER_BYTES){abortVoiceAfterAsrError(new Error('аудиобуфер превысил безопасный лимит 8 МБ'));return ''}
    state.inFlight=true;
    try{
      const lang=localStorage.getItem('leshy.voice.language')||'';
      const response=await fetch('/v1/voice/transcribe?language='+encodeURIComponent(lang),{method:'POST',headers:{'Content-Type':blob.type||'audio/webm'},body:blob});
      const data=await response.json().catch(()=>({error:'Некорректный ответ ASR'}));if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
      if(generation!==state.generation||state.asrFailed)return '';
      const text=String(data.text||'').trim();
      if(text){state.latestText=text;const input=$('chatInput');if(input){input.value=(state.baseText+(state.baseText?' ':'')+text).trimStart();input.dispatchEvent(new Event('input',{bubbles:true}))}}
      if(final)notice(text?'Речь распознана локально':'Речь не распознана',1000);else notice('Слушаю · '+(text||'распознаю…'),1300);
      return text;
    }catch(error){
      // Критично: после ошибки ASR не повторяем запрос каждые несколько секунд.
      if(generation===state.generation)abortVoiceAfterAsrError(error);
      return '';
    }finally{state.inFlight=false}
  }

  function speechTextFromLastAnswer(){
    const messages=[...document.querySelectorAll('#messages .msg.assistant')];const last=messages[messages.length-1];if(!last)return '';
    const body=last.querySelector('.chat-rich-body')||last;let text=String(body.innerText||body.textContent||'');
    text=text.replace(/```[\s\S]*?```/g,' Код приведён на экране. ').replace(/\bКопировать\b/g,' ').replace(/https?:\/\/\S+/g,' ссылка ');
    text=text.replace(/[`*_#>|]/g,' ').replace(/\s+/g,' ').trim();return text.slice(0,12000);
  }

  function stopPlayback(){
    const audio=state.playback;state.playback=null;state.speaking=false;setButton();
    if(audio){try{audio.pause();audio.src=''}catch(_e){}}
    if(state.playbackUrl){try{URL.revokeObjectURL(state.playbackUrl)}catch(_e){}state.playbackUrl=''}
  }

  async function speakLatestAssistant(){
    if(!state.enabled||state.ttsUnavailable)return;
    const text=speechTextFromLastAnswer();if(!text)return;
    notice('Леший говорит…',1200);
    try{
      const response=await fetch('/v1/voice/speak',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
      if(!response.ok){const data=await response.json().catch(()=>({error:'Озвучка недоступна'}));throw new Error(data.error||('HTTP '+response.status))}
      const blob=await response.blob();if(!state.enabled)return;
      const url=URL.createObjectURL(blob),audio=new Audio(url);state.playbackUrl=url;state.playback=audio;state.speaking=true;setButton();
      await new Promise(resolve=>{audio.onended=resolve;audio.onerror=resolve;const p=audio.play();if(p&&typeof p.catch==='function')p.catch(resolve)});
      stopPlayback();
    }catch(error){state.ttsUnavailable=true;notice('Локальная озвучка недоступна: '+error.message,4000);stopPlayback()}
  }

  async function finishTurn({send=true,keepSession=true}={}){
    if(!state.recording)return;
    state.recording=false;setButton();closeAudioGraph();const generation=state.generation,recorder=state.recorder;
    await new Promise(resolve=>{if(!recorder||recorder.state==='inactive'){resolve();return}const old=recorder.onstop;recorder.onstop=e=>{try{old&&old.call(recorder,e)}catch(_e){}resolve()};try{recorder.requestData()}catch(_e){}try{recorder.stop()}catch(_e){resolve()}setTimeout(resolve,700)});
    stopTracks();await new Promise(r=>setTimeout(r,80));
    // Если промежуточный ASR ещё занят, не создаём второй параллельный запрос.
    let waits=0;while(state.inFlight&&waits<80&&generation===state.generation){await new Promise(r=>setTimeout(r,100));waits+=1}
    const text=state.latestText||await transcribeSnapshot(true,generation);state.recorder=null;
    if(send&&text&&generation===state.generation&&!state.asrFailed){
      const input=$('chatInput');if(input)input.value=(state.baseText+(state.baseText?' ':'')+text).trim();notice('Отправляю…',700);
      try{if(typeof window.sendFrom==='function')await window.sendFrom(input);else $('chatSend')?.click()}catch(_e){}
      if(state.enabled)await speakLatestAssistant();
    }
    clearRecordedAudio();if(keepSession&&state.enabled&&!state.asrFailed)scheduleRestartAfterAnswer();
  }

  function vadLoop(generation){
    if(!state.recording||generation!==state.generation||!state.analyser)return;
    const data=new Float32Array(state.analyser.fftSize);state.analyser.getFloatTimeDomainData(data);let sum=0;for(let i=0;i<data.length;i++)sum+=data[i]*data[i];const rms=Math.sqrt(sum/data.length),now=performance.now(),threshold=0.022;
    if(rms>threshold){state.speechSeen=true;state.lastVoiceAt=now}
    if(now-state.startedAt>MAX_RECORDING_MS){finishTurn({send:true,keepSession:true});return}
    if(state.speechSeen&&now-state.lastVoiceAt>1150&&now-state.startedAt>900){finishTurn({send:true,keepSession:true});return}
    state.raf=requestAnimationFrame(()=>vadLoop(generation));
  }

  async function startRecording(){
    if(!state.enabled||state.recording||state.speaking||state.asrFailed)return;
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){notice('Браузер не умеет передавать аудио с микрофона',3200);state.enabled=false;setButton();return}
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true,channelCount:1}});if(!state.enabled){stream.getTracks().forEach(t=>t.stop());return}
      state.generation+=1;const generation=state.generation;state.stream=stream;state.chunks=[];state.chunkBytes=0;state.latestText='';state.baseText=$('chatInput')?.value.trim()||'';state.speechSeen=false;state.lastVoiceAt=performance.now();state.startedAt=performance.now();state.mime=chooseMime();
      state.recorder=state.mime?new MediaRecorder(stream,{mimeType:state.mime,audioBitsPerSecond:64000}):new MediaRecorder(stream);state.mime=state.recorder.mimeType||state.mime||'audio/webm';
      state.recorder.ondataavailable=e=>{if(generation!==state.generation||!e.data||e.data.size<=0)return;state.chunks.push(e.data);state.chunkBytes+=e.data.size;if(state.chunkBytes>MAX_BUFFER_BYTES)abortVoiceAfterAsrError(new Error('аудиобуфер превысил безопасный лимит 8 МБ'))};
      state.recorder.onerror=e=>abortVoiceAfterAsrError(e.error||new Error('ошибка записи микрофона'));
      state.recorder.start(700);state.recording=true;setButton();notice('Слушаю локально…',1500);
      const AC=window.AudioContext||window.webkitAudioContext;if(AC){state.audioContext=new AC();const source=state.audioContext.createMediaStreamSource(stream);state.analyser=state.audioContext.createAnalyser();state.analyser.fftSize=1024;state.analyser.smoothingTimeConstant=.25;source.connect(state.analyser);state.raf=requestAnimationFrame(()=>vadLoop(generation))}
      state.snapshotTimer=setInterval(()=>{if(state.recording&&!state.inFlight&&state.chunks.length&&!state.asrFailed)transcribeSnapshot(false,generation)},SNAPSHOT_INTERVAL_MS);
    }catch(error){state.enabled=false;setButton();const msg=error&&error.name==='NotAllowedError'?'Нет разрешения на микрофон':(error.message||String(error));notice('Микрофон: '+msg,3500)}
  }

  function scheduleRestartAfterAnswer(){
    clearTimeout(state.restartTimer);const wait=()=>{if(!state.enabled||state.asrFailed)return;if(state.speaking){state.restartTimer=setTimeout(wait,200);return}const send=$('chatSend');if(send?.classList.contains('streaming')){state.restartTimer=setTimeout(wait,250);return}state.restartTimer=setTimeout(()=>{if(state.enabled&&!state.recording&&!state.speaking&&!state.asrFailed)startRecording()},220)};wait();
  }

  async function disableVoice(){state.enabled=false;clearTimeout(state.restartTimer);stopPlayback();setButton();if(state.recording)await finishTurn({send:false,keepSession:false});closeAudioGraph();stopTracks();clearRecordedAudio();notice('Голосовой режим выключен',1000)}

  async function toggleVoice(){
    if(state.speaking){stopPlayback();if(!state.enabled)state.enabled=true;state.asrFailed=false;setButton();await startRecording();return}
    if(state.enabled){await disableVoice();return}
    const send=$('chatSend');if(send?.classList.contains('streaming')){try{send.click()}catch(_e){}await new Promise(r=>setTimeout(r,120))}
    state.enabled=true;state.asrFailed=false;state.ttsUnavailable=false;setButton();await startRecording();
  }

  function install(){
    const b=$('chatMicButton');if(!b||b.dataset.localVoiceBound==='1')return false;b.dataset.localVoiceBound='1';b.onclick=e=>{e.preventDefault();e.stopPropagation();toggleVoice()};setButton();
    if(!$('leshyLocalVoiceStyle')){const style=document.createElement('style');style.id='leshyLocalVoiceStyle';style.textContent='#chatMicButton.voice-live-active{box-shadow:0 0 0 1px #737373 inset,0 0 0 4px rgba(255,255,255,.035)}#chatMicButton.voice-live-speaking{animation:leshyVoicePulse 1s ease-in-out infinite}@keyframes leshyVoicePulse{50%{box-shadow:0 0 0 6px rgba(255,255,255,.08)}}';document.head.appendChild(style)}
    return true;
  }

  function boot(){if(!install()){const observer=new MutationObserver(()=>{if(install())observer.disconnect()});observer.observe(document.documentElement,{childList:true,subtree:true})}window.addEventListener('beforeunload',()=>{state.enabled=false;state.generation+=1;stopPlayback();closeAudioGraph();stopTracks();clearRecordedAudio()})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
