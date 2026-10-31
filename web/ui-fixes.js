(function(){
  'use strict';
  const $=id=>document.getElementById(id);

  function installStyle(){
    if($('leshyUiFixesStyle')) return;
    const style=document.createElement('style');
    style.id='leshyUiFixesStyle';
    style.textContent=`
      @media(min-width:1100px){
        body.full.leshy-context-open .workspace{grid-template-columns:272px minmax(0,1fr) 360px!important}
        body.full .context{position:static!important;right:auto!important;top:auto!important;bottom:auto!important;width:auto!important;height:100vh!important;z-index:auto!important;box-shadow:none!important;transform:none!important;display:none!important;grid-column:3!important;grid-row:1!important}
        body.full.leshy-context-open .context.open{display:block!important}
        body.full.leshy-context-open .main{grid-column:2!important;min-width:0!important}
      }
      @media(max-width:1099px){
        body.full .context{position:fixed!important;right:0!important;top:0!important;bottom:0!important;width:min(360px,92vw)!important;height:100vh!important;z-index:320!important;box-shadow:-16px 0 48px rgba(0,0,0,.38)!important}
      }
    `;
    document.head.appendChild(style);
  }

  function syncContextLayout(){
    const panel=$('contextPanel')||document.querySelector('.context');
    document.body.classList.toggle('leshy-context-open',!!(panel&&panel.classList.contains('open')));
  }

  function watchContext(){
    const panel=$('contextPanel')||document.querySelector('.context');
    if(!panel||panel.dataset.layoutFixBound==='1') return;
    panel.dataset.layoutFixBound='1';
    new MutationObserver(syncContextLayout).observe(panel,{attributes:true,attributeFilter:['class']});
    syncContextLayout();
  }

  async function request(path,options={}){
    const response=await fetch(path,Object.assign({headers:{'Content-Type':'application/json'}},options));
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok) throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function enhanceNasTest(){
    const button=$('nasTest');
    if(!button||button.dataset.smb1219Fix==='1') return;
    button.dataset.smb1219Fix='1';
    button.onclick=async function(){
      button.disabled=true;
      const status=$('nasStatus');
      if(status) status.textContent='Проверяю NAS…';
      try{
        const data=await request('/v1/nas/test',{method:'POST',body:'{}'});
        const connection=data.connection||{};
        const lines=[
          connection.connected?'SMB: доступ разрешён':'SMB: подключение не подтверждено',
          'Сервер: '+(connection.server||data.effective_server||'—'),
          'Torrent-папка: '+(data.torrent_watch||'—'),
          'Папка доступна: '+(data.torrent_watch_exists?'да':'нет')
        ];
        if(connection.using_existing_session){
          lines.push(connection.credential_conflict
            ? 'Windows уже подключена к NAS под другими учётными данными. Леший использует существующую рабочую SMB-сессию.'
            : 'Используется уже активная SMB-сессия Windows. Повторная авторизация не требуется.');
        }else if(connection.attempted===false && !data.smb_username){
          lines.push('Авторизация не выполнялась: логин не указан.');
        }else if(connection.using_saved_credentials){
          lines.push('Использованы сохранённые SMB-учётные данные.');
        }
        if(connection.note && !connection.using_existing_session) lines.push(connection.note);
        if(connection.error) lines.push('Ошибка: '+connection.error);
        if(status) status.textContent=lines.join('\n');
      }catch(error){
        if(status) status.textContent='Ошибка проверки NAS: '+error.message;
      }finally{
        button.disabled=false;
      }
    };
  }

  function boot(){
    installStyle();
    watchContext();
    enhanceNasTest();
    const observer=new MutationObserver(()=>{watchContext();enhanceNasTest();});
    observer.observe(document.documentElement,{childList:true,subtree:true});
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',boot,{once:true});
  else boot();
})();
