(function(){
  'use strict';

  const OWNER_KEY='leshy.owner_id';
  const RELOAD_KEY='leshy.identity.reload-owner';
  const originalFetch=window.fetch.bind(window);
  let pollTimer=null;

  const ownerId=()=>String(localStorage.getItem(OWNER_KEY)||'web:local').trim().slice(0,200)||'web:local';
  const esc=value=>String(value==null?'':value).replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));

  // Старые части Web UI ещё передают owner_id='web:local'. Пока они постепенно
  // переводятся на Owner Identity, подменяем только owner_id в запросах /v1/ на
  // подтверждённый UUID текущего браузера. Остальные поля/URL не меняются.
  window.fetch=function(input,init){
    try{
      const requestUrl=typeof input==='string'?input:(input&&input.url)||'';
      const url=new URL(requestUrl,location.origin);
      if(url.origin===location.origin&&url.pathname.startsWith('/v1/')){
        if(url.searchParams.has('owner_id'))url.searchParams.set('owner_id',ownerId());
        if(typeof input==='string')input=url.pathname+url.search+url.hash;
        else if(input instanceof Request)input=new Request(url.toString(),input);
        if(init&&typeof init.body==='string'&&(init.headers||{})){
          const contentType=String((init.headers instanceof Headers?init.headers.get('Content-Type'):(init.headers['Content-Type']||init.headers['content-type']))||'');
          if(contentType.includes('application/json')){
            try{
              const body=JSON.parse(init.body);
              if(body&&typeof body==='object'&&Object.prototype.hasOwnProperty.call(body,'owner_id')){
                body.owner_id=ownerId();
                init=Object.assign({},init,{body:JSON.stringify(body)});
              }
            }catch(_e){}
          }
        }
      }
    }catch(_e){}
    return originalFetch(input,init);
  };

  async function api(path,options={}){
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function shortId(value){const text=String(value||'');return text.length>16?text.slice(0,8)+'…'+text.slice(-6):text;}
  function status(text){const node=document.getElementById('ownerIdentityStatus');if(node)node.textContent=text||'';}

  function ensureProfileMenuButton(){
    const menu=document.getElementById('profileMenu');
    if(!menu||document.getElementById('ownerIdentityMenuButton'))return;
    const button=document.createElement('button');button.id='ownerIdentityMenuButton';button.type='button';button.textContent='Профиль';
    button.onclick=()=>{
      if(typeof window.showSection==='function')window.showSection('settings');
      menu.classList.remove('open');
      setTimeout(()=>document.getElementById('ownerIdentityCard')?.scrollIntoView({behavior:'smooth',block:'start'}),100);
    };
    const sep=menu.querySelector('.sep');
    if(sep)menu.insertBefore(button,sep);else menu.prepend(button);
  }

  function ensureCard(){
    let card=document.getElementById('ownerIdentityCard');if(card)return card;
    const pane=document.getElementById('pane-settings');if(!pane)return null;
    card=document.createElement('div');card.id='ownerIdentityCard';card.className='card';card.style.marginBottom='12px';
    card.innerHTML='<h3>Профиль Лешего</h3><div id="ownerIdentityBody" class="muted">Загрузка профиля…</div><div id="ownerIdentityStatus" class="hint" style="margin-top:9px"></div>';
    const first=pane.querySelector('.card');if(first)first.insertAdjacentElement('beforebegin',card);else pane.appendChild(card);
    return card;
  }

  function render(data){
    ensureCard();
    const host=document.getElementById('ownerIdentityBody');if(!host)return;
    const telegram=Array.isArray(data.telegram)?data.telegram:[];
    const linked=telegram[0]||null;
    const who=linked?(linked.label||('@'+(linked.username||''))||('Telegram '+linked.user_id)):'';
    host.innerHTML=`
      <div class="row"><span>Профиль</span><b>${esc(data.display_name||'Профиль Лешего')}</b></div>
      <div class="row"><span>Owner ID</span><span class="mono" title="${esc(data.owner_id||'')}">${esc(shortId(data.owner_id))}</span></div>
      <div class="divider"></div>
      <div class="item-title">Telegram</div>
      ${linked?`
        <div class="item-meta good" style="margin-top:6px">✓ Привязан: ${esc(who||('ID '+linked.user_id))}</div>
        <div class="item-meta">Telegram ID: ${esc(linked.user_id)}</div>
        <div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:9px"><button class="button secondary small" id="ownerIdentityUnlink">Отвязать Telegram</button></div>
      `:`
        <div class="item-meta warn" style="margin-top:6px">Telegram пока не привязан к этому Web-профилю.</div>
        <div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:9px"><button class="button small" id="ownerIdentityLink">Привязать Telegram / войти</button></div>
      `}
      <div id="ownerIdentityLinkBox" style="margin-top:9px"></div>
      <div class="item-meta" style="margin-top:10px">Другие пользователи могут общаться с Лешим в Telegram без Web-авторизации. Пока они сами не привяжут профиль, у каждого остаётся отдельная сессия и отдельный owner.</div>
      <div class="item-meta" style="margin-top:5px">Привязка объединяет Web, Telegram, память и подключённые сервисы. Telegram username используется только для отображения; идентичность подтверждается неизменяемым Telegram user_id.</div>`;
    document.getElementById('ownerIdentityLink')?.addEventListener('click',startLink);
    document.getElementById('ownerIdentityUnlink')?.addEventListener('click',()=>unlink(linked.user_id));
  }

  async function loadIdentity({reloadIfChanged=true}={}){
    ensureCard();ensureProfileMenuButton();
    try{
      const before=ownerId();
      const data=await api('/v1/identity/status?owner_id='+encodeURIComponent(before));
      const canonical=String(data.owner_id||'').trim();
      if(canonical&&canonical!==before){
        localStorage.setItem(OWNER_KEY,canonical);
        if(reloadIfChanged&&sessionStorage.getItem(RELOAD_KEY)!==canonical){
          sessionStorage.setItem(RELOAD_KEY,canonical);
          location.reload();
          return data;
        }
      }
      if(canonical)sessionStorage.removeItem(RELOAD_KEY);
      render(data);status(data.telegram_linked?'Web и Telegram используют единый профиль.':'Web-профиль готов. Telegram можно привязать одноразовым кодом.');
      return data;
    }catch(error){status('Ошибка профиля: '+error.message);return null;}
  }

  async function startLink(){
    status('Создаю одноразовый код…');
    try{
      const data=await api('/v1/identity/telegram/link',{method:'POST',body:JSON.stringify({owner_id:ownerId()})});
      const box=document.getElementById('ownerIdentityLinkBox');if(!box)return;
      const deep=data.deep_link?`<a class="button small" style="display:inline-block;text-decoration:none" target="_blank" rel="noopener noreferrer" href="${esc(data.deep_link)}">Открыть Telegram</a>`:'';
      box.innerHTML=`<div class="item" style="margin-top:7px"><div class="item-title">Код: <span class="mono" style="font-size:15px">${esc(data.code)}</span></div><div class="item-body">Отправьте боту команду <span class="mono">/link ${esc(data.code)}</span> или откройте Telegram кнопкой ниже. Код действует 5 минут и используется один раз.</div><div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:8px">${deep}<button class="button secondary small" id="ownerIdentityCopyCode">Копировать код</button></div></div>`;
      document.getElementById('ownerIdentityCopyCode')?.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(String(data.code));status('Код скопирован.')}catch(_e){status('Не удалось скопировать код.')}});
      status(data.telegram_already_linked?'Подтвердите в Telegram — этот браузер войдёт в Ваш существующий профиль.':'Жду подтверждение из Telegram…');
      clearInterval(pollTimer);let ticks=0;
      pollTimer=setInterval(async()=>{
        ticks++;
        const profile=await loadIdentity({reloadIfChanged:true});
        if(profile&&profile.telegram_linked){clearInterval(pollTimer);pollTimer=null;}
        if(ticks>155){clearInterval(pollTimer);pollTimer=null;status('Время кода истекло. Создайте новый код.');}
      },2000);
    }catch(error){status('Не удалось создать код: '+error.message);}
  }

  async function unlink(userId){
    if(!window.confirm('Отвязать Telegram от этого Web-профиля? В Telegram снова будет отдельная сессия.'))return;
    status('Отвязываю Telegram…');
    try{
      const data=await api('/v1/identity/telegram/unlink',{method:'POST',body:JSON.stringify({owner_id:ownerId(),telegram_user_id:String(userId||'')})});
      render(data);status('Telegram отвязан. Его дальнейшие сообщения снова будут отдельным профилем.');
    }catch(error){status('Ошибка: '+error.message);}
  }

  function boot(){
    ensureCard();ensureProfileMenuButton();loadIdentity();
    new MutationObserver(()=>{ensureCard();ensureProfileMenuButton();}).observe(document.documentElement,{childList:true,subtree:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
