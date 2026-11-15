(function(){
  'use strict';

  const OWNER_KEY='leshy.owner_id';
  const RELOAD_KEY='leshy.identity.reload-owner';
  const originalFetch=window.fetch.bind(window);
  let pollTimer=null;
  let currentProfile=null;
  let photoRefreshTimer=null;
  let photoRefreshAttempts=0;

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

  function applySidebarProfile(data){
    if(!data)return;
    const profile=data.profile||{};
    const isSigned=profile.provider&&profile.provider!=='local';
    const display=isSigned?String(profile.name||data.display_name||'Пользователь').trim():'Гость';
    const provider=isSigned?String(profile.provider||'').toUpperCase():'Гостевой профиль';
    let avatarUrl=String(profile.avatar_url||'').trim();
    const name=document.querySelector('#profileCopy b');
    const subtitle=document.querySelector('#profileCopy span');
    const avatar=document.getElementById('profileAvatar');
    if(avatar&&avatarUrl&&avatar.dataset.profileAvatarFailed===avatarUrl){
      if(Date.now()<Number(avatar.dataset.profileAvatarRetryAt||0))avatarUrl='';
      else avatar.dataset.profileAvatarFailed='';
    }
    if(name&&name.textContent!==display)name.textContent=display;
    if(subtitle&&subtitle.textContent!==provider)subtitle.textContent=provider;
    if(avatar){
      const old=avatar.dataset.profileAvatarUrl||'';
      const initials=display.slice(0,1).toUpperCase()||'Г';
      if(old!==avatarUrl || (!avatarUrl&&avatar.textContent!==initials)){
        avatar.dataset.profileAvatarUrl=avatarUrl;
        avatar.replaceChildren();
        if(avatarUrl){
          const img=document.createElement('img');
          img.src=avatarUrl;img.alt=display;
          img.referrerPolicy='no-referrer';
          img.width=32;img.height=32;
          img.style.cssText='width:32px;height:32px;object-fit:cover;border-radius:50%';
          img.onerror=()=>{
            const attempts=Number(avatar.dataset.profileAvatarRetryCount||0)+1;
            const delay=Math.min(30000,attempts*5000);
            avatar.dataset.profileAvatarRetryCount=String(attempts);
            avatar.dataset.profileAvatarFailed=avatarUrl;
            avatar.dataset.profileAvatarRetryAt=String(Date.now()+delay);
            avatar.dataset.profileAvatarUrl='';
            avatar.replaceChildren();
            avatar.textContent=initials;
            // Network/proxy errors can be temporary; never permanently blacklist
            // a locally served image just because the first fetch failed.
            if(attempts<=4)setTimeout(()=>{if(currentProfile)applySidebarProfile(currentProfile);},delay+100);
          };
          img.onload=()=>{
            avatar.dataset.profileAvatarRetryCount='0';
            avatar.dataset.profileAvatarFailed='';
            avatar.dataset.profileAvatarRetryAt='0';
          };
          avatar.appendChild(img);
        }else avatar.textContent=initials;
      }
    }
  }

  function render(data){
    ensureCard();
    const host=document.getElementById('ownerIdentityBody');if(!host)return;
    const telegram=Array.isArray(data.telegram)?data.telegram:[];
    const linked=telegram[0]||null;
    const who=linked?(linked.label||('@'+(linked.username||''))||('Telegram '+linked.user_id)):'';
    const avatar=data.avatar||{};
    const profile=data.profile||{};
    const displayName=profile.name||data.display_name||'Гостевой профиль';
    const displayAvatar=profile.avatar_url||avatar.url||'';
    const providers=Array.isArray(data.providers)?data.providers:[];
    const oauth=providers.filter(item=>item.type==='oauth');
    const providerDetails=oauth.map(item=>`<div class="item-meta" style="margin:4px 0"><b>${esc(item.title)}:</b> ${esc(item.reason||'Недоступно')}</div>`).join('');
    host.innerHTML=`
      <div class="row"><span>Профиль</span><b>${esc(profile.provider==='telegram'?displayName:'Гостевой профиль')}</b></div>
      ${profile.provider==='telegram'?'<div class="item-meta good">✓ Подтверждён через Telegram</div>':'<div class="item-meta">Гостевой доступ: регистрация для общения не требуется.</div>'}
      ${displayAvatar?`<img src="${esc(displayAvatar)}" alt="Фото профиля" referrerpolicy="no-referrer" width="54" height="54" style="border-radius:50%;object-fit:cover;margin:6px 0">`:''}
      <div class="row"><span>Owner ID</span><span class="mono" title="${esc(data.owner_id||'')}">${esc(shortId(data.owner_id))}</span></div>
      ${profile.provider==='telegram'?`
        ${profile.username?`<div class="row"><span>Telegram</span><b>@${esc(profile.username)}</b></div>`:''}
        ${profile.language_code?`<div class="row"><span>Язык профиля</span><b>${esc(profile.language_code)}</b></div>`:''}
      `:''}
      <div class="divider"></div>
      <div class="item-title">Аватар Gravatar</div>
      <div class="item-meta" style="margin:5px 0">Не используется для входа. При включении браузер получает изображение с Gravatar по хешу email.</div>
      <div style="display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:8px 0">
        ${avatar.url?`<img id="ownerIdentityAvatar" src="${esc(avatar.url)}" alt="Аватар" referrerpolicy="no-referrer" width="52" height="52" style="border-radius:50%;object-fit:cover">`:''}
        <input id="ownerIdentityGravatarEmail" type="email" autocomplete="off" placeholder="Email для аватара" style="min-width:190px;flex:1" />
        <button class="button small" id="ownerIdentitySetGravatar">Применить</button>
        ${avatar.url?'<button class="button secondary small" id="ownerIdentityClearGravatar">Убрать</button>':''}
      </div>
      <div class="divider"></div>
      <div class="item-title">Вход на другом компьютере по IP</div>
      <div class="item-meta" style="margin:5px 0">На устройстве с текущим профилем создайте одноразовый код. На другом откройте Лешего по локальному IP и введите его здесь. Код действителен 5 минут. Только для доверенной локальной сети: на HTTP код не защищён от перехвата в сети.</div>
      <div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:8px">
        <button class="button small" id="ownerIdentityBrowserPair">Получить код для другого ПК</button>
        <input id="ownerIdentityBrowserCode" placeholder="Одноразовый код с первого ПК" autocomplete="off" spellcheck="false" style="flex:1;min-width:180px" />
        <button class="button secondary small" id="ownerIdentityBrowserAccept">Войти по коду</button>
      </div>
      <div id="ownerIdentityBrowserPairCode" class="item-meta mono" style="margin:7px 0;overflow-wrap:anywhere"></div>
      <div class="divider"></div>
      <div class="item-title">Внешние провайдеры</div>
      <div class="item-meta" style="margin:5px 0">Google, Apple и VK ID: вход не включён. Для Web OAuth необходима регистрация приложения и допустимый адрес возврата; обычный LAN-IP не является таким адресом. Вход по IP и привязка Telegram продолжают работать независимо.</div>
      ${providerDetails}
      <div class="divider"></div>
      <div class="item-title">Войти через Telegram</div>
      <div class="item-meta" style="margin:6px 0">Без внешнего домена. Откройте бота и подтвердите вход кнопкой после проверки кода на экране. Гостевые диалоги сохраняются.</div>
      ${data.telegram_login_button_available?'<button class="button small" id="ownerIdentityTelegramLogin">Войти через Telegram</button>':'<div class="item-meta warn">Чтобы войти через Telegram, настройте бот и дождитесь подключения.</div>'}
      <div id="ownerIdentityTelegramLoginBox" class="item-meta" style="margin:9px 0"></div>
      <div class="divider"></div>
      <div class="item-title">Привязка Telegram (резервный способ)</div>
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
    document.getElementById('ownerIdentitySetGravatar')?.addEventListener('click',setGravatar);
    document.getElementById('ownerIdentityClearGravatar')?.addEventListener('click',clearGravatar);
    document.getElementById('ownerIdentityBrowserPair')?.addEventListener('click',startBrowserPair);
    document.getElementById('ownerIdentityBrowserAccept')?.addEventListener('click',acceptBrowserPair);
    document.getElementById('ownerIdentityAvatar')?.addEventListener('error',e=>e.target.style.display='none');
    document.getElementById('ownerIdentityTelegramLogin')?.addEventListener('click',startTelegramLogin);
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
      currentProfile=data;
      render(data);
      applySidebarProfile(data);
      // Telegram confirms the browser before the Bot API photo is fetched.
      // Refresh only while the server reports the avatar as pending; no
      // repeated login and no endless polling for users without photos.
      if(data.profile?.provider==='telegram'&&data.profile.photo_pending){
        if(!photoRefreshTimer&&photoRefreshAttempts<18){
          photoRefreshTimer=setTimeout(()=>{
            photoRefreshTimer=null;
            photoRefreshAttempts++;
            loadIdentity({reloadIfChanged:false});
          },4000);
        }
      }else{
        if(photoRefreshTimer){clearTimeout(photoRefreshTimer);photoRefreshTimer=null;}
        photoRefreshAttempts=0;
      }
      status(data.telegram_linked?'Web и Telegram используют единый профиль.':'Гостевой профиль сохранён. Можно общаться без авторизации.');
      return data;
    }catch(error){status('Ошибка профиля: '+error.message);return null;}
  }

  async function setGravatar(){
    const field=document.getElementById('ownerIdentityGravatarEmail');
    const email=String(field?.value||'').trim();
    if(!email){status('Укажите email, к которому привязан Gravatar.');return;}
    try{
      await api('/v1/identity/gravatar',{method:'POST',body:JSON.stringify({email})});
      await loadIdentity({reloadIfChanged:false});
      status('Аватар Gravatar включён. Email не хранится в Лешем, сохраняется его SHA-256.');
    }catch(error){status('Ошибка аватара: '+error.message);}
  }

  async function clearGravatar(){
    try{
      await api('/v1/identity/gravatar/clear',{method:'POST',body:'{}'});
      await loadIdentity({reloadIfChanged:false});
      status('Gravatar отключён.');
    }catch(error){status('Ошибка отключения аватара: '+error.message);}
  }

  async function startBrowserPair(){
    try{
      const result=await api('/v1/identity/browser/pair',{method:'POST',body:'{}'});
      const node=document.getElementById('ownerIdentityBrowserPairCode');
      if(node)node.textContent='Код: '+String(result.code)+' (5 минут, один раз)';
      status('Код создан. Введите его на другом устройстве.');
    }catch(error){status('Не удалось создать код: '+error.message);}
  }

  async function acceptBrowserPair(){
    const code=String(document.getElementById('ownerIdentityBrowserCode')?.value||'').trim();
    if(!/^[a-fA-F0-9]{32}$/.test(code)){
      status('Введите 32-значный код с первого устройства.');return;
    }
    try{
      const result=await api('/v1/identity/browser/accept',{
        method:'POST',body:JSON.stringify({code})
      });
      if(result.owner_id){
        localStorage.setItem(OWNER_KEY,String(result.owner_id));
        status('Профиль подтверждён. Обновляю интерфейс…');
        location.reload();
      }
    }catch(error){status('Не удалось войти по коду: '+error.message);}
  }

  async function startTelegramLogin(){
    status('Создаю запрос входа через Telegram…');
    try{
      const result=await api('/v1/identity/telegram/login/start',{
        method:'POST',body:'{}'
      });
      const box=document.getElementById('ownerIdentityTelegramLoginBox');
      if(!box)return;
      const link=String(result.deep_link||'');
      if(!/^https:\/\/t\.me\/[A-Za-z0-9_]+\?start=auth_[A-Za-z0-9_-]+$/.test(link)){
        throw new Error('Некорректная ссылка Telegram');
      }
      box.innerHTML=`<div class="item"><div class="item-title">Код проверки: <span class="mono">${esc(result.short_code)}</span></div><div class="item-meta">Он должен совпадать с кодом, который пришлёт бот. Если Вы не открывали запрос — нажмите «Отменить».</div><a href="${esc(link)}" class="button small" style="display:inline-block;text-decoration:none;margin-top:7px" target="_blank" rel="noopener noreferrer">Открыть Telegram и подтвердить</a></div>`;
      status('Откройте Telegram по кнопке и подтвердите вход в личном чате с ботом.');
      clearInterval(pollTimer);
      let tries=0;
      pollTimer=setInterval(async()=>{
        tries++;
        try{
          const state=await api('/v1/identity/telegram/login/status');
          if(state.status==='confirmed'){
            clearInterval(pollTimer);pollTimer=null;
            if(state.owner_id)localStorage.setItem(OWNER_KEY,String(state.owner_id));
            status('Telegram подтвердил вход. Загружаю Ваш профиль…');
            location.reload();return;
          }
          if(state.status==='denied'||state.status==='expired'){
            clearInterval(pollTimer);pollTimer=null;
            status(state.status==='denied'?'Вход отклонён в Telegram.':'Срок ожидания истёк. Создайте новый запрос.');
            return;
          }
        }catch(error){status('Ожидание Telegram: '+error.message);}
        if(tries>=155){
          clearInterval(pollTimer);pollTimer=null;
          status('Срок ожидания истёк. Создайте новый запрос.');
        }
      },2000);
    }catch(error){status('Не удалось начать вход: '+error.message);}
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
    new MutationObserver(()=>{ensureCard();ensureProfileMenuButton();applySidebarProfile(currentProfile);}).observe(document.documentElement,{childList:true,subtree:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
