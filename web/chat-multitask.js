(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const OWNER='web:local';
  const SESSION_KEY='leshy.session';
  const streams=new Map();
  const unread=new Set();
  let archived=false;

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
  function sleep(ms){return new Promise(r=>setTimeout(r,ms))}

  function style(){
    if($('leshyMultitaskStyle'))return;
    const s=document.createElement('style');s.id='leshyMultitaskStyle';s.textContent=`
      .leshy-session-work,.leshy-session-unread{display:inline-block;width:7px;height:7px;border-radius:50%;margin-left:7px;vertical-align:1px;flex:0 0 7px}.leshy-session-work{background:#9fb9d2;box-shadow:0 0 0 3px rgba(159,185,210,.08);animation:leshyWorkPulse 1.2s ease-in-out infinite}.leshy-session-unread{background:#7dbbff}.leshy-session-open.has-unread .leshy-session-title{font-weight:650;color:#fff}.leshy-session-open.is-working .leshy-session-title{color:#d9e6f3}@keyframes leshyWorkPulse{50%{opacity:.35;transform:scale(.8)}}
      .leshy-stream-plain{white-space:pre-wrap;word-break:break-word;line-height:1.58}.leshy-stream-caret{display:inline-block;width:6px;height:1em;background:#8eb8dc;margin-left:3px;vertical-align:-2px;animation:leshyBlink .8s steps(1) infinite}@keyframes leshyBlink{50%{opacity:0}}
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

  function makeStreamingBox(raw=''){
    const messages=$('messages');if(!messages)return null;
    const box=document.createElement('div');box.className='msg assistant leshy-background-stream';
    const who=document.createElement('div');who.className='who';who.textContent='Леший';
    const body=document.createElement('div');body.className='leshy-stream-plain';body.textContent=raw;
    const caret=document.createElement('span');caret.className='leshy-stream-caret';body.appendChild(caret);box.append(who,body);messages.appendChild(box);messages.scrollTop=messages.scrollHeight;return {box,body};
  }
  function updateStreamingBox(stream){
    if(sid()!==stream.sessionId||!stream.body||!stream.body.isConnected)return;
    stream.body.textContent=stream.raw;const c=document.createElement('span');c.className='leshy-stream-caret';stream.body.appendChild(c);
    const m=$('messages');if(m&&m.scrollHeight-m.scrollTop-m.clientHeight<180)m.scrollTop=m.scrollHeight;
  }
  function scheduleRender(stream){
    if(stream.renderPending)return;stream.renderPending=true;
    setTimeout(()=>requestAnimationFrame(()=>{stream.renderPending=false;updateStreamingBox(stream)}),60);
  }
  function event(type,detail){try{window.dispatchEvent(new CustomEvent('leshy:'+type,{detail:detail||{}}))}catch(_e){}}
  function stateForCurrent(){const current=streams.get(sid());const s=$('chatState');if(!s)return;if(current)s.textContent=current.activity|| (current.raw?'Леший отвечает…':'Леший думает…');else s.textContent='Готов'}

  function parseSse(buffer,onEvent){let p;while((p=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,p);buffer=buffer.slice(p+2);let type='message',raw='';for(const line of block.split('\n')){if(line.startsWith('event:'))type=line.slice(6).trim();else if(line.startsWith('data:'))raw+=(raw?'\n':'')+line.slice(5).trim()}if(raw){try{onEvent(type,JSON.parse(raw))}catch(_e){}}}return buffer}

  async function stop(stream){
    if(!stream)return;stream.stopping=true;
    try{fetch('/v1/chat/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:stream.requestId}),keepalive:true})}catch(_e){}
    try{stream.controller.abort()}catch(_e){}
    streams.delete(stream.sessionId||stream.tempKey);stateForCurrent();decorateSidebar();event('done',{session_id:stream.sessionId,stopped:true});
  }
  window.stopCurrentLeshyStream=()=>stop(streams.get(sid()));

  async function loadHistory(id){
    setSid(id);unread.delete(id);document.title='Леший';const messages=$('messages');if(messages)messages.textContent='';
    try{
      const r=await api('/v1/history?session_id='+encodeURIComponent(id)+'&owner_id='+encodeURIComponent(OWNER));
      for(const m of r.items||[])window.addMessage(m.role,m.content);
      const live=streams.get(id);if(live){const ui=makeStreamingBox(live.raw);if(ui){live.box=ui.box;live.body=ui.body}event('activity',{session_id:id,kind:live.activityKind||'',text:live.activity||'Леший думает…'})}
      if(typeof window.setLocked==='function')window.setLocked(r.session||null);
    }catch(e){if(messages){messages.textContent='';window.addMessage('assistant','Не удалось загрузить историю: '+e.message)}}
    try{if(typeof window.showSection==='function')window.showSection('chat')}catch(_e){}stateForCurrent();decorateSidebar();
  }
  window.loadHistory=loadHistory;
  window.newChat=function(){setSid('');const messages=$('messages');if(messages)messages.textContent='';try{if(typeof window.showSection==='function')window.showSection('chat')}catch(_e){}const input=$('chatInput');if(input){input.disabled=false;input.focus()}stateForCurrent();decorateSidebar()};

  async function refreshActiveView(stream){
    if(!stream.sessionId||sid()!==stream.sessionId)return;
    await loadHistory(stream.sessionId);
  }

  async function runStream(text,requestedSession,input){
    const requestId=(crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random());const controller=new AbortController();
    const tempKey=requestedSession||('pending:'+requestId);const stream={requestId,controller,tempKey,sessionId:requestedSession||'',raw:'',activity:'Леший думает…',activityKind:'',renderPending:false,body:null,box:null,meta:null};
    streams.set(tempKey,stream);if(requestedSession)streams.set(requestedSession,stream);
    const ui=makeStreamingBox('');if(ui){stream.box=ui.box;stream.body=ui.body}stateForCurrent();decorateSidebar();event('activity',{session_id:requestedSession||'',text:'Леший думает…'});
    try{
      const response=await fetch('/v1/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,session_id:requestedSession||'',owner_id:OWNER,request_id:requestId}),signal:controller.signal});
      if(!response.ok)throw new Error('HTTP '+response.status);if(!response.body)throw new Error('Браузер не поддерживает потоковые ответы');
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',done=null;
      while(true){const part=await reader.read();if(part.done)break;buffer+=decoder.decode(part.value,{stream:true});buffer=parseSse(buffer,(type,data)=>{
        if(type==='start'&&data.session_id){
          const old=stream.sessionId||stream.tempKey;stream.sessionId=String(data.session_id);streams.delete(old);streams.set(stream.sessionId,stream);
          if(!requestedSession&&sid()==='')setSid(stream.sessionId);stream.meta={title:'Диалог'};decorateSidebar();
        }else if(type==='activity'){
          stream.activity=String(data.text||'Леший работает…');stream.activityKind=String(data.kind||'');if(sid()===stream.sessionId){const cs=$('chatState');if(cs)cs.textContent=stream.activity;event('activity',Object.assign({session_id:stream.sessionId},data))}
        }else if(type==='delta'){
          stream.raw+=String(data.text||'');stream.activity='Леший отвечает…';scheduleRender(stream);if(sid()===stream.sessionId){const cs=$('chatState');if(cs)cs.textContent='Леший отвечает…';event('delta',{session_id:stream.sessionId,text:data.text||''})}
        }else if(type==='done')done=data;else if(type==='error')throw new Error(data.error||'Ошибка генерации');
      })}
      const answer=done&&typeof done.answer==='string'?done.answer:stream.raw;stream.raw=answer;streams.delete(stream.sessionId||stream.tempKey);event('done',{session_id:stream.sessionId,answer});notifyDone(stream,answer);await refreshActiveView(stream);stateForCurrent();await Promise.allSettled([window.loadSessions?window.loadSessions():Promise.resolve(),typeof window.refreshStatus==='function'?window.refreshStatus():Promise.resolve()]);
    }catch(e){
      streams.delete(stream.sessionId||stream.tempKey);if(e.name!=='AbortError'){if(sid()===stream.sessionId||(!stream.sessionId&&sid()===''))window.addMessage('assistant','Ошибка: '+e.message);toast('Ошибка ответа Лешего: '+e.message)}stateForCurrent();decorateSidebar();event('done',{session_id:stream.sessionId,error:e.message||''});
    }
  }

  window.sendFrom=async function(input){
    const current=streams.get(sid());if(current){await stop(current);return}
    const text=String(input&&input.value||'').trim();if(!text)return;ensureNotifyPermission();const requested=sid();input.value='';try{if(typeof window.showSection==='function')window.showSection('chat')}catch(_e){}window.addMessage('user',text);runStream(text,requested,input);
  };

  async function decorateSidebar(){
    const list=$('sessionList');if(!list)return;
    try{
      const r=await api('/v1/sessions?owner_id='+encodeURIComponent(OWNER)+'&limit=100&archived='+(archived?'1':'0'));const rows=[...list.querySelectorAll('.leshy-session-item,.item')];
      (r.items||[]).forEach((item,i)=>{const row=rows[i];if(!row)return;row.dataset.sessionId=item.id;const open=row.querySelector('.leshy-session-open')||row.querySelector('button');if(!open)return;open.querySelectorAll('.leshy-session-work,.leshy-session-unread').forEach(x=>x.remove());open.classList.remove('is-working','has-unread');if(streams.has(item.id)){const d=document.createElement('i');d.className='leshy-session-work';d.title='Леший отвечает';open.appendChild(d);open.classList.add('is-working')}else if(unread.has(item.id)){const d=document.createElement('i');d.className='leshy-session-unread';d.title='Новый ответ';open.appendChild(d);open.classList.add('has-unread')}});
    }catch(_e){}
  }

  function wrapSessions(){
    const original=window.loadSessions;if(!original||original.__multitaskWrapped)return;
    const wrapped=async function(){const r=await original.apply(this,arguments);await decorateSidebar();return r};wrapped.__multitaskWrapped=true;window.loadSessions=wrapped;
  }

  function boot(){style();wrapSessions();decorateSidebar();stateForCurrent();setInterval(decorateSidebar,1800);window.addEventListener('leshy:session-changed',stateForCurrent)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
