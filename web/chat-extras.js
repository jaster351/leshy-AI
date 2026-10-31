(function(){
  'use strict';

  const byId=id=>document.getElementById(id);
  const OWNER='web:local';
  const SESSION_KEY='leshy.session';
  let activeStream=null;
  let archivedMode=false;
  let sessionCache=new Map();
  let currentSessionMeta=null;

  const icons={
    pin:'<svg viewBox="0 0 24 24"><path d="M14 4l6 6-3 1-4 4-1 5-2-2-2-2 5-1 4-4 1-3Z"/><path d="M5 19l5-5"/></svg>',
    more:'<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>',
    copy:'<svg viewBox="0 0 24 24"><rect x="9" y="9" width="10" height="10" rx="2"/><path d="M15 9V7a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/></svg>'
  };

  async function api(path,options={}){
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok) throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function installStyle(){
    if(byId('leshyChatExtrasStyle')) return;
    const s=document.createElement('style');
    s.id='leshyChatExtrasStyle';
    s.textContent=`
      .chat-rich-body{display:grid;gap:9px;min-width:0}.chat-rich-text{white-space:pre-wrap;word-break:break-word;line-height:1.58}
      .chat-rich-body p{margin:0}.chat-rich-body h1,.chat-rich-body h2,.chat-rich-body h3{margin:7px 0 1px;line-height:1.3}.chat-rich-body h1{font-size:20px}.chat-rich-body h2{font-size:17px}.chat-rich-body h3{font-size:15px}
      .chat-rich-body ul,.chat-rich-body ol{margin:0;padding-left:22px;display:grid;gap:4px}.chat-rich-body blockquote{margin:0;padding:3px 0 3px 12px;border-left:3px solid #3d4b5b;color:#b9c2cd}
      .chat-inline-code{font-family:Consolas,'Cascadia Code',ui-monospace,monospace;background:#171d24;border:1px solid #2d3640;border-radius:5px;padding:1px 5px;font-size:.94em;color:#e6edf3}
      .chat-code-block{border:1px solid #313a45;border-radius:10px;overflow:hidden;background:#0b0f14;min-width:min(700px,78vw);max-width:100%;box-shadow:0 5px 18px rgba(0,0,0,.14)}
      .chat-code-head{height:36px;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:0 8px 0 12px;background:#171c22;border-bottom:1px solid #2b333d;color:#aeb7c2;font-size:11px}
      .chat-code-copy{height:27px;border:0;background:transparent;color:#d6dce4;border-radius:6px;padding:0 8px;display:flex;align-items:center;gap:6px;cursor:pointer;font-size:11px}.chat-code-copy:hover{background:#282f37;color:#fff}.chat-code-copy svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.8}
      .chat-code-block pre{margin:0;padding:14px 16px;overflow:auto;max-height:65vh;white-space:pre!important;tab-size:4}.chat-code-block code{font-family:Consolas,'Cascadia Code',ui-monospace,monospace;font-size:12px;line-height:1.55;color:#e6edf3;white-space:pre}
      .chat-stream-caret{display:inline-block;width:7px;height:1.05em;margin-left:2px;vertical-align:-2px;background:#8eb8dc;animation:leshy-caret .8s steps(1) infinite}@keyframes leshy-caret{50%{opacity:0}}
      #chatSend.streaming{min-width:70px;background:#2a3139!important;border-color:#48515d!important}.chat-locked-note{font-size:11px;color:#d8b976;padding:4px 0 0}
      #sidebarHistoryTitle{display:flex;align-items:center;justify-content:space-between;gap:8px}#sessionArchiveToggle{border:0;background:transparent;color:#8f98a4;font-size:10px;border-radius:6px;padding:4px 6px;cursor:pointer}#sessionArchiveToggle:hover{background:#222831;color:#e4e8ee}
      #sidebarHistory #sessionList .leshy-session-item{position:relative;display:flex!important;align-items:center;gap:2px;padding:2px 3px 2px 9px!important;min-height:38px}.leshy-session-open{min-width:0;flex:1;border:0;background:transparent;color:inherit;text-align:left;padding:8px 4px;cursor:pointer;overflow:hidden}.leshy-session-title{display:block;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.leshy-session-state{font-size:9px;color:#87919d;margin-left:5px}
      .leshy-session-icon{width:28px;height:28px;display:grid;place-items:center;border:0;background:transparent;color:#8e98a5;border-radius:7px;cursor:pointer;opacity:0}.leshy-session-item:hover .leshy-session-icon,.leshy-session-icon.active,.leshy-session-item.menu-open .leshy-session-icon{opacity:1}.leshy-session-icon:hover{background:#30363f;color:#fff}.leshy-session-icon svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      #leshySessionMenu{position:fixed;z-index:900;width:245px;background:#2b2b2b;border:1px solid #414141;border-radius:14px;padding:7px;box-shadow:0 18px 55px rgba(0,0,0,.52);display:none}#leshySessionMenu.open{display:block}#leshySessionMenu button{width:100%;height:40px;border:0;background:transparent;color:#ececec;border-radius:8px;padding:0 10px;display:flex;align-items:center;gap:10px;text-align:left;cursor:pointer;font-size:13px}#leshySessionMenu button:hover{background:#3a3a3a}#leshySessionMenu button.danger{color:#ff6262}#leshySessionMenu .menu-sep{height:1px;background:#474747;margin:5px 7px}
      #leshySessionMenu .menu-icon{width:18px;text-align:center;color:#c9c9c9}.stream-error{color:#ff9e9e;font-size:12px;margin-top:6px}
      @media(max-width:720px){.chat-code-block{min-width:0;width:100%}.leshy-session-icon{opacity:1}}
    `;
    document.head.appendChild(s);
  }

  function toast(text){
    let n=byId('shellToast');
    if(!n){n=document.createElement('div');n.id='shellToast';document.body.appendChild(n)}
    n.textContent=text;n.classList.add('show');clearTimeout(n._t);n._t=setTimeout(()=>n.classList.remove('show'),1800);
  }

  async function copyText(text,button){
    try{
      await navigator.clipboard.writeText(String(text||''));
      if(button){const old=button.innerHTML;button.textContent='Скопировано';setTimeout(()=>button.innerHTML=old,1200)}
      else toast('Скопировано');
    }catch(_e){toast('Не удалось скопировать')}
  }

  function appendInline(parent,text){
    const value=String(text||'');
    const re=/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^)]+\))/g;
    let at=0,match;
    while((match=re.exec(value))){
      if(match.index>at) parent.appendChild(document.createTextNode(value.slice(at,match.index)));
      const token=match[0];
      if(token.startsWith('`')){const code=document.createElement('code');code.className='chat-inline-code';code.textContent=token.slice(1,-1);parent.appendChild(code)}
      else if(token.startsWith('**')){const b=document.createElement('strong');b.textContent=token.slice(2,-2);parent.appendChild(b)}
      else{const m=/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/.exec(token);if(m){const a=document.createElement('a');a.href=m[2];a.target='_blank';a.rel='noopener noreferrer';a.textContent=m[1];parent.appendChild(a)}else parent.appendChild(document.createTextNode(token))}
      at=re.lastIndex;
    }
    if(at<value.length) parent.appendChild(document.createTextNode(value.slice(at)));
  }

  function renderTextPart(host,text,streaming){
    const value=String(text||'');
    if(streaming){const p=document.createElement('div');p.className='chat-rich-text';p.textContent=value;host.appendChild(p);return}
    const lines=value.split(/\r?\n/);let list=null,listType='';
    const finishList=()=>{list=null;listType=''};
    for(const raw of lines){
      const line=raw.trimEnd();
      if(!line.trim()){finishList();continue}
      let m;
      if((m=/^(#{1,3})\s+(.+)$/.exec(line))){finishList();const h=document.createElement('h'+m[1].length);appendInline(h,m[2]);host.appendChild(h);continue}
      if((m=/^>\s?(.*)$/.exec(line))){finishList();const q=document.createElement('blockquote');appendInline(q,m[1]);host.appendChild(q);continue}
      if((m=/^[-*]\s+(.+)$/.exec(line))){if(!list||listType!=='ul'){finishList();list=document.createElement('ul');listType='ul';host.appendChild(list)}const li=document.createElement('li');appendInline(li,m[1]);list.appendChild(li);continue}
      if((m=/^\d+[.)]\s+(.+)$/.exec(line))){if(!list||listType!=='ol'){finishList();list=document.createElement('ol');listType='ol';host.appendChild(list)}const li=document.createElement('li');appendInline(li,m[1]);list.appendChild(li);continue}
      finishList();const p=document.createElement('p');appendInline(p,line);host.appendChild(p);
    }
  }

  function codeBlock(language,code){
    const wrap=document.createElement('div');wrap.className='chat-code-block';
    const head=document.createElement('div');head.className='chat-code-head';
    const lang=document.createElement('span');lang.className='chat-code-language';lang.textContent=language||'код';
    const copy=document.createElement('button');copy.type='button';copy.className='chat-code-copy';copy.innerHTML=icons.copy+'<span>Копировать</span>';copy.onclick=()=>copyText(code,copy);
    const pre=document.createElement('pre'),c=document.createElement('code');c.textContent=code;pre.appendChild(c);head.append(lang,copy);wrap.append(head,pre);return wrap;
  }

  function renderRich(host,text,{streaming=false}={}){
    host.textContent='';host.className='chat-rich-body';
    const value=String(text||'');let pos=0;
    while(pos<value.length){
      const start=value.indexOf('```',pos);
      if(start<0){renderTextPart(host,value.slice(pos),streaming);break}
      if(start>pos) renderTextPart(host,value.slice(pos,start),streaming);
      const headerEnd=value.indexOf('\n',start+3);
      if(headerEnd<0){renderTextPart(host,value.slice(start),streaming);break}
      const language=value.slice(start+3,headerEnd).trim().slice(0,40);
      const end=value.indexOf('```',headerEnd+1);
      if(end<0){host.appendChild(codeBlock(language,value.slice(headerEnd+1)));pos=value.length;break}
      host.appendChild(codeBlock(language,value.slice(headerEnd+1,end)));pos=end+3;
    }
    if(!value){const p=document.createElement('div');p.className='chat-rich-text';host.appendChild(p)}
    if(streaming){const caret=document.createElement('span');caret.className='chat-stream-caret';host.appendChild(caret)}
  }

  function appendSources(box,sources){
    if(!sources||!sources.length)return;const src=document.createElement('div');src.className='sources';src.textContent='Источники:';
    for(const s of sources){const a=document.createElement('a');a.href=s.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=s.title||s.url;src.appendChild(a)}box.appendChild(src);
  }

  function makeMessage(role,text,sources=[],streaming=false){
    const box=document.createElement('div');box.className='msg '+role;const who=document.createElement('div');who.className='who';who.textContent=role==='user'?'Вы':'Леший';box.appendChild(who);
    const body=document.createElement('div');box.appendChild(body);
    if(role==='assistant') renderRich(body,text,{streaming}); else{body.className='chat-rich-text';body.textContent=String(text||'')}
    appendSources(box,sources);byId('messages')?.appendChild(box);if(byId('messages'))byId('messages').scrollTop=byId('messages').scrollHeight;return {box,body};
  }

  window.addMessage=addMessage=function(role,text,sources=[]){return makeMessage(role,text,sources,false).box};

  function setSessionId(id){
    try{sessionId=id}catch(_e){}
    if(id)localStorage.setItem(SESSION_KEY,id);else localStorage.removeItem(SESSION_KEY);
  }
  function getSessionId(){try{return sessionId||localStorage.getItem(SESSION_KEY)||''}catch(_e){return localStorage.getItem(SESSION_KEY)||''}}
  function showChat(){try{if(typeof showSection==='function')showSection('chat')}catch(_e){}}

  function setLocked(meta){
    currentSessionMeta=meta||null;const locked=!!(meta&&meta.locked);const input=byId('chatInput');
    if(input){input.disabled=locked;input.placeholder=locked?'Этот чат заблокирован. Разблокируйте его через ⋯':'Сообщение… Для явного поиска: /search запрос'}
    let note=byId('chatLockedNote');
    if(locked&&!note){note=document.createElement('div');note.id='chatLockedNote';note.className='chat-locked-note';note.textContent='Чат заблокирован: история доступна только для чтения.';byId('chatInput')?.closest('.composer')?.insertAdjacentElement('afterend',note)}
    if(note)note.style.display=locked?'block':'none';
  }

  window.loadHistory=loadHistory=async function(id){
    if(activeStream) await stopStream();setSessionId(id);const messages=byId('messages');if(messages)messages.textContent='';
    try{const r=await api('/v1/history?session_id='+encodeURIComponent(id)+'&owner_id='+encodeURIComponent(OWNER));for(const m of r.items||[])addMessage(m.role,m.content);setLocked(r.session||sessionCache.get(id)||null)}
    catch(e){if(messages){messages.textContent='';makeMessage('assistant','Не удалось загрузить историю: '+e.message)}}showChat();
  };

  window.newChat=newChat=function(){if(activeStream)stopStream();setSessionId('');setLocked(null);if(byId('messages'))byId('messages').textContent='';showChat();if(byId('chatInput')){byId('chatInput').disabled=false;byId('chatInput').focus()}};

  function ensureArchiveToggle(){
    const title=byId('sidebarHistoryTitle');if(!title||byId('sessionArchiveToggle'))return;
    const b=document.createElement('button');b.id='sessionArchiveToggle';b.type='button';b.textContent='Архив';b.onclick=()=>{archivedMode=!archivedMode;b.textContent=archivedMode?'Назад':'Архив';loadSessions()};title.appendChild(b);
  }

  function closeSessionMenu(){const menu=byId('leshySessionMenu');if(menu)menu.classList.remove('open');document.querySelectorAll('.leshy-session-item.menu-open').forEach(x=>x.classList.remove('menu-open'))}
  function ensureSessionMenu(){let menu=byId('leshySessionMenu');if(menu)return menu;menu=document.createElement('div');menu.id='leshySessionMenu';document.body.appendChild(menu);return menu}

  async function updateSession(item,changes){await api('/v1/sessions/update',{method:'POST',body:JSON.stringify(Object.assign({owner_id:OWNER,id:item.id},changes))});await loadSessions();if(item.id===getSessionId()){const fresh=sessionCache.get(item.id);if(fresh)setLocked(fresh)}}
  async function shareSession(item){const u=new URL(location.href);u.searchParams.set('session',item.id);try{if(navigator.share)await navigator.share({title:item.title||'Диалог с Лешим',url:u.toString()});else{await navigator.clipboard.writeText(u.toString());toast('Ссылка скопирована')}}catch(e){if(e.name!=='AbortError')toast('Не удалось поделиться')}}

  function openSessionMenu(item,button,row){
    const menu=ensureSessionMenu();menu.textContent='';row.classList.add('menu-open');
    const add=(label,icon,fn,cls='')=>{const b=document.createElement('button');b.className=cls;b.innerHTML='<span class="menu-icon">'+icon+'</span><span>'+label+'</span>';b.onclick=async e=>{e.stopPropagation();closeSessionMenu();await fn()};menu.appendChild(b)};
    add('Переименовать','✎',async()=>{const value=prompt('Название чата',item.title||'');if(value&&value.trim())await updateSession(item,{title:value.trim()})});
    add(item.pinned?'Открепить':'Закрепить','⌖',()=>updateSession(item,{pinned:!item.pinned}));
    add(item.locked?'Разблокировать чат':'Заблокировать чат','▣',()=>updateSession(item,{locked:!item.locked}));
    const sep1=document.createElement('div');sep1.className='menu-sep';menu.appendChild(sep1);
    add('Поделиться','⇧',()=>shareSession(item));
    const sep2=document.createElement('div');sep2.className='menu-sep';menu.appendChild(sep2);
    add(item.archived?'Вернуть из архива':'Архивировать','▤',()=>updateSession(item,{archived:!item.archived}));
    add('Удалить','⌫',async()=>{if(confirm('Удалить этот чат и всю его историю?')){await api('/v1/sessions/delete',{method:'POST',body:JSON.stringify({owner_id:OWNER,id:item.id})});if(item.id===getSessionId())newChat();await loadSessions()}},'danger');
    const rect=button.getBoundingClientRect();menu.classList.add('open');const w=245,h=Math.min(menu.offsetHeight||320,360);let left=rect.right+6,top=rect.top-8;if(left+w>innerWidth-8)left=Math.max(8,rect.left-w-6);if(top+h>innerHeight-8)top=Math.max(8,innerHeight-h-8);menu.style.left=left+'px';menu.style.top=top+'px';
  }

  window.loadSessions=loadSessions=async function(){
    ensureArchiveToggle();const list=byId('sessionList');if(!list)return;
    try{
      const r=await api('/v1/sessions?owner_id='+encodeURIComponent(OWNER)+'&limit=100&archived='+(archivedMode?'1':'0'));list.textContent='';sessionCache=new Map((r.items||[]).map(x=>[x.id,x]));
      if(!(r.items||[]).length){const e=document.createElement('div');e.className='empty';e.textContent=archivedMode?'Архив пуст':'Диалогов пока нет';list.appendChild(e);return}
      for(const item of r.items){
        const row=document.createElement('div');row.className='item leshy-session-item';
        const open=document.createElement('button');open.className='leshy-session-open';open.type='button';const title=document.createElement('span');title.className='leshy-session-title';title.textContent=item.title||'Новый диалог';if(item.locked){const st=document.createElement('span');st.className='leshy-session-state';st.textContent='🔒';title.appendChild(st)}open.appendChild(title);open.onclick=()=>loadHistory(item.id);
        const pin=document.createElement('button');pin.className='leshy-session-icon'+(item.pinned?' active':'');pin.type='button';pin.title=item.pinned?'Открепить':'Закрепить';pin.innerHTML=icons.pin;pin.onclick=async e=>{e.stopPropagation();await updateSession(item,{pinned:!item.pinned})};
        const more=document.createElement('button');more.className='leshy-session-icon';more.type='button';more.title='Действия с чатом';more.innerHTML=icons.more;more.onclick=e=>{e.stopPropagation();closeSessionMenu();openSessionMenu(item,more,row)};
        row.append(open,pin,more);list.appendChild(row);
      }
    }catch(e){list.textContent='';const x=document.createElement('div');x.className='empty';x.textContent='Ошибка загрузки чатов: '+e.message;list.appendChild(x)}
  };

  function setStreamingUi(on){
    const b=byId('chatSend');if(b){b.classList.toggle('streaming',on);b.textContent=on?'■ Стоп':'Отправить'}
    const state=byId('chatState');if(state)state.textContent=on?'Леший отвечает…':'Готов';
  }

  async function stopStream(){
    if(!activeStream)return;const current=activeStream;try{fetch('/v1/chat/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:current.requestId}),keepalive:true})}catch(_e){}
    try{current.controller.abort()}catch(_e){}activeStream=null;setStreamingUi(false);if(byId('chatState'))byId('chatState').textContent='Остановлено';
    if(current.body)renderRich(current.body,current.raw||'',{streaming:false});
  }

  function parseSse(buffer,onEvent){
    let boundary;while((boundary=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);let type='message',data='';for(const line of block.split('\n')){if(line.startsWith('event:'))type=line.slice(6).trim();else if(line.startsWith('data:'))data+=(data?'\n':'')+line.slice(5).trim()}if(data){try{onEvent(type,JSON.parse(data))}catch(_e){}}}return buffer;
  }

  async function watchImage(job,box){
    if(!job||!job.id)return;const note=document.createElement('div');note.className='muted';note.textContent='Генерация изображения: '+(job.progress||0)+'%';box.appendChild(note);
    let current=job;while(current&& !['done','error'].includes(current.state)){await new Promise(r=>setTimeout(r,1100));try{current=await api('/v1/image/job?job_id='+encodeURIComponent(job.id));note.textContent='Генерация изображения: '+Math.round(Number(current.progress||0))+'%'}catch(_e){}}
    if(current&&current.image_url){const img=document.createElement('img');img.className='generated-image';img.src=current.image_url+'?t='+Date.now();box.appendChild(img)}else if(current&&current.error)note.textContent='Ошибка генерации: '+current.error;
  }

  window.sendFrom=sendFrom=async function(input){
    if(activeStream){await stopStream();return}
    const text=(input&&input.value||'').trim();if(!text)return;if(currentSessionMeta&&currentSessionMeta.locked){toast('Чат заблокирован');return}
    input.value='';showChat();makeMessage('user',text);const result=makeMessage('assistant','',[],true);const requestId=(crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random());const controller=new AbortController();
    activeStream={requestId,controller,box:result.box,body:result.body,raw:''};setStreamingUi(true);
    try{
      const response=await fetch('/v1/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,session_id:getSessionId(),owner_id:OWNER,request_id:requestId}),signal:controller.signal});
      if(!response.ok)throw new Error('HTTP '+response.status);if(!response.body)throw new Error('Браузер не поддерживает потоковые ответы');
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',donePayload=null;
      while(true){const read=await reader.read();if(read.done)break;buffer+=decoder.decode(read.value,{stream:true});buffer=parseSse(buffer,(type,data)=>{
        if(type==='start'&&data.session_id){setSessionId(data.session_id)}
        else if(type==='delta'){activeStream.raw+=String(data.text||'');renderRich(activeStream.body,activeStream.raw,{streaming:true});if(byId('messages'))byId('messages').scrollTop=byId('messages').scrollHeight}
        else if(type==='done'){donePayload=data}
        else if(type==='error'){throw new Error(data.error||'Ошибка генерации')}
      })}
      const stream=activeStream;if(!stream)return;const finalText=(donePayload&&typeof donePayload.answer==='string')?donePayload.answer:stream.raw;stream.raw=finalText;renderRich(stream.body,finalText,{streaming:false});
      if(donePayload&&donePayload.sources)appendSources(stream.box,donePayload.sources);if(donePayload&&donePayload.image_job)watchImage(donePayload.image_job,stream.box);
      activeStream=null;setStreamingUi(false);await Promise.allSettled([loadSessions(),typeof refreshStatus==='function'?refreshStatus():Promise.resolve()]);
      const meta=sessionCache.get(getSessionId());if(meta)setLocked(meta);
    }catch(e){
      if(e.name==='AbortError')return;const stream=activeStream;if(stream){renderRich(stream.body,stream.raw,{streaming:false});const err=document.createElement('div');err.className='stream-error';err.textContent='Ошибка: '+e.message;stream.box.appendChild(err)}activeStream=null;setStreamingUi(false);if(byId('chatState'))byId('chatState').textContent='Ошибка';
    }
  };

  function addArchiveToProfile(){
    const menu=byId('profileMenu');if(!menu||byId('profileArchivedChats'))return;const sep=document.createElement('div');sep.className='sep';const b=document.createElement('button');b.id='profileArchivedChats';b.textContent='Архивированные чаты';b.onclick=()=>{archivedMode=true;ensureArchiveToggle();const t=byId('sessionArchiveToggle');if(t)t.textContent='Назад';menu.classList.remove('open');loadSessions()};menu.insertBefore(sep,menu.lastElementChild);menu.insertBefore(b,menu.lastElementChild);
  }

  function boot(){
    installStyle();ensureArchiveToggle();ensureSessionMenu();addArchiveToProfile();loadSessions();
    document.addEventListener('click',e=>{if(!e.target.closest('#leshySessionMenu')&&!e.target.closest('.leshy-session-icon'))closeSessionMenu()});
    window.addEventListener('resize',closeSessionMenu);setInterval(addArchiveToProfile,1500);
    const sid=getSessionId();if(sid)loadHistory(sid);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
