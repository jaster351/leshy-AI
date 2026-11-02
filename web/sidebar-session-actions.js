(function(){
  'use strict';

  const OWNER='web:local';
  const SESSION_KEY='leshy.session';
  let archivedView=false;
  let currentMenuItem=null;
  let refreshBusy=false;

  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'');
  const svg=body=>`<svg viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
  const ICONS={
    edit:svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/>'),
    pin:svg('<path d="m14 4 6 6-3 1-4 4-1 4-2-2-5 5-1-1 5-5-2-2 4-1 4-4Z"/>'),
    lock:svg('<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>'),
    unlock:svg('<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 7-2"/>'),
    share:svg('<path d="M12 16V3"/><path d="m7 8 5-5 5 5"/><path d="M5 12v7h14v-7"/>'),
    archive:svg('<rect x="4" y="7" width="16" height="13" rx="2"/><path d="M3 4h18v3H3zM9 11h6"/>'),
    trash:svg('<path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"/>'),
    folder:svg('<path d="M3 6h7l2 2h9v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>'),
    more:svg('<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'),
    back:svg('<path d="m15 18-6-6 6-6"/>')
  };

  async function api(path,options={}){
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function toast(text){
    let node=$('shellToast');
    if(!node){node=document.createElement('div');node.id='shellToast';document.body.appendChild(node)}
    node.textContent=text;node.classList.add('show');clearTimeout(node._sidebarTimer);
    node._sidebarTimer=setTimeout(()=>node.classList.remove('show'),2200);
  }

  function installCss(){
    if($('leshySidebarSessionActionsStyle'))return;
    const style=document.createElement('style');style.id='leshySidebarSessionActionsStyle';style.textContent=`
      #sidebarHistoryTitle{display:flex;align-items:center;justify-content:space-between;gap:8px;min-width:0;max-width:100%;box-sizing:border-box}
      #leshyArchiveToggle{border:0;background:transparent;color:#9a9a9a;border-radius:7px;height:28px;padding:0 7px;cursor:pointer;display:flex;align-items:center;gap:5px;font-size:11px;flex:0 0 auto}
      #leshyArchiveToggle:hover{background:#2a2a2a;color:#eee}#leshyArchiveToggle svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      #sessionList{min-width:0!important;max-width:100%!important;overflow-x:hidden!important}
      #sessionList .leshy-session-section{padding:13px 8px 5px;color:#8f8f8f;font-size:11px;font-weight:600;letter-spacing:.01em}
      #sessionList .leshy-project-name{padding:7px 8px 3px;color:#ababab;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      #sessionList .leshy-session-item{display:flex!important;align-items:center!important;gap:3px!important;position:relative!important;padding:2px 4px!important;min-height:38px!important;width:100%!important;max-width:100%!important;min-width:0!important;box-sizing:border-box!important;overflow:hidden!important}
      .leshy-session-open{min-width:0!important;width:0!important;max-width:100%!important;flex:1 1 0!important;height:34px;display:flex;align-items:center;gap:7px;border:0;background:transparent;color:#e8e8e8;border-radius:7px;padding:0 7px;cursor:pointer;text-align:left;overflow:hidden!important}
      .leshy-session-open:hover,.leshy-session-item.menu-open{background:#2a2a2a!important}.leshy-session-title{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
      .leshy-session-mark{display:inline-flex;align-items:center;color:#969696;flex:0 0 auto}.leshy-session-mark svg{width:13px;height:13px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      .leshy-session-more{width:30px;height:30px;display:grid;place-items:center;flex:0 0 30px!important;margin-left:auto!important;position:relative;right:0;border:0;background:transparent;color:#a8a8a8;border-radius:7px;cursor:pointer;opacity:1!important}
      .leshy-session-more:hover,.leshy-session-more:focus,.leshy-session-item.menu-open .leshy-session-more{background:#3a3a3a;color:#fff;opacity:1!important}.leshy-session-more svg{width:17px;height:17px;fill:currentColor;stroke:none}
      #leshySessionMenu{position:fixed;display:none;z-index:2400;width:236px;max-width:calc(100vw - 18px);padding:6px;background:#2b2b2b;border:1px solid #444;border-radius:13px;box-shadow:0 18px 55px rgba(0,0,0,.55)}#leshySessionMenu.open{display:block}
      #leshySessionMenu button{width:100%;height:39px;border:0;background:transparent;color:#eee;border-radius:8px;padding:0 9px;display:flex;align-items:center;gap:10px;cursor:pointer;text-align:left;font-size:14px}#leshySessionMenu button:hover,#leshySessionMenu button:focus{background:#3a3a3a;outline:none}#leshySessionMenu button.danger{color:#ff8f8f}#leshySessionMenu svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex:0 0 17px}.leshy-menu-sep{height:1px;background:#444;margin:5px 4px}
      .leshy-session-rename{width:100%;height:28px;background:#202020;border:1px solid #666;border-radius:6px;color:#fff;padding:0 6px;outline:none;font:inherit}
      #leshyProjectOverlay{position:fixed;inset:0;display:none;z-index:2500;background:rgba(0,0,0,.48);align-items:center;justify-content:center;padding:18px}#leshyProjectOverlay.open{display:flex}
      #leshyProjectDialog{width:min(430px,96vw);max-height:min(560px,88vh);overflow:auto;background:#2b2b2b;border:1px solid #484848;border-radius:16px;box-shadow:0 24px 70px rgba(0,0,0,.55);padding:14px}#leshyProjectDialog h3{margin:4px 6px 14px;font-size:16px}
      .leshy-project-choice{width:100%;height:42px;border:0;background:transparent;color:#eee;border-radius:9px;padding:0 10px;text-align:left;cursor:pointer;display:flex;align-items:center;gap:9px}.leshy-project-choice:hover{background:#3a3a3a}.leshy-project-choice svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.7}.leshy-project-footer{display:flex;gap:8px;margin-top:10px;padding-top:10px;border-top:1px solid #444}.leshy-project-footer button{border:0;border-radius:9px;background:#3a3a3a;color:#eee;padding:9px 12px;cursor:pointer}.leshy-project-footer button:first-child{flex:1;text-align:left}
      @media(max-width:820px){#leshySessionMenu{width:min(250px,calc(100vw - 18px))}}
    `;document.head.appendChild(style);
  }

  function ensureHeader(){
    const title=$('sidebarHistoryTitle');if(!title)return;
    let label=title.querySelector('.leshy-history-label');
    if(!label){title.textContent='';label=document.createElement('span');label.className='leshy-history-label';title.appendChild(label)}
    label.textContent=archivedView?'Архив':'Чаты';
    let toggle=$('leshyArchiveToggle');
    if(!toggle){toggle=document.createElement('button');toggle.id='leshyArchiveToggle';toggle.type='button';title.appendChild(toggle);toggle.onclick=async e=>{e.preventDefault();archivedView=!archivedView;closeMenu();await renderSessions()}}
    toggle.innerHTML=archivedView?ICONS.back+'<span>Назад</span>':ICONS.archive+'<span>Архив</span>';
  }

  function section(container,text){const h=document.createElement('div');h.className='leshy-session-section';h.textContent=text;container.appendChild(h)}
  function projectName(container,text){const h=document.createElement('div');h.className='leshy-project-name';h.textContent=text;container.appendChild(h)}

  function marks(item){
    const fragment=document.createDocumentFragment();
    if(item.locked){const x=document.createElement('span');x.className='leshy-session-mark';x.title='Чат заблокирован';x.innerHTML=ICONS.lock;fragment.appendChild(x)}
    return fragment;
  }

  function rowFor(item){
    const row=document.createElement('div');row.className='item leshy-session-item';row.dataset.sessionId=item.id;
    const open=document.createElement('button');open.type='button';open.className='leshy-session-open';open.title=item.title||'Новый диалог';
    const title=document.createElement('span');title.className='leshy-session-title';title.textContent=item.title||'Новый диалог';
    open.append(marks(item),title);open.onclick=e=>{e.preventDefault();e.stopPropagation();closeMenu();if(typeof window.loadHistory==='function')window.loadHistory(item.id)};
    const more=document.createElement('button');more.type='button';more.className='leshy-session-more';more.title='Действия с чатом';more.setAttribute('aria-label','Действия с чатом');more.innerHTML=ICONS.more;more.onclick=e=>{e.preventDefault();e.stopPropagation();openMenu(item,row,more)};
    row.append(open,more);return row;
  }

  async function renderSessions(){
    if(refreshBusy)return;refreshBusy=true;
    try{
      ensureHeader();const list=$('sessionList');if(!list)return;
      const result=await api('/v1/sessions?owner_id='+encodeURIComponent(OWNER)+'&limit=100&archived='+(archivedView?'1':'0'));
      const items=Array.isArray(result.items)?result.items:[];list.textContent='';
      if(!items.length){const empty=document.createElement('div');empty.className='empty';empty.textContent=archivedView?'Архив пуст':'Диалогов пока нет';list.appendChild(empty);return}
      if(archivedView){items.forEach(item=>list.appendChild(rowFor(item)));return}
      let category='',lastProject='';
      for(const item of items){
        const next=item.pinned?'pinned':(item.project_id?'projects':'recent');
        if(next!==category){category=next;lastProject='';section(list,next==='pinned'?'Закрепленные':next==='projects'?'Проекты':'Недавние')}
        if(next==='projects'&&item.project_name&&item.project_name!==lastProject){lastProject=item.project_name;projectName(list,lastProject)}
        list.appendChild(rowFor(item));
      }
    }catch(error){toast('Не удалось загрузить историю: '+error.message)}finally{refreshBusy=false}
  }

  function ensureMenu(){
    let menu=$('leshySessionMenu');if(menu)return menu;
    menu=document.createElement('div');menu.id='leshySessionMenu';menu.setAttribute('role','menu');document.body.appendChild(menu);return menu;
  }
  function closeMenu(){const menu=$('leshySessionMenu');if(menu)menu.classList.remove('open');document.querySelectorAll('.leshy-session-item.menu-open').forEach(r=>r.classList.remove('menu-open'));currentMenuItem=null}
  function menuAction(menu,label,icon,handler,extra=''){const b=document.createElement('button');b.type='button';b.className=extra;b.setAttribute('role','menuitem');b.innerHTML=icon+'<span>'+label+'</span>';b.onclick=async e=>{e.preventDefault();e.stopPropagation();try{await handler()}catch(error){toast(error.message||String(error))}};menu.appendChild(b);return b}
  function separator(menu){const d=document.createElement('div');d.className='leshy-menu-sep';menu.appendChild(d)}

  function positionMenu(menu,button){
    menu.classList.add('open');const rect=button.getBoundingClientRect();const width=Math.min(236,window.innerWidth-18);menu.style.width=width+'px';
    requestAnimationFrame(()=>{const h=menu.offsetHeight;let left=rect.right-width;left=Math.max(9,Math.min(window.innerWidth-width-9,left));let top=rect.bottom+5;if(top+h>window.innerHeight-9)top=Math.max(9,rect.top-h-5);menu.style.left=left+'px';menu.style.top=top+'px'});
  }

  function inlineRename(item,row){
    closeMenu();const title=row.querySelector('.leshy-session-title');if(!title)return;const input=document.createElement('input');input.className='leshy-session-rename';input.value=item.title||'';title.replaceWith(input);input.focus();input.select();let finished=false;
    const finish=async save=>{if(finished)return;finished=true;if(!save){await renderSessions();return}const value=input.value.trim();if(!value){toast('Название чата не может быть пустым');await renderSessions();return}try{await api('/v1/sessions/update',{method:'POST',body:JSON.stringify({owner_id:OWNER,id:item.id,title:value})});await reloadList()}catch(error){toast(error.message);await renderSessions()}};
    input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();finish(true)}else if(e.key==='Escape'){e.preventDefault();finish(false)}});input.addEventListener('blur',()=>finish(true),{once:true});
  }

  async function toggleUpdate(item,key){await api('/v1/sessions/update',{method:'POST',body:JSON.stringify({owner_id:OWNER,id:item.id,[key]:!Boolean(item[key])})});closeMenu();await reloadList()}
  async function share(item){
    const result=await api('/v1/sessions/share',{method:'POST',body:JSON.stringify({owner_id:OWNER,id:item.id})});const url=new URL(result.path,location.origin).toString();closeMenu();
    try{if(navigator.share)await navigator.share({title:item.title||'Диалог с Лешим',url});else{await navigator.clipboard.writeText(url);toast('Ссылка на чат скопирована')}}catch(error){if(error&&error.name!=='AbortError')throw error}
  }
  async function remove(item){
    if(item.locked){toast('Сначала разблокируйте чат');return}
    if(!window.confirm('Удалить чат «'+(item.title||'Новый диалог')+'»? Это действие нельзя отменить.'))return;
    await api('/v1/sessions/delete',{method:'POST',body:JSON.stringify({owner_id:OWNER,id:item.id})});closeMenu();
    if((localStorage.getItem(SESSION_KEY)||'')===item.id&&typeof window.newChat==='function')window.newChat();await reloadList();
  }

  function ensureProjectDialog(){
    let overlay=$('leshyProjectOverlay');if(overlay)return overlay;
    overlay=document.createElement('div');overlay.id='leshyProjectOverlay';overlay.innerHTML='<div id="leshyProjectDialog" role="dialog" aria-modal="true"><h3>Переместить в проект</h3><div id="leshyProjectChoices"></div><div class="leshy-project-footer"><button id="leshyProjectNew" type="button">+ Новый проект</button><button id="leshyProjectCancel" type="button">Отмена</button></div></div>';document.body.appendChild(overlay);overlay.addEventListener('click',e=>{if(e.target===overlay)overlay.classList.remove('open')});$('leshyProjectCancel').onclick=()=>overlay.classList.remove('open');return overlay;
  }
  async function moveToProject(item){
    closeMenu();const overlay=ensureProjectDialog(),choices=$('leshyProjectChoices');const result=await api('/v1/chat-projects?owner_id='+encodeURIComponent(OWNER));choices.textContent='';
    const addChoice=(label,id,active=false)=>{const b=document.createElement('button');b.type='button';b.className='leshy-project-choice';b.innerHTML=ICONS.folder+'<span>'+label+(active?' · текущий':'')+'</span>';b.onclick=async()=>{await api('/v1/sessions/project',{method:'POST',body:JSON.stringify({owner_id:OWNER,id:item.id,project_id:id||''})});overlay.classList.remove('open');await reloadList()};choices.appendChild(b)};
    addChoice('Без проекта','',!item.project_id);for(const project of result.items||[])addChoice(project.name,project.id,project.id===item.project_id);
    $('leshyProjectNew').onclick=async()=>{const name=window.prompt('Название нового проекта');if(!name||!name.trim())return;try{const created=await api('/v1/chat-projects/create',{method:'POST',body:JSON.stringify({owner_id:OWNER,name:name.trim()})});await api('/v1/sessions/project',{method:'POST',body:JSON.stringify({owner_id:OWNER,id:item.id,project_id:created.id})});overlay.classList.remove('open');await reloadList()}catch(error){toast(error.message)}};
    overlay.classList.add('open');
  }

  function openMenu(item,row,button){
    closeMenu();currentMenuItem=item;row.classList.add('menu-open');const menu=ensureMenu();menu.textContent='';
    menuAction(menu,'Переименовать',ICONS.edit,()=>inlineRename(item,row));
    menuAction(menu,item.pinned?'Открепить':'Закрепить',ICONS.pin,()=>toggleUpdate(item,'pinned'));
    menuAction(menu,item.locked?'Разблокировать чат':'Заблокировать чат',item.locked?ICONS.unlock:ICONS.lock,()=>toggleUpdate(item,'locked'));
    menuAction(menu,'Поделиться',ICONS.share,()=>share(item));
    menuAction(menu,item.archived?'Вернуть из архива':'Архивировать',ICONS.archive,()=>toggleUpdate(item,'archived'));
    menuAction(menu,'Удалить',ICONS.trash,()=>remove(item),'danger');separator(menu);
    menuAction(menu,'Переместить в проект',ICONS.folder,()=>moveToProject(item));positionMenu(menu,button);
  }

  async function reloadList(){
    if(typeof window.loadSessions==='function'&&window.loadSessions.__leshySidebarActionsWrapped){await renderSessions();return}
    if(typeof window.loadSessions==='function')await window.loadSessions();else await renderSessions();
  }

  function wrapLoadSessions(){
    const original=window.loadSessions;if(!original||original.__leshySidebarActionsWrapped)return;
    const wrapped=async function(){const result=await original.apply(this,arguments);await renderSessions();return result};wrapped.__leshySidebarActionsWrapped=true;wrapped.__leshySidebarOriginal=original;window.loadSessions=wrapped;
  }

  function boot(){
    installCss();ensureHeader();wrapLoadSessions();renderSessions();
    document.addEventListener('click',e=>{const menu=$('leshySessionMenu');if(menu&&menu.classList.contains('open')&&!menu.contains(e.target)&&!e.target.closest?.('.leshy-session-more'))closeMenu()});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeMenu();$('leshyProjectOverlay')?.classList.remove('open')}});
    window.addEventListener('resize',closeMenu);
    window.addEventListener('leshy:session-changed',()=>setTimeout(renderSessions,40));
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();