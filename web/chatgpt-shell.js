(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const svg=p=>`<svg viewBox="0 0 24 24" aria-hidden="true">${p}</svg>`;
  const I={
    edit:svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/>'),
    search:svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
    share:svg('<path d="M12 16V3"/><path d="m7 8 5-5 5 5"/><path d="M5 12v7h14v-7"/>'),
    more:svg('<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'),
    panel:svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 4v16"/>'),
    plus:svg('<path d="M12 5v14M5 12h14"/>'),
    mic:svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4M9 21h6"/>'),
    send:svg('<path d="M12 19V5"/><path d="m7 10 5-5 5 5"/>'),
    stop:svg('<rect x="7" y="7" width="10" height="10" rx="1"/>')
  };

  function installCss(){
    if($('leshyChatGptShellStyle'))return;
    const s=document.createElement('style');s.id='leshyChatGptShellStyle';s.textContent=`
      :root{--cg-bg:#212121;--cg-side:#171717;--cg-hover:#2a2a2a;--cg-text:#ececec;--cg-muted:#a9a9a9;--cg-composer:#303030;--cg-line:#3a3a3a}
      html,body{background:var(--cg-bg)!important;color:var(--cg-text)!important}
      body.full .start{display:none!important}
      body.full .workspace{display:grid!important;grid-template-columns:260px minmax(0,1fr)!important;gap:0!important;background:var(--cg-bg)!important}
      body.full .sidebar{position:relative!important;display:flex!important;flex-direction:column!important;min-width:0!important;padding:0!important;background:var(--cg-side)!important;border:0!important;border-right:1px solid #242424!important}
      body.full .sidebar nav{display:none!important}
      body.full .brand{height:54px!important;display:flex!important;align-items:center!important;gap:9px!important;padding:8px 10px!important;font-size:15px!important;font-weight:650!important;background:var(--cg-side)!important}
      body.full .mini-core{width:28px!important;height:28px!important;flex:0 0 28px!important;box-shadow:none!important;background:radial-gradient(circle at 40% 35%,#e9fff2 0 7%,#73c795 8% 28%,#2e6c4b 52%,#173526 72%)!important}
      #sidebarNewChat{margin-left:auto;width:34px;height:34px;border:0;background:transparent;color:#ddd;border-radius:8px;display:grid;place-items:center;cursor:pointer}
      #sidebarNewChat:hover,#chatSidebarNew:hover,#profileMenuButton:hover,#profileMenuButton.open,#leshyTopActions button:hover,#leshyTopActions button.open{background:var(--cg-hover)!important}
      #sidebarNewChat svg,#chatSidebarNew svg,#leshyTopActions svg,#chatAttachButton svg,#chatMicButton svg,#chatSend svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      #chatSidebarTools{padding:2px 9px 8px}
      #chatSidebarNew{height:40px;width:100%;display:flex;align-items:center;gap:10px;border:0;background:transparent;color:#eee;border-radius:8px;padding:0 10px;cursor:pointer;text-align:left;font-size:13px}
      #chatHistorySearchWrap{position:relative;margin-top:4px}#chatHistorySearchWrap svg{position:absolute;left:10px;top:10px;width:16px;height:16px;fill:none;stroke:#909090;stroke-width:1.8}
      #chatHistorySearch{width:100%;height:36px;padding:0 10px 0 34px!important;border-radius:8px!important;background:#202020!important;border:1px solid transparent!important;color:#eee!important;font-size:12px!important}
      #chatHistorySearch:focus{border-color:#424242!important}
      #sidebarHistory{flex:1;min-height:0;overflow:auto;padding:3px 8px 8px}
      #sidebarHistoryTitle{padding:9px 8px 6px;color:#8f8f8f;font-size:11px;font-weight:600}
      #sidebarHistory #sessionList{display:grid;gap:2px}
      #sidebarHistory #sessionList .item,#sidebarHistory #sessionList .leshy-session-item{border:0!important;background:transparent!important;border-radius:8px!important;min-height:36px!important;padding:2px 3px!important;color:#e8e8e8!important}
      #sidebarHistory #sessionList .item:hover,#sidebarHistory #sessionList .leshy-session-item:hover{background:#242424!important}
      #sidebarHistory #sessionList .item-title,#sidebarHistory .leshy-session-title{font-size:12px!important;font-weight:400!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important}
      #sidebarHistory #sessionList .item-meta,#sidebarHistory #sessionList .item-body{display:none!important}
      body.full .sidebar-foot{margin-top:0!important;padding:7px 8px 9px!important;background:var(--cg-side)!important;border-top:1px solid #282828!important}
      body.full .sidebar-foot>.hint,body.full #minimalMode,body.full #showSessions,body.full #newChat{display:none!important}
      #profileMenuButton{width:100%;height:48px;display:flex;align-items:center;gap:10px;border:0;background:transparent;color:#eee;border-radius:9px;padding:5px 7px;cursor:pointer;text-align:left}
      #profileAvatar{width:32px;height:32px;border-radius:50%;display:grid;place-items:center;background:#2f6f4d;color:white;font-weight:700;font-size:13px;flex:0 0 32px}
      #profileCopy{min-width:0;flex:1;display:grid;gap:1px}#profileCopy b{font-size:12px}#profileCopy span{font-size:10px;color:#929292}
      #profileMenu{display:none;position:absolute;left:8px;right:8px;bottom:62px;z-index:1200;background:#2b2b2b;border:1px solid #444;border-radius:14px;padding:6px;box-shadow:0 18px 55px rgba(0,0,0,.5)}
      #profileMenu.open,#chatMoreMenu.open{display:block!important}
      #profileMenu button,#chatMoreMenu button{width:100%;height:39px;border:0;background:transparent;color:#eee;border-radius:8px;padding:0 10px;font-size:12px;cursor:pointer;text-align:left}
      #profileMenu button:hover,#chatMoreMenu button:hover{background:#3a3a3a}.sep{height:1px;background:#444;margin:5px 4px}
      body.full .main{background:var(--cg-bg)!important;min-width:0!important}
      body.full .main-head{height:54px!important;display:flex!important;align-items:center!important;padding:0 12px 0 18px!important;border:0!important;background:var(--cg-bg)!important;z-index:40!important}
      body.full .head-title{display:flex!important;align-items:center!important;gap:4px!important}body.full .head-title strong{font-size:16px!important;font-weight:650!important;color:#eee!important}body.full .head-title strong:after{content:'⌄';margin-left:6px;color:#a8a8a8;font-size:13px;vertical-align:2px}body.full .head-title span{display:none!important}
      body.full .head-actions{display:none!important}
      #leshyTopActions{margin-left:auto;display:flex;align-items:center;gap:4px}
      #leshyTopActions button{height:34px;border:0;background:transparent;color:#e5e5e5;border-radius:8px;display:flex;align-items:center;justify-content:center;gap:7px;padding:0 10px;cursor:pointer;font-size:12px}
      #leshyTopActions .icon{width:34px;padding:0}
      #chatMoreMenu{display:none;position:fixed;right:12px;top:49px;z-index:1300;width:230px;max-width:calc(100vw - 20px);background:#2b2b2b;border:1px solid #444;border-radius:14px;padding:6px;box-shadow:0 18px 55px rgba(0,0,0,.5)}
      body.full .context,body.full .context.open,body.full.leshy-context-open .context{display:none!important;visibility:hidden!important;pointer-events:none!important}
      body.full .pane{background:var(--cg-bg)!important;color:#ececec!important}
      body.full .pane:not(.chat-pane){padding:22px clamp(18px,5vw,64px)!important}
      body.full .card{background:#282828!important;border:1px solid #3c3c3c!important;box-shadow:none!important}
      body.full .field{background:#202020!important;border-color:#424242!important;color:#eee!important}
      #pane-chat{position:relative!important;background:var(--cg-bg)!important}
      body.full .messages{padding:30px max(20px,calc((100vw - 260px - 820px)/2)) 155px!important;scrollbar-gutter:stable!important}
      #messages .msg{max-width:780px!important;width:100%!important;margin-left:auto!important;margin-right:auto!important}
      #messages .msg.user{width:fit-content!important;max-width:min(72%,680px)!important;margin-left:auto!important;margin-right:max(0px,calc((100% - 780px)/2))!important;background:#303030!important;border:0!important;border-radius:20px!important;padding:10px 15px!important;color:#eee!important}
      #messages .msg.user .who{display:none!important}
      #messages .msg.assistant{background:transparent!important;border:0!important;padding:4px 0 18px!important;color:#eee!important}
      #messages .msg.assistant>.who{font-size:11px!important;text-transform:none!important;letter-spacing:0!important;color:#bdbdbd!important;margin-bottom:7px!important;font-weight:600!important}
      #messages .msg.assistant .chat-rich-text,#messages .msg.assistant p{font-size:15px!important;line-height:1.62!important}
      body.full .chat-compose{position:absolute!important;left:0!important;right:0!important;bottom:0!important;padding:10px max(20px,calc((100vw - 260px - 820px)/2)) 18px!important;border:0!important;background:linear-gradient(180deg,rgba(33,33,33,0),#212121 28%,#212121 100%)!important;z-index:30!important}
      body.full .chat-compose>.composer{position:relative!important;display:grid!important;grid-template-columns:36px minmax(0,1fr) auto auto!important;grid-template-rows:auto 36px!important;align-items:center!important;gap:5px!important;min-height:92px!important;border:1px solid #4a4a4a!important;background:var(--cg-composer)!important;border-radius:26px!important;padding:10px 10px 9px!important;box-shadow:none!important;max-width:820px!important;margin:0 auto!important}
      body.full .chat-compose>.composer:focus-within{border-color:#5d5d5d!important}
      #chatInput{grid-column:1/-1!important;grid-row:1!important;min-height:42px!important;max-height:160px!important;padding:8px 10px 4px!important;font-size:15px!important;line-height:1.45!important;color:#f2f2f2!important;background:transparent!important;border:0!important;outline:0!important;resize:none!important}#chatInput::placeholder{color:#aaa!important}
      #chatAttachButton,#chatMicButton,#chatSend{width:36px!important;height:36px!important;min-width:36px!important;padding:0!important;border-radius:50%!important;border:0!important;display:grid!important;place-items:center!important}
      #chatAttachButton{grid-column:1!important;grid-row:2!important;background:transparent!important;color:#ddd!important;border:1px solid #555!important}#chatMicButton{grid-column:3!important;grid-row:2!important;background:transparent!important;color:#e0e0e0!important}#chatSend{grid-column:4!important;grid-row:2!important;background:#f4f4f4!important;color:#171717!important}#chatAttachButton:hover,#chatMicButton:hover{background:#3b3b3b!important}#chatSend:hover{background:white!important}
      #chatComposerHint{max-width:820px;margin:7px auto 0;text-align:center;font-size:10px;color:#777}#chatUploadInput{display:none!important}
      #chatVoiceState{position:absolute;right:55px;bottom:52px;padding:5px 8px;border-radius:8px;background:#232323;border:1px solid #393939;color:#cfcfcf;font-size:10px;display:none;max-width:320px}#chatVoiceState.show{display:block}
      #shellToast{position:fixed;left:50%;bottom:28px;z-index:1500;transform:translate(-50%,12px);opacity:0;pointer-events:none;background:#2b2b2b;border:1px solid #444;border-radius:10px;padding:9px 13px;color:#eee;font-size:12px;transition:.18s}#shellToast.show{opacity:1;transform:translate(-50%,0)}
      @media(max-width:820px){body.full .workspace{grid-template-columns:1fr!important}body.full .sidebar{position:fixed!important;left:0!important;top:0!important;bottom:0!important;width:min(290px,88vw)!important;z-index:1400!important;transform:translateX(-102%);transition:.18s;box-shadow:16px 0 48px rgba(0,0,0,.42)}body.full .sidebar.mobile-open{transform:translateX(0)}#topSidebarToggle{display:flex!important}.shareText{display:none!important}body.full .messages{padding:20px 14px 145px!important}body.full .chat-compose{padding:8px 10px 14px!important}#messages .msg.user{max-width:86%!important;margin-right:0!important}}
      @media(min-width:821px){#topSidebarToggle{display:none!important}}
    `;document.head.appendChild(s);
  }

  function toast(t){let n=$('shellToast');if(!n){n=document.createElement('div');n.id='shellToast';document.body.appendChild(n)}n.textContent=t;n.classList.add('show');clearTimeout(n._t);n._t=setTimeout(()=>n.classList.remove('show'),1800)}
  function section(id){
    if(typeof window.showSection==='function')window.showSection(id);else document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('active',p.id==='pane-'+id));
    $('profileMenu')?.classList.remove('open');$('profileMenuButton')?.classList.remove('open');document.querySelector('.sidebar')?.classList.remove('mobile-open');
  }
  function fresh(){if(typeof window.newChat==='function')window.newChat();else{localStorage.removeItem('leshy.session');$('messages')?.replaceChildren();section('chat')}const u=new URL(location.href);u.searchParams.delete('session');history.replaceState({},'',u);setTimeout(()=>window.loadSessions&&window.loadSessions(),50)}

  function buildSidebar(){
    const side=document.querySelector('.sidebar');if(!side)return;
    const brand=side.querySelector('.brand');
    if(brand&&!$('sidebarNewChat')){const b=document.createElement('button');b.id='sidebarNewChat';b.type='button';b.title='Новый чат';b.innerHTML=I.edit;b.onclick=fresh;brand.appendChild(b)}
    if(!$('chatSidebarTools')){
      const tools=document.createElement('div');tools.id='chatSidebarTools';tools.innerHTML=`<button id="chatSidebarNew" type="button">${I.edit}<span>Новый чат</span></button><div id="chatHistorySearchWrap">${I.search}<input id="chatHistorySearch" placeholder="Поиск чатов"></div>`;
      (side.querySelector('nav')||brand).insertAdjacentElement('afterend',tools);$('chatSidebarNew').onclick=fresh;
    }
    if(!$('sidebarHistory')){
      const wrap=document.createElement('div');wrap.id='sidebarHistory';wrap.innerHTML='<div id="sidebarHistoryTitle">Чаты</div>';
      let list=$('sessionList');if(list){const old=list.closest('.card');wrap.appendChild(list);if(old)old.style.display='none'}else{list=document.createElement('div');list.id='sessionList';list.className='list';wrap.appendChild(list)}
      const foot=side.querySelector('.sidebar-foot');if(foot)side.insertBefore(wrap,foot);else side.appendChild(wrap);
      const search=$('chatHistorySearch');if(search)search.oninput=e=>{const q=e.target.value.trim().toLowerCase();list.querySelectorAll('.item,.leshy-session-item').forEach(i=>i.style.display=!q||i.textContent.toLowerCase().includes(q)?'':'none')};
    }
    buildProfile(side);
    window.loadSessions&&window.loadSessions();
  }

  function menuButton(label,fn){const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=fn;return b}
  function buildProfile(side){
    if($('profileMenuButton'))return;let foot=side.querySelector('.sidebar-foot');if(!foot){foot=document.createElement('div');foot.className='sidebar-foot';side.appendChild(foot)}foot.replaceChildren();
    const btn=document.createElement('button');btn.id='profileMenuButton';btn.innerHTML='<span id="profileAvatar">Л</span><span id="profileCopy"><b>Леший</b><span>Меню и настройки</span></span>';
    const menu=document.createElement('div');menu.id='profileMenu';
    for(const [id,label] of [['overview','Обзор'],['knowledge','Знания'],['learning','Обучение'],['tasks','Задачи'],['tools','Инструменты'],['system','Система'],['logs','Логи']])menu.appendChild(menuButton(label,()=>section(id)));
    const sep=document.createElement('div');sep.className='sep';menu.appendChild(sep);
    menu.appendChild(menuButton('Marketplace / Магазин',()=>{section('settings');setTimeout(()=>$('marketplaceCard')?.scrollIntoView({behavior:'smooth',block:'start'}),80)}));
    menu.appendChild(menuButton('Настройки',()=>section('settings')));
    foot.append(btn,menu);btn.onclick=e=>{e.stopPropagation();menu.classList.toggle('open');btn.classList.toggle('open',menu.classList.contains('open'))};
  }

  function buildTop(){
    const head=document.querySelector('.main-head');if(!head||$('leshyTopActions'))return;
    const actions=document.createElement('div');actions.id='leshyTopActions';
    const mob=document.createElement('button');mob.id='topSidebarToggle';mob.className='icon';mob.title='История чатов';mob.innerHTML=I.panel;mob.onclick=()=>document.querySelector('.sidebar')?.classList.toggle('mobile-open');
    const share=document.createElement('button');share.innerHTML=I.share+'<span class="shareText">Поделиться</span>';share.onclick=shareChat;
    const more=document.createElement('button');more.id='chatMoreButton';more.className='icon';more.title='Действия';more.innerHTML=I.more;
    actions.append(mob,share,more);head.appendChild(actions);
    const menu=document.createElement('div');menu.id='chatMoreMenu';
    menu.appendChild(menuButton('Состояние Лешего',()=>section('system')));
    menu.appendChild(menuButton('Обзор',()=>section('overview')));
    menu.appendChild(menuButton('Копировать текст диалога',copyChat));
    menu.appendChild(menuButton('Новый чат',fresh));document.body.appendChild(menu);
    more.onclick=e=>{e.stopPropagation();menu.classList.toggle('open');more.classList.toggle('open',menu.classList.contains('open'));if(menu.classList.contains('open')){const r=more.getBoundingClientRect();menu.style.left=Math.max(10,Math.min(innerWidth-menu.offsetWidth-10,r.right-menu.offsetWidth))+'px';menu.style.top=(r.bottom+7)+'px'}};
  }

  async function shareChat(){const sid=localStorage.getItem('leshy.session')||'';if(!sid){toast('Сначала начните диалог');return}const u=new URL(location.href);u.searchParams.set('session',sid);try{if(navigator.share)await navigator.share({title:'Диалог с Лешим',url:u.toString()});else{await navigator.clipboard.writeText(u.toString());toast('Ссылка на диалог скопирована')}}catch(e){if(e.name!=='AbortError')toast('Не удалось поделиться')}}
  async function copyChat(){const text=[...document.querySelectorAll('#messages .msg')].map(n=>n.innerText.trim()).join('\n\n');if(!text){toast('Диалог пуст');return}try{await navigator.clipboard.writeText(text);toast('Диалог скопирован')}catch(_e){toast('Буфер обмена недоступен')}}

  function languageFor(name){const ext=(name.split('.').pop()||'').toLowerCase();return({py:'python',js:'javascript',ts:'typescript',cs:'csharp',cpp:'cpp',c:'c',h:'c',java:'java',go:'go',rs:'rust',sh:'bash',ps1:'powershell',json:'json',xml:'xml',yml:'yaml',yaml:'yaml',md:'markdown',sql:'sql',html:'html',css:'css'}[ext]||'text')}
  async function attachFiles(files,input){const allowed=/\.(txt|md|csv|json|log|py|js|ts|cs|cpp|c|h|java|go|rs|sh|ps1|yaml|yml|xml|sql|html|css)$/i;const parts=[];for(const file of files){if(!allowed.test(file.name)){toast('Пока поддерживаются текстовые и кодовые файлы');continue}if(file.size>2*1024*1024){toast('Файл '+file.name+' больше 2 МБ');continue}try{const text=await file.text();parts.push('Файл: '+file.name+'\n```'+languageFor(file.name)+'\n'+text+'\n```')}catch(_e){toast('Не удалось прочитать '+file.name)}}if(!parts.length)return;const prefix=input.value.trim();input.value=(prefix+(prefix?'\n\n':'')+parts.join('\n\n')).trimStart();input.focus();toast('Файл добавлен в сообщение')}
  function buildComposer(){
    const composer=document.querySelector('#pane-chat .chat-compose>.composer');if(!composer||$('chatAttachButton'))return;const input=$('chatInput'),send=$('chatSend');if(!input||!send)return;
    const attach=document.createElement('button');attach.id='chatAttachButton';attach.type='button';attach.title='Прикрепить файл';attach.innerHTML=I.plus;
    const mic=document.createElement('button');mic.id='chatMicButton';mic.type='button';mic.title='Голос';mic.innerHTML=I.mic;
    const upload=document.createElement('input');upload.id='chatUploadInput';upload.type='file';upload.multiple=true;upload.accept='.txt,.md,.csv,.json,.log,.py,.js,.ts,.cs,.cpp,.c,.h,.java,.go,.rs,.sh,.ps1,.yaml,.yml,.xml,.sql,.html,.css,text/*';
    const voice=document.createElement('div');voice.id='chatVoiceState';composer.insertBefore(attach,input);composer.insertBefore(mic,send);composer.append(upload,voice);send.innerHTML=I.send;send.title='Отправить';
    attach.onclick=()=>upload.click();upload.onchange=async()=>{await attachFiles([...upload.files||[]],input);upload.value=''};
    if(!$('chatComposerHint')){const hint=document.createElement('div');hint.id='chatComposerHint';hint.textContent='Леший может ошибаться. Важную информацию лучше перепроверять.';composer.parentElement.appendChild(hint)}
  }
  function updateSend(){const send=$('chatSend');if(!send)return;const streaming=send.classList.contains('streaming');send.innerHTML=streaming?I.stop:I.send;send.title=streaming?'Остановить ответ':'Отправить'}
  function observeSend(){const send=$('chatSend');if(!send||send.dataset.cgObserved==='1')return;send.dataset.cgObserved='1';new MutationObserver(updateSend).observe(send,{attributes:true,attributeFilter:['class']});updateSend()}

  function closeMenus(){document.addEventListener('click',e=>{if(!e.target.closest('#profileMenuButton')&&!e.target.closest('#profileMenu')){$('profileMenu')?.classList.remove('open');$('profileMenuButton')?.classList.remove('open')}if(!e.target.closest('#chatMoreButton')&&!e.target.closest('#chatMoreMenu')){$('chatMoreMenu')?.classList.remove('open');$('chatMoreButton')?.classList.remove('open')}})}
  function restoreShared(){const sid=new URL(location.href).searchParams.get('session');if(sid)setTimeout(()=>window.loadHistory&&window.loadHistory(sid),120)}
  function forceModernMode(){document.body.classList.add('full');document.body.classList.remove('leshy-context-open');const context=$('contextPanel')||document.querySelector('.context');if(context){context.classList.remove('open');context.style.setProperty('display','none','important')}if(!document.querySelector('.pane.active'))section('chat')}

  function boot(){installCss();forceModernMode();buildSidebar();buildTop();buildComposer();observeSend();closeMenus();restoreShared();if(!new URL(location.href).searchParams.get('session'))section('chat');const o=new MutationObserver(()=>{forceModernMode();buildSidebar();buildTop();buildComposer();observeSend()});o.observe(document.body,{childList:true,subtree:true});setInterval(()=>window.loadSessions&&window.loadSessions(),7000)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
