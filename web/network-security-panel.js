(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  let busy=false,portsDirty=false,acmeDirty=false,localDirty=false,pollTimer=0,lastStatus=null;
  const pendingActions=new Set();

  async function api(path,body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),25000);
    try{
      const response=await fetch(path,{
        method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,
        headers:{'Content-Type':'application/json','X-Leshy-UI':'network-security'},
        body:body===undefined?undefined:JSON.stringify(body),
      });
      const data=await response.json().catch(()=>{throw new Error('Сервер вернул некорректный ответ. Результат действия не подтверждён; проверьте состояние перед повтором.')});
      if(!response.ok){const error=new Error(data?.error||('HTTP '+response.status));error.status=response.status;throw error}
      const settings=data?.settings;
      if(!settings||typeof settings!=='object'||Array.isArray(settings)||typeof settings.hostname!=='string'||
        ['http_enabled','https_enabled'].some(key=>typeof settings[key]!=='boolean')||
        ['http_port','https_port'].some(key=>!Number.isInteger(settings[key])||settings[key]<1||settings[key]>65535)){
        throw new Error('Сервер не подтвердил состояние подключения. Результат действия неизвестен; проверьте состояние перед повтором.');
      }
      return data;
    }catch(error){
      if(error.name==='AbortError')throw new Error('Сервер не ответил вовремя. Проверьте состояние перед повторным действием.');
      throw error;
    }finally{clearTimeout(timer)}
  }

  function note(text,bad=false){const node=$('networkSecurityNote');if(node){node.textContent=text||'';node.classList.toggle('bad',bad)}}
  function secureUpload(){
    const host=location.hostname.toLowerCase().replace(/^\[|\]$/g,'');
    return location.protocol==='https:'||['localhost','::1'].includes(host)||(/^127(?:\.\d{1,3}){3}$/.test(host)&&host.split('.').every(x=>Number(x)<=255));
  }

  function ensure(){
    if($('networkSecurityCard'))return true;const pane=$('pane-settings');if(!pane)return false;
    const style=document.createElement('style');style.textContent=`
      #networkSecurityCard{margin-top:12px}#networkSecurityCard[hidden]{display:none!important}
      #networkSecurityCard .network-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px;margin:12px 0}
      #networkSecurityCard label{display:grid;gap:6px;font-size:12px;line-height:1.45;min-width:0}#networkSecurityCard .network-check{display:flex;align-items:flex-start;gap:8px}
      #networkSecurityCard input:not([type=checkbox]),#networkSecurityCard select,#networkSecurityCard textarea{box-sizing:border-box;min-width:0;width:100%}#networkSecurityCard textarea{resize:vertical}#networkSecurityCard input[type=file]{font-size:12px;max-width:100%}
      #networkSecurityCard .network-actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}#networkSecurityCard button{min-height:40px}
      #networkSecurityCard .network-status{white-space:pre-line;overflow-wrap:anywhere;font-size:12px;line-height:1.55;padding:10px 0}#networkSecurityCard details{margin-top:12px;border-top:1px solid var(--line,#36463f);padding-top:12px}#networkSecurityCard summary{cursor:pointer;font-size:14px}
      #networkSecurityCard .bad{color:#ffb4a8}#networkSecurityCard a{color:#a8dfca}#networkSecurityCard [hidden]{display:none!important}
    `;document.head.appendChild(style);
    const card=document.createElement('div');card.id='networkSecurityCard';card.className='card';card.hidden=true;
    card.innerHTML=`
      <h3>Подключение к Лешему: HTTP и HTTPS</h3>
      <p class="hint">HTTP подходит для обычного чата. Для микрофона на телефоне и планшете нужен доверенный HTTPS. Параметры меняет подтверждённый хозяин этого сервера.</p>
      <p class="hint">По умолчанию сервер принимает подключения на интерфейсах IPv4 и IPv6. Можно использовать любой адрес сервера и любой домен, DNS которого направлен на него. Для HTTPS имя или IP должны входить в сертификат. Адреса 0.0.0.0 и :: обозначают привязку ко всем интерфейсам; в браузере открывают localhost, реальный IP или домен.</p>
      <div class="network-fields">
        <label class="network-check"><input id="networkHttpEnabled" type="checkbox"> Включить HTTP</label>
        <label>Порт HTTP<input id="networkHttpPort" class="field" type="number" min="1" max="65535" step="1" placeholder="8765"></label>
        <label class="network-check"><input id="networkHttpsEnabled" type="checkbox"> Включить HTTPS</label>
        <label>Порт HTTPS<input id="networkHttpsPort" class="field" type="number" min="1" max="65535" step="1" placeholder="8766"></label>
        <label>Имя сервера в сертификате<input id="networkHostname" class="field" autocomplete="off" placeholder="leshy.example.org" maxlength="253"></label>
      </div>
      <div class="network-actions"><button class="button" id="networkSave" type="button">Сохранить порты</button><button class="button secondary" id="networkRefresh" type="button">Обновить состояние</button></div>
      <div id="networkRuntimeStatus" class="network-status" role="status" aria-live="polite"></div>
      <div id="networkCertificateStatus" class="network-status"></div>
      <details><summary>Загрузить свой сертификат</summary>
        <p class="hint">Выберите PEM-сертификат с цепочкой и соответствующий закрытый ключ. Можно использовать сертификат, купленный у выбранного Вами центра сертификации. Проверка файла не гарантирует доверие на каждом устройстве.</p>
        <div class="network-fields">
          <label>Сертификат и цепочка<input id="networkCertificateFile" type="file" accept=".pem,.crt,.cer"></label>
          <label>Закрытый ключ<input id="networkKeyFile" type="file" accept=".pem,.key"></label>
          <label>Пароль ключа, если задан<input id="networkKeyPassword" class="field" type="password" autocomplete="new-password"></label>
        </div>
        <p id="networkImportHelp" class="hint"></p><button class="button secondary" id="networkImport" type="button">Проверить и установить сертификат</button>
      </details>
      <details><summary>HTTPS по IP без домена</summary>
        <p class="hint">Леший создаст локальный центр сертификации и сертификат для указанных IP и имён. Закрытые ключи остаются на сервере. Бесплатное продление работает, пока запущен Леший. При изменении IP добавьте новый адрес и выпустите сертификат повторно.</p>
        <div class="network-fields"><label>IP и имена сервера, по одному на строку<textarea id="networkLocalNames" class="field" rows="4" maxlength="8192" placeholder="IP сервера или его имя"></textarea></label></div>
        <label class="network-check"><input id="networkLocalRenew" type="checkbox"> Автоматически продлевать локальный сертификат</label>
        <p class="hint">На каждом устройстве нужно один раз установить доверие к корневому сертификату этого сервера. Такое доверие распространяется на сертификаты, подписанные его ключом. Сверьте отпечаток SHA-256 через доверенный канал. Леший не меняет доверие устройств автоматически.</p>
        <label class="network-check"><input id="networkLocalTrustConsent" type="checkbox"> Понимаю действие корневого сертификата и подтверждаю выпуск для перечисленных адресов.</label>
        <p id="networkLocalHelp" class="hint"></p>
        <div class="network-actions"><button class="button" id="networkLocalIssue" type="button">Выпустить локальный сертификат</button><button class="button secondary" id="networkLocalRoot" type="button">Скачать корневой сертификат</button><button class="button secondary" id="networkLocalDisable" type="button">Отключить локальное продление</button></div>
        <div id="networkLocalStatus" class="network-status" role="status" aria-live="polite"></div>
      </details>
      <details><summary>Let's Encrypt для домена и автопродление</summary>
        <p class="hint">Леший подтвердит владение доменом через HTTP-01. Центр сертификации обращается к внешнему порту 80; перенаправление на работающий HTTP-порт Лешего нужно настроить на Вашем роутере или сервере. Порты приложения можно выбирать отдельно. Перед выключением HTTP завершите выпуск и отключите автопродление HTTP-01.</p>
        <div class="network-fields">
          <label>Домены через запятую<input id="networkAcmeDomains" class="field" maxlength="1500" placeholder="leshy.example.org"></label>
          <label>Электронная почта для центра сертификации<input id="networkAcmeEmail" class="field" type="email" maxlength="254" autocomplete="email"></label>
          <label>Центр сертификации<select id="networkAcmeEnvironment" class="field"><option value="staging">Let's Encrypt — проверочный</option><option value="production">Let's Encrypt — рабочий</option></select></label>
        </div>
        <p class="hint">Проверочный сертификат не считается доверенным браузером. Для обычного голосового разговора нужен рабочий сертификат. При выпуске домены и указанный email передаются выбранному центру сертификации.</p>
        <p><a id="networkAcmeTerms" target="_blank" rel="noopener noreferrer" hidden>Условия выбранного центра сертификации</a></p>
        <label class="network-check"><input id="networkAcmeTermsAgreed" type="checkbox"> Я прочитал и принимаю текущие условия центра сертификации для этих доменов и email.</label>
        <label class="network-check"><input id="networkAcmeContactConsent" type="checkbox"> Разрешаю передать эти домены и email выбранному центру сертификации для выпуска и продления.</label>
        <label class="network-check"><input id="networkAcmeRenew" type="checkbox"> Автоматически продлевать этот сертификат, пока Леший работает</label>
        <div class="network-actions"><button class="button secondary" id="networkAcmeSave" type="button">Сохранить параметры / получить условия</button><button class="button" id="networkAcmeIssue" type="button">Получить и установить сертификат</button></div>
        <div id="networkAcmeStatus" class="network-status" role="status" aria-live="polite"></div>
      </details>
      <p id="networkSecurityNote" class="hint" role="status" aria-live="polite"></p>
    `;pane.appendChild(card);
    for(const id of ['networkHttpEnabled','networkHttpPort','networkHttpsEnabled','networkHttpsPort','networkHostname'])$(id).addEventListener('input',()=>{portsDirty=true});
    for(const id of ['networkAcmeDomains','networkAcmeEmail','networkAcmeEnvironment'])$(id).addEventListener('input',()=>{acmeDirty=true;$('networkAcmeTermsAgreed').checked=false;$('networkAcmeContactConsent').checked=false});
    for(const id of ['networkAcmeTermsAgreed','networkAcmeRenew','networkAcmeContactConsent'])$(id).addEventListener('change',()=>{acmeDirty=true});
    for(const id of ['networkLocalNames','networkLocalRenew'])$(id).addEventListener('input',()=>{localDirty=true;$('networkLocalTrustConsent').checked=false;syncButtons()});
    $('networkLocalTrustConsent').addEventListener('change',syncButtons);
    $('networkRefresh').onclick=()=>refresh(false);
    $('networkSave').onclick=savePorts;$('networkImport').onclick=importCertificate;
    $('networkAcmeSave').onclick=()=>saveAcme(false);$('networkAcmeIssue').onclick=()=>saveAcme(true);
    $('networkLocalIssue').onclick=issueLocal;$('networkLocalDisable').onclick=disableLocal;$('networkLocalRoot').onclick=downloadLocalRoot;
    return true;
  }

  function listenerText(name,listener,enabled,configuredPort){
    let text=listener?.listening===true?name+': запущен, порт '+listener.port
      :listener?.listening===false?name+': не запущен'+(listener.error?' — '+listener.error:'')
      :name+': состояние запуска не подтверждено; сохранён '+(enabled?'порт '+configuredPort:'выключенный режим');
    const labels={bound:'порт привязан',listening:'работает',unavailable:'семейство недоступно',bind_failed:'ошибка привязки',start_failed:'ошибка запуска',stopped:'остановлен'};
    for(const binding of listener?.bindings||[]){
      const address=binding.family==='IPv6'?'['+binding.address+']':binding.address;
      text+='\n  '+binding.family+' '+address+':'+binding.port+' — '+(labels[binding.status]||'не подтверждено')+(binding.error?' ('+binding.error+')':'');
    }
    return text;
  }

  function render(data,fill=false){
    if(!ensure())return;if(!data.settings)throw new Error('Сервер не вернул настройки подключения');
    lastStatus=data;const settings=data.settings,runtime=data.runtime||{},cert=data.certificate||{},acme=data.acme||{},local=data.local_ca||{};
    $('networkSecurityCard').hidden=false;
    if(fill||!portsDirty){
      $('networkHttpEnabled').checked=settings.http_enabled===true;$('networkHttpsEnabled').checked=settings.https_enabled===true;
      $('networkHttpPort').value=String(settings.http_port??'');$('networkHttpsPort').value=String(settings.https_port??'');$('networkHostname').value=settings.hostname||'';portsDirty=false;
    }
    $('networkRuntimeStatus').textContent=[
      listenerText('HTTP',runtime.http,settings.http_enabled,settings.http_port),
      listenerText('HTTPS',runtime.https,settings.https_enabled,settings.https_port),
      runtime.http_port_override?'Порт HTTP задан при запуске. Чтобы применить сохранённый порт, сначала уберите это переопределение.':'',
      runtime.https_port_override?'Порт HTTPS задан при запуске. Чтобы применить сохранённый порт, сначала уберите это переопределение.':'',
      runtime.restart_required?'Сохранённые настройки отличаются от применённых. Нужен перезапуск Лешего.':'',
    ].filter(Boolean).join('\n');
    const certNode=$('networkCertificateStatus');certNode.classList.toggle('bad',cert.configured===true&&cert.valid!==true);
    certNode.textContent=!cert.configured?'Сертификат пока не установлен. HTTPS без подходящего сертификата не запускается.':[
      cert.valid===true?'Сертификат прошёл локальную проверку.':'Сохранённый сертификат нельзя использовать: '+(cert.error||'срок или содержимое не прошли проверку'),
      'Имена: '+(Array.isArray(cert.names)?cert.names.join(', '):'не подтверждены')+'. Срок всей цепочки: '+(cert.valid_until||cert.not_after||'не подтверждён'),
      cert.active_for_new_connections===true?'Применён для новых HTTPS-соединений.':'Применение для новых HTTPS-соединений не подтверждено.',
      cert.fingerprint_sha256?'Отпечаток SHA-256: '+cert.fingerprint_sha256:'',
      'Доверие сертификату на Вашем устройстве проверяется браузером.',
    ].filter(Boolean).join('\n');
    $('networkImportHelp').textContent=secureUpload()&&data.connection?.can_import_certificate===true?'Ключ отправляется только этому серверу и не возвращается в ответе API.':'Для загрузки закрытого ключа откройте HTTPS или localhost на самом сервере.';
    if(fill||!localDirty){
      const names=Array.isArray(local.names)&&local.names.length?local.names:[settings.hostname,location.hostname.replace(/^\[|\]$/g,'')].filter(Boolean);
      const nextNames=[...new Set(names)].join('\n'),nextRenew=local.configured?local.auto_renew===true:true;
      if($('networkLocalNames').value!==nextNames||$('networkLocalRenew').checked!==nextRenew)$('networkLocalTrustConsent').checked=false;
      $('networkLocalNames').value=nextNames;$('networkLocalRenew').checked=nextRenew;localDirty=false;
    }
    const localAllowed=secureUpload()&&data.connection?.can_issue_local_ca===true;
    $('networkLocalHelp').textContent=localAllowed
      ?'Включите HTTPS в настройках портов и укажите основное IP или имя, входящее в список выше. Установка своего сертификата или настройка Let\'s Encrypt отключает локальное продление. После установки доверия откройте HTTPS по указанному IP или имени.'
      :'Первый выпуск и скачивание корневого сертификата доступны через доверенный HTTPS или localhost на самом сервере. По удалённому HTTP откройте эту админку на сервере, затем передайте корневой сертификат на устройства через доверенный канал.';
    const localLabels={not_configured:'Не настроено',issued:'Локальный сертификат установлен',disabled:'Локальное продление выключено',attention:'Нужна проверка состояния'};
    $('networkLocalStatus').classList.toggle('bad',local.attention_required===true);
    $('networkLocalStatus').textContent=[
      'Состояние: '+(localLabels[local.state]||'не подтверждено'),
      local.names?.length?'Адреса: '+local.names.join(', '):'',
      local.root_fingerprint_sha256?'Отпечаток корневого сертификата SHA-256: '+local.root_fingerprint_sha256:'',
      local.root_not_after?'Корневой сертификат действует до: '+local.root_not_after:'',
      local.certificate_not_after?'Сертификат сервера действует до: '+local.certificate_not_after:'',
      local.auto_renew===true&&local.renew_at?'Продление после: '+local.renew_at:'',
      local.last_error||'',
      'Доверие на этом устройстве ещё нужно проверить в браузере.',
    ].filter(Boolean).join('\n');
    if(fill||!acmeDirty){
      $('networkAcmeDomains').value=(acme.domains||[]).join(', ');$('networkAcmeEmail').value=acme.email||'';
      $('networkAcmeEnvironment').value=acme.environment||'staging';$('networkAcmeRenew').checked=acme.auto_renew===true;$('networkAcmeTermsAgreed').checked=acme.terms_agreed===true;$('networkAcmeContactConsent').checked=acme.contact_consent===true;acmeDirty=false;
    }
    let terms=null;try{const url=new URL(acme.terms_url);if(url.protocol==='https:'&&['letsencrypt.org','www.letsencrypt.org'].includes(url.hostname))terms=url.href}catch(_e){}
    const termsLink=$('networkAcmeTerms');termsLink.hidden=!terms;if(terms)termsLink.href=terms;else termsLink.removeAttribute('href');
    const issuing=acme.in_progress===true;
    syncButtons();
    const acmeLabels={disabled:'Выключено',needs_consent:'Нужно согласие с текущими условиями',ready:'Настроено, выпуск ещё не подтверждён',issuing:'Выпуск выполняется',issued:'Сертификат получен',error:'Нужна проверка ошибки',outcome_unknown:'Результат прошлого запроса неизвестен',terms_changed:'Условия центра изменились — нужно новое согласие'};
    $('networkAcmeStatus').textContent='Состояние: '+(acmeLabels[acme.state]||'не подтверждено')+(acme.last_error?'\n'+acme.last_error:'')+(acme.renew_at?'\nПроверка продления после: '+acme.renew_at:'');
    clearTimeout(pollTimer);if(issuing&&!document.hidden)pollTimer=setTimeout(()=>refresh(false),2500);
  }

  async function refresh(fill=false){
    if(busy||!ensure())return false;busy=true;
    try{render(await api('/v1/web-security/status'),fill);note('');return true}
    catch(error){if([401,403].includes(error.status))$('networkSecurityCard').hidden=true;else note(error.message,true);return false}
    finally{busy=false}
  }

  async function action(button,work){
    if(button.disabled)return;pendingActions.add(button.id);syncButtons();note('Выполняю…');
    try{await work()}catch(error){await refresh(false);note(error.message,true)}finally{pendingActions.delete(button.id);syncButtons()}
  }

  function syncButtons(){
    if(!$('networkSecurityCard'))return;
    for(const input of $('networkSecurityCard').querySelectorAll('input,select,textarea'))input.disabled=pendingActions.size>0;
    $('networkRefresh').disabled=pendingActions.size>0;
    for(const id of ['networkSave','networkImport','networkAcmeSave','networkAcmeIssue','networkLocalIssue','networkLocalDisable','networkLocalRoot']){
      let disabled=pendingActions.size>0;
      if(id==='networkImport')disabled=disabled||!secureUpload()||lastStatus?.connection?.can_import_certificate!==true;
      if(id==='networkAcmeIssue')disabled=disabled||lastStatus?.acme?.in_progress===true||$('networkAcmeTerms').hidden;
      if(id==='networkLocalIssue')disabled=disabled||!secureUpload()||lastStatus?.connection?.can_issue_local_ca!==true||lastStatus?.acme?.in_progress===true||!$('networkLocalTrustConsent').checked;
      if(id==='networkLocalRoot')disabled=disabled||!secureUpload()||lastStatus?.connection?.can_download_local_ca!==true||!lastStatus?.local_ca?.root_fingerprint_sha256;
      if(id==='networkLocalDisable')disabled=disabled||lastStatus?.local_ca?.enabled!==true;
      $(id).disabled=disabled;
    }
  }

  async function issueLocal(){
    await action($('networkLocalIssue'),async()=>{
      if(!secureUpload()||lastStatus?.connection?.can_issue_local_ca!==true)throw new Error('Откройте доверенный HTTPS или localhost на самом сервере');
      if(!$('networkLocalTrustConsent').checked)throw new Error('Подтвердите действие корневого сертификата');
      const names=$('networkLocalNames').value.split(/[,\s]+/).filter(Boolean);
      if(!names.length||names.length>32)throw new Error('Укажите от 1 до 32 IP или имён сервера');
      if(!window.confirm('Выпустить локальный сертификат для:\n'+names.join('\n')+'\nПродление Let\'s Encrypt будет выключено. Доверие к корневому сертификату нужно установить на каждом устройстве.')){note('Выпуск отменён.');return}
      await api('/v1/web-security/local-ca/issue',{names,auto_renew:$('networkLocalRenew').checked,confirm:true,trust_consent:true});
      localDirty=false;$('networkLocalTrustConsent').checked=false;
      const updated=await refresh(false);note(updated?'Локальный сертификат сохранён. Скачайте корневой сертификат, сверьте его отпечаток и настройте доверие на устройствах.':'Запрос завершён, но состояние обновить не удалось. Проверьте его перед повторным выпуском.',!updated);
    });
  }

  async function disableLocal(){
    await action($('networkLocalDisable'),async()=>{
      if(!window.confirm('Отключить локальное продление? Текущий сертификат останется установленным до истечения срока. Доверие к корневому сертификату на устройствах снимается отдельно.')){note('Отключение отменено.');return}
      await api('/v1/web-security/local-ca/disable',{confirm:true});localDirty=false;
      const updated=await refresh(false);note(updated?'Локальное продление выключено. Текущее состояние показано выше.':'Запрос завершён, но состояние обновить не удалось.',!updated);
    });
  }

  async function downloadLocalRoot(){
    await action($('networkLocalRoot'),async()=>{
      if(!secureUpload()||lastStatus?.connection?.can_download_local_ca!==true)throw new Error('Скачайте корневой сертификат через доверенный HTTPS или localhost на самом сервере');
      const fingerprint=String(lastStatus?.local_ca?.root_fingerprint_sha256||'').toLowerCase().replace(/:/g,'');
      if(!/^[0-9a-f]{64}$/.test(fingerprint)||!globalThis.crypto?.subtle)throw new Error('Не удалось проверить отпечаток корневого сертификата');
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),25000);
      let bytes;
      try{
        const response=await fetch('/v1/web-security/local-ca/root.cer',{credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'X-Leshy-UI':'network-security'}});
        if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.error||('HTTP '+response.status))}
        const length=response.headers.get('Content-Length');
        if(!/^[1-9]\d*$/.test(length||'')||Number(length)>64*1024)throw new Error('Сервер не подтвердил допустимый размер корневого сертификата');
        bytes=await response.arrayBuffer();if(!bytes.byteLength||bytes.byteLength>64*1024)throw new Error('Некорректный размер корневого сертификата');
      }catch(error){if(error.name==='AbortError')throw new Error('Скачивание не завершено. Проверьте соединение перед повтором.');throw error}
      finally{clearTimeout(timer)}
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');
      if(digest!==fingerprint)throw new Error('Отпечаток корневого сертификата изменился. Обновите состояние и проверьте его на сервере.');
      const url=URL.createObjectURL(new Blob([bytes],{type:'application/x-x509-ca-cert'}));
      const link=document.createElement('a');link.href=url;link.download='Leshy-local-root.cer';document.body.appendChild(link);
      try{link.click()}finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000)}
      note('Корневой сертификат передан браузеру для сохранения. Сверьте SHA-256 с сервером через доверенный канал; установите доверие средствами Вашего устройства.');
    });
  }

  function port(id){const value=Number($(id).value);if(!Number.isInteger(value)||value<1||value>65535)throw new Error('Порт должен быть целым числом от 1 до 65535');return value}
  async function savePorts(){
    await action($('networkSave'),async()=>{
      const body={http_enabled:$('networkHttpEnabled').checked,https_enabled:$('networkHttpsEnabled').checked,http_port:port('networkHttpPort'),https_port:port('networkHttpsPort'),hostname:$('networkHostname').value.trim(),confirm:true};
      if(!body.http_enabled&&!body.https_enabled)throw new Error('Оставьте включённым хотя бы один протокол');
      if(body.http_port===body.https_port)throw new Error('Для HTTP и HTTPS укажите разные порты');
      await api('/v1/web-security/settings',body);portsDirty=false;const updated=await refresh(false);note(updated?'Настройки сохранены. Состояние применённых портов показано выше.':'Настройки сохранены, но состояние сервера обновить не удалось.',!updated);
    });
  }

  async function importCertificate(){
    await action($('networkImport'),async()=>{
      if(!secureUpload()||lastStatus?.connection?.can_import_certificate!==true)throw new Error('Загрузка закрытого ключа доступна через HTTPS или localhost на самом сервере');
      const certificate=$('networkCertificateFile').files[0],key=$('networkKeyFile').files[0];
      if(!certificate||!key)throw new Error('Выберите сертификат и соответствующий закрытый ключ');
      if(certificate.size>256*1024||key.size>64*1024)throw new Error('Сертификат ограничен 256 КБ, закрытый ключ — 64 КБ');
      try{
        await api('/v1/web-security/certificate/import',{certificate_pem:await certificate.text(),private_key_pem:await key.text(),key_password:$('networkKeyPassword').value,confirm:true});
        const updated=await refresh(false);note(updated?'Сертификат проверен и сохранён. Фактическое состояние HTTPS показано выше.':'Импорт завершён, но состояние HTTPS обновить не удалось. Обновите его перед следующим действием.',!updated);
      }finally{$('networkCertificateFile').value='';$('networkKeyFile').value='';$('networkKeyPassword').value=''}
    });
  }

  function acmePayload(){
    const link=$('networkAcmeTerms');
    return {domains:$('networkAcmeDomains').value.split(/[,\s]+/).map(x=>x.trim()).filter(Boolean),email:$('networkAcmeEmail').value.trim(),environment:$('networkAcmeEnvironment').value,auto_renew:$('networkAcmeRenew').checked,terms_agreed:$('networkAcmeTermsAgreed').checked,contact_consent:$('networkAcmeContactConsent').checked,terms_url:link.hidden?'':link.href,confirm:true};
  }
  async function saveAcme(issue){
    await action($(issue?'networkAcmeIssue':'networkAcmeSave'),async()=>{
      const body=acmePayload();if(!body.domains.length||!body.email)throw new Error('Укажите домены и email для центра сертификации');
      if(issue&&!body.terms_agreed)throw new Error('Прочитайте и примите текущие условия центра сертификации');
      if(issue&&!body.contact_consent)throw new Error('Подтвердите передачу доменов и email выбранному центру сертификации');
      let abandonUnknown=false;
      if(issue&&lastStatus?.acme?.outcome_unknown===true){
        abandonUnknown=window.confirm('Предыдущий запрос мог уже выпустить сертификат. Сначала проверьте его результат у центра сертификации. Вы проверили результат и всё равно хотите начать новый выпуск?');
        if(!abandonUnknown)return;
      }
      const configured=await api('/v1/web-security/acme/configure',body);acmeDirty=false;render(configured);
      if(issue){
        if(configured.acme?.enabled!==true||configured.acme?.terms_agreed!==true||configured.acme?.contact_consent!==true){
          note('Проверьте актуальные условия и снова подтвердите согласие перед выпуском.',true);return;
        }
        if(configured.acme.outcome_unknown===true&&!abandonUnknown){
          note('Результат предыдущего выпуска неизвестен. Проверьте его перед новым запросом.',true);return;
        }
        render(await api('/v1/web-security/acme/issue',{confirm:true,...(abandonUnknown?{abandon_unknown:true}:{})}));
      }
      const updated=await refresh(false);note(updated?(issue?'Запрос выпуска передан. Дождитесь фактического результата в состоянии сертификата.':'Параметры сохранены. Проверьте ссылку на актуальные условия перед выпуском.'):'Запрос завершён, но состояние обновить не удалось. Проверьте его перед следующим действием.',!updated);
    });
  }

  function boot(){
    if(!ensure()){const observer=new MutationObserver(()=>{if(ensure()){observer.disconnect();boot()}});observer.observe(document.documentElement,{childList:true,subtree:true});return}
    refresh();const pane=$('pane-settings');new MutationObserver(()=>{if(pane.classList.contains('active'))refresh()}).observe(pane,{attributes:true,attributeFilter:['class']});
    document.addEventListener('visibilitychange',()=>{clearTimeout(pollTimer);if(!document.hidden&&pane.classList.contains('active'))refresh()});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
