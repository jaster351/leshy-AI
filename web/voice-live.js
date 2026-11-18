(function(){
  'use strict';

  const $=id=>document.getElementById(id);
  const MAX_RECORDING_MS=4*60*1000;
  const MAX_BUFFER_BYTES=8*1024*1024;
  const SNAPSHOT_INTERVAL_MS=3500;
  const MIN_ASR_BYTES=8000;
  const STOP_ICON='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1"/></svg>';
  const state={
    enabled:false,mode:'conversation',phase:'off',error:'',recording:false,speaking:false,level:0,
    epoch:0,capture:null,starting:false,finishing:false,restartTimer:0,restartDeadline:0,
    controllers:new Set(),pendingChat:null,playback:null,sessionId:'',lastLevelAt:0,
    outputContext:null,outputUnlock:null,outputDecode:null,
  };

  function currentSession(){return localStorage.getItem('leshy.session')||''}
  function current(epoch){return state.enabled&&epoch===state.epoch}
  function currentCapture(capture){return !!capture&&state.capture===capture&&current(capture.epoch)}
  function cancelled(){return new DOMException('Голосовой разговор завершён','AbortError')}
  function requireSession(epoch){
    if(!current(epoch))throw cancelled();
    if(currentSession()!==state.sessionId)throw new Error('Чат изменился. Разговор остановлен, реплика не отправлена.');
  }

  function notice(text,ms=2400){
    let node=$('chatVoiceState');
    if(!node){
      const composer=document.querySelector('#pane-chat .composer');if(!composer)return;
      node=document.createElement('div');node.id='chatVoiceState';composer.appendChild(node);
    }
    node.textContent=text;node.classList.add('show');clearTimeout(node._t);
    node._t=setTimeout(()=>node.classList.remove('show'),ms);
  }

  function publishState(reason='state'){
    const detail={
      enabled:state.enabled,recording:state.recording,speaking:state.speaking,
      phase:state.phase,level:state.level,error:state.error,mode:state.mode,stopped:!state.enabled,reason,
    };
    window.leshyVoiceState=detail;
    window.dispatchEvent(new CustomEvent('leshy:voice-state',{detail}));
  }

  function updateButtons(reason='state'){
    const button=$('chatMicButton');
    if(button){
      if(!button.dataset.voiceIdleHtml)button.dataset.voiceIdleHtml=button.innerHTML;
      button.classList.toggle('voice-live-active',state.enabled);
      button.classList.toggle('voice-live-speaking',state.speaking);
      button.innerHTML=state.enabled?STOP_ICON:button.dataset.voiceIdleHtml;
      button.dataset.voiceState=state.phase;
      button.setAttribute('aria-pressed',String(state.enabled));
      button.setAttribute('aria-label',state.enabled?'Завершить голосовой разговор':'Позвонить Лешему');
      button.title=state.enabled?'Завершить голосовой разговор':'Позвонить Лешему';
    }
    const dictate=$('chatDictateButton'),dictating=state.enabled&&state.mode==='dictation';
    if(dictate){
      dictate.classList.toggle('voice-live-active',dictating);
      dictate.setAttribute('aria-pressed',String(dictating));
      dictate.textContent=dictating?'■ Завершить диктовку':'Диктовка';
    }
    publishState(reason);
  }

  function phase(value,reason=value,error=''){
    state.phase=value;state.error=error;state.level=0;updateButtons(reason);
  }

  function chooseMime(){
    const candidates=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4','audio/webm','audio/ogg'];
    for(const value of candidates){
      try{if(typeof MediaRecorder.isTypeSupported==='function'&&MediaRecorder.isTypeSupported(value))return value}catch(_e){}
    }
    return '';
  }

  function preflight(){
    if(!window.isSecureContext)throw new Error('Для микрофона откройте Лешего по доверенному HTTPS. HTTP по адресу локальной сети не подходит; localhost разрешён только на этом устройстве.');
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)throw new Error('В этом браузере недоступна запись микрофона. Откройте Лешего в браузере с MediaRecorder.');
    if(state.mode==='conversation'&&!(window.AudioContext||window.webkitAudioContext))throw new Error('Браузер не поддерживает определение пауз речи. Доступна отдельная диктовка.');
    if(document.hidden)throw new Error('Для голосового разговора оставьте вкладку Лешего открытой на экране.');
    if($('chatInput')?.disabled)throw new Error('Этот чат недоступен для отправки сообщений. Сначала разблокируйте его.');
  }

  async function voiceRequest(path,options,epoch,timeoutMs,decode){
    requireSession(epoch);
    const controller=new AbortController();state.controllers.add(controller);
    let timedOut=false;
    const timer=setTimeout(()=>{timedOut=true;controller.abort()},timeoutMs);
    try{
      const response=await fetch(path,Object.assign({},options,{
        credentials:'same-origin',signal:controller.signal,
        headers:Object.assign({},options.headers||{}, {'X-Leshy-UI':'voice'}),
      }));
      if(!response.ok){
        const data=await response.json().catch(()=>({}));
        throw new Error(data.error||('Голосовой сервер: HTTP '+response.status));
      }
      const data=await decode(response);
      requireSession(epoch);
      return data;
    }catch(error){
      if(timedOut&&current(epoch))throw new Error('Голосовой сервер не ответил вовремя. Разговор остановлен; запрос не повторяется.');
      throw error;
    }finally{
      clearTimeout(timer);state.controllers.delete(controller);
    }
  }

  function closeGraph(capture){
    if(!capture)return;
    if(capture.raf)cancelAnimationFrame(capture.raf);
    clearInterval(capture.snapshotTimer);clearTimeout(capture.maxTimer);
    capture.raf=0;capture.snapshotTimer=0;capture.maxTimer=0;
    if(capture.inputResume)capture.inputResume(cancelled());
    try{capture.inputSource?.disconnect();capture.analyser?.disconnect()}catch(_e){}
    if(capture.ownsContext&&capture.audioContext){try{const result=capture.audioContext.close();result?.catch(()=>{})}catch(_e){}}
    capture.audioContext=null;capture.inputSource=null;capture.analyser=null;
  }

  function resumeInput(capture){
    const context=capture.audioContext;
    if(context.state==='running')return Promise.resolve();
    return new Promise((resolve,reject)=>{
      let done=false,timer=0;
      const finish=error=>{
        if(done)return;done=true;clearTimeout(timer);
        if(capture.inputResume===finish)capture.inputResume=null;
        if(error)reject(error);else resolve();
      };
      capture.inputResume=finish;
      timer=setTimeout(()=>finish(new Error('Браузер не разрешил обработку микрофона. Нажмите «Позвонить» ещё раз.')),5000);
      try{
        Promise.resolve(context.resume()).then(()=>{
          if(!currentCapture(capture))finish(cancelled());
          else if(context.state!=='running')finish(new Error('Обработка микрофона приостановлена браузером.'));
          else finish();
        },error=>finish(error));
      }catch(error){finish(error)}
    });
  }

  function releaseTracks(capture){
    if(!capture)return;
    for(const track of capture.stream?.getTracks?.()||[]){
      track.onended=null;try{track.stop()}catch(_e){}
    }
    capture.stream=null;
  }

  function discardCapture(){
    const capture=state.capture;state.capture=null;state.recording=false;state.starting=false;state.finishing=false;
    if(!capture)return;
    closeGraph(capture);
    if(capture.stopSettle)capture.stopSettle(cancelled());
    try{if(capture.recorder&&capture.recorder.state!=='inactive')capture.recorder.stop()}catch(_e){}
    releaseTracks(capture);capture.chunks=[];capture.latestText='';
  }

  function stopPlayback(){
    const playback=state.playback;state.playback=null;state.speaking=false;
    if(!playback)return;
    playback.source.onended=null;
    try{playback.source.stop();playback.source.disconnect()}catch(_e){}
    // Остановленный источник может не прислать ended: завершаем ожидание явно.
    playback.finish();
  }

  function closeOutput(){
    if(state.outputUnlock)state.outputUnlock(cancelled());
    if(state.outputDecode)state.outputDecode(cancelled());
    const context=state.outputContext;state.outputContext=null;
    if(context){try{context.close().catch(()=>{})}catch(_e){}}
  }

  function unlockOutput(epoch){
    const AC=window.AudioContext||window.webkitAudioContext;
    // Вызывается из start до разрешения микрофона и сетевых запросов:
    // мобильный браузер должен получить пользовательский жест для звука сейчас.
    const context=state.outputContext||new AC();state.outputContext=context;
    return new Promise((resolve,reject)=>{
      let done=false,timer=0;
      const finish=error=>{
        if(done)return;done=true;clearTimeout(timer);
        if(state.outputUnlock===finish)state.outputUnlock=null;
        if(error)reject(error);else resolve();
      };
      state.outputUnlock=finish;
      timer=setTimeout(()=>finish(new Error('Браузер не разрешил звук. Разрешите воспроизведение для сайта и снова нажмите «Позвонить».')),5000);
      try{
        const resumed=context.resume();
        Promise.resolve(resumed).then(()=>{
          if(!current(epoch))finish(cancelled());
          else if(context.state!=='running')finish(new Error('Звук приостановлен браузером. Нажмите «Позвонить» ещё раз.'));
          else finish();
        },error=>finish(error));
      }catch(error){finish(error)}
    });
  }

  function decodeSpeech(context,buffer){
    return new Promise((resolve,reject)=>{
      let done=false,timer=0;
      const finish=(error,decoded)=>{
        if(done)return;done=true;clearTimeout(timer);
        if(state.outputDecode===finish)state.outputDecode=null;
        if(error)reject(error);else resolve(decoded);
      };
      state.outputDecode=finish;
      timer=setTimeout(()=>finish(new Error('Браузер не успел подготовить голосовой ответ.')),15000);
      try{
        const decoding=context.decodeAudioData(buffer,decoded=>finish(null,decoded),()=>finish(new Error('Браузер не смог декодировать голосовой ответ.')));
        if(decoding&&typeof decoding.catch==='function')decoding.catch(error=>finish(error));
      }
      catch(error){finish(error)}
    });
  }

  function stopOwnRequest(pending){
    if(!pending?.requestId)return;
    // Останавливаем только запрос этого звонка, а не текущий чат по кнопке Send.
    fetch('/v1/chat/stop',{
      method:'POST',credentials:'same-origin',keepalive:true,
      headers:{'Content-Type':'application/json','X-Leshy-UI':'voice'},
      body:JSON.stringify({request_id:pending.requestId}),
    }).catch(()=>{});
  }

  function invalidate(keepOutput=false){
    state.epoch+=1;clearTimeout(state.restartTimer);state.restartTimer=0;
    const pending=state.pendingChat;
    if(pending){stopOwnRequest(pending);pending.settle(cancelled())}
    for(const controller of state.controllers)controller.abort();
    state.controllers.clear();stopPlayback();
    if(state.outputDecode)state.outputDecode(cancelled());
    if(!keepOutput)closeOutput();
    discardCapture();state.level=0;
  }

  function stop(reason='stopped'){
    state.enabled=false;invalidate();phase('off',reason);
    return Promise.resolve();
  }

  function fail(error,reason='error'){
    const message=error?.message||String(error);
    state.enabled=false;invalidate();phase('error',reason,message);notice(message,6500);
  }

  async function transcribe(capture,final=false){
    if(!currentCapture(capture))return '';
    requireSession(capture.epoch);
    if(capture.asrTask){
      if(!final)return capture.latestText;
      await capture.asrTask;
      if(!currentCapture(capture))return '';
    }
    const blob=new Blob(capture.chunks,{type:capture.mime||'audio/webm'});
    if(blob.size<MIN_ASR_BYTES)return capture.latestText;
    if(blob.size>MAX_BUFFER_BYTES)throw new Error('Аудиобуфер превысил безопасный лимит 8 МБ.');
    const task=(async()=>{
      try{
        const language=localStorage.getItem('leshy.voice.language')||'';
        const data=await voiceRequest('/v1/voice/transcribe?language='+encodeURIComponent(language),{
          method:'POST',headers:{'Content-Type':blob.type||'audio/webm'},body:blob,
        },capture.epoch,90000,response=>response.json());
        if(!currentCapture(capture))return '';
        requireSession(capture.epoch);
        const text=String(data.text||'').trim();
        if(final&&!text&&capture.latestText)throw new Error('Завершённая запись не подтвердила промежуточный текст. Проверьте его в поле сообщения перед отправкой.');
        if(text){
          capture.latestText=text;
          const input=$('chatInput');
          if(input){
            input.value=(capture.baseText+(capture.baseText?' ':'')+text).trimStart();
            input.dispatchEvent(new Event('input',{bubbles:true}));
          }
        }
        return text;
      }catch(error){
        if(!currentCapture(capture)||error?.name==='AbortError')return '';
        const temporary=/Invalid data found|End of file|invalid webm|truncated/i.test(String(error.message||error));
        if(!final&&temporary){notice('Слушаю · ожидаю полный аудиофрагмент',1400);return capture.latestText}
        throw error;
      }
    })();
    capture.asrTask=task;
    try{return await task}finally{if(capture.asrTask===task)capture.asrTask=null}
  }

  async function stopRecording(capture){
    capture.recording=false;state.recording=false;closeGraph(capture);
    await new Promise((resolve,reject)=>{
      const recorder=capture.recorder;
      if(!recorder||recorder.state==='inactive'){resolve();return}
      let timer=0,done=false;
      const finish=error=>{
        if(done)return;done=true;clearTimeout(timer);recorder.onstop=null;
        if(capture.stopSettle===finish)capture.stopSettle=null;
        if(error)reject(error);else resolve();
      };
      capture.stopSettle=finish;
      recorder.onstop=()=>finish();
      timer=setTimeout(()=>finish(new Error('Микрофон не завершил запись вовремя. Реплика не отправлена: проверьте текст и повторите вручную.')),1500);
      try{recorder.requestData()}catch(_e){}
      try{recorder.stop()}catch(error){finish(error)}
    });
    releaseTracks(capture);capture.recorder=null;
  }

  function sendVoiceTurn(text,epoch){
    try{requireSession(epoch)}catch(error){return Promise.reject(error)}
    if(typeof window.sendFrom!=='function')return Promise.reject(new Error('Модуль чата ещё не готов. Повторно включите разговор после загрузки интерфейса.'));
    if($('chatSend')?.classList.contains('streaming'))return Promise.reject(new Error('В этом чате уже идёт другой ответ. Дождитесь его завершения перед звонком.'));
    const input=$('chatInput');
    if(!input||input.disabled)return Promise.reject(new Error('Нельзя отправить голосовую реплику в этот чат.'));
    return new Promise((resolve,reject)=>{
      const pending={requestId:'',initialSession:currentSession(),accepting:false,settled:false,timer:0,settle:null};
      const cleanup=()=>{
        clearTimeout(pending.timer);window.removeEventListener('leshy:activity',onActivity);
        window.removeEventListener('leshy:done',onDone);
        if(state.pendingChat===pending)state.pendingChat=null;
      };
      pending.settle=(error,result)=>{
        if(pending.settled)return;pending.settled=true;cleanup();
        if(error)reject(error);else resolve(result);
      };
      const onActivity=event=>{
        const detail=event.detail||{};
        // Первый activity публикуется синхронно внутри именно нашего sendFrom,
        // до await fetch и до назначения session_id новому чату.
        if(pending.accepting&&!pending.requestId&&typeof detail.request_id==='string'){
          pending.requestId=detail.request_id;
        }
      };
      const onDone=event=>{
        const detail=event.detail||{};
        if(!pending.requestId||detail.request_id!==pending.requestId)return;
        if(!current(epoch)){pending.settle(cancelled());return}
        if(detail.session_id&&detail.session_id!==currentSession()){
          pending.settle(new Error('Текущий чат изменился. Голосовой ответ не воспроизводится.'));return;
        }
        if(detail.error){pending.settle(new Error(detail.error));return}
        if(detail.duplicate){pending.settle(new Error('Сервер сообщил о повторном запросе. Реплика не отправляется повторно.'));return}
        if(detail.session_id)state.sessionId=detail.session_id;
        pending.settle(null,{answer:String(detail.answer||''),stopped:!!detail.stopped});
      };
      state.pendingChat=pending;
      window.addEventListener('leshy:activity',onActivity);window.addEventListener('leshy:done',onDone);
      pending.timer=setTimeout(()=>{
        stopOwnRequest(pending);
        pending.settle(new Error('Ответ не завершился за допустимое время. Автоматический повтор отключён.'));
      },180000);
      input.value=text;
      try{
        pending.accepting=true;
        const returned=window.sendFrom(input);
        pending.accepting=false;
        // Поддержка контроллера, который уже возвращает {answer}; текущий
        // multitask-контроллер возвращает undefined и завершает ход событием done.
        Promise.resolve(returned).then(result=>{
          if(pending.settled)return;
          if(!current(epoch)){pending.settle(cancelled());return}
          if(result&&typeof result.answer==='string'){
            if(result.session_id&&result.session_id!==currentSession())pending.settle(new Error('Текущий чат изменился. Голосовой ответ не воспроизводится.'));
            else if(result.error)pending.settle(new Error(result.error));
            else if(result.duplicate)pending.settle(new Error('Сервер сообщил о повторном запросе. Реплика не отправляется повторно.'));
            else pending.settle(null,result);
          }else if(!pending.requestId){
            pending.settle(new Error('Чат не подтвердил запуск голосовой реплики.'));
          }
        },error=>pending.settle(error));
      }catch(error){pending.accepting=false;pending.settle(error)}
    });
  }

  function speechText(answer){
    return String(answer||'')
      .replace(/\x60{3}[\s\S]*?\x60{3}/g,' Код приведён на экране. ')
      .replace(/https?:\/\/\S+/g,' ссылка ')
      .replace(/[\x60*_#>|]/g,' ').replace(/\s+/g,' ').trim();
  }

  async function speak(answer,epoch){
    const text=speechText(answer);if(!text||!current(epoch))return;
    phase('thinking','preparing-speech');
    const blob=await voiceRequest('/v1/voice/speak',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text}),
    },epoch,90000,response=>response.blob());
    if(!current(epoch))return;
    if(!blob.size)throw new Error('Сервер вернул пустую озвучку.');
    const context=state.outputContext;
    if(!context||context.state!=='running')throw new Error('Браузер приостановил звук. Разрешите воспроизведение для сайта и снова нажмите «Позвонить».');
    const encoded=await blob.arrayBuffer();if(!current(epoch))return;
    const decoded=await decodeSpeech(context,encoded);if(!current(epoch))return;
    if(context.state!=='running')throw new Error('Браузер приостановил звук. Снова нажмите «Позвонить».');
    const source=context.createBufferSource();source.buffer=decoded;source.connect(context.destination);
    try{
      await new Promise((resolve,reject)=>{
        let settled=false,timer=0;
        const finish=error=>{
          if(settled)return;settled=true;clearTimeout(timer);
          if(error)reject(error);else resolve();
        };
        state.playback={source,finish};source.onended=()=>finish();
        timer=setTimeout(()=>finish(new Error('Воспроизведение не завершилось вовремя.')),Math.min(180000,Math.max(15000,decoded.duration*1000+5000)));
        try{source.start();state.speaking=true;phase('speaking','playing')}
        catch(error){finish(new Error('Не удалось воспроизвести голос: '+(error?.message||String(error))))}
      });
    }finally{
      if(state.playback?.source===source)stopPlayback();
    }
  }

  function scheduleRestart(epoch){
    if(!current(epoch))return;
    state.restartDeadline=performance.now()+30000;
    const wait=()=>{
      if(!current(epoch))return;
      if($('chatSend')?.classList.contains('streaming')){
        if(performance.now()>state.restartDeadline){fail(new Error('Чат ещё завершает ответ. Разговор остановлен; реплика не повторяется.'));return}
        state.restartTimer=setTimeout(wait,200);return;
      }
      state.restartTimer=setTimeout(()=>startRecording(epoch),220);
    };
    phase('requesting','next-turn');wait();
  }

  async function finishTurn(capture,{send=true,keepSession=true}={}){
    if(!currentCapture(capture)||capture.finishing)return;
    capture.finishing=true;state.finishing=true;phase('transcribing','transcribing');
    try{
      await stopRecording(capture);
      if(!currentCapture(capture))return;
      const text=await transcribe(capture,true);
      if(!currentCapture(capture))return;
      if(send&&state.mode==='conversation'&&text){
        const message=(capture.baseText+(capture.baseText?' ':'')+text).trim();
        phase('thinking','awaiting-answer');
        const result=await sendVoiceTurn(message,capture.epoch);
        if(!currentCapture(capture))return;
        if(!result.stopped){
          if(!result.answer)throw new Error('Леший завершил ответ без текста для озвучки.');
          await speak(result.answer,capture.epoch);
        }
      }
      if(!currentCapture(capture))return;
      state.capture=null;state.finishing=false;capture.chunks=[];
      if(keepSession&&state.enabled)scheduleRestart(capture.epoch);
      else stop('dictation-finished');
    }catch(error){
      if(current(capture.epoch)&&error?.name!=='AbortError')fail(error);
    }finally{
      if(capture.epoch===state.epoch)state.finishing=false;
    }
  }

  function vadLoop(capture){
    if(!currentCapture(capture)||!capture.recording||!capture.analyser)return;
    const data=capture.samples;capture.analyser.getFloatTimeDomainData(data);
    let sum=0;for(let i=0;i<data.length;i++)sum+=data[i]*data[i];
    const rms=Math.sqrt(sum/data.length),now=performance.now();
    state.level=Math.max(0,Math.min(1,rms/.15));
    if(now-state.lastLevelAt>100){state.lastLevelAt=now;publishState('level')}
    if(rms>.022){
      if(!capture.speechSeen)capture.speechStartedAt=now;
      capture.speechSeen=true;capture.lastVoiceAt=now;
    }
    const spokenFor=Math.max(0,capture.lastVoiceAt-capture.speechStartedAt);
    const silenceMs=spokenFor<1600?6500:spokenFor<3500?5500:5000;
    if(state.mode==='conversation'&&capture.speechSeen&&now-capture.lastVoiceAt>silenceMs&&now-capture.startedAt>1800){
      void finishTurn(capture);return;
    }
    capture.raf=requestAnimationFrame(()=>vadLoop(capture));
  }

  async function startRecording(epoch){
    if(!current(epoch)||state.starting||state.recording||state.finishing)return;
    state.starting=true;phase('requesting','microphone');
    let stream=null,capture=null;
    try{
      preflight();
      requireSession(epoch);
      stream=await navigator.mediaDevices.getUserMedia({
        audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true,channelCount:1},
      });
      if(!current(epoch)){stream.getTracks().forEach(track=>track.stop());return}
      requireSession(epoch);
      capture={
        epoch,stream,recorder:null,recording:false,finishing:false,chunks:[],chunkBytes:0,latestText:'',
        baseText:$('chatInput')?.value.trim()||'',mime:chooseMime(),asrTask:null,
        audioContext:null,ownsContext:false,inputSource:null,inputResume:null,analyser:null,samples:null,raf:0,snapshotTimer:0,maxTimer:0,
        speechSeen:false,speechStartedAt:0,lastVoiceAt:performance.now(),startedAt:performance.now(),
      };
      state.capture=capture;
      const AC=window.AudioContext||window.webkitAudioContext;
      if(AC){
        // Разговор повторно использует контекст, разблокированный первым нажатием.
        capture.audioContext=state.mode==='conversation'?state.outputContext:new AC();
        capture.ownsContext=capture.audioContext!==state.outputContext;
        if(!capture.audioContext)throw new Error('Звуковой контекст разговора недоступен. Нажмите «Позвонить» ещё раз.');
        await resumeInput(capture);
        if(!currentCapture(capture)){closeGraph(capture);releaseTracks(capture);return}
        requireSession(epoch);
        const source=capture.audioContext.createMediaStreamSource(stream);capture.inputSource=source;
        capture.analyser=capture.audioContext.createAnalyser();capture.analyser.fftSize=1024;
        capture.analyser.smoothingTimeConstant=.25;capture.samples=new Float32Array(1024);
        source.connect(capture.analyser);
      }
      capture.recorder=capture.mime
        ?new MediaRecorder(stream,{mimeType:capture.mime,audioBitsPerSecond:64000})
        :new MediaRecorder(stream);
      capture.mime=capture.recorder.mimeType||capture.mime||'audio/webm';
      capture.recorder.ondataavailable=event=>{
        if(!currentCapture(capture)||!event.data?.size)return;
        capture.chunks.push(event.data);capture.chunkBytes+=event.data.size;
        if(capture.chunkBytes>MAX_BUFFER_BYTES)fail(new Error('Аудиобуфер превысил безопасный лимит 8 МБ.'));
      };
      capture.recorder.onerror=event=>{if(currentCapture(capture))fail(event.error||new Error('Ошибка записи микрофона.'))};
      for(const track of stream.getAudioTracks())track.onended=()=>{
        if(currentCapture(capture)&&capture.recording)fail(new Error('Микрофон отключён или разрешение отозвано.'));
      };
      capture.recorder.start(700);capture.recording=true;state.recording=true;
      phase('listening',state.mode==='dictation'?'dictating':'listening');
      if(capture.analyser)capture.raf=requestAnimationFrame(()=>vadLoop(capture));
      capture.snapshotTimer=setInterval(()=>{
        if(!currentCapture(capture)||!capture.recording||capture.asrTask||!capture.chunks.length)return;
        transcribe(capture).catch(error=>{if(currentCapture(capture))fail(error)});
      },SNAPSHOT_INTERVAL_MS);
      capture.maxTimer=setTimeout(()=>{
        if(currentCapture(capture))void finishTurn(capture,{
          send:state.mode==='conversation',keepSession:state.mode==='conversation',
        });
      },MAX_RECORDING_MS);
    }catch(error){
      if(current(epoch)){
        if(capture){closeGraph(capture);releaseTracks(capture)}
        else stream?.getTracks().forEach(track=>track.stop());
        if(error?.name==='NotAllowedError')fail(new Error('Нет разрешения на микрофон. Разрешите его в настройках браузера и нажмите «Позвонить».'));
        else if(error?.name!=='AbortError')fail(error);
      }
    }finally{if(epoch===state.epoch)state.starting=false}
  }

  async function start(mode='conversation'){
    if(state.enabled&&state.mode===mode)return;
    if(state.enabled)await stop('mode-changed');
    state.mode=mode;state.error='';state.sessionId=currentSession();
    let epoch=null;
    try{
      preflight();
      if($('chatSend')?.classList.contains('streaming'))throw new Error('Сначала дождитесь завершения текущего ответа в чате.');
      state.epoch+=1;epoch=state.epoch;state.enabled=true;phase('requesting','enabled');
      if(mode==='conversation')await unlockOutput(epoch);
      if(current(epoch))await startRecording(epoch);
    }catch(error){if(epoch===null||current(epoch))fail(error)}
  }

  function interrupt(){
    if(!state.enabled||!['thinking','speaking','transcribing'].includes(state.phase))return Promise.resolve();
    invalidate(true);state.enabled=true;state.sessionId=currentSession();
    scheduleRestart(state.epoch);return Promise.resolve();
  }

  async function toggleDictation(){
    if(state.enabled&&state.mode==='dictation'){
      if(state.capture?.recording)await finishTurn(state.capture,{send:false,keepSession:false});
      else await stop('dictation-stopped');
      return;
    }
    await start('dictation');
    if(state.enabled)notice('Диктовка: завершите её кнопкой, затем проверьте текст перед отправкой.',4000);
  }

  function install(){
    const button=$('chatMicButton');if(!button||button.dataset.localVoiceBound==='1')return false;
    button.dataset.localVoiceBound='1';button.dataset.voiceIdleHtml=button.innerHTML;
    button.onclick=event=>{event.preventDefault();event.stopPropagation();if(state.enabled)void stop();else void start()};
    const composer=document.querySelector('#pane-chat .composer');
    if(composer&&!$('chatDictateButton')){
      const dictate=document.createElement('button');dictate.id='chatDictateButton';dictate.type='button';
      dictate.className='button secondary small';dictate.textContent='Диктовка';
      dictate.onclick=event=>{event.preventDefault();event.stopPropagation();void toggleDictation()};
      composer.insertBefore(dictate,$('chatSend')||null);
    }
    if(!$('leshyLocalVoiceStyle')){
      const style=document.createElement('style');style.id='leshyLocalVoiceStyle';
      style.textContent='#chatMicButton.voice-live-active{box-shadow:0 0 0 2px #93ddb1 inset}#chatMicButton.voice-live-speaking{animation:leshyVoicePulse 1s ease-in-out infinite}@keyframes leshyVoicePulse{50%{box-shadow:0 0 0 6px rgba(147,221,177,.16)}}@media(prefers-reduced-motion:reduce){#chatMicButton.voice-live-speaking{animation:none}}';
      document.head.appendChild(style);
    }
    updateButtons('installed');return true;
  }

  function boot(){
    if(!install()){
      const observer=new MutationObserver(()=>{if(install())observer.disconnect()});
      observer.observe(document.documentElement,{childList:true,subtree:true});
    }
    window.addEventListener('leshy:session-changed',()=>{
      if(!state.enabled)return;
      const next=currentSession();if(next===state.sessionId)return;
      const pending=state.pendingChat;
      if(next&&pending?.requestId&&!pending.initialSession&&!state.sessionId){
        // SSE назначает id первому чату. На done дополнительно сверяется id ответа.
        state.sessionId=next;return;
      }
      void stop('session-changed');notice('Разговор завершён при переходе в другой чат.');
    });
    window.addEventListener('storage',event=>{
      if(!state.enabled||(event.key!==null&&event.key!=='leshy.session'))return;
      if(event.key!==null&&currentSession()===state.sessionId)return;
      void stop('session-changed');notice('Разговор завершён: чат изменился в другой вкладке.');
    });
    document.addEventListener('click',event=>{
      if(!state.enabled||!(event.target instanceof Element))return;
      const open=event.target.closest('.leshy-session-open');
      const newChat=event.target.closest('#chatSidebarNew,#sidebarNewChat,#newChat');
      if(newChat||(open&&open.closest('.leshy-session-item')?.dataset.sessionId!==state.sessionId))void stop('chat-navigation');
    },true);
    document.addEventListener('visibilitychange',()=>{
      if(document.hidden&&state.enabled){void stop('background');notice('Разговор остановлен: вернитесь во вкладку и нажмите «Позвонить».',4000)}
    });
    window.addEventListener('beforeunload',()=>{state.enabled=false;invalidate()});
    publishState('ready');
  }

  window.leshyVoiceCall={
    start:()=>start('conversation'),
    stop:()=>stop(),
    interrupt:()=>interrupt(),
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
