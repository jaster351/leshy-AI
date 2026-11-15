(function(){
  'use strict';

  const originalFetch=window.fetch.bind(window);
  const originalSend=typeof window.sendFrom==='function'?window.sendFrom:null;
  let lastSubmit={input:null,text:'',at:0};
  const SAME_EVENT_WINDOW_MS=150;

  function requestId(){
    try{return crypto.randomUUID()}catch(_e){return Date.now()+'-'+Math.random().toString(16).slice(2)}
  }

  function notifySuppressed(text){
    try{window.dispatchEvent(new CustomEvent('leshy:duplicate-submit-suppressed',{detail:{text:String(text||'').slice(0,200)}}))}catch(_e){}
  }

  // Fallback/non-stream API тоже получает request_id. Если вызывающая сторона
  // повторяет тот же RequestInit (например после сетевого сбоя), ID сохраняется
  // в JSON body и сервер вернёт уже готовый ответ вместо второй генерации.
  window.fetch=function(input,init){
    try{
      const url=typeof input==='string'?input:(input&&input.url)||'';
      if((url==='/v1/chat'||url.endsWith('/v1/chat'))&&init&&typeof init.body==='string'){
        const body=JSON.parse(init.body);
        if(body&&typeof body==='object'&&!body.request_id){
          body.request_id=requestId();
          init=Object.assign({},init,{body:JSON.stringify(body)});
        }
      }
    }catch(_e){}
    return originalFetch(input,init);
  };

  if(originalSend){
    window.sendFrom=async function(input){
      const text=String(input&&input.value||'').trim();
      const now=(window.performance&&performance.now)?performance.now():Date.now();
      if(text&&lastSubmit.input===input&&lastSubmit.text===text&&now-lastSubmit.at<SAME_EVENT_WINDOW_MS){
        notifySuppressed(text);
        return {duplicate:true,suppressed:true};
      }
      if(text)lastSubmit={input,text,at:now};
      return await originalSend.apply(this,arguments);
    };
  }

  // Дополнительный capture-guard на случай, если legacy и recovery-слой оба
  // подписаны на один Enter/click. Первый обработчик получает событие, второй
  // видит уже установленный флаг и не инициирует ещё одну отправку.
  function guardEvent(event){
    if(event.__leshySubmitGuard)return false;
    try{Object.defineProperty(event,'__leshySubmitGuard',{value:true,configurable:true})}catch(_e){event.__leshySubmitGuard=true}
    return true;
  }

  document.addEventListener('keydown',function(event){
    if(event.key!=='Enter'||event.shiftKey)return;
    const target=event.target;
    if(!target||!(target.id==='chatInput'||target.id==='heroInput'))return;
    if(!guardEvent(event)){
      event.preventDefault();event.stopImmediatePropagation();notifySuppressed(target.value||'');
    }
  },true);

  document.addEventListener('click',function(event){
    const target=event.target&&event.target.closest?event.target.closest('#chatSend,#heroSend'):null;
    if(!target)return;
    guardEvent(event);
  },true);
})();
