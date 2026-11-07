(function(){
  'use strict';

  const $=id=>document.getElementById(id);
  const OWNER='web:local';
  const svg=path=>`<svg viewBox="0 0 24 24" aria-hidden="true">${path}</svg>`;
  const ICONS={
    copy:svg('<rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
    share:svg('<path d="M12 16V3"/><path d="m7 8 5-5 5 5"/><path d="M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6"/>'),
    edit:svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/>'),
    up:svg('<path d="M7 10v11H3V10h4Z"/><path d="M7 19h9.2a2 2 0 0 0 1.9-1.4l2.2-7A2 2 0 0 0 18.4 8H14l.7-3.1A2.4 2.4 0 0 0 10.2 3L7 10Z"/>'),
    down:svg('<path d="M7 14V3H3v11h4Z"/><path d="M7 5h9.2a2 2 0 0 1 1.9 1.4l2.2 7a2 2 0 0 1-1.9 2.6H14l.7 3.1a2.4 2.4 0 0 1-4.5 1.9L7 14Z"/>'),
    speaker:svg('<path d="M11 5 6.5 9H3v6h3.5L11 19V5Z"/><path d="M15 9.5a4 4 0 0 1 0 5"/><path d="M17.5 7a7.5 7.5 0 0 1 0 10"/>'),
    retry:svg('<path d="M20 6v5h-5"/><path d="M18.2 16.2A8 8 0 1 1 20 11"/>'),
    more:svg('<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'),
    branch:svg('<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="12" r="2"/><path d="M8 5h2a3 3 0 0 1 3 3v1a3 3 0 0 0 3 3M8 19h2a3 3 0 0 0 3-3v-1a3 3 0 0 1 3-3"/>')
  };
  let speakingAudio=null;
  let speakingUrl='';
  let speakingButton=null;
  const activityStates=new Map();
  let activityTimer=0;

  function toast(text){
    let n=$('shellToast');
    if(!n){n=document.createElement('div');n.id='shellToast';document.body.appendChild(n)}
    n.textContent=text;n.classList.add('show');clearTimeout(n._t);n._t=setTimeout(()=>n.classList.remove('show'),2000);
  }

  async function api(path,body){
    const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
    const d=await r.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!r.ok)throw new Error(d.error||('HTTP '+r.status));
    return d;
  }

  function currentSession(){return localStorage.getItem('leshy.session')||''}

  function cleanText(node){
    if(!node)return '';
    const clone=node.cloneNode(true);
    clone.querySelectorAll('.who,.sources,.leshy-message-actions,.leshy-message-more,.leshy-message-time,.leshy-stream-caret,.leshy-live-activity,button').forEach(x=>x.remove());
    const body=clone.querySelector('.chat-rich-body,.leshy-stream-plain')||clone;
    return String(body.innerText||body.textContent||'').replace(/\u00a0/g,' ').trim();
  }

  function messageInfo(node){
    return {role:node.classList.contains('user')?'user':'assistant',content:cleanText(node)};
  }

  function stableMessages(){
    const root=$('messages');if(!root)return [];
    return [...root.querySelectorAll(':scope > .msg')].filter(n=>!n.classList.contains('leshy-background-stream')&&!n.classList.contains('leshy-streaming'));
  }

  function messagePayload(nodes){
    return nodes.map(messageInfo).filter(m=>m.content&&['user','assistant'].includes(m.role));
  }

  async function createBranch(messages,title='Ветка разговора'){
    // До изменения ветки завершаем активный поток исходного чата.
    // Иначе поздний "start"/"done" старого ответа меняет уже новый диалог.
    if(typeof window.stopCurrentLeshyStream==='function'){
      await window.stopCurrentLeshyStream();
    }
    const d=await api('/v1/chat/branch',{owner_id:OWNER,source_session_id:currentSession(),title,messages:messagePayload(messages)});
    const sid=String(d.session_id||'');if(!sid)throw new Error('Сервер не вернул ID ветки');
    localStorage.setItem('leshy.session',sid);
    try{window.sessionId=sid}catch(_e){}
    if(typeof window.loadHistory==='function')await window.loadHistory(sid);
    return sid;
  }

  async function copyText(text){
    try{await navigator.clipboard.writeText(text);toast('Скопировано')}catch(_e){toast('Буфер обмена недоступен')}
  }

  async function shareText(text){
    try{
      if(navigator.share)await navigator.share({title:'Леший',text});
      else{await navigator.clipboard.writeText(text);toast('Текст скопирован для отправки')}
    }catch(e){if(e&&e.name!=='AbortError')toast('Не удалось поделиться')}
  }

  async function editUser(node){
    const nodes=stableMessages(),idx=nodes.indexOf(node);if(idx<0)return;
    const text=cleanText(node);if(!text)return;
    try{
      await createBranch(nodes.slice(0,idx),'Редактирование сообщения');
      const input=$('chatInput');if(input){input.value=text;input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();input.setSelectionRange?.(text.length,text.length)}
      toast('Измените сообщение и отправьте его заново');
    }catch(e){toast('Не удалось начать редактирование: '+e.message)}
  }

  async function branchAt(node){
    const nodes=stableMessages(),idx=nodes.indexOf(node);if(idx<0)return;
    try{await createBranch(nodes.slice(0,idx+1),'Ветка разговора');toast('Ветка разговора создана')}catch(e){toast('Не удалось создать ветку: '+e.message)}
  }

  async function regenerate(node){
    const nodes=stableMessages(),idx=nodes.indexOf(node);if(idx<0)return;
    let userIndex=-1;
    for(let i=idx-1;i>=0;i--){if(nodes[i].classList.contains('user')){userIndex=i;break}}
    if(userIndex<0){toast('Не найден исходный вопрос');return}
    const prompt=cleanText(nodes[userIndex]);if(!prompt)return;
    try{
      await createBranch(nodes.slice(0,userIndex),'Новый вариант ответа');
      const input=$('chatInput');if(!input)throw new Error('Поле ввода недоступно');
      input.value=prompt;input.dispatchEvent(new Event('input',{bubbles:true}));
      if(typeof window.sendFrom==='function')await window.sendFrom(input);else $('chatSend')?.click();
    }catch(e){toast('Не удалось повторить ответ: '+e.message)}
  }

  function stopSpeaking(){
    const a=speakingAudio;speakingAudio=null;
    if(a){try{a.pause();a.src=''}catch(_e){}}
    if(speakingUrl){try{URL.revokeObjectURL(speakingUrl)}catch(_e){}speakingUrl=''}
    if(speakingButton){speakingButton.classList.remove('active');speakingButton=null}
  }

  async function speak(node,button){
    if(speakingAudio){stopSpeaking();if(button===speakingButton)return}
    const text=cleanText(node);if(!text)return;
    try{
      button.classList.add('active');speakingButton=button;
      const r=await fetch('/v1/voice/speak',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
      if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.error||('HTTP '+r.status))}
      const blob=await r.blob(),url=URL.createObjectURL(blob),audio=new Audio(url);speakingUrl=url;speakingAudio=audio;
      audio.onended=stopSpeaking;audio.onerror=stopSpeaking;await audio.play();
    }catch(e){stopSpeaking();toast('Озвучка недоступна: '+e.message)}
  }

  function feedback(node,kind,button,other){
    const active=button.classList.toggle('active');if(other)other.classList.remove('active');
    const key='leshy.feedback.'+currentSession()+'.'+stableMessages().indexOf(node);
    if(active)localStorage.setItem(key,kind);else localStorage.removeItem(key);
    api('/v1/chat/feedback',{owner_id:OWNER,session_id:currentSession(),kind:active?kind:'clear',role:'assistant',content:cleanText(node).slice(0,1000)}).catch(()=>{});
  }

  function button(icon,label,fn){
    const b=document.createElement('button');b.type='button';b.className='leshy-message-action';b.innerHTML=icon;b.dataset.tip=label;b.setAttribute('aria-label',label);b.title=label;b.onclick=e=>{e.preventDefault();e.stopPropagation();fn(b,e)};return b;
  }

  function closeMenus(except){document.querySelectorAll('.leshy-message-more.open').forEach(m=>{if(m!==except)m.classList.remove('open')})}

  function addMore(node,row){
    const wrap=document.createElement('span');wrap.className='leshy-more-wrap';
    const menu=document.createElement('div');menu.className='leshy-message-more';
    const item=document.createElement('button');item.type='button';item.innerHTML=ICONS.branch+'<span>Создать ветку разговора</span>';item.onclick=e=>{e.stopPropagation();menu.classList.remove('open');branchAt(node)};menu.appendChild(item);
    const more=button(ICONS.more,'Ещё',()=>{const opening=!menu.classList.contains('open');closeMenus();menu.classList.toggle('open',opening)});
    wrap.append(more,menu);row.appendChild(wrap);
  }

  function decorate(node){
    if(!node||node.dataset.leshyActions==='1'||node.classList.contains('leshy-background-stream'))return;
    const role=node.classList.contains('user')?'user':node.classList.contains('assistant')?'assistant':'';if(!role)return;
    node.dataset.leshyActions='1';
    const row=document.createElement('div');row.className='leshy-message-actions '+role;
    const get=()=>cleanText(node);
    if(role==='user'){
      row.append(
        button(ICONS.copy,'Копировать',()=>copyText(get())),
        button(ICONS.share,'Поделиться',()=>shareText(get())),
        button(ICONS.edit,'Редактировать сообщение',()=>editUser(node))
      );
    }else{
      const copy=button(ICONS.copy,'Копировать',()=>copyText(get()));
      let up,down;
      up=button(ICONS.up,'Хороший ответ',b=>feedback(node,'up',b,down));
      down=button(ICONS.down,'Плохой ответ',b=>feedback(node,'down',b,up));
      const key='leshy.feedback.'+currentSession()+'.'+stableMessages().indexOf(node),saved=localStorage.getItem(key);if(saved==='up')up.classList.add('active');if(saved==='down')down.classList.add('active');
      row.append(copy,up,down,button(ICONS.speaker,'Прочитать вслух',b=>speak(node,b)),button(ICONS.share,'Поделиться',()=>shareText(get())),button(ICONS.retry,'Повторить ответ',()=>regenerate(node)));
      addMore(node,row);
    }
    node.appendChild(row);
  }

  function decorateAll(){stableMessages().forEach(decorate)}

  function activityKey(detail){return String(detail&&detail.session_id||currentSession()||'pending')}
  function activeStreamingBox(){
    const root=$('messages');if(!root)return null;
    const boxes=[...root.querySelectorAll(':scope > .msg.assistant.leshy-background-stream')];
    return boxes.length?boxes[boxes.length-1]:null;
  }
  function ensureActivityPanel(box){
    if(!box)return null;
    let panel=box.querySelector(':scope > .leshy-live-activity');
    if(panel)return panel;
    panel=document.createElement('div');panel.className='leshy-live-activity';
    panel.innerHTML='<div class="leshy-live-activity-head"><span class="leshy-live-spinner" aria-hidden="true"></span><span class="leshy-live-stage"></span><span class="leshy-live-time"></span></div><div class="leshy-live-reason"></div><div class="leshy-live-sources"></div><div class="leshy-live-warning"></div>';
    const body=box.querySelector(':scope > .leshy-stream-plain');
    if(body)box.insertBefore(panel,body);else box.appendChild(panel);
    return panel;
  }
  function sourceTitle(item){
    const title=String(item&&item.title||'').trim();if(title)return title;
    try{return new URL(String(item&&item.url||'')).hostname||'Источник'}catch(_e){return 'Источник'}
  }
  function renderActivityState(state){
    const panel=state&&state.panel;if(!panel||!panel.isConnected)return;
    const now=Date.now(),elapsed=Math.max(0,Math.floor((now-state.startedAt)/1000)),stale=Math.max(0,Math.floor((now-state.updatedAt)/1000));
    const stage=panel.querySelector('.leshy-live-stage'),time=panel.querySelector('.leshy-live-time'),reason=panel.querySelector('.leshy-live-reason'),sources=panel.querySelector('.leshy-live-sources'),warning=panel.querySelector('.leshy-live-warning');
    if(stage)stage.textContent=state.text||'Леший работает…';
    if(time)time.textContent=elapsed+' с';
    if(reason){reason.textContent=state.reason?String(state.reason):'';reason.style.display=state.reason?'block':'none'}
    if(sources){
      sources.textContent='';
      const items=Array.isArray(state.sources)?state.sources.slice(0,5):[];
      if(items.length){
        const label=document.createElement('div');label.className='leshy-live-sources-label';label.textContent='Найденные источники:';sources.appendChild(label);
        items.forEach((item,index)=>{
          const row=document.createElement('a');row.className='leshy-live-source';row.textContent=(index+1)+'. '+sourceTitle(item);const url=String(item&&item.url||'').trim();
          if(url){row.href=url;row.target='_blank';row.rel='noopener noreferrer'}else row.removeAttribute('href');sources.appendChild(row);
        });
      }
    }
    if(warning){
      if(stale>=30){warning.textContent='Нет нового этапа '+stale+' с. Возможно, внешний источник или обработка отвечают медленно; запрос всё ещё открыт.';warning.classList.add('visible','strong')}
      else if(stale>=12){warning.textContent='Этап занимает дольше обычного · нового статуса '+stale+' с.';warning.classList.add('visible');warning.classList.remove('strong')}
      else{warning.textContent='';warning.classList.remove('visible','strong')}
    }
  }
  function onActivity(detail){
    detail=detail||{};const key=activityKey(detail),box=activeStreamingBox();if(!box)return;
    let state=activityStates.get(key);
    if(!state){state={startedAt:Date.now(),updatedAt:Date.now(),text:'Леший работает…',reason:'',sources:[],panel:null};activityStates.set(key,state)}
    state.updatedAt=Date.now();state.text=String(detail.text||state.text||'Леший работает…');state.reason=String(detail.reason||'');
    if(Array.isArray(detail.sources))state.sources=detail.sources;
    state.panel=ensureActivityPanel(box);state.panel.style.display='block';renderActivityState(state);
  }
  function hideActivity(detail){
    const key=activityKey(detail||{}),state=activityStates.get(key);
    if(state&&state.panel)state.panel.style.display='none';
  }
  function finishActivity(detail){
    const key=activityKey(detail||{}),state=activityStates.get(key);
    if(state&&state.panel)state.panel.style.display='none';activityStates.delete(key);
    if(key!=='pending'&&activityStates.has('pending')){const pending=activityStates.get('pending');if(pending&&pending.panel)pending.panel.style.display='none';activityStates.delete('pending')}
  }
  function refreshActivities(){activityStates.forEach(state=>renderActivityState(state))}

  function css(){
    if($('leshyMessageActionsStyle'))return;
    const s=document.createElement('style');s.id='leshyMessageActionsStyle';s.textContent=`
      #messages .msg{position:relative}
      .leshy-message-actions{display:flex;align-items:center;gap:1px;margin-top:6px;min-height:28px;color:#a7a7a7}
      .leshy-message-actions.user{justify-content:flex-end;opacity:0;transition:opacity .12s}
      #messages .msg.user:hover>.leshy-message-actions.user,#messages .msg.user:focus-within>.leshy-message-actions.user{opacity:1}
      .leshy-message-action{position:relative;width:30px;height:30px;border:0;border-radius:8px;background:transparent;color:#a9a9a9;padding:6px;display:grid;place-items:center;cursor:pointer}
      .leshy-message-action:hover,.leshy-message-action.active{background:#2f2f2f;color:#f1f1f1}
      .leshy-message-action svg,.leshy-message-more svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.65;stroke-linecap:round;stroke-linejoin:round}
      .leshy-message-action[data-tip]:hover:after{content:attr(data-tip);position:absolute;left:50%;bottom:36px;transform:translateX(-50%);background:#343434;color:#eee;border:1px solid #484848;padding:5px 8px;border-radius:7px;font-size:11px;white-space:nowrap;z-index:1600;pointer-events:none;box-shadow:0 8px 24px rgba(0,0,0,.28)}
      .leshy-more-wrap{position:relative;display:inline-flex}
      .leshy-message-more{display:none;position:absolute;left:0;bottom:34px;z-index:1550;min-width:230px;background:#303030;border:1px solid #4a4a4a;border-radius:12px;padding:6px;box-shadow:0 16px 48px rgba(0,0,0,.45)}
      .leshy-message-more.open{display:block}
      .leshy-message-more button{width:100%;height:38px;display:flex;align-items:center;gap:10px;border:0;border-radius:8px;background:transparent;color:#eee;padding:0 9px;cursor:pointer;text-align:left}
      .leshy-message-more button:hover{background:#3c3c3c}.leshy-message-more button svg{flex:0 0 17px}
      .leshy-live-activity{max-width:700px;margin:7px 0 9px;padding:9px 11px;border:1px solid rgba(142,184,220,.18);border-radius:11px;background:rgba(142,184,220,.055);color:#cbd8e3;font-size:12.5px;line-height:1.38}
      .leshy-live-activity-head{display:flex;align-items:center;gap:8px;min-height:20px}.leshy-live-stage{font-weight:600;color:#dce8f1}.leshy-live-time{margin-left:auto;color:#758b9d;font-variant-numeric:tabular-nums}
      .leshy-live-spinner{width:12px;height:12px;border:1.5px solid rgba(158,194,224,.28);border-top-color:#8eb8dc;border-radius:50%;animation:leshyActivitySpin .85s linear infinite;flex:0 0 12px}@keyframes leshyActivitySpin{to{transform:rotate(360deg)}}
      .leshy-live-reason{display:none;margin:3px 0 0 20px;color:#8196a8}.leshy-live-sources{margin:7px 0 0 20px;display:grid;gap:3px}.leshy-live-sources:empty{display:none}.leshy-live-sources-label{color:#8fa4b5;margin-bottom:1px}
      .leshy-live-source{display:block;max-width:640px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#a8c9e5;text-decoration:none}.leshy-live-source:hover{text-decoration:underline;color:#c8e2f7}
      .leshy-live-warning{display:none;margin:7px 0 0 20px;color:#c7aa72}.leshy-live-warning.visible{display:block}.leshy-live-warning.strong{color:#e4ad76}
      @media(max-width:720px){.leshy-message-actions.user{opacity:1}.leshy-message-action{width:32px;height:32px}.leshy-live-activity{max-width:100%;margin-right:4px}.leshy-live-source{max-width:78vw}}
    `;document.head.appendChild(s);
  }

  function boot(){
    css();decorateAll();
    const root=$('messages');if(root){const observer=new MutationObserver(()=>decorateAll());observer.observe(root,{childList:true,subtree:false})}
    document.addEventListener('click',e=>{if(!e.target.closest('.leshy-more-wrap'))closeMenus()});
    window.addEventListener('leshy:session-changed',()=>setTimeout(decorateAll,0));
    window.addEventListener('leshy:activity',e=>onActivity(e.detail||{}));
    window.addEventListener('leshy:delta',e=>hideActivity(e.detail||{}));
    window.addEventListener('leshy:done',e=>finishActivity(e.detail||{}));
    activityTimer=window.setInterval(refreshActivities,1000);
    window.addEventListener('beforeunload',()=>{stopSpeaking();if(activityTimer)clearInterval(activityTimer)});
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
