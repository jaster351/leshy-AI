(function(){
  'use strict';

  const byId=id=>document.getElementById(id);

  async function requestJson(path,options={}){
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok) throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function ensureStyle(){
    if(byId('leshyNasPanelStyle')) return;
    const style=document.createElement('style');
    style.id='leshyNasPanelStyle';
    style.textContent=`
      #nasSettingsCard{margin-top:12px}
      #nasSettingsCard .nas-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:10px}
      #nasSettingsCard .nas-grid .wide{grid-column:1/-1}
      #nasSettingsCard label{display:grid;gap:5px;font-size:11px;color:var(--muted)}
      #nasSettingsCard .nas-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
      #nasSettingsCard .nas-actions button{flex:1 1 180px}
      #nasSettingsCard .nas-status{margin-top:9px;white-space:pre-wrap;word-break:break-word}
      @media(max-width:720px){#nasSettingsCard .nas-grid{grid-template-columns:1fr}#nasSettingsCard .nas-grid .wide{grid-column:auto}}
    `;
    document.head.appendChild(style);
  }

  function install(){
    ensureStyle();
    if(byId('nasSettingsCard')) return;
    const pane=byId('pane-settings');
    if(!pane) return;

    const card=document.createElement('div');
    card.id='nasSettingsCard';
    card.className='card';
    card.innerHTML=`
      <h3>NAS / SMB</h3>
      <div class="muted">Куда Леший складывает torrent-файлы. Если учётные данные не указаны, он просто использует UNC-путь без отдельной авторизации.</div>
      <div class="nas-grid">
        <label class="wide">Папка для torrent-файлов
          <input class="field" id="nasTorrentWatch" placeholder="\\\\Nas\\для торрент файлов">
        </label>
        <label>SMB-сервер
          <input class="field" id="nasServer" placeholder="\\\\Nas">
        </label>
        <label>Домен / рабочая группа
          <input class="field" id="nasDomain" placeholder="необязательно">
        </label>
        <label>Логин SMB
          <input class="field" id="nasUsername" autocomplete="username" placeholder="необязательно">
        </label>
        <label>Пароль SMB
          <input class="field" id="nasPassword" type="password" autocomplete="new-password" placeholder="оставьте пустым, чтобы не менять сохранённый">
        </label>
      </div>
      <div class="nas-actions">
        <button class="button" id="nasSave" type="button">Сохранить NAS</button>
        <button class="button secondary" id="nasTest" type="button">Проверить подключение</button>
        <button class="button secondary" id="nasClearCredentials" type="button">Убрать SMB-авторизацию</button>
      </div>
      <div class="hint nas-status" id="nasStatus">Загрузка настроек…</div>
      <div class="hint" style="margin-top:7px">Если NAS недоступен при доставке torrent-файла, Леший сохраняет файл в локальную папку «Загрузки» и сообщает об этом.</div>
    `;

    const brainCard=byId('brainDir')?.closest('.card');
    if(brainCard && brainCard.parentNode) brainCard.parentNode.insertBefore(card,brainCard);
    else pane.appendChild(card);

    byId('nasSave').onclick=save;
    byId('nasTest').onclick=test;
    byId('nasClearCredentials').onclick=clearCredentials;
    load();
  }

  function render(data){
    if(!data) return;
    byId('nasTorrentWatch').value=data.torrent_watch||'\\\\Nas\\для торрент файлов';
    byId('nasServer').value=data.smb_server||'\\\\Nas';
    byId('nasDomain').value=data.smb_domain||'';
    byId('nasUsername').value=data.smb_username||'';
    byId('nasPassword').value='';
    const auth=data.smb_username
      ? ('SMB-авторизация: '+(data.password_saved?'логин и защищённый пароль сохранены':'логин сохранён, пароль не сохранён'))
      : 'SMB-авторизация: не требуется; используется доступ Windows к указанному пути';
    byId('nasStatus').textContent='Torrent: '+(data.torrent_watch||'—')+'\n'+auth;
  }

  async function load(){
    try{ render(await requestJson('/v1/nas')); }
    catch(error){ if(byId('nasStatus')) byId('nasStatus').textContent='Не удалось загрузить настройки NAS: '+error.message; }
  }

  async function save(){
    const button=byId('nasSave');
    button.disabled=true;
    byId('nasStatus').textContent='Сохраняю настройки NAS…';
    try{
      const payload={
        torrent_watch:byId('nasTorrentWatch').value.trim(),
        smb_server:byId('nasServer').value.trim(),
        smb_domain:byId('nasDomain').value.trim(),
        smb_username:byId('nasUsername').value.trim(),
        password:byId('nasPassword').value
      };
      const data=await requestJson('/v1/nas/config',{method:'POST',body:JSON.stringify(payload)});
      render(data);
      byId('nasStatus').textContent+='\nНастройки сохранены.';
    }catch(error){byId('nasStatus').textContent='Ошибка сохранения: '+error.message;}
    finally{button.disabled=false;}
  }

  async function test(){
    const button=byId('nasTest');
    button.disabled=true;
    byId('nasStatus').textContent='Проверяю NAS…';
    try{
      const data=await requestJson('/v1/nas/test',{method:'POST',body:'{}'});
      const connection=data.connection||{};
      const lines=[
        connection.connected?'SMB: доступ разрешён':'SMB: подключение не подтверждено',
        'Сервер: '+(connection.server||data.effective_server||'—'),
        'Torrent-папка: '+(data.torrent_watch||'—'),
        'Папка доступна: '+(data.torrent_watch_exists?'да':'нет')
      ];
      if(connection.attempted===false && !data.smb_username) lines.push('Авторизация не выполнялась: логин не указан.');
      if(connection.error) lines.push('Ошибка: '+connection.error);
      byId('nasStatus').textContent=lines.join('\n');
    }catch(error){byId('nasStatus').textContent='Ошибка проверки NAS: '+error.message;}
    finally{button.disabled=false;}
  }

  async function clearCredentials(){
    if(!confirm('Убрать сохранённые SMB-логин, домен и пароль? Путь NAS останется без изменений.')) return;
    const button=byId('nasClearCredentials');
    button.disabled=true;
    try{
      const data=await requestJson('/v1/nas/config',{method:'POST',body:JSON.stringify({
        torrent_watch:byId('nasTorrentWatch').value.trim(),
        smb_server:byId('nasServer').value.trim(),
        smb_domain:'',
        smb_username:'',
        password:'',
        clear_password:true
      })});
      render(data);
      byId('nasStatus').textContent+='\nSMB-авторизация отключена.';
    }catch(error){byId('nasStatus').textContent='Ошибка: '+error.message;}
    finally{button.disabled=false;}
  }

  function boot(){
    install();
    const observer=new MutationObserver(()=>install());
    observer.observe(document.documentElement,{childList:true,subtree:true});
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',boot,{once:true});
  else boot();
})();

(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const svg=p=>`<svg viewBox="0 0 24 24" aria-hidden="true">${p}</svg>`;
  const I={
    edit:svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/>'),
    search:svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
    share:svg('<path d="M12 16V3"/><path d="m7 8 5-5 5 5"/><path d="M5 12v7h14v-7"/>'),
    more:svg('<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'),
    panel:svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>')
  };

  function css(){
    if($('leshyChatShellStyle'))return;
    const s=document.createElement('style');s.id='leshyChatShellStyle';s.textContent=`
      body.full .workspace{grid-template-columns:272px minmax(0,1fr)!important;background:#1b222b!important}
      body.full .sidebar{position:relative!important;display:flex!important;flex-direction:column!important;min-width:0!important;background:#11151b!important;border-right:1px solid #252b33!important}
      body.full .sidebar nav{display:none!important}
      body.full .brand{height:56px!important;background:#11151b!important;border-bottom:0!important;padding:8px 10px 4px!important;font-size:15px!important}
      body.full .brand .mini-core{width:28px!important;height:28px!important;flex-basis:28px!important;border-radius:50%!important}
      body.full .brand .mini-core:after{display:none!important}
      #sidebarNewChat{margin-left:auto;width:34px;height:34px;border:0;background:transparent;color:#d8dee7;border-radius:9px;display:grid;place-items:center;cursor:pointer}
      #sidebarNewChat:hover,#chatSidebarNew:hover,#profileMenuButton:hover,#profileMenuButton.open,#leshyTopActions button:hover,#leshyTopActions button.open{background:#222831}
      #sidebarNewChat svg,#chatSidebarNew svg,#leshyTopActions svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      #chatSidebarTools{padding:4px 10px 8px}
      #chatSidebarNew{height:40px;width:100%;display:flex;align-items:center;gap:10px;border:0;background:transparent;color:#e7ebf0;border-radius:9px;padding:0 10px;cursor:pointer;text-align:left}
      #chatHistorySearchWrap{position:relative;margin-top:4px}
      #chatHistorySearchWrap svg{position:absolute;left:10px;top:10px;width:16px;height:16px;fill:none;stroke:#8d96a3;stroke-width:1.8}
      #chatHistorySearch{width:100%;height:36px;padding:0 10px 0 34px!important;border-radius:9px!important;background:#171b22!important;border:1px solid transparent!important;font-size:12px!important}
      #sidebarHistory{flex:1;min-height:0;overflow:auto;padding:5px 8px 10px}
      #sidebarHistoryTitle{padding:8px 8px 5px;color:#8b94a1;font-size:11px;font-weight:600}
      #sidebarHistory #sessionList{display:grid;gap:2px}
      #sidebarHistory #sessionList .item{border:0!important;background:transparent!important;border-radius:8px!important;padding:9px 10px!important;cursor:pointer!important;overflow:hidden}
      #sidebarHistory #sessionList .item:hover{background:#222831!important}
      #sidebarHistory #sessionList .item-title{font-size:12px!important;font-weight:450!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      #sidebarHistory #sessionList .item-meta,#sidebarHistory #sessionList .item-body{display:none!important}
      body.full .sidebar-foot{margin-top:0!important;padding:7px 8px 9px!important;background:#11151b!important;border-top:1px solid #252b33!important}
      body.full .sidebar-foot>.hint,body.full #minimalMode,#showSessions,#newChat{display:none!important}
      #profileMenuButton{width:100%;height:48px;display:flex;align-items:center;gap:10px;border:0;background:transparent;color:#e7ebf0;border-radius:10px;padding:5px 7px;cursor:pointer;text-align:left}
      #profileAvatar{width:32px;height:32px;border-radius:50%;display:grid;place-items:center;background:linear-gradient(145deg,#356ca4,#4e9ee8);color:#fff;font-weight:700;font-size:13px;flex:0 0 32px}
      #profileCopy{min-width:0;flex:1;display:grid;gap:1px}#profileCopy b{font-size:12px}#profileCopy span{font-size:10px;color:#8e97a4}
      #profileMenu{display:none;position:absolute;left:8px;right:8px;bottom:62px;z-index:300;background:#20242b;border:1px solid #343a43;border-radius:12px;padding:6px;box-shadow:0 14px 42px rgba(0,0,0,.45)}
      #profileMenu.open,#chatMoreMenu.open{display:block}
      #profileMenu button,#chatMoreMenu button{width:100%;height:38px;border:0;background:transparent;color:#e3e7ed;border-radius:8px;padding:0 10px;font-size:12px;cursor:pointer;text-align:left}
      #profileMenu button:hover,#chatMoreMenu button:hover{background:#2b3038}
      #profileMenu .sep{height:1px;background:#343a43;margin:5px 3px}
      body.full .main-head{height:56px!important;background:#0f1318!important;padding:0 14px 0 18px!important}
      body.full .main-head .head-title span{display:none!important}body.full .head-actions{display:none!important}
      #leshyTopActions{margin-left:auto;display:flex;align-items:center;gap:6px}
      #leshyTopActions button{height:34px;border:0;background:transparent;color:#dce1e8;border-radius:9px;display:flex;align-items:center;justify-content:center;gap:7px;padding:0 10px;cursor:pointer;font-size:12px}
      #leshyTopActions .icon{width:34px;padding:0}
      #chatMoreMenu{display:none;position:fixed;right:14px;top:50px;z-index:350;width:220px;background:#20242b;border:1px solid #343a43;border-radius:12px;padding:6px;box-shadow:0 14px 42px rgba(0,0,0,.45)}
      body.full .context{display:block!important;position:fixed!important;right:0!important;top:0!important;bottom:0!important;width:min(360px,92vw)!important;height:100vh!important;z-index:320!important;background:#12171e!important;border-left:1px solid #303741!important;box-shadow:-16px 0 48px rgba(0,0,0,.38)!important;transform:translateX(105%)!important;transition:transform .18s ease!important}
      body.full .context.open{transform:translateX(0)!important}
      body.full .messages{padding:26px clamp(24px,11vw,160px)!important}body.full .chat-compose{padding:12px clamp(24px,11vw,160px) 22px!important}
      #shellToast{position:fixed;left:50%;bottom:28px;z-index:500;transform:translate(-50%,12px);opacity:0;pointer-events:none;background:#252a31;border:1px solid #3a414b;border-radius:10px;padding:9px 13px;color:#eef1f5;font-size:12px;transition:.18s}#shellToast.show{opacity:1;transform:translate(-50%,0)}
      @media(max-width:820px){body.full .workspace{grid-template-columns:1fr!important}body.full .sidebar{position:fixed!important;left:0;top:0;bottom:0;width:min(290px,88vw);z-index:360;transform:translateX(-102%);transition:.18s;box-shadow:16px 0 48px rgba(0,0,0,.42)}body.full .sidebar.mobile-open{transform:translateX(0)}#topSidebarToggle{display:flex!important}.shareText{display:none}body.full .messages,body.full .chat-compose{padding-left:16px!important;padding-right:16px!important}}
      @media(min-width:821px){#topSidebarToggle{display:none!important}}
    `;document.head.appendChild(s);
  }

  function toast(t){let n=$('shellToast');if(!n){n=document.createElement('div');n.id='shellToast';document.body.appendChild(n)}n.textContent=t;n.classList.add('show');clearTimeout(n._t);n._t=setTimeout(()=>n.classList.remove('show'),1800)}
  function section(id){if(typeof window.showSection==='function')window.showSection(id);else document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('active',p.id==='pane-'+id));$('profileMenu')?.classList.remove('open');document.querySelector('.sidebar')?.classList.remove('mobile-open')}
  function fresh(){if(typeof window.newChat==='function')window.newChat();else{localStorage.removeItem('leshy.session');$('messages').textContent='';section('chat')}const u=new URL(location.href);u.searchParams.delete('session');history.replaceState({},'',u);setTimeout(()=>window.loadSessions&&window.loadSessions(),50)}

  function sidebar(){const s=document.querySelector('.sidebar');if(!s||$('chatSidebarTools'))return;const b=s.querySelector('.brand');if(b){const x=document.createElement('button');x.id='sidebarNewChat';x.type='button';x.title='Новый чат';x.innerHTML=I.edit;x.onclick=fresh;b.appendChild(x)}
    const t=document.createElement('div');t.id='chatSidebarTools';t.innerHTML=`<button id="chatSidebarNew" type="button">${I.edit}<span>Новый чат</span></button><div id="chatHistorySearchWrap">${I.search}<input id="chatHistorySearch" placeholder="Поиск чатов"></div>`;const nav=s.querySelector('nav');(nav||b).insertAdjacentElement('afterend',t);$('chatSidebarNew').onclick=fresh;
    const w=document.createElement('div');w.id='sidebarHistory';w.innerHTML='<div id="sidebarHistoryTitle">Чаты</div>';let list=$('sessionList');if(list){const old=list.closest('.card');w.appendChild(list);if(old)old.style.display='none'}else{list=document.createElement('div');list.id='sessionList';list.className='list';w.appendChild(list)}const foot=s.querySelector('.sidebar-foot');if(foot)s.insertBefore(w,foot);else s.appendChild(w);$('chatHistorySearch').oninput=e=>{const q=e.target.value.trim().toLowerCase();list.querySelectorAll('.item').forEach(i=>i.style.display=!q||i.textContent.toLowerCase().includes(q)?'':'none')};profile(s,foot);window.loadSessions&&window.loadSessions();}

  function profile(side,foot){if($('profileMenuButton'))return;if(!foot){foot=document.createElement('div');foot.className='sidebar-foot';side.appendChild(foot)}foot.innerHTML='';const btn=document.createElement('button');btn.id='profileMenuButton';btn.innerHTML='<span id="profileAvatar">Л</span><span id="profileCopy"><b>Леший</b><span>Меню и настройки</span></span>';const m=document.createElement('div');m.id='profileMenu';for(const x of [['overview','Обзор'],['knowledge','Знания'],['learning','Обучение'],['tasks','Задачи'],['tools','Инструменты'],['system','Система'],['logs','Логи']]){const q=document.createElement('button');q.textContent=x[1];q.onclick=()=>section(x[0]);m.appendChild(q)}const sep=document.createElement('div');sep.className='sep';m.appendChild(sep);const st=document.createElement('button');st.textContent='Настройки';st.onclick=()=>section('settings');m.appendChild(st);foot.append(btn,m);btn.onclick=e=>{e.stopPropagation();m.classList.toggle('open');btn.classList.toggle('open',m.classList.contains('open'))}}

  function top(){const h=document.querySelector('.main-head');if(!h||$('leshyTopActions'))return;const a=document.createElement('div');a.id='leshyTopActions';const mob=document.createElement('button');mob.id='topSidebarToggle';mob.className='icon';mob.title='История чатов';mob.innerHTML=I.panel;mob.onclick=()=>document.querySelector('.sidebar')?.classList.toggle('mobile-open');const sh=document.createElement('button');sh.innerHTML=I.share+'<span class="shareText">Поделиться</span>';sh.onclick=share;const more=document.createElement('button');more.id='chatMoreButton';more.className='icon';more.innerHTML=I.more;a.append(mob,sh,more);h.appendChild(a);const menu=document.createElement('div');menu.id='chatMoreMenu';for(const [label,fn] of [['Состояние Лешего',()=>{$('contextPanel')?.classList.add('open')}],['Обзор',()=>section('overview')],['Копировать текст диалога',copy],['Новый чат',fresh]]){const q=document.createElement('button');q.textContent=label;q.onclick=()=>{fn();menu.classList.remove('open')};menu.appendChild(q)}document.body.appendChild(menu);more.onclick=e=>{e.stopPropagation();menu.classList.toggle('open');more.classList.toggle('open',menu.classList.contains('open'))}}

  async function share(){const sid=localStorage.getItem('leshy.session')||'';if(!sid){toast('Сначала начните диалог');return}const u=new URL(location.href);u.searchParams.set('session',sid);try{if(navigator.share){await navigator.share({title:'Диалог с Лешим',url:u.toString()})}else{await navigator.clipboard.writeText(u.toString());toast('Ссылка на диалог скопирована')}}catch(e){if(e.name!=='AbortError')toast('Не удалось поделиться')}}
  async function copy(){const text=[...document.querySelectorAll('#messages .msg')].map(n=>n.innerText.trim()).join('\n\n');if(!text){toast('Диалог пуст');return}try{await navigator.clipboard.writeText(text);toast('Диалог скопирован')}catch(_e){toast('Буфер обмена недоступен')}}
  function shared(){const sid=new URL(location.href).searchParams.get('session');if(sid)setTimeout(()=>window.loadHistory&&window.loadHistory(sid),100)}
  function close(){document.addEventListener('click',e=>{if(!e.target.closest('#profileMenuButton')&&!e.target.closest('#profileMenu')){$('profileMenu')?.classList.remove('open');$('profileMenuButton')?.classList.remove('open')}if(!e.target.closest('#chatMoreButton')&&!e.target.closest('#chatMoreMenu')){$('chatMoreMenu')?.classList.remove('open');$('chatMoreButton')?.classList.remove('open')}})}
  function boot(){css();sidebar();top();if($('contextClose'))$('contextClose').onclick=()=>$('contextPanel')?.classList.remove('open');close();shared();setInterval(()=>{if(typeof window.loadSessions==='function')window.loadSessions()},7000);const o=new MutationObserver(()=>{sidebar();top()});o.observe(document.body,{childList:true,subtree:true})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
