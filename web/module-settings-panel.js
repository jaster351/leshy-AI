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
      if(name==='distributed-cluster')await loadClusterStats();
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

  // Settings summary is deliberately aggregate-only: do not expose peers, IPs or secrets.
  async function loadClusterStats(){
    const state=$('leshyClusterStatus');
    if(!state)return;
    try{
      const data=await api('/v1/cluster/summary');
      const fill=(id,value)=>{const el=$(id);if(el)el.textContent=String(value)};
      fill('leshyClusterNodes',data.node_count??0);
      fill('leshyClusterBusy',data.busy_nodes??0);
      fill('leshyClusterAvailable',data.available_nodes??0);
      fill('leshyClusterCpu',data.cpu_threads??0);
      fill('leshyClusterRam',(data.ram_total_gb??0)+' ГБ');
      fill('leshyClusterGpu',data.gpu_count??0);
      fill('leshyClusterNpu',data.npu_count??0);
      state.textContent=data.enabled
        ?'Работает локальный кластер доверенных компьютеров (LAN). Статистика отражает обнаруженные узлы, а не всю будущую публичную сеть.'
        :'Модуль распределённого кластера выключен. Включить его можно переключателем «Распределённый кластер Лешего» выше.';
    }catch(error){
      state.textContent='Не удалось получить статистику кластера: '+error.message;
    }
  }

  function installClusterStats(){
    if($('leshyClusterCard'))return;
    const pane=$('pane-settings');if(!pane)return;
    const card=document.createElement('section');
    card.id='leshyClusterCard';
    card.className='card';
    card.style.marginTop='12px';
    card.innerHTML=`
      <div class="row" style="align-items:center;justify-content:space-between;gap:12px">
        <h3>Распределённые вычисления</h3>
        <button type="button" id="leshyClusterRefresh" aria-label="Обновить статистику вычислительной сети">Обновить</button>
      </div>
      <div class="muted">Чем больше компьютеров запускают Лешего и добровольно предоставляют ресурсы, тем выше общая вычислительная мощность сети и быстрее выполняются параллельные задачи. Это не означает, что один ответ ускоряется пропорционально количеству компьютеров.</div>
      <div class="list" style="margin-top:10px">
        <div class="item"><div class="item-title">Компьютеров в кластере: <strong id="leshyClusterNodes">—</strong></div><div class="item-meta">Заняты задачами: <strong id="leshyClusterBusy">—</strong> · Свободны: <strong id="leshyClusterAvailable">—</strong></div></div>
        <div class="item"><div class="item-title">Совокупные ресурсы обнаруженных узлов</div><div class="item-meta">CPU-потоков: <strong id="leshyClusterCpu">—</strong> · RAM: <strong id="leshyClusterRam">—</strong> · GPU: <strong id="leshyClusterGpu">—</strong> · NPU: <strong id="leshyClusterNpu">—</strong></div></div>
      </div>
      <div class="hint" id="leshyClusterStatus" role="status" style="margin-top:10px">Загрузка статистики…</div>
      <div class="muted" style="margin-top:8px">Планировщик учитывает текущую занятость, доступную память и ускорители, чтобы не направлять все запросы на единственный мощный компьютер. Публичная P2P-сеть пока не реализована; данные относятся к существующему доверенному LAN-кластеру. IP-адреса, личные данные и ключи здесь не отображаются.</div>`;
    const modules=$('leshyModulesCard');
    if(modules)modules.insertAdjacentElement('afterend',card);else pane.appendChild(card);
    $('leshyClusterRefresh').addEventListener('click',loadClusterStats);
    loadClusterStats();
  }

  function boot(){
    install();
    installClusterStats();
    new MutationObserver(()=>{install();installClusterStats()}).observe(document.documentElement,{childList:true,subtree:true});
    setInterval(()=>{const card=$('leshyClusterCard');if(card&&card.getClientRects().length&&document.visibilityState==='visible')loadClusterStats()},15000);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();

(function(){
  'use strict';

  const THEME_KEY='leshy.ui.theme';
  const FOREST_IMAGE='data:image/webp;base64,UklGRpIoAABXRUJQVlA4IIYoAABwHAGdASqEA/8BPulysVSpJjSxIbHZQpAdCWdCu94mLRTTRL+H0JkxFnQj1QVR3f1VBpzW+6yHcAO7IXqR3G/OvPmv8L/8nom+Of5fiz7wSVyKpS21T+9IeuaXQ7xarOsEFVx/zRwcEWRQPzaAzIMgrSi2OZlmgxBZXYfRxPeo6RRoGRjgZfuf9fnMLE8G/k2qlQMfhPYJMJ7BIe4noJLMRIkw7CuDwJ7RJpjS6JGsW0Qm93++dXzF5maZBxUkubQWlfNTr497i7PAt5qAq5Lwxe1pf7ZuimAUJqCUWCLsSJgxJO/zUmPaNEZN5Ap14HcICWVLLjZZ+xQwl29HKQ0bVtyJ/z5zC801z3pvfx8HUwaZ/2YoZ9uLNtVqLePy8aCqRYGf1gO6VdaL8qwWTrh1Nssmim1pCdARmfxFxLR4tXxSE0jsrI/iBRN6pIoEElZSbak3ZDpZK3PVIzBgAfd4/ghB+VJjHoaM6sDWYEmSJ9lOnSdHVEGts/hRX0amh8QP7Y4TvwQ2koD6F4fRQ4DBWjE5Gkn+mFf/oU2tg7Vnhm13kCVQpmzPXMoP0AU8ysBh1EyAGfI+lf1iNvViaA82gcicTXBqn/cV2TS6Br93/LDGUmgY9UTUIMBaeQaqIZq1jOEN4qNmyph4+24x2MSIbxmw60q8vLQCnCVzAy50IPFTwcU6LybXLKrJI9YPp4RyI6KnWN4nx3HlnZ6o+y3BgNBItDT5I3Ach2TRblbFk60aucUCaovC83fYagKESwB6RmjF5EfMkvKBXl6msEDbe2Rq0fWmHJzaP5/X+MUVD4ks/PB+ZaXij5kDnS2ehpSaqDOCseaCaNFjDUD1nN4UKRBKcfzJAnRR8DHpYmOkZNsbvpt8G/lC3ihXZgSIyzqHxPLMWfDbeokdutt0A4QEiA3mBNBT64ITrALTxG4DXqyNLKr0bS18glm/W/D1KN0/3g5k8zXwVURp4CxGEW+N0qZ0TbkhVoR3dXjjLAuuOnNkXfgOkYeql9S+sqz8G9oJbCjF7x8pUuyTfoLDW/hwlMLWvX00itwbKP8WCUxB4UYEXRPoQHI6iuPW6A04Hmch0t5yLn0YPlvp7LE4/ycnTufWwTxftvIcHfxucYSepns7YF0QaD/W/qVlJoSRF+XxKwUoKEAIWTwdu0G6QUBneyDCCZt4jNI8EgyTk+gJ2649ReQDRnKl0AebudnUL8Z5C+7iHhwDLPwZlAYPvJjg36nsokiU7SdN3oVla5gchuEQuwSsSQ5+ID67XgmJYdnbyojmnGgtbvMgWjWv3PInhYSW4lICRcQM0I0a83uQemgXEKQduZwmhwZR4YRA0r5Zm93CVYltsBkWf52qFr+XNH/1hsPD8R+MRFa4ODKnT/cCRjf/mkYYqSQL28JVJXWyk6zUTJtEQP/CsJszJTJLndg63fBw3e7AlujOfjQBNfNPIxRkNBaCvcaAgh9pNyHJvKMnaABau20z+ow0Oax08VnWIUzBLYCeMhIFdQtnTsWdvwLpWLVCIoovyWVCn69RFlkw8STd4LxJZdUpzCtOgWNYehfxRHqM/PDurBzLEsvfiXHdSXhV49R0PFBDoD7YkFJDE5LY5iVIT8JeKboFSCpWyuoqRVgvkChTWWDWBh7+31tSyZy2cy6vdndPltoPdgSIKTW7JqP8heJ+J/2r7XP7Ot1ZF9gJi+dRpYntqhAavhGjFaKkXrDD/lD7KyjVpPayOpFZS/AXYaqXRCrRzP9qB+7+VU3NIMoV5hZjPzti5UYTX8zWjPhI1/UyVX8SoovwR0DepNPTA90EdUSB+St9/6Exq4IM42/7f1v0xWF1/PEaXe9bVXS4aoXkIyIZJYD1yJ4JE1/w9Tcj5hqZPlAR1El4GgTgQZF97CzE1IECGniJSOjO2U+mNk2gffVrR3OL19dRXxbpbShUtGVlGCSSIt8Na9P+FZrl9clNSbuV1TizwoxGuIlWHpNpckMxpGxSxmAlgQv/JkFVU7BxjvcD5TKEWa8eXIe+r6pIHWCwcQmT9Ka+6YbuL26AlkY2Ed+peUTBi3FLKol4oFGfEVms4gqsmbmaxxDk/zZHQzd/EyBBxHLuhMmAc19T6dy61m9+kb5tKs7uZLuvE7NOLJToq8A2098AcG5LOZ9I0hNAl3R+yG4szdiDpaaF2miQ/gGbEnXHguYISWLhGa0EgoQKoN7Sxat+cRxGvBFp2LqhA51wDkbrUdoKjHPsSriEr1fFLuoIvFOrnsRnGDy0aU9LpYBt4erOAatbx1KOURuKhW5aptRNcEssZVayBIvkoYCwn1+yRv4+ZYlp/uiBDV7wlMVlWNuC2ZrlWW5JE6wfhnKQvufMCbPPCBbwmbyuL/1ekROA3y+mOFT8KE8tGTW16M7tNqYiNRfuHL2qWqottMC8eWzqYQ9i/Xf1vMik7HcUmvoOiQwAvy+NXY4JQiCHQJG0FYWVhZmCtvi+kgEOzlrdSX1JPeo2sSrgcevXgJiaF88SDWqWyTnW9maX1HJ/svErlFJwYBYo+apYHx3fUmbNJTSaa+JcQMqLfYaWtiW9t8f4gzimoLftyh5SG39ctrLK0mh9yAcTa9ABRJWpqRZFvu2q4+7Nx4zxpm0H83elBjAUW47S4wLiJaKF+USrFGnXUwZheNEaN4fh6ZISakiTLraPiBmUcGd/OX/TLEDKv1k+/kRrgyb6Pnk68AiAFO8EIXKhhOIqoBxv1avZ3ca9cE5AuX3MEwxL8tw/b3VTOe4KP22lvsPCgqvyRdI1/hL/FL/f53jVPN3hQ9Wk0yO2umh9xbUOBM2q9THyYVgnnVyAsGbPNTm3Ikj0cCa5ja4EUaAjU9FvjkAiEUSLVV+STx2b/B6Q1cWLBP5waoEMAHQ95q9BWX79//30ldA3yy+72U+iwMoJAoMpwmOAIVdf2hBODhoXjGHU6hw8yZtlE2RAzM5o+N77v52exS8YOr2ksFPQuzH1/sPqV6DsFfmZ+E4o4FJc0R/uyMsW5jdidIgfcAPB/LKifmj4uy+YMD8DMzIM9czAAP7yJGsk0vQgxmHs1KX9Yoe1bDs+sVQe/XLmKxX9a+FSlDc5yZtmFV4Ruc2LrgFI9CmDA+QhqpXulUEEHJ+SbjH3ugfdo5PWkuLZX3BdGTF0IyxuCjCXvcPQg5QwoY0268yXxfkK6mKD7tP7qAFNPxXJeHRjTNETAkEybhs2mw4WJ205rRC/7mK/OPeTDtqx/DRM/326uqJPp+qd4blf2+eox9qTogSaFCK8ojpNXjy5k6WfzhmsBu81IF41B3chCfHP9sv/StKgUldDnavqW9hF7lmVR4/rF50gmevPrbdoLETj6SwvVEirdwRwUcb3OAAtwZ1w7I/axvnvjIma6HsWrOk0xYKsWqz2FMoSl+RHYM9JiI8dmKmW25UtQblvj1WUNXFqAXpHNhnRja8KpfLiT/v4I+NXNO69GGJvln3BjwQiIODl++m8wpdntLRDWagtGSVsSAmJSNmyCXbnIuBxnZZpKg4x6v4WtPhZV/y4gN3kakPTPUGuLSTTrCW/MthjbGXa658wwLU4TH1E0ia0C31vslY7gG82HaDdNbuJCaoJov544zm6Ry8661+42ZG4v5GcQ5bblEe0Ea49OGeZOvMAWmSJnCkdjIEWtc+UQ3Efgb3lQ2U+ps5/21UmxHk0xB8khHV4PvNBSk7+6JwNifMd+dotv3wXlHs+SyKxVMKUiVQake6AiEGbgjdlsd3WtNCeYKe/mvLo8dXldJe1HfgRa8Qh9BTnPAO/OQaZT3NJmb/kVriwC9+XLw3w7DqP2bsxqiiqPSP371YjYuaZoXFooH+mT+ItsnGdA/4OPViDF6o7I6BmSUpshZLOBYKoDnCFOkACG0eBGUUfTeYuRFLZO4w+xBybmTYeovQXEzoKjGGAn0WowpkbpKTsvMbB3pIYH8WbNotIowvUrR0RzJ0ogaJctXzea8EwIyKWcu8fgNYUX+6IG5uJsFPYHXimrqfJqoeZeT6qnaKzaHmRnTl74WZ7KsFmMw96A7KwDRVGv51rQ8wmNnEdpdD+/N0xFOVazJCdA1pUsXxzGA1ghngJ7kdRJU0iTE3AKPtWWy0ET9WJC1gs4LQz8rkjCQX40OE/0j3qtmHkCuTGXl0V6Kup8ZfAMzbiNgGEkjPBsd6HzUncmURFF+nVyEN6ZeBFIu8ahLFa4SkGHUmfm/LQqciOwcXyKxzNnDekplC6UUsNmELhzZI0oKvjSc3QFr836JO0WlXfnTmxhHBO3Zn0Qb0AYQ9gHhrt4f37FvWa8OZmP/RkhB47aNxsETMb3PdZ6t3bzSctIygCiEC+pjDV/SsDsPFjDcVLleFQH/FIC2ajTdxQpQQ8gpvoAomdT38eaBnJKg+slZHrvlWT2iz5M/t/N4AhIZvSsDe8wvdIY2CauG+QphR1yhjOwOw/0qWOkYWRoRXGq4TwNAFpMRaKf+8L4S1oEPFWrpFHj9K9cX/ih6NDRwxqUDDbXyX9KAcA2iEk9OmAe3vnjKOGKWC6BVZU8+68U/sAJ5zF6GmKH4YeYYRPrLodhJR4ZSKONoY4YdTY6DGXoVDxNergRDp+an3otzFPYFYWUiRZx+qFS0FgM2h7odWiShgcofgoTUrG2/hhphB3q3PxW/UxI3XignZC+qSOaFMfwnE3oxgD1Ai8EV70TTjVbvEPSuhV5Jc2xITyNdIBsoS3olU78aD4U9u32WjDmubp1HvV5SYpCkxNolHtwobxCc+OBCCmoDMuuJefzCEK4GkneMzr9AMsrpUD0kp4e7QrJ2zvzDtF/sEu0FpQh+VznozpzpjgzxnhHJjBKQ2/E9BiCJ+AjDwXtyvnivuTfv9dhJStR33l0QXHDrx03bQyw5pDNmny2BLIZhQHd74FrkLSAfvX+FQoj/ZqGcvkamWlRAutjLG73hgYYX0pWDTtMOvID16FlMYLUONDqDEQynutbHAvE3W2ty97L98217FODVej1zg3XK0Kr+wWe+H12etEgtcXSi4fGqzMqoUi/0EH+QtP+yh2SCkIHEyyDO49rAWVFPHQ2wtAeZ/jhu5LpVZziYMILsZ94J8ElJt2cIBWbe6d0we44qm4/V+dNE6gdyrWm46n3NW63saOXMdPmYZ2/nYcl/Cbuxoa/9DXUHndlwfqt/tQY7rX/emhnHzqNT/FmkEmexU1kTccIucp8isfAcrNm0MUGpn6QyPVSl53Oe60VmoY4OS2cNatc12zfjmTA9zdWETFjjMWWwJWZ7SRXbgwNvbc5+wwGZL1CksZOxnYhuuDYnHDZ7at1ZnzLmLI5YbJ2bHrSiGk/CNL4DRsgHAgcDZ00hPpxjh4gFncCCF8pnwAqV3qPgLkVuS5fw7Qxt90gDPks6AACzkpHsA0jcH5Ril/aH6UtbS5n8eFQPqkayGIeptAuesRlTF48z+vRcjGv2Ql7mrqKd4SchzwYLvC7aTJAXbLENYK8bxjl0qnqFwMkwPqRkfqsDFMrHhoaaDbtZUCZh+KNASzWH1KQgi1rdK/M+ybLUdkY643NgD5mK9EQ0dwc3HGbGYE3jfUoGopCir/f8XoKplNQbCSp6inJhLw4zc47LaKhBpFuld4UPlScvB+M/9YqCEhInqeaI0bq0bmJokPjsErGQbUVpwQYaer8x2r5hhUxec66VAQeDdbS+aIgM+RDNEEN78Y6o5+T5re5zJjVJmaeYxiK8VdTy+0WGeDW0cFcmb9AVCd9PQjyHilZBgp/Bpv2BptV+1w+KDWCRdWyTVG7OBdare8AxdSz8iyZvBSpPWi2MDNfwbkR0xzdBnjx2Xze1sUQG+BqQSE8piPM9HILi7t81mUjNhWKn8XXOGJTcPgWm++hat1lNEbmJvjrFeWU8NJqC21Uk8J5JnT+mUSZI3SpKC3Bg/piNahLBAPlb+IUaiCrqIAbrpFiCtJAuuOEU80sfIYoeyVOzrTS0nDYuFtBlRu/bZ1J8dKRlPdV3I9JSfLtkFhXmIvfS2Md6/+7c+ouG4RSY1Zau5Tt8Z3rMg5xAH7K0dE4Z4BFI61BM+O9hHqNXPDmRiqm/vusmbtd81zmD94UWmsIFM9niifjLgnTWsRbgUOgtPhH/WA0wPDsrU/0UwpfioMyAQPocuccTNclzlZPZ0+VZeTCtPwmAPAGixP9T2rUwKPZj0ICOEOFjgyGvsT04h8wYsmjD3I2IdaiNWivPE73khO8yEOR5XTe5bhAcweJLvBP+S+0YlJg5XiE5DdrzhwNOwcinWXAFnAxXZSmc/b6G8ZWg3YLpIz9N4FplAAbRfvAyrd7CNxmkUArkQstoikU3RiFW/Fr5eZjNtPSjcn+qbAvxagn325OvrmFmkz1si0+bvm89V0ZSW0mm8sUFFHF3ikoTj34bxoiBEeXP7BRTwepkJEp6FBK51qjvZBgq+JjYLTWe7h0PWnnNOtxTe/s89xe24wVpyEFlUue9ZKLI5oBjK5sXYr1Sw0nrft1cAJIIb8+tp6sQ2nMLs36EqgumjUl8vTAEgX91bvT7oDIqQpeLhue+1r5dAjmIG+nF6h/mnMPb1D7kWsq8Iwt77Q2wnD963NYpwes81UGt/zcz3nGqzISVbFa1/nJ8TJ0eLlM/v6QPMrR2uvrehxK+UJiUtqsiNsn5kstQGdZIj/J5eJ/7Slyyaax4RO+Qb42AsJru6H/pPd2dstxh5t3bnp/zjGQWzwb4o79/dbAWFy17l5YjoFTDAxEQDpThp0/Lwu30ahNrVakCKAlM8U+wF79QcAxDeSzh68DRcEIJquyXGl4D9J0azAhuJiCJ8UNMYCnvrxhY7GtLZz7hc8EA4+syeaoaDPzKUl/zQqGnAGNXC9+eYss9H+cp52UkVTT3PM7GKWbhVAP4mUIIlSFgBz1NPbiaOaioECYwUB/38NPJFo2yzs7KKq/a40rl1qmqNGBbH+F7xDELJmjpB09Oe1uSu38NqPaU4YQ6fr+T6lOG9ih/ysX5szJtnt+hsGDqDIt2N26fbudWWszKpTTugo8XNpuh1vDwpi2VenJ01NYoUlQjGCqmNVvRMGbO1VKMWupryQX2J8PgK7Hn9E+LzFQeOcqcsqplSy7iJrHlQ8DzdLvkhItNkND3AiETfx7569LKy+DwMSwxH8EQgSMAB4UqV7uCv7SUxeo9G4DWzkDLA7eSs+MLEFOPV6rTTO0GSEtgDZAuDfdjppBIMp8bsQgkJJBle+PaQuSKZYqBhbw6nrENPOdugZCFt/2UkdlfCr7rlJAOhw8YkjYpg6+XZEKhVKckqoZwYGqKwAGWyGAEaAAAjzCutMWqZtRZwJ9T+lGB8/uAtl2R13Z3cX/FZI3BmVOWuhCKgxp2ukAgzMnMalPcYTD2eC6Hag/GcG9bVvihrsyKlEDsSrio8ncTT5wzyclYVCNiQ92fbxZhoJW6XJLbJFIv0P1uMnoY+4A/6oCoYcOY/RVyjiBrcE/ZoNxXn6IWS/0WezScWRV2qvf979gz04AAAAARRPbK9yMMVTJdqanfDsiHe9VKabI4Tjt7FXRF9Rx9g+jNi00BNUr+tfiILNCMD2hvdcHIq5DrEpfmHUd63fPs0KjdVVV69xMQWaP3wjCeWhS4pyTaArsQ6ee5V2h2FIj2iTqG7Z6fKVv1YEAAORmAOgxlVpzTJerTzPzfUcmms3KriVBYIDc+PuOJPSNpnCJlI3Wvt0k75BH1UiC3M2iHO3rsFNYk1zHvI9kDUAhq5s3aPKsD+grfcOSO0WdduCvahOrnwGO3+syG11ACpFGUB4tzgAq5dA8uzwPmHDPulR8OsUWqA8YqaMLK1aYKFkcVk3FAFgilgBm0ePKpBKaKM+6Hsyh8Jc/8IzulapVpoddidzQ8i1+VBo2CkOpEv8tyXP74vU6LAB5AYOIJpL7kdNmxBE3qcKVjw3c2Ud0EQAdoMpGhdIcnvih4ss1eAb2sWDAAAAAR0QOSQ9IoAiy2Y7JM9PWjGxmXSvpFpxLLPznakyR4AUAL06TWgiOupofBovpoDYWqcp2md+s8TJrbiXInfz46rh7rFoBNgRTIhEcCw+OCAgBsbU5FRMntpvNwft/t8AvsCuxZp5lY3v3121yiVOEvoXwzJxcQxxY+4AFS0oGSP13Whl8YBrq6ZifnuYjkAz96/SkJ6FZsPmNhBh00WwAGC6o546ErmWwArM4ga5MaJc7Ydgriidj/Q8zPfNhhAuIeDig0lJFkc5gAGUYbOcG3S+yRF6q6QKeyeVW2rE1/H3a7GDhfdmBlbZdDK/gDztnkTvRFdry+1gUHWN5NvgMPKaPIC4y8iWO4rrPqo1wfb/JDv6qR4YKmGWXkNgGPoGhC19FwHL9VKOx2z1JYKynRBKWETAlyBvWXJun8nufbwKpMz4/inDAtmi7sEHzkYIbcE1seSP0px5PB7aCMEhan3z2kRE9u7SDAGlRGn/47E+oT78MMHX1EpcAJe0USCxGyq+ZHqi2R3UJx16XFIBhoW/YZnYKWg8tG2DYUT/bw4veiz4O+46MJMKM6fVbbBD6XWk4a1dTiz32gHKSNR41AiZcm/u5FOt6xrbv0ZkbAz4qRWJPMdskWEDZrV6jQSEwuhVy8cjd3iJCZZSAMWqjzTb4x1iha0BqaRXKcUhUu4wZs9/dfwVssN5lfJzYN47iHDjcUOuXNFRgJ1uVw5F2ZCiM1zGbBqtdIEGnpDaybMcz+wfex3Urc1qsNdC8AQE+EcD0PWonbCgvfi5WdiRWKYxIL+X2g7hqvDeXZiWGDkuEQ/EfD44H02mRIbcWIMZqdxsJxhjib7Wun3ZBthLaCc/TCit+oWX7IcYMg9bF/4gRNCYphZShjOOOeZu3SKKkwlv1FGUpt/Aekvey/iPBw40M3BWsreiXWQGIYzYOFIbi/fx9K0ZDKtnvmGb8vt7THKyxK3muoQdn1CAZQ/dhQOeLNqGMsZvElYATlEd1QgK704eAW3TP0dpUz1nEONBO0SMIcCWxGgLyHksBUGorqFnv/MjBZsGSWRNX4fpVxpKS0clErRgNfoZ6pcMKVYcvVE+83JoxPj4lmSJn8EmWxKMI/9bZ02TjH60c+zh38PwSCPb5njW9CwMfkZxnp5AMToTpVX6djewW34oj2CHsNkaXVmioGNnzYPwQQ7vpaTvVFwBbHwIeCwb3O+qQ1m6u4nYJrg95B55y0zV2BUx8ZzSibV24hc0XFD4TwZUWZD3xFyfCq05vGvGEAbiKEIBpUxfM2Sr4DZ4dqPo3Yz3t5WxhXqR8IINmn9vlcV7KdV3rnMFEMjVH5ZimwpBeqDVtHYO1k+XqMkpbUlQ3gVzRsKZa6G/c8IXL0bCPq6eYRM2VyI8JpQY0eAbFxhVB9/QPMN1R8kTA39z51S9tIxaUpY8/ds0ldXStHMOrvnxQcwGvTSccZHaiRTiYreOksgl1OuROpswtkVs7yb8uY4cx6xXrYTPDI7LUJ8itkiiizeaXlp8mn6cB39yuofX/Gj3vJHzzA5rTTx5vlb0oixVNRGiE9XsRzi357Zu5lRnAzPX/cyVoa2K1rPGkPp8IERi2zscReJkjaYPwRazQBQpvPUT0Z5hUCsaOoz0rMYfKiaUKs3liI2E8w2x0kWqjU5tmjb84FXgtvRZ5IHeJVlTP5tCf9bvmxu+gh25dmP5UWMsgQ0xeydCb8YQDNXY5ysEIkjJVNsCPl09HVGx5M9Z44osKMqRzEwwvHG5se/QDAsxrZh7/nZ4nnhhaBC0YDTJjBM2KguVvuggXrwiOkd9TiDerhdG7Vw4cQZuASWIDVgxKp4/ggqt5e6i/uO6G8CYECql5ok0BUClHn7yHwhSXqI8fAbsl3k0K+hmoxVk3K7uOBJ/uK7R4R1vLr/alvVfJR5h59aYnv6n9RFURzMOVJvXrODeBMt8ZFSDeUmaVg08imZo40rsDqqmXr7fB+yC3r/LedVkEM/LHbyICIwulBuHePq3dFmz9GTRC2zCxfLWciEHnomGtFnfrNzocnvgCbR9V+8Tzbqyp8c0q4eSwCETvo5cUgH1S+UUTFuD9BYPlIp+O+PA30C16fSoACcEOSTraSQykWUE+uKHdyGhLkyjTx/Qc3nE/KkdVVRyUeb6uqOVc1g7w/TbSgdBYf01Gkyqrqmo0QQf54fZyIgratHyBrUA+dEJuRbn3VEVuwwyDIX0Rn6VK6QqUWtjWyBh6nRc7tf4deeBIH5zH6w1YUo5ZNazsEYWil9Uqr4a421rxW6InEbb7PnWB4lmJ32JOE7aBByi4T164V1AGV8ekHJuBTzUUqoPOAYGnYVNsoTQsIsRV9Ea5H6z60kBQTUa3P28Ls2ceNMO1sbGy0UaBH8dqnLRMQI2SAvFcNo2xYcZXdtMftMRMuGEVchhOTCs6M9jxpLE6bMquhwju/NUEQBJ06gnGceEf3p0LdGjNmai+7LlHrCyEtt4pczXO70xn0I7m0WuvtZQRS1c31md+VLX60YMcgRX9PwbEmwIzW3OvtG8ZTadgl+KCtfzcsi7JcNuf19zCdEg6pWrjoG100FikWRrMisxXgXfP1/Cl6j9+gTqwlEr3IhxzBGvlg7oOjmmkTDxQYibrNY/QIta1RId/KASJ66sjpYRDenDvZYWSzU6CIayWmlb0I+iE9aHq0UUdU1YMJIO40Tos7XYqxTj3ioEFo+G2k+l80acU7CLGRMI68g4j/OvzklLKgJtOh8DV6D2WdhYmfa/BukmqPddDD7LOKH3TtaNVG8GELd01HzR2V8Dyw4ojy1L/ANK8Reai+ddFLJMwrs+s8ISsTyaEMkwg+YaWUQqvmmtCjlQTc68xChKTb0jZMKv193Brfc2PeqOTBhysiauEcGvgAvPzBnU1eewcQUpk0FWAFyNfThikUUuayQV1NaZb4rNMOabuSyDNrBL/4PH4segOhYvdKPwV9y9nJ6BH83XvJlgTJRQ0Yq7cQpmEmKig29kqo1EH6E784Gv8odl/SEtdZ4Mea8uEqVU6mggxqvBZW3R1wH/Wyxmw5Ilz/uGZt0lnHxdQOpNoUHC9mIF1IC7MIORI6vRvupA7qBU+SXSaqJvcmj5HjVenWZvVazvsOJtr+M+5JH5s2nMYZkxkcod15zKlCOWJhqVXu7p5P+TAa/SE27kJzLgwzmfvXr05U82KNTay6/lIEKsNB0LhhwqWWK8fqhXso48juVOjZs6ubx/qQGq+ftWN1+qCTUcPXBrsfM2zhqevu4YNNVarrZXJqHFYAqC+5ueiPMAUePkdUCcR/UHc15xjeCl3GMy7bXEZYnXc0ebxEJLTGC/QSJ4W7bKOdeapOHFS2MQk4j/YNQ5bTsPwYKj3D5Cg47NujMn+n0Hz5IS/cJmpjnphzuFZiTntQbra7hgpnpTYayiLSmGlkaPL3zcZVgZe0w6r3GSKQMW3Z7VuypFFWPq+UVPIiqMEIxnpiv2XXUrkX6k+Z8sJyjZ0zVP6LK7WjZWgaz9JbXDzDTlYC/UUZ0rSY5pm2pH7X79SWrpOIOx/01DRJRBTgeitZNdoiNFvO87lkQbW4xkEgyEOVJvv0EnOuZ71AS7Sp8Ywer9LCSwx1RMqCwXBXESvExgaDlc1v6EOImO+YcNOao2Z0R9spPj3ocwacrWGqJjNqllQ+mJhDOuQfpxMO2gB3olaytDfv5WEMBSbV6IS3C5SGQBuWT7kaK8Dkj99cPhwiovKNoDhFpA+EohIyYI5gP5Dvhc+xfDVWJt/uF5bbo81vQlsW8wo1FSSSyksnpsChGKVlPhfRq/K9Mj/yM4mOpvw6XSEFGTc67YmWenPk3+xxd/5A3z5xrQqeGL9zr7Ipj6PWcnoeO5A8diseA2xnBuxBs2ynoMzf29Gdm5+xBkpUSZT699eyk0IHxP26wKNOPrWLg3KHri/3yJmhIvz4fAw4sODfkZK2d/EWpPDnK2VDA8EDytdJ74bvmfL8ImcngKzeldQWnawundPRYCteiNGxNgVd9gulfJsVUKivmbcAhkxinIAcOHcf+AKZ4wWr8nG029+RDpXCYk2rsfwKiXFLTyvUUsqXrTegp4oi4ojFiqKC66O2ARaSqnwlvExpLOMCaq3BSvdCVEcOJP30tARgx30riauzGXK1Bgkdkowc7zu7yueKZ63j3GBYW1bB9xdoeDgnxU14rYxIxd8q5c97B8+jeJnwvg+4p27wNqr7FeXVW8hL3e41RNCgS8TC1Byw09tLZJ73R1jHF1uY5fqHvuGg8t77ENr4PeNLkckhw8Y32/3EhHTzaJv1UayzljCQt+RYXpLoTHtc5tWNexFxMmsDgsCt7NZ5dotFTyQAeYcin9J0w2dPIyOsom8Sd8frLz0Bq3qOasURBb1U6I+8flDVEJPbIV92l+nEN/1w5nwxdQD6i9HHWExZa9ovyN5BET0B92f8WkGXhpGSNEXSHDTAe+sFv/W/ZYiXc+E004oCVcLNPIPsYM7LP3hev+xM3m3AkLNgPWUk7HPvP2hv9T7NdsvpK6xedLLOJn/LgV28gFQ1Sc+wMa+k5wJcEqyRY9o6LxAzFqsCI8fqg8FBCYAcuiwXkyKbequPhDEVo0ZuK4t/yrbAFrBScHLraRbQsIod5rxd9QlUu6PuuMH16eJ5AfGVcB1TbwkgRLTasgdJWW6LmU+SDwLXBmQWl55/KNH52RyRFnnnCcnVpEfIXYb7VvfEJDn6CgQYaMjeXvhSQycX0JANVSmhKIlgsmyCK8li1mWS/CLsvg45IYjftIbro+wRtMZxgVsqvtNa7a2crIFLr73NEBtXSlfItgARbiK3L0nVK3H0g5+mZQmctFL0rLXAyaeOxGg03mZE2AGbUCVt4Ls6OUe2FpfP/p7OF6IzTkAaWT/iILzYTaMR9okaenJaf5ojs7KWgapQ0xg1W5GROKvD3vbEjX9+zMqYj0UBvHzKLyS0MS/YqrrT+CswPQlJw/tNsv2zXOXp6pP74nq/uCCvLhbzBHB1vmpElsC2YElTiP2u8WoJ3e7mC0B9N4nDkztOBXspIUGZIdFTQ+UTpI/0TZAnFvTbG5yQZSsTHw/CIGosFiTwTGhwGg2fYX8NfkyLH63RLqraCGD7/CRaaMWbqCOENUWmziI9vVUV+4oJHqAMtmV+wgS1hoJulLp0KpKe/qGQyyaQwBv/HeGRewG3oQ4Me058fk5kRgZhCo9dGJrI4OB4eEadLHDfFm+34aoLNss7tOc26OP5/zLmU9I1qLFO5UVyGR04lLFrqts4Yqq9ILWHhTS2b698YZ1EEhu+D2/fFOI9I+v8Hdncv75LUAgG/TzL5fXCbtQfYP8iDHIlHOF0nr2kxKWR5IJCkHxoiZte52+KvFSv6YpvX4tQe1aSR3N43q16eUt6w466I0AEI166wjUJgRxjZwFQK8n+IeiedMiMUUkRyrCMVCmbP2JFh1IB4CmZUtpzebEXj1G6wZWXCt2YHXGmuI50pW+EDH41VdMRE6TA9gK/xuMzugeP1H169045BCFiBP4AU/b8id7nXCOv5kbDFTKxvvxP/zeKqXMiQwBlUOaKxmXNf4y/zcLAvc9hx2zMSyL57njoSaA80I3Vpj4TKPEVVyYBsISO7Z6RtOwJSUVma5v29IoFG5eD8oGOz2jzyyuugBi2g7eNu/4fUZRvycm5JNj2r8QtZgmbDmjuhRmVeVDTD2IUWT6PnzAOYvDvZqcWY9NXzkCHcbGXKiucwnopI6qr0VpPU5RnurpNPs0JeFhi8mUDGZ9IWS5dMs+jqF+DbT2ARz5Viq+ywhC/7FY4f5DoNtSud9BEx9Kf6jpIr4OPTzq4gAAAA=';

  const $=id=>document.getElementById(id);
  let raf=0;
  let autoPhase=0;

  function installStyle(){
    if($('leshyForestThemeStyle'))return;
    const style=document.createElement('style');
    style.id='leshyForestThemeStyle';
    style.textContent=`
      body.leshy-theme-forest{--cg-bg:#07110e;--cg-side:#07100c;--cg-hover:rgba(118,164,94,.16);--cg-text:#eef4ee;--cg-composer:rgba(19,31,23,.92)}
      body.leshy-theme-forest .workspace{background:#07110e!important}
      body.leshy-theme-forest .sidebar{background:linear-gradient(180deg,rgba(6,18,11,.98),rgba(8,19,12,.96))!important;border-right:1px solid rgba(146,192,112,.16)!important;box-shadow:inset -1px 0 rgba(188,220,155,.04)!important}
      body.leshy-theme-forest .sidebar .brand,body.leshy-theme-forest .sidebar-foot{background:transparent!important}
      body.leshy-theme-forest #sidebarHistory #sessionList .item:hover,body.leshy-theme-forest #sidebarHistory #sessionList .leshy-session-item:hover,body.leshy-theme-forest #sidebarNewChat:hover,body.leshy-theme-forest #chatSidebarNew:hover,body.leshy-theme-forest #profileMenuButton:hover{background:rgba(128,171,98,.13)!important}
      body.leshy-theme-forest #sidebarHistory #sessionList .active,body.leshy-theme-forest #sidebarHistory #sessionList [aria-current="true"]{background:rgba(130,182,88,.16)!important;box-shadow:inset 0 0 0 1px rgba(174,226,116,.18)}
      body.leshy-theme-forest .main,body.leshy-theme-forest .main-head{background:rgba(8,16,12,.88)!important}
      body.leshy-theme-forest .main-head{backdrop-filter:blur(18px);border-bottom:1px solid rgba(170,207,142,.10)!important}
      body.leshy-theme-forest #pane-chat{position:relative!important;isolation:isolate;background:#07110e!important;overflow:hidden!important;--forest-x:0px;--forest-y:0px}
      body.leshy-theme-forest #pane-chat::before{content:"";position:absolute;z-index:-4;inset:-34px;background-image:linear-gradient(rgba(2,12,10,.17),rgba(2,10,8,.36)),var(--leshy-forest-image);background-size:cover;background-position:center;transform:translate(var(--forest-x),var(--forest-y)) scale(1.055);transition:transform .13s linear;will-change:transform}
      body.leshy-theme-forest #pane-chat::after{content:"";position:absolute;z-index:-3;inset:0;pointer-events:none;background:radial-gradient(ellipse at 48% 45%,transparent 20%,rgba(2,9,7,.18) 62%,rgba(1,7,5,.52) 100%),linear-gradient(180deg,rgba(4,14,10,.05),rgba(4,13,9,.24));box-shadow:inset 0 0 120px rgba(0,0,0,.38)}
      #leshyForestMist{display:none}body.leshy-theme-forest #leshyForestMist{display:block;position:absolute;z-index:-2;left:-10%;right:-10%;top:36%;height:190px;pointer-events:none;background:radial-gradient(ellipse,rgba(171,199,180,.12),transparent 67%);filter:blur(25px);animation:leshyForestMist 18s ease-in-out infinite alternate}
      @keyframes leshyForestMist{from{transform:translateX(-2%)}to{transform:translateX(7%)}}
      #leshyFireflies{display:none}body.leshy-theme-forest #leshyFireflies{display:block;position:absolute;z-index:1;inset:0;pointer-events:none;overflow:hidden}
      #leshyFireflies i{position:absolute;width:4px;height:4px;border-radius:50%;background:#eaf57b;box-shadow:0 0 8px #e4ef72,0 0 17px rgba(226,239,114,.58);opacity:.62;animation:leshyFirefly var(--fd) ease-in-out infinite alternate,leshyFireflyGlow 2.1s ease-in-out infinite alternate;animation-delay:var(--delay)}
      @keyframes leshyFirefly{0%{transform:translate(0,0) scale(.65)}50%{transform:translate(calc(var(--fx)*.45),calc(var(--fy)*-.35)) scale(1)}100%{transform:translate(var(--fx),var(--fy)) scale(.85)}}
      @keyframes leshyFireflyGlow{from{opacity:.22}to{opacity:.92}}
      body.leshy-theme-forest .messages{position:relative!important;z-index:3!important;background:transparent!important}
      body.leshy-theme-forest #messages .msg{position:relative!important;color:#f0f4ef!important;text-shadow:0 1px 2px rgba(0,0,0,.34)}
      body.leshy-theme-forest #messages .msg.user,body.leshy-theme-forest #messages .msg.assistant{background:linear-gradient(180deg,rgba(16,31,21,.89),rgba(13,25,18,.85))!important;border:1px solid rgba(152,190,112,.22)!important;border-radius:20px!important;padding:13px 16px!important;box-shadow:0 12px 35px rgba(0,0,0,.20),inset 0 1px rgba(215,236,190,.03)!important;backdrop-filter:blur(8px)}
      body.leshy-theme-forest #messages .msg.user::before,body.leshy-theme-forest #messages .msg.assistant::before{content:"";position:absolute;left:8px;right:8px;top:-5px;height:9px;pointer-events:none;opacity:.83;background:radial-gradient(ellipse at 8% 55%,#5f7749 0 17%,transparent 19%),radial-gradient(ellipse at 24% 50%,#3f5d38 0 19%,transparent 21%),radial-gradient(ellipse at 43% 58%,#6b7e50 0 18%,transparent 20%),radial-gradient(ellipse at 62% 48%,#45623c 0 18%,transparent 20%),radial-gradient(ellipse at 82% 54%,#6a7a4d 0 18%,transparent 20%);filter:blur(.25px)}
      body.leshy-theme-forest #messages .msg.assistant{max-width:780px!important}
      body.leshy-theme-forest #messages .msg .who{color:#b8d391!important}
      body.leshy-theme-forest .chat-compose{z-index:5!important;background:linear-gradient(180deg,rgba(4,12,9,0),rgba(5,14,9,.68) 28%,rgba(5,14,9,.93) 100%)!important}
      body.leshy-theme-forest .chat-compose>.composer{background:linear-gradient(180deg,rgba(23,38,27,.95),rgba(18,31,22,.94))!important;border:1px solid rgba(167,207,118,.30)!important;box-shadow:0 10px 34px rgba(0,0,0,.31),0 0 0 1px rgba(111,151,75,.05),inset 0 1px rgba(223,239,202,.04)!important}
      body.leshy-theme-forest .chat-compose>.composer::before{content:"";position:absolute;pointer-events:none;left:16px;right:16px;top:-5px;height:10px;background:radial-gradient(ellipse at 8% 45%,#5b7446 0 16%,transparent 18%),radial-gradient(ellipse at 25% 50%,#405d38 0 18%,transparent 20%),radial-gradient(ellipse at 49% 48%,#677d4d 0 17%,transparent 19%),radial-gradient(ellipse at 72% 55%,#46633a 0 18%,transparent 20%),radial-gradient(ellipse at 91% 46%,#677e4d 0 16%,transparent 18%);opacity:.9}
      body.leshy-theme-forest #chatAttachButton{border-color:rgba(175,208,145,.28)!important}body.leshy-theme-forest #chatSend{background:#eef3e9!important;color:#172018!important;box-shadow:0 0 18px rgba(177,223,108,.12)}
      body.leshy-theme-forest #chatComposerHint{color:rgba(220,231,217,.58)!important}
      body.leshy-theme-forest #profileMenu,body.leshy-theme-forest #chatMoreMenu{background:rgba(14,28,19,.97)!important;border-color:rgba(154,191,121,.17)!important;backdrop-filter:blur(18px)}
      #leshyAppearanceCard .leshy-theme-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:11px}
      #leshyAppearanceCard .leshy-theme-choice{min-height:92px;border:1px solid var(--line);border-radius:14px;background:rgba(255,255,255,.025);color:var(--text);padding:12px;text-align:left;cursor:pointer}
      #leshyAppearanceCard .leshy-theme-choice.active{border-color:rgba(140,205,103,.55);background:rgba(109,166,78,.12);box-shadow:inset 0 0 0 1px rgba(166,219,109,.12)}
      #leshyAppearanceCard .leshy-theme-choice b{display:block;margin-bottom:5px}#leshyAppearanceCard .leshy-theme-choice span{display:block;color:var(--muted);font-size:11px;line-height:1.35}
      #leshyAppearanceCard .leshy-theme-preview{height:32px;border-radius:9px;margin-bottom:8px;border:1px solid rgba(255,255,255,.08)}
      #leshyAppearanceCard .leshy-theme-preview.classic{background:linear-gradient(135deg,#171717,#303030)}
      #leshyAppearanceCard .leshy-theme-preview.forest{background-image:linear-gradient(rgba(3,15,11,.15),rgba(3,15,11,.24)),var(--leshy-forest-image);background-size:cover;background-position:center 55%}
      @media(max-width:650px){#leshyAppearanceCard .leshy-theme-grid{grid-template-columns:1fr}}
      @media(prefers-reduced-motion:reduce){body.leshy-theme-forest #pane-chat::before{transition:none!important}#leshyForestMist,#leshyFireflies i{animation:none!important}}
    `;
    document.head.appendChild(style);
    document.documentElement.style.setProperty('--leshy-forest-image',`url("${FOREST_IMAGE}")`);
  }

  function installDecor(){
    const pane=$('pane-chat');
    if(!pane)return false;
    if(!$('leshyForestMist')){
      const mist=document.createElement('div');mist.id='leshyForestMist';pane.prepend(mist);
    }
    if(!$('leshyFireflies')){
      const box=document.createElement('div');box.id='leshyFireflies';
      for(let i=0;i<30;i++){
        const dot=document.createElement('i');
        dot.style.left=(3+Math.random()*94).toFixed(1)+'%';
        dot.style.top=(7+Math.random()*78).toFixed(1)+'%';
        dot.style.setProperty('--fd',(3.5+Math.random()*6.5).toFixed(2)+'s');
        dot.style.setProperty('--delay',(-Math.random()*7).toFixed(2)+'s');
        dot.style.setProperty('--fx',(-55+Math.random()*110).toFixed(0)+'px');
        dot.style.setProperty('--fy',(-45+Math.random()*90).toFixed(0)+'px');
        box.appendChild(dot);
      }
      pane.prepend(box);
    }
    return true;
  }

  function updateChoice(theme){
    document.querySelectorAll('[data-leshy-theme]').forEach(button=>button.classList.toggle('active',button.dataset.leshyTheme===theme));
  }

  function applyTheme(theme,save=true){
    const value=theme==='classic'?'classic':'forest';
    document.body.classList.toggle('leshy-theme-forest',value==='forest');
    if(save)localStorage.setItem(THEME_KEY,value);
    updateChoice(value);
    installDecor();
  }

  function installAppearance(){
    const pane=$('pane-settings');
    if(!pane)return false;
    if($('leshyAppearanceCard'))return true;
    const card=document.createElement('div');
    card.id='leshyAppearanceCard';
    card.className='card';
    card.style.marginTop='12px';
    card.innerHTML=`
      <h3>Оформление чата</h3>
      <div class="muted">Меняется только внешний вид. История чатов, поиск, профиль, меню и поведение кнопок остаются прежними.</div>
      <div class="leshy-theme-grid">
        <button type="button" class="leshy-theme-choice" data-leshy-theme="classic"><div class="leshy-theme-preview classic"></div><b>Обычная</b><span>Текущий тёмный интерфейс в стиле ChatGPT.</span></button>
        <button type="button" class="leshy-theme-choice" data-leshy-theme="forest"><div class="leshy-theme-preview forest"></div><b>Лес Лешего</b><span>Вечерний лес, мох, светлячки, туман и мягкий параллакс.</span></button>
      </div>`;
    const moduleCard=$('leshyModulesCard');
    if(moduleCard)moduleCard.insertAdjacentElement('beforebegin',card);
    else{
      const first=pane.querySelector('.card');
      if(first)first.insertAdjacentElement('beforebegin',card);else pane.appendChild(card);
    }
    card.querySelectorAll('[data-leshy-theme]').forEach(button=>button.addEventListener('click',()=>applyTheme(button.dataset.leshyTheme,true)));
    updateChoice(localStorage.getItem(THEME_KEY)||'forest');
    return true;
  }

  function installParallax(){
    const pane=$('pane-chat');
    if(!pane||pane.dataset.leshyForestParallax==='1')return false;
    pane.dataset.leshyForestParallax='1';
    const set=(x,y)=>{
      pane.style.setProperty('--forest-x',(x*11).toFixed(2)+'px');
      pane.style.setProperty('--forest-y',(y*7).toFixed(2)+'px');
    };
    pane.addEventListener('pointermove',event=>{
      if(!document.body.classList.contains('leshy-theme-forest')||event.pointerType==='touch')return;
      const rect=pane.getBoundingClientRect();
      set(((event.clientX-rect.left)/Math.max(1,rect.width)-.5)*2,((event.clientY-rect.top)/Math.max(1,rect.height)-.5)*2);
    });
    pane.addEventListener('pointerleave',()=>set(0,0));
    const animate=()=>{
      if(document.body.classList.contains('leshy-theme-forest')&&!pane.matches(':hover')){
        autoPhase+=.006;
        set(Math.sin(autoPhase)*.24,Math.cos(autoPhase*.72)*.17);
      }
      raf=requestAnimationFrame(animate);
    };
    raf=requestAnimationFrame(animate);
    return true;
  }

  function boot(){
    installStyle();
    const initial=localStorage.getItem(THEME_KEY)||'forest';
    applyTheme(initial,false);
    const sync=()=>{installAppearance();installDecor();installParallax();updateChoice(localStorage.getItem(THEME_KEY)||'forest');};
    sync();
    new MutationObserver(sync).observe(document.documentElement,{childList:true,subtree:true});
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
