(function(){
  'use strict';

  const STALL_TIMEOUT_MS=90000;
  const SESSION_KEY='leshy.session';
  let timer=null;
  let active=false;
  let trackedSession='';

  function currentSession(){
    try{return localStorage.getItem(SESSION_KEY)||''}catch(_e){return ''}
  }

  function clearWatch(){
    active=false;
    trackedSession='';
    if(timer){clearTimeout(timer);timer=null}
  }

  function relevant(detail){
    const sid=String((detail&&detail.session_id)||'');
    const current=currentSession();
    if(!trackedSession)return true;
    if(!sid)return trackedSession===current||!current;
    return sid===trackedSession||sid===current;
  }

  function arm(detail){
    if(!relevant(detail))return;
    const sid=String((detail&&detail.session_id)||'');
    if(sid)trackedSession=sid;
    else if(!trackedSession)trackedSession=currentSession();
    active=true;
    if(timer)clearTimeout(timer);
    timer=setTimeout(onStall,STALL_TIMEOUT_MS);
  }

  async function onStall(){
    timer=null;
    if(!active)return;
    const current=currentSession();
    if(trackedSession&&current&&trackedSession!==current){
      clearWatch();
      return;
    }
    active=false;
    try{
      if(typeof window.stopCurrentLeshyStream==='function')await window.stopCurrentLeshyStream();
    }catch(_e){}
    const state=document.getElementById('chatState');
    if(state)state.textContent='Ответ остановлен: превышено время ожидания';
    try{
      if(typeof window.addMessage==='function'){
        window.addMessage('assistant','Ответ не пришёл за 90 секунд без признаков прогресса. Запрос остановлен, чтобы Web-интерфейс не зависал. Можно отправить его повторно.');
      }
    }catch(_e){}
    try{window.dispatchEvent(new CustomEvent('leshy:stream-stalled',{detail:{session_id:current,timeout_ms:STALL_TIMEOUT_MS}}))}catch(_e){}
    clearWatch();
  }

  window.addEventListener('leshy:activity',event=>arm(event.detail||{}));
  window.addEventListener('leshy:delta',event=>arm(event.detail||{}));
  window.addEventListener('leshy:done',event=>{
    if(relevant(event.detail||{}))clearWatch();
  });

  // На случай legacy send-path, который не публикует activity до первого SSE.
  const original=typeof window.sendFrom==='function'?window.sendFrom:null;
  if(original){
    window.sendFrom=async function(input){
      arm({session_id:currentSession()});
      try{return await original.apply(this,arguments)}
      finally{if(active&&document.getElementById('chatState')?.textContent==='Готов')clearWatch()}
    };
  }
})();
