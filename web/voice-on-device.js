(function(){
  'use strict';
  // Этот адаптер не записывает и не отправляет аудио на сервер.
  // Вызов prepare выполняется только после явного нажатия микрофона.
  const languageTag=value=>{
    const raw=String(value||'ru-RU').trim().replace(/_/g,'-');
    try{return Intl.getCanonicalLocales(raw)[0]||'ru-RU'}catch(_e){return 'ru-RU'}
  };
  const error=(code,message)=>Object.assign(new Error(message),{code});
  const cancelled=()=>error('cancelled','Голосовой режим отменён');
  const secure=()=>window.isSecureContext===true;
  const constructor=()=>{
    const R=window.SpeechRecognition||window.webkitSpeechRecognition;
    return R&&typeof R.available==='function'&&typeof R.install==='function'&&'processLocally' in R.prototype?R:null;
  };
  // API браузера не предоставляет количество байтов или скорость скачивания.
  // Отмена ожидания не гарантирует остановку системной загрузки пакета.
  function wait(promise,signal){
    if(!signal)return Promise.resolve(promise);
    return new Promise((resolve,reject)=>{
      const abort=()=>{signal.removeEventListener('abort',abort);reject(cancelled())};
      if(signal.aborted){Promise.resolve(promise).catch(()=>{});reject(cancelled());return}
      signal.addEventListener('abort',abort,{once:true});
      Promise.resolve(promise).then(
        value=>{signal.removeEventListener('abort',abort);resolve(value)},
        reason=>{signal.removeEventListener('abort',abort);reject(reason)}
      );
    });
  }
  async function prepare({language,signal,onStatus=()=>{}}={}){
    const check=()=>{if(signal?.aborted)throw cancelled()};
    check();
    if(!secure())return {status:'insecure',message:'Для удалённого микрофона откройте Лешего по доверенному HTTPS. Обычный HTTP разрешён браузером только для localhost.'};
    const R=constructor(),lang=languageTag(language);
    if(!R)return {status:'unsupported',message:'Локальная модель браузера недоступна. Можно отдельно выбрать распознавание на сервере Лешего.'};
    const opts={langs:[lang],processLocally:true};
    onStatus({status:'checking',language:lang});
    try{
      let available=await wait(R.available(opts),signal);check();
      if(available==='downloadable'||available==='downloading'){
        onStatus({status:'downloading',language:lang});
        const installed=await wait(R.install(opts),signal);check();
        if(installed!==true)return {status:'not-installed',language:lang,message:'Модель не установлена или загрузка отменена. Повторите вручную или выберите серверное распознавание.'};
        available=await wait(R.available(opts),signal);check();
      }
      if(available!=='available')return {status:'unavailable',language:lang,message:'Локальная модель для выбранного языка недоступна.'};
      onStatus({status:'ready',language:lang});
      return {status:'ready',language:lang};
    }catch(e){
      if(signal?.aborted||e?.code==='cancelled')throw cancelled();
      return {status:'prepare-error',language:lang,message:'Не удалось подготовить локальную модель: '+(e?.message||String(e))};
    }
  }
  // Вызывающая сторона владеет сессией чата и отправкой текста.
  // В режиме разговора отправлять только успешный финальный результат.
  function start({language,signal,onText=()=>{},onStatus=()=>{},continuous=true,track}={}){
    if(signal?.aborted)throw cancelled();
    if(!secure())throw error('insecure','Микрофон требует доверенный HTTPS');
    const R=constructor();
    if(!R)throw error('unsupported','Локальное распознавание не поддерживается браузером');
    const recognition=new R();
    recognition.processLocally=true;
    if(recognition.processLocally!==true)throw error('unsupported','Не удалось включить локальную обработку');
    recognition.lang=languageTag(language);
    recognition.continuous=!!continuous;
    recognition.interimResults=true;
    recognition.maxAlternatives=1;
    let settled=false,stopping=false,finalText='',resolveDone,rejectDone,timer=0;
    const done=new Promise((resolve,reject)=>{resolveDone=resolve;rejectDone=reject});
    // Не оставлять необработанный rejection до подключения контроллера.
    done.catch(()=>{});
    const cleanup=()=>{
      clearTimeout(timer);
      signal?.removeEventListener('abort',abort);
      recognition.onstart=recognition.onresult=recognition.onerror=recognition.onend=null;
    };
    const settle=(reason)=>{
      if(settled)return;
      settled=true;cleanup();
      if(reason)rejectDone(reason);else resolveDone(finalText);
    };
    function abort(){
      if(settled)return;
      settle(cancelled());
      try{recognition.abort()}catch(_e){}
    }
    function stop(){
      if(settled||stopping)return done;
      stopping=true;
      timer=setTimeout(()=>{
        settle(error('stop-timeout','Браузер не завершил распознавание'));
        try{recognition.abort()}catch(_e){}
      },5000);
      try{recognition.stop()}catch(e){settle(e);try{recognition.abort()}catch(_e){}}
      return done;
    }
    recognition.onstart=()=>{if(!settled)onStatus({status:'listening'})};
    recognition.onresult=event=>{
      if(settled||signal?.aborted)return;
      const finals=[],interims=[];
      for(let i=0;i<event.results.length;i++){
        const result=event.results[i],text=String(result[0]?.transcript||'').trim();
        if(text)(result.isFinal?finals:interims).push(text);
      }
      finalText=finals.join(' ');
      onText({finalText,interimText:interims.join(' ')});
    };
    recognition.onerror=event=>{
      const code=String(event.error||'recognition-error');
      settle(error(code,'Локальное распознавание остановлено: '+code));
      try{recognition.abort()}catch(_e){}
    };
    recognition.onend=()=>settle(signal?.aborted?cancelled():null);
    signal?.addEventListener('abort',abort,{once:true});
    try{if(track)recognition.start(track);else recognition.start()}catch(e){settle(e);try{recognition.abort()}catch(_e){}}
    return {done,stop,abort};
  }
  window.leshyOnDeviceVoice=Object.freeze({prepare,start});
})();
