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
    clone.querySelectorAll('.who,.sources,.leshy-message-actions,.leshy-message-more,.leshy-stream-caret,button').forEach(x=>x.remove());
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
      @media(max-width:720px){.leshy-message-actions.user{opacity:1}.leshy-message-action{width:32px;height:32px}}
    `;document.head.appendChild(s);
  }

  function boot(){
    css();decorateAll();
    const root=$('messages');if(root){const observer=new MutationObserver(()=>decorateAll());observer.observe(root,{childList:true,subtree:false})}
    document.addEventListener('click',e=>{if(!e.target.closest('.leshy-more-wrap'))closeMenus()});
    window.addEventListener('leshy:session-changed',()=>setTimeout(decorateAll,0));
    window.addEventListener('beforeunload',stopSpeaking);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
