(function(){
  'use strict';

  const $=id=>document.getElementById(id);
  const icon=(paths)=>`<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
  const icons={
    newChat:icon('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/>'),
    search:icon('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
    share:icon('<path d="M12 16V3"/><path d="m7 8 5-5 5 5"/><path d="M5 12v7h14v-7"/>'),
    more:icon('<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'),
    chevron:icon('<path d="m9 18 6-6-6-6"/>'),
    panel:icon('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>')
  };

  function ensureStyle(){
    if($('chatShellStyle')) return;
    const style=document.createElement('style');
    style.id='chatShellStyle';
    style.textContent=`
      body.full .workspace{grid-template-columns:272px minmax(0,1fr)!important;background:#1b222b!important}
      body.full .sidebar{position:relative!important;display:flex!important;flex-direction:column!important;min-width:0!important;background:#11151b!important;border-right:1px solid #252b33!important}
      body.full .sidebar nav{display:none!important}
      body.full .brand{height:56px!important;background:#11151b!important;border-bottom:0!important;padding:8px 10px 4px!important;font-size:15px!important}
      body.full .brand .mini-core{border-radius:50%!important;width:28px!important;height:28px!important;flex-basis:28px!important}
      body.full .brand .mini-core:after{display:none!important}
      #sidebarNewChat{margin-left:auto;width:34px;height:34px;border:0;background:transparent;color:#d8dee7;border-radius:9px;display:grid;place-items:center;cursor:pointer}
      #sidebarNewChat:hover{background:#222831}
      #sidebarNewChat svg,.chat-shell-icon svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      #chatSidebarTools{padding:4px 10px 8px}
      #chatSidebarNew{height:40px;width:100%;display:flex;align-items:center;gap:10px;border:0;background:transparent;color:#e7ebf0;border-radius:9px;padding:0 10px;cursor:pointer;text-align:left}
      #chatSidebarNew:hover{background:#222831}
      #chatSidebarNew svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      #chatHistorySearchWrap{position:relative;margin-top:4px}
      #chatHistorySearchWrap svg{position:absolute;left:10px;top:10px;width:16px;height:16px;fill:none;stroke:#8d96a3;stroke-width:1.8}
      #chatHistorySearch{width:100%;height:36px;padding:0 10px 0 34px!important;border-radius:9px!important;background:#171b22!important;border:1px solid transparent!important;font-size:12px!important}
      #chatHistorySearch:focus{border-color:#343b46!important}
      #sidebarHistory{flex:1;min-height:0;overflow:auto;padding:5px 8px 10px}
      #sidebarHistoryTitle{padding:8px 8px 5px;color:#8b94a1;font-size:11px;font-weight:600}
      #sidebarHistory #sessionList{display:grid;gap:2px}
      #sidebarHistory #sessionList .item{border:0!important;background:transparent!important;border-radius:8px!important;padding:9px 10px!important;cursor:pointer!important;overflow:hidden}
      #sidebarHistory #sessionList .item:hover{background:#222831!important}
      #sidebarHistory #sessionList .item-title{font-size:12px!important;font-weight:450!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      #sidebarHistory #sessionList .item-meta,#sidebarHistory #sessionList .item-body{display:none!important}
      body.full .sidebar-foot{margin-top:0!important;padding:7px 8px 9px!important;background:#11151b!important;border-top:1px solid #252b33!important}
      body.full .sidebar-foot>.hint,body.full #minimalMode{display:none!important}
      #profileMenuButton{width:100%;height:48px;display:flex;align-items:center;gap:10px;border:0;background:transparent;color:#e7ebf0;border-radius:10px;padding:5px 7px;cursor:pointer;text-align:left}
      #profileMenuButton:hover,#profileMenuButton.open{background:#222831}
      #profileAvatar{width:32px;height:32px;border-radius:50%;display:grid;place-items:center;background:linear-gradient(145deg,#356ca4,#4e9ee8);color:white;font-weight:700;font-size:13px;flex:0 0 32px}
      #profileMenuButton .profile-copy{min-width:0;flex:1;display:grid;gap:1px}
      #profileMenuButton .profile-name{font-size:12px;font-weight:600}
      #profileMenuButton .profile-sub{font-size:10px;color:#8e97a4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      #profileMenuButton .profile-chevron svg{width:16px;height:16px;fill:none;stroke:#8d96a3;stroke-width:1.8}
      #profileMenu{display:none;position:absolute;left:8px;right:8px;bottom:62px;z-index:200;background:#20242b;border:1px solid #343a43;border-radius:12px;padding:6px;box-shadow:0 14px 42px rgba(0,0,0,.45)}
      #profileMenu.open{display:block}
      #profileMenu button{width:100%;height:38px;display:flex;align-items:center;justify-content:space-between;border:0;background:transparent;color:#e3e7ed;border-radius:8px;padding:0 10px;font-size:12px;cursor:pointer;text-align:left}
      #profileMenu button:hover{background:#2b3038}
      #profileMenu .menu-separator{height:1px;background:#343a43;margin:5px 3px}
      body.full .main-head{height:56px!important;background:#0f1318!important;padding:0 14px 0 18px!important}
      body.full .main-head .head-title{min-width:0}
      body.full .main-head .head-title strong{font-size:14px!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:48vw}
      body.full .main-head .head-title span{display:none!important}
      body.full .head-actions{display:none!important}
      #chatTopActions{margin-left:auto;display:flex;align-items:center;gap:6px}
      #chatTopActions button{height:34px;border:0;background:transparent;color:#dce1e8;border-radius:9px;display:flex;align-items:center;justify-content:center;gap:7px;padding:0 10px;cursor:pointer;font-size:12px}
      #chatTopActions button:hover,#chatTopActions button.open{background:#222831}
      #chatTopActions .icon-only{width:34px;padding:0}
      #chatTopActions svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      #chatMoreMenu{display:none;position:fixed;right:14px;top:50px;z-index:300;width:220px;background:#20242b;border:1px solid #343a43;border-radius:12px;padding:6px;box-shadow:0 14px 42px rgba(0,0,0,.45)}
      #chatMoreMenu.open{display:block}
      #chatMoreMenu button{width:100%;height:38px;border:0;background:transparent;color:#e3e7ed;border-radius:8px;padding:0 10px;text-align:left;font-size:12px;cursor:pointer}
      #chatMoreMenu button:hover{background:#2b3038}
      body.full .context{display:block!important;position:fixed!important;right:0!important;top:0!important;bottom:0!important;width:min(360px,92vw)!important;height:100vh!important;z-index:250!important;background:#12171e!important;border-left:1px solid #303741!important;box-shadow:-16px 0 48px rgba(0,0,0,.38)!important;transform:translateX(105%)!important;transition:transform .18s ease!important}
      body.full .context.open{transform:translateX(0)!important}
      body.full .context-open{display:inline-flex!important}
      #showSessions,#newChat{display:none!important}
      body.full .messages{padding:26px clamp(24px,11vw,160px)!important}
      body.full .chat-compose{padding:12px clamp(24px,11vw,160px) 22px!important}
      #chatShellToast{position:fixed;left:50%;bottom:28px;z-index:500;transform:translate(-50%,14px);opacity:0;pointer-events:none;background:#252a31;border:1px solid #3a414b;border-radius:10px;padding:9px 13px;color:#eef1f5;font-size:12px;transition:.18s}
      #chatShellToast.show{opacity:1;transform:translate(-50%,0)}
      @media(max-width:820px){
        body.full .workspace{grid-template-columns:1fr!important}
        body.full .sidebar{position:fixed!important;left:0;top:0;bottom:0;width:min(290px,88vw);z-index:260;transform:translateX(-102%);transition:.18s;box-shadow:16px 0 48px rgba(0,0,0,.42)}
        body.full .sidebar.mobile-open{transform:translateX(0)}
        #chatTopSidebar{display:flex!important}
        #chatTopActions .share-label{display:none}
        body.full .messages,body.full .chat-compose{padding-left:16px!important;padding-right:16px!important}
      }
      @media(min-width:821px){#chatTopSidebar{display:none!important}}
    `;
    document.head.appendChild(style);
  }

  function toast(text){
    let node=$('chatShellToast');
    if(!node){node=document.createElement('div');node.id='chatShellToast';document.body.appendChild(node);}
    node.textContent=text;
    node.classList.add('show');
    clearTimeout(node._timer);
    node._timer=setTimeout(()=>node.classList.remove('show'),1800);
  }

  function showSection(section){
    if(typeof window.showSection==='function') window.showSection(section);
    else{
      document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('active',p.id==='pane-'+section));
      try{localStorage.setItem('leshy.ui.section',section);}catch(_e){}
    }
    closeProfile();
    document.querySelector('.sidebar')?.classList.remove('mobile-open');
  }

  function newChat(){
    if(typeof window.newChat==='function') window.newChat();
    else{
      try{localStorage.removeItem('leshy.session');}catch(_e){}
      const messages=$('messages');if(messages)messages.textContent='';
      showSection('chat');
    }
    const url=new URL(location.href);url.searchParams.delete('session');history.replaceState({},'',url);
    if(typeof window.loadSessions==='function') window.loadSessions();
  }

  function installSidebar(){
    const sidebar=document.querySelector('.sidebar');
    if(!sidebar||$('chatSidebarTools')) return;
    const brand=sidebar.querySelector('.brand');
    if(brand){
      const button=document.createElement('button');
      button.id='sidebarNewChat';button.type='button';button.title='Новый чат';button.innerHTML=icons.newChat;button.onclick=newChat;
      brand.appendChild(button);
    }

    const tools=document.createElement('div');tools.id='chatSidebarTools';
    tools.innerHTML=`<button id="chatSidebarNew" type="button">${icons.newChat}<span>Новый чат</span></button><div id="chatHistorySearchWrap">${icons.search}<input id="chatHistorySearch" placeholder="Поиск чатов" autocomplete="off"></div>`;
    const nav=sidebar.querySelector('nav');
    if(nav) nav.insertAdjacentElement('afterend',tools);else if(brand)brand.insertAdjacentElement('afterend',tools);else sidebar.prepend(tools);
    $('chatSidebarNew').onclick=newChat;

    const historyWrap=document.createElement('div');historyWrap.id='sidebarHistory';
    const title=document.createElement('div');title.id='sidebarHistoryTitle';title.textContent='Чаты';historyWrap.appendChild(title);
    let sessionList=$('sessionList');
    if(sessionList){
      const oldCard=sessionList.closest('.card');
      historyWrap.appendChild(sessionList);
      if(oldCard&&oldCard!==historyWrap) oldCard.style.display='none';
    }else{
      sessionList=document.createElement('div');sessionList.id='sessionList';sessionList.className='list';historyWrap.appendChild(sessionList);
    }
    const foot=sidebar.querySelector('.sidebar-foot');
    if(foot) sidebar.insertBefore(historyWrap,foot);else sidebar.appendChild(historyWrap);

    $('chatHistorySearch').addEventListener('input',event=>{
      const query=String(event.target.value||'').trim().toLowerCase();
      sessionList.querySelectorAll('.item').forEach(item=>{item.style.display=!query||item.textContent.toLowerCase().includes(query)?'':'none';});
    });

    installProfileMenu(sidebar,foot||sidebar);
    if(typeof window.loadSessions==='function') window.loadSessions();
  }

  function installProfileMenu(sidebar,foot){
    if($('profileMenuButton')) return;
    if(!foot.classList.contains('sidebar-foot')){const f=document.createElement('div');f.className='sidebar-foot';sidebar.appendChild(f);foot=f;}
    foot.innerHTML='';
    const button=document.createElement('button');button.id='profileMenuButton';button.type='button';
    button.innerHTML='<span id="profileAvatar">Л</span><span class="profile-copy"><span class="profile-name">Леший</span><span class="profile-sub">Меню и настройки</span></span><span class="profile-chevron">'+icons.chevron+'</span>';
    const menu=document.createElement('div');menu.id='profileMenu';
    const sections=[['overview','Обзор'],['knowledge','Знания'],['learning','Обучение'],['tasks','Задачи'],['tools','Инструменты'],['system','Система'],['logs','Логи']];
    for(const [section,label] of sections){const item=document.createElement('button');item.type='button';item.textContent=label;item.onclick=()=>showSection(section);menu.appendChild(item);}
    const sep=document.createElement('div');sep.className='menu-separator';menu.appendChild(sep);
    const settings=document.createElement('button');settings.type='button';settings.textContent='Настройки';settings.onclick=()=>showSection('settings');menu.appendChild(settings);
    foot.append(button,menu);
    button.onclick=event=>{event.stopPropagation();const open=menu.classList.toggle('open');button.classList.toggle('open',open);};
  }

  function closeProfile(){ $('profileMenu')?.classList.remove('open');$('profileMenuButton')?.classList.remove('open'); }
  function closeMore(){ $('chatMoreMenu')?.classList.remove('open');$('chatMoreButton')?.classList.remove('open'); }

  function installTopBar(){
    const header=document.querySelector('.main-head');
    if(!header||$('chatTopActions')) return;
    const actions=document.createElement('div');actions.id='chatTopActions';
    const mobile=document.createElement('button');mobile.id='chatTopSidebar';mobile.className='icon-only';mobile.type='button';mobile.title='Открыть историю чатов';mobile.innerHTML=icons.panel;mobile.onclick=()=>document.querySelector('.sidebar')?.classList.toggle('mobile-open');
    const share=document.createElement('button');share.id='chatShareButton';share.type='button';share.innerHTML=icons.share+'<span class="share-label">Поделиться</span>';share.onclick=shareChat;
    const more=document.createElement('button');more.id='chatMoreButton';more.className='icon-only';more.type='button';more.title='Ещё';more.innerHTML=icons.more;
    actions.append(mobile,share,more);header.appendChild(actions);

    const menu=document.createElement('div');menu.id='chatMoreMenu';
    const status=document.createElement('button');status.type='button';status.textContent='Состояние Лешего';status.onclick=()=>{document.getElementById('contextPanel')?.classList.add('open');closeMore();};
    const overview=document.createElement('button');overview.type='button';overview.textContent='Обзор';overview.onclick=()=>{showSection('overview');closeMore();};
    const copy=document.createElement('button');copy.type='button';copy.textContent='Копировать текст диалога';copy.onclick=async()=>{await copyTranscript();closeMore();};
    const fresh=document.createElement('button');fresh.type='button';fresh.textContent='Новый чат';fresh.onclick=()=>{newChat();closeMore();};
    menu.append(status,overview,copy,fresh);document.body.appendChild(menu);
    more.onclick=event=>{event.stopPropagation();const open=menu.classList.toggle('open');more.classList.toggle('open',open);};
  }

  async function shareChat(){
    const sid=localStorage.getItem('leshy.session')||'';
    if(!sid){toast('Сначала начните диалог');return;}
    const url=new URL(location.href);url.searchParams.set('session',sid);
    try{
      if(navigator.share){await navigator.share({title:'Диалог с Лешим',url:url.toString()});return;}
      await navigator.clipboard.writeText(url.toString());toast('Ссылка на диалог скопирована');
    }catch(error){if(error&&error.name!=='AbortError')toast('Не удалось поделиться');}
  }

  async function copyTranscript(){
    const messages=[...document.querySelectorAll('#messages .msg')].map(node=>{
      const who=node.querySelector('.who')?.textContent||'';
      const clone=node.cloneNode(true);clone.querySelector('.who')?.remove();return who+': '+clone.textContent.trim();
    }).join('\n\n');
    if(!messages){toast('Диалог пуст');return;}
    try{await navigator.clipboard.writeText(messages);toast('Диалог скопирован');}catch(_e){toast('Буфер обмена недоступен');}
  }

  function installContextClose(){
    const panel=$('contextPanel');if(!panel)return;
    let close=$('contextClose');
    if(close) close.onclick=()=>panel.classList.remove('open');
  }

  function loadSharedSession(){
    const sid=new URL(location.href).searchParams.get('session');
    if(!sid)return;
    const run=()=>{if(typeof window.loadHistory==='function')window.loadHistory(sid);};
    setTimeout(run,80);
  }

  function bindGlobalClose(){
    document.addEventListener('click',event=>{
      if(!event.target.closest('#profileMenu')&&!event.target.closest('#profileMenuButton'))closeProfile();
      if(!event.target.closest('#chatMoreMenu')&&!event.target.closest('#chatMoreButton'))closeMore();
      if(innerWidth<=820&&!event.target.closest('.sidebar')&&!event.target.closest('#chatTopSidebar'))document.querySelector('.sidebar')?.classList.remove('mobile-open');
    });
  }

  function boot(){
    ensureStyle();
    installSidebar();
    installTopBar();
    installContextClose();
    bindGlobalClose();
    loadSharedSession();
    const observer=new MutationObserver(()=>{installSidebar();installTopBar();installContextClose();});
    observer.observe(document.body,{childList:true,subtree:true});
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
