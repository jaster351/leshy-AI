(function(){
  'use strict';

  const $=id=>document.getElementById(id);
  const esc=value=>String(value==null?'':value).replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));

  async function api(path,options={}){
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function dependencyText(item){
    const deps=Array.isArray(item.dependencies)?item.dependencies:[];
    if(!deps.length)return '';
    const labels=deps.map(dep=>{
      const title=(window.__leshyModulesByName&&window.__leshyModulesByName[dep.name])||dep.name;
      return title+(dep.enabled?'':' · выключен');
    });
    return 'Зависимости: '+labels.join(', ');
  }

  function statusText(item){
    if(item.enabled)return 'Включён';
    if(item.configured_enabled){
      const disabled=(item.dependencies||[]).filter(dep=>!dep.enabled).map(dep=>(window.__leshyModulesByName&&window.__leshyModulesByName[dep.name])||dep.name);
      if(disabled.length)return 'Ожидает зависимость: '+disabled.join(', ');
    }
    return 'Выключен';
  }

  function render(items){
    const host=$('leshyModuleList');
    if(!host)return;
    const list=Array.isArray(items)?items:[];
    window.__leshyModulesByName={};
    list.forEach(item=>{window.__leshyModulesByName[item.name]=item.title||item.name});
    if(!list.length){host.innerHTML='<div class="empty">Управляемые модули пока не зарегистрированы.</div>';return;}
    host.innerHTML=list.map(item=>{
      const configured=!!item.configured_enabled;
      const effective=!!item.enabled;
      const dep=dependencyText(item);
      return `<div class="item" data-module="${esc(item.name)}">
        <div class="row" style="align-items:flex-start">
          <div style="min-width:0;flex:1">
            <div class="item-title">${esc(item.title||item.name)}</div>
            <div class="item-body" style="margin-top:5px">${esc(item.description||'')}</div>
            ${dep?`<div class="item-meta">${esc(dep)}</div>`:''}
            <div class="item-meta ${effective?'good':(configured?'warn':'')}">${esc(statusText(item))}</div>
          </div>
          <button class="switch ${configured?'on':''}" type="button" role="switch" aria-checked="${configured?'true':'false'}" aria-label="${esc(item.title||item.name)}" data-module-toggle="${esc(item.name)}"></button>
        </div>
      </div>`;
    }).join('');
    host.querySelectorAll('[data-module-toggle]').forEach(button=>{
      button.addEventListener('click',()=>toggle(button.dataset.moduleToggle,button.classList.contains('on')));
    });
  }

  async function load(){
    const status=$('leshyModulesStatus');
    if(status)status.textContent='Загружаю состояние модулей…';
    try{
      const data=await api('/v1/modules');
      render(data.items||[]);
      if(status)status.textContent='Переключатели сохраняются в локальных настройках Лешего и действуют после перезапуска.';
    }catch(error){
      if(status)status.textContent='Ошибка загрузки модулей: '+error.message;
    }
  }

  async function toggle(name,current){
    const status=$('leshyModulesStatus');
    const button=document.querySelector(`[data-module-toggle="${CSS.escape(name)}"]`);
    if(button)button.disabled=true;
    if(status)status.textContent=(current?'Выключаю ':'Включаю ')+name+'…';
    try{
      const data=await api('/v1/modules/toggle',{method:'POST',body:JSON.stringify({name,enabled:!current})});
      render(data.items||[]);
      if(status)status.textContent=data.message||'Настройка сохранена.';
    }catch(error){
      if(status)status.textContent='Ошибка: '+error.message;
    }finally{
      if(button)button.disabled=false;
    }
  }

  function install(){
    if($('leshyModulesCard'))return;
    const pane=$('pane-settings');
    if(!pane)return;
    const card=document.createElement('div');
    card.id='leshyModulesCard';
    card.className='card';
    card.style.marginTop='12px';
    card.innerHTML=`
      <h3>Модули Лешего</h3>
      <div class="muted">Отдельные возможности можно включать и выключать независимо. Выключенный модуль не перехватывает запросы и не запускает свою фоновую работу.</div>
      <div class="list" id="leshyModuleList" style="margin-top:10px"></div>
      <div class="hint" id="leshyModulesStatus" style="margin-top:9px">Загрузка…</div>`;
    const firstCard=pane.querySelector('.card');
    if(firstCard)firstCard.insertAdjacentElement('beforebegin',card);else pane.appendChild(card);
    load();
  }

  function boot(){
    install();
    new MutationObserver(install).observe(document.documentElement,{childList:true,subtree:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
