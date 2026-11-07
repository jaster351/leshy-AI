/* Telegram proxy controls in existing Settings > Telegram card. */
(function(){
  'use strict';
  const byId=id=>document.getElementById(id);
  const markup=[
    '<section id="tgProxyPanel" style="margin-top:14px;padding-top:12px;border-top:1px solid #545454">',
    '<div style="font-weight:600;margin-bottom:10px">Прокси для Telegram</div>',
    '<label style="display:flex;gap:8px;align-items:center;margin-bottom:10px"><input type="checkbox" id="tgProxyEnabled"> Использовать прокси</label>',
    '<label class="hint" for="tgProxyScheme">Протокол</label>',
    '<select class="field" id="tgProxyScheme" style="width:100%;margin:4px 0 9px"><option value="http">HTTP (CONNECT)</option><option value="socks5">SOCKS5</option></select>',
    '<label class="hint" for="tgProxyHost">Адрес прокси</label>',
    '<input class="field" id="tgProxyHost" type="text" autocomplete="off" placeholder="proxy.example.com" style="width:100%;margin:4px 0 9px">',
    '<label class="hint" for="tgProxyPort">Порт</label>',
    '<input class="field" id="tgProxyPort" type="number" min="1" max="65535" inputmode="numeric" placeholder="1080" style="width:100%;margin:4px 0 9px">',
    '<label style="display:flex;gap:8px;align-items:center;margin-bottom:10px"><input type="checkbox" id="tgProxyAuth"> Авторизация (логин/пароль)</label>',
    '<div id="tgProxyCredentials">',
    '<label class="hint" for="tgProxyUsername">Логин</label>',
    '<input class="field" id="tgProxyUsername" type="text" autocomplete="off" style="width:100%;margin:4px 0 9px">',
    '<label class="hint" for="tgProxyPassword">Пароль</label>',
    '<input class="field" id="tgProxyPassword" type="password" autocomplete="new-password" style="width:100%;margin:4px 0 9px">',
    '<div class="hint" id="tgProxyPasswordInfo"></div>',
    '</div>',
    '<button id="tgProxySave" type="button" class="button" style="width:100%;margin:8px 0">Сохранить прокси</button>',
    '<button id="tgProxyTest" type="button" class="button secondary" style="width:100%">Проверить соединение</button>',
    '<div id="tgProxyStatus" class="hint" role="status" aria-live="polite" style="margin-top:8px"></div>',
    '<div class="hint" style="margin-top:8px">Только Telegram. Прокси применяется без перезапуска. Пароль не показывается; учётные данные могут быть видны самому прокси-серверу.</div>',
    '</section>'
  ].join('');
  async function api(path,options){
    const response=await fetch(path,Object.assign({
      credentials:'same-origin',cache:'no-store',
      headers:{'Content-Type':'application/json'}
    },options||{}));
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||data.message||('HTTP '+response.status));
    return data;
  }
  function notify(message,bad){
    const node=byId('tgProxyStatus');
    if(node){node.textContent=message||'';node.style.color=bad?'#ff8888':'';}
  }
  function state(){
    const enabled=byId('tgProxyEnabled').checked;
    const auth=enabled&&byId('tgProxyAuth').checked;
    ['tgProxyScheme','tgProxyHost','tgProxyPort','tgProxyAuth'].forEach(id=>{
      byId(id).disabled=!enabled;
    });
    byId('tgProxyCredentials').hidden=!auth;
    byId('tgProxyUsername').disabled=!auth;
    byId('tgProxyPassword').disabled=!auth;
  }
  async function load(){
    const info=await api('/v1/telegram/proxy');
    byId('tgProxyEnabled').checked=!!info.enabled;
    byId('tgProxyScheme').value=info.scheme==='socks5'?'socks5':'http';
    byId('tgProxyHost').value=info.host||'';
    byId('tgProxyPort').value=info.port||'';
    byId('tgProxyAuth').checked=!!info.auth;
    byId('tgProxyUsername').value=info.username||'';
    byId('tgProxyPassword').value='';
    byId('tgProxyPasswordInfo').textContent=info.has_password
      ?'Пароль сохранён. Пустое поле означает «не менять».'
      :'Укажите пароль прокси.';
    state();
  }
  async function save(){
    const button=byId('tgProxySave');button.disabled=true;notify('Сохраняю настройки…');
    try{
      await api('/v1/telegram/proxy',{method:'POST',body:JSON.stringify({
        enabled:byId('tgProxyEnabled').checked,
        scheme:byId('tgProxyScheme').value,
        host:byId('tgProxyHost').value.trim(),
        port:Number(byId('tgProxyPort').value)||0,
        auth:byId('tgProxyAuth').checked,
        username:byId('tgProxyUsername').value,
        password:byId('tgProxyPassword').value
      })});
      await load();
      notify('Настройки сохранены. Следующие запросы Telegram используют выбранное соединение.');
    }catch(err){notify(err.message||'Не удалось сохранить',true);}
    finally{button.disabled=false;}
  }
  async function test(){
    const button=byId('tgProxyTest');button.disabled=true;notify('Проверяю связь с Telegram…');
    try{
      const answer=await api('/v1/telegram/proxy/test',{method:'POST',body:'{}'});
      notify(answer.message||'Соединение установлено');
    }catch(err){notify(err.message||'Нет соединения',true);}
    finally{button.disabled=false;}
  }
  function init(){
    const button=byId('tgSave');
    const card=button&&button.closest('.card');
    if(!card||byId('tgProxyPanel'))return;
    card.insertAdjacentHTML('beforeend',markup);
    byId('tgProxyEnabled').addEventListener('change',state);
    byId('tgProxyAuth').addEventListener('change',state);
    byId('tgProxySave').addEventListener('click',save);
    byId('tgProxyTest').addEventListener('click',test);
    load().catch(err=>notify(err.message||'Не удалось загрузить настройки',true));
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();