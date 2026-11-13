(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const OWNER='web:local';
  const SESSION_KEY='leshy.session';
  const streams=new Map();
  const unread=new Set();
  let historyGeneration=0;
  let lastRequestMs=0;
  let requestSequence=0;

  async function api(path,options={}){
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const r=await fetch(path,init);
    const d=await r.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!r.ok)throw new Error(d.error||('HTTP '+r.status));
    return d;
  }
  function sid(){return localStorage.getItem(SESSION_KEY)||''}
  function setSid(id){if(id)localStorage.setItem(SESSION_KEY,id);else localStorage.removeItem(SESSION_KEY);try{window.sessionId=id}catch(_e){};window.dispatchEvent(new CustomEvent('leshy:session-changed',{detail:{session_id:id||''}}))}
  function toast(t){let n=$('shellToast');if(!n){n=document.createElement('div');n.id='shellToast';document.body.appendChild(n)}n.textContent=t;n.classList.add('show');clearTimeout(n._t);n._t=setTimeout(()=>n.classList.remove('show'),2200)}
  function nextRequestId(){
    const now=Date.now();
    if(now===lastRequestMs)requestSequence+=1;else{lastRequestMs=now;requestSequence=0}
    const timestamp=new Date(now).toISOString().replace(/\D/g,'').slice(0,17);
    let entropy='';
    try{const values=new Uint32Array(1);crypto.getRandomValues(values);entropy=values[0].toString(16).padStart(8,'0')}
    catch(_e){entropy=Math.random().toString(16).slice(2,10).padEnd(8,'0')}
    return 'web:'+timestamp+':'+String(requestSequence).padStart(3,'0')+':'+entropy;
  }

  function style(){
    if($('leshyMultitaskStyle'))return;
    const s=document.createElement('style');s.id='leshyMultitaskStyle';s.textContent=`
      .leshy-session-work,.leshy-session-unread{display:inline-block;width:7px;height:7px;border-radius:50%;margin-left:7px;vertical-align:1px;flex:0 0 7px}.leshy-session-work{background:#9fb9d2;box-shadow:0 0 0 3px rgba(159,185,210,.08);animation:leshyWorkPulse 1.2s ease-in-out infinite}.leshy-session-unread{background:#7dbbff}.leshy-session-open.has-unread .leshy-session-title{font-weight:650;color:#fff}.leshy-session-open.is-working .leshy-session-title{color:#d9e6f3}@keyframes leshyWorkPulse{50%{opacity:.35;transform:scale(.8)}}
      .leshy-stream-plain{white-space:pre-wrap;word-break:break-word;line-height:1.58}.leshy-stream-caret{display:inline-block;width:6px;height:1em;background:#8eb8dc;margin-left:3px;vertical-align:-2px;animation:leshyBlink .8s steps(1) infinite}@keyframes leshyBlink{50%{opacity:0}}
      #chatSend.streaming{cursor:pointer!important;opacity:1!important}#chatSend.streaming:focus-visible{outline:2px solid #dcefff!important;outline-offset:3px}
      .leshy-background-stream .leshy-message-time{margin-top:7px;text-align:right;font-size:10px;color:#8f9baa;opacity:.72}
    `;document.head.appendChild(s);
  }

  function ensureNotifyPermission(){
    try{if('Notification'in window&&Notification.permission==='default')Notification.requestPermission().catch(()=>{})}catch(_e){}
  }
  function notifyDone(stream,answer){
    const other=sid()!==stream.sessionId;
    if(other)unread.add(stream.sessionId);
    if(other||document.hidden){
      const meta=stream.meta||{};const title=meta.title||'Леший ответил';const body=String(answer||'Ответ готов').replace(/\s+/g,' ').slice(0,180);
      try{if('Notification'in window&&Notification.permission==='granted')new Notification('Леший · '+title,{body,tag:'leshy-'+stream.sessionId,renotify:true})}catch(_e){}
      if(!document.hidden)toast('Леший ответил в другом чате');
      document.title='● Леший';
    }
    decorateSidebar();
  }
  window.addEventListener('focus',()=>{if(!unread.size)document.title='Леший'});

  function activeForCurrent(){
    const id=sid();
    const direct=streams.get(id);
    if(direct)return direct;
    // Новый чат ещё не имеет session_id; его единственная временная запись
    // должна быть доступна кнопке остановки немедленно после отправки.
    if(id===''){
      for(const stream of streams.values()){
        if(!stream.sessionId&&!stream.stopping)return stream;
      }
    }
    return null;
  }
  function removeStream(stream){
    for(const [key,value] of streams){
      if(value===stream)streams.delete(key);
    }
  }

  function makeStreamingBox(raw=''){
    const messages=$('messages');if(!messages)return null;
    const box=document.createElement('div');box.className='msg assistant leshy-background-stream';
    const who=document.createElement('div');who.className='who';who.textContent='Леший';
    const body=document.createElement('div');body.className='leshy-stream-plain';body.textContent=raw;
    const caret=document.createElement('span');caret.className='leshy-stream-caret';body.appendChild(caret);box.append(who,body);
    const time=document.createElement('time');time.className='leshy-message-time';
    const now=new Date();time.dateTime=now.toISOString();time.textContent=new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(now).replace(',','');box.appendChild(time);
    messages.appendChild(box);messages.scrollTop=messages.scrollHeight;return {box,body};
  }
  function updateStreamingBox(stream){
    if(stream.finished||sid()!==stream.sessionId||!stream.body||!stream.body.isConnected)return;
    stream.body.textContent=stream.raw;const c=document.createElement('span');c.className='leshy-stream-caret';stream.body.appendChild(c);
    const m=$('messages');if(m&&m.scrollHeight-m.scrollTop-m.clientHeight<180)m.scrollTop=m.scrollHeight;
  }
  function scheduleRender(stream){
    if(stream.renderPending)return;stream.renderPending=true;
    setTimeout(()=>requestAnimationFrame(()=>{stream.renderPending=false;updateStreamingBox(stream)}),60);
  }
  function event(type,detail){try{window.dispatchEvent(new CustomEvent('leshy:'+type,{detail:detail||{}}))}catch(_e){}}
  function stateForCurrent(){
    const current=activeForCurrent();
    const send=$('chatSend');
    if(send){
      send.classList.toggle('streaming',!!current);
      send.setAttribute('aria-label',current?'Прервать ответ Лешего':'Отправить сообщение');
      send.title=current?'Прервать ответ':'Отправить';
      // Существующая оболочка наблюдает за .streaming и рисует иконку ■.
      if(!send.dataset.cgStableObserved)send.textContent=current?'■':'Отправить';
    }
    const s=$('chatState');
    if(s)s.textContent=current?(current.activity||(current.raw?'Леший отвечает…':'Леший думает…')):'Готов';
  }

  function parseSse(buffer,onEvent){
    let p;
    while((p=buffer.indexOf('\n\n'))>=0){
      const block=buffer.slice(0,p);buffer=buffer.slice(p+2);let type='message',raw='';
      for(const line of block.split('\n')){if(line.startsWith('event:'))type=line.slice(6).trim();else if(line.startsWith('data:'))raw+=(raw?'\n':'')+line.slice(5).trim()}
      if(raw){let data;try{data=JSON.parse(raw)}catch(_e){continue}onEvent(type,data)}
    }
    return buffer;
  }

  async function stop(stream){
    if(!stream||stream.stopping)return;
    stream.stopping=true;
    // Не ждать ответа stop-endpoint: UI разблокируется немедленно.
    // Сервер запоминает раннюю отмену, даже если SSE ещё не зарегистрирован.
    try{
      fetch('/v1/chat/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:stream.requestId}),keepalive:true}).catch(()=>{});
    }catch(_e){}
    try{stream.controller.abort()}catch(_e){}
    removeStream(stream);
    if(stream.body&&stream.body.isConnected){
      stream.body.textContent=stream.raw;
      if(!stream.raw&&stream.box)stream.box.remove();
    }
    stateForCurrent();decorateSidebar();
    event('done',{session_id:stream.sessionId,request_id:stream.requestId,stopped:true});
  }
  window.stopCurrentLeshyStream=()=>stop(activeForCurrent());

  async function loadHistory(id,options={}){
    const generation=++historyGeneration;
    const keepVisible=!!options.keepVisible&&sid()===id;
    setSid(id);unread.delete(id);if(!unread.size)document.title='Леший';
    const messages=$('messages');
    // При переключении диалога не показывать чужую переписку. При обновлении
    // ТЕКУЩЕГО диалога не уничтожать сообщения до подтверждения ответа API.
    if(messages&&!keepVisible)messages.textContent='';
    try{
      const r=await api('/v1/history?session_id='+encodeURIComponent(id)+'&owner_id='+encodeURIComponent(OWNER));
      if(generation!==historyGeneration||sid()!==id)return;
      const items=Array.isArray(r.items)?r.items:[];
      const expectedAnswer=String(options.expectedAnswer||'');
      const hasExpectedAnswer=!expectedAnswer||items.some(m=>m.role==='assistant'&&String(m.content||'')===expectedAnswer);
      if(keepVisible&&messages&&messages.querySelector('.msg')&&(!items.length||!hasExpectedAnswer)){
        // Пустой/устаревший ответ истории не может заменить видимый ответ,
        // который пользователь только что получил через SSE.
        event('history-sync-deferred',{session_id:id});
      }else{
        if(messages)messages.textContent='';
        for(const m of items)window.addMessage(m.role,m.content,[],m.created_at);
        const live=streams.get(id);
        if(live){const ui=makeStreamingBox(live.raw);if(ui){live.box=ui.box;live.body=ui.body}event('activity',{session_id:id,request_id:live.requestId,kind:live.activityKind||'',text:live.activity||'Леший думает…'})}
      }
    }catch(e){
      if(generation===historyGeneration&&sid()===id&&messages){
        if(keepVisible&&messages.querySelector('.msg')){
          event('history-sync-failed',{session_id:id});
          toast('История временно недоступна; текущие сообщения сохранены на экране');
        }else{
          messages.textContent='';
          window.addMessage('assistant','Не удалось загрузить историю: '+e.message);
        }
      }
    }
    try{if(typeof window.showSection==='function')window.showSection('chat')}catch(_e){}
    stateForCurrent();decorateSidebar();
  }
  window.loadHistory=loadHistory;
  window.newChat=function(){
    // Не теряем зависший первый поток без session_id.
    if(sid()===''&&activeForCurrent())stop(activeForCurrent());
    ++historyGeneration;
    setSid('');const messages=$('messages');if(messages)messages.textContent='';
    try{if(typeof window.showSection==='function')window.showSection('chat')}catch(_e){}
    const input=$('chatInput');if(input){input.disabled=false;input.focus()}
    stateForCurrent();decorateSidebar();
  };

  async function refreshActiveView(stream){
    if(!stream.sessionId||sid()!==stream.sessionId)return;
    // Финализируем SSE-карточку сразу; фоновая сверка истории не должна
    // временно удалять ни вопрос пользователя, ни только что полученный ответ.
    stream.finished=true;
    if(stream.body&&stream.body.isConnected)stream.body.textContent=stream.raw;
    await loadHistory(stream.sessionId,{keepVisible:true,expectedAnswer:stream.raw});
  }

  async function runStream(text,requestedSession){
    const requestId=nextRequestId();const controller=new AbortController();
    const tempKey=requestedSession||('pending:'+requestId);const stream={requestId,controller,tempKey,sessionId:requestedSession||'',raw:'',activity:'Леший думает…',activityKind:'',renderPending:false,body:null,box:null,meta:null};
    streams.set(tempKey,stream);if(requestedSession)streams.set(requestedSession,stream);
    const ui=makeStreamingBox('');if(ui){stream.box=ui.box;stream.body=ui.body}stateForCurrent();decorateSidebar();event('activity',{session_id:requestedSession||'',request_id:requestId,text:'Леший думает…'});
    try{
      const response=await fetch('/v1/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,session_id:requestedSession||'',owner_id:OWNER,request_id:requestId}),signal:controller.signal});
      if(stream.stopping)return;
      if(!response.ok)throw new Error('HTTP '+response.status);if(!response.body)throw new Error('Браузер не поддерживает потоковые ответы');
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',done=null,terminal=false;
      while(!terminal&&!stream.stopping){
        const part=await reader.read();if(part.done)break;
        if(stream.stopping)return;
        buffer+=decoder.decode(part.value,{stream:true});
        buffer=parseSse(buffer,(type,data)=>{
          if(stream.stopping)return;
          if(type==='start'&&data.session_id){
            const old=stream.sessionId||stream.tempKey;stream.sessionId=String(data.session_id);streams.delete(old);streams.set(stream.sessionId,stream);
            if(!requestedSession&&sid()==='')setSid(stream.sessionId);stream.meta={title:'Диалог'};decorateSidebar();
          }else if(type==='activity'){
            stream.activity=String(data.text||'Леший работает…');stream.activityKind=String(data.kind||'');if(sid()===stream.sessionId){const cs=$('chatState');if(cs)cs.textContent=stream.activity;event('activity',Object.assign({session_id:stream.sessionId,request_id:requestId},data))}
          }else if(type==='delta'){
            stream.raw+=String(data.text||'');stream.activity='Леший отвечает…';scheduleRender(stream);if(sid()===stream.sessionId){const cs=$('chatState');if(cs)cs.textContent='Леший отвечает…';event('delta',{session_id:stream.sessionId,request_id:requestId,text:data.text||''})}
          }else if(type==='done'){
            done=data||{};terminal=true;
          }else if(type==='duplicate'){
            done={duplicate:true,duplicate_status:String(data.status||''),answer:stream.raw};terminal=true;
          }else if(type==='error'){
            throw new Error(data.error||'Ошибка генерации');
          }
        });
        if(terminal){try{await reader.cancel('leshy logical stream terminal')}catch(_e){}break}
      }
      if(stream.stopping)return;
      if(!terminal&&!done)throw new Error('Поток прервался без завершающего события; проверьте доступность модели');
      const answer=done&&typeof done.answer==='string'?done.answer:stream.raw;stream.raw=answer;removeStream(stream);event('done',{session_id:stream.sessionId,request_id:requestId,answer,duplicate:!!(done&&done.duplicate)});notifyDone(stream,answer);await refreshActiveView(stream);stateForCurrent();await Promise.allSettled([window.loadSessions?window.loadSessions():Promise.resolve(),typeof window.refreshStatus==='function'?window.refreshStatus():Promise.resolve()]);
    }catch(e){
      if(stream.stopping)return;
      removeStream(stream);if(e.name!=='AbortError'){if(sid()===stream.sessionId||(!stream.sessionId&&sid()===''))window.addMessage('assistant','Ошибка: '+e.message);toast('Ошибка ответа Лешего: '+e.message)}stateForCurrent();decorateSidebar();event('done',{session_id:stream.sessionId,request_id:requestId,error:e.message||''});
    }
  }

  window.sendFrom=async function(input){
    const current=activeForCurrent();if(current){await stop(current);return}
    const text=String(input&&input.value||'').trim();if(!text)return;ensureNotifyPermission();const requested=sid();input.value='';try{if(typeof window.showSection==='function')window.showSection('chat')}catch(_e){}window.addMessage('user',text,[],new Date().toISOString());runStream(text,requested);
  };

  async function decorateSidebar(){
    const list=$('sessionList');if(!list)return;
    try{
      const archivedView=$('sessionArchiveToggle')?.textContent?.trim()==='Назад';
      const r=await api('/v1/sessions?owner_id='+encodeURIComponent(OWNER)+'&limit=100&archived='+(archivedView?'1':'0'));const rows=[...list.querySelectorAll('.leshy-session-item,.item')];
      (r.items||[]).forEach((item,i)=>{const row=rows[i];if(!row)return;row.dataset.sessionId=item.id;const open=row.querySelector('.leshy-session-open')||row.querySelector('button');if(!open)return;open.querySelectorAll('.leshy-session-work,.leshy-session-unread').forEach(x=>x.remove());open.classList.remove('is-working','has-unread');if(streams.has(item.id)){const d=document.createElement('i');d.className='leshy-session-work';d.title='Леший отвечает';open.appendChild(d);open.classList.add('is-working')}else if(unread.has(item.id)){const d=document.createElement('i');d.className='leshy-session-unread';d.title='Новый ответ';open.appendChild(d);open.classList.add('has-unread')}});
    }catch(_e){}
  }

  function wrapSessions(){
    const original=window.loadSessions;if(!original||original.__multitaskWrapped)return;
    const wrapped=async function(){const r=await original.apply(this,arguments);await decorateSidebar();return r};wrapped.__multitaskWrapped=true;window.loadSessions=wrapped;
  }

  function boot(){style();wrapSessions();decorateSidebar();stateForCurrent();window.addEventListener('leshy:session-changed',stateForCurrent)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
