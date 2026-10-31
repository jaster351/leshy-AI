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
