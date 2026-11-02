(function(){
  'use strict';

  const ownerId='web:local';
  let busy=false;
  let lastResult='';

  async function api(path,options={}){
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function esc(value){
    return String(value==null?'':value).replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
  }

  function riskLabel(risk){
    return ({mutate:'Изменение данных',commit:'Внешнее действие',financial:'Оплата'})[risk]||risk||'Действие';
  }

  function ensurePanel(){
    let host=document.getElementById('leshyBrowserApprovalHost');
    if(host)return host;
    host=document.createElement('div');
    host.id='leshyBrowserApprovalHost';
    host.style.cssText='position:fixed;right:18px;bottom:18px;z-index:2147483000;width:min(420px,calc(100vw - 36px));font-family:inherit;pointer-events:none';
    document.body.appendChild(host);
    return host;
  }

  function card(item){
    const financial=item.risk==='financial';
    return `<section data-approval-card="${esc(item.approval_id)}" style="pointer-events:auto;background:#242424;color:#f2f2f2;border:1px solid ${financial?'#8f6a35':'#4a4a4a'};border-radius:16px;padding:15px 16px;margin-top:10px;box-shadow:0 18px 55px rgba(0,0,0,.38)">
      <div style="font-size:12px;color:${financial?'#f0bd72':'#aaa'};font-weight:650;letter-spacing:.02em">${esc(riskLabel(item.risk))} · требуется подтверждение</div>
      <div style="font-size:14px;line-height:1.45;margin-top:8px;white-space:pre-wrap">${esc(item.summary||item.description||item.action)}</div>
      <div style="font-size:11px;color:#999;margin-top:7px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(item.url||'')}</div>
      <div style="display:flex;gap:8px;margin-top:13px">
        <button data-resolve="approve" data-id="${esc(item.approval_id)}" style="border:0;border-radius:10px;padding:9px 13px;background:#ececec;color:#161616;font-weight:650;cursor:pointer">${financial?'Подтвердить оплату':'Подтвердить'}</button>
        <button data-resolve="deny" data-id="${esc(item.approval_id)}" style="border:1px solid #555;border-radius:10px;padding:9px 13px;background:#303030;color:#eee;cursor:pointer">Отмена</button>
      </div>
    </section>`;
  }

  function render(items){
    const host=ensurePanel();
    const list=Array.isArray(items)?items:[];
    const result=lastResult?`<section style="pointer-events:auto;background:#242424;color:#eee;border:1px solid #444;border-radius:14px;padding:12px 14px;margin-top:10px;box-shadow:0 18px 55px rgba(0,0,0,.32)"><div style="font-size:13px;white-space:pre-wrap">${esc(lastResult)}</div></section>`:'';
    host.innerHTML=list.map(card).join('')+result;
    host.querySelectorAll('[data-resolve]').forEach(button=>button.addEventListener('click',()=>resolve(button.dataset.id,button.dataset.resolve==='approve',button)));
  }

  async function load(){
    if(busy)return;
    try{
      const data=await api('/v1/browser/approvals?owner_id='+encodeURIComponent(ownerId));
      render(data.items||[]);
    }catch(_error){
      // Панель не мешает основному чату, если Browser Agent выключен/недоступен.
    }
  }

  async function resolve(id,approved,button){
    if(busy)return;
    busy=true;
    const siblings=button.closest('[data-approval-card]')?.querySelectorAll('button')||[];
    siblings.forEach(item=>item.disabled=true);
    const previous=button.textContent;
    button.textContent=approved?'Выполняю…':'Отменяю…';
    try{
      const data=await api('/v1/browser/approvals/resolve',{
        method:'POST',
        body:JSON.stringify({owner_id:ownerId,approval_id:id,approved:!!approved})
      });
      const detail=(data.data&&data.data.result)?String(data.data.result):'';
      lastResult=(approved?'Результат: ':'Отменено: ')+(detail||data.message||data.status||'готово');
      window.dispatchEvent(new CustomEvent('leshy-browser-action-result',{detail:data}));
      setTimeout(()=>{lastResult='';load();},15000);
    }catch(error){
      lastResult='Ошибка Browser Agent: '+error.message;
      button.textContent=previous;
    }finally{
      busy=false;
      load();
    }
  }

  function boot(){
    ensurePanel();
    load();
    setInterval(load,2200);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
