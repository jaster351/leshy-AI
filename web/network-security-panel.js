(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  let busy=false,portsDirty=false,acmeDirty=false,pollTimer=0,lastStatus=null;
  const pendingActions=new Set();

  async function api(path,body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),25000);
    try{
      const response=await fetch(path,{
        method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,
        headers:{'Content-Type':'application/json','X-Leshy-UI':'network-security'},
        body:body===undefined?undefined:JSON.stringify(body),
      });
      const data=await response.json().catch(()=>({error:'Сервер вернул некорректный ответ'}));
      if(!response.ok){const error=new Error(data.error||('HTTP '+response.status));error.status=response.status;throw error}
      return data;
    }catch(error){
      if(error.name==='AbortError')throw new Error('Сервер не ответил вовремя. Проверьте состояние перед повторным действием.');
      throw error;
    }finally{clearTimeout(timer)}
  }

  function note(text,bad=false){const node=$('networkSecurityNote');if(node){node.textContent=text||'';node.classList.toggle('bad',bad)}}
  function secureUpload(){return location.protocol==='https:'||['localhost','127.0.0.1','[::1]','::1'].includes(location.hostname)}

  function ensure(){
    if($('networkSecurityCard'))return true;const pane=$('pane-settings');if(!pane)return false;
    const style=document.createElement('style');style.textContent=`
      #networkSecurityCard{margin-top:12px}#networkSecurityCard[hidden]{display:none!important}
      #networkSecurityCard .network-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px;margin:12px 0}
      #networkSecurityCard label{display:grid;gap:6px;font-size:12px;line-height:1.45;min-width:0}#networkSecurityCard .network-check{display:flex;align-items:flex-start;gap:8px}
      #networkSecurityCard input:not([type=checkbox]),#networkSecurityCard select{box-sizing:border-box;min-width:0;width:100%}#networkSecurityCard input[type=file]{font-size:12px;max-width:100%}
      #networkSecurityCard .network-actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}#networkSecurityCard button{min-height:40px}
      #networkSecurityCard .network-status{white-space:pre-line;overflow-wrap:anywhere;font-size:12px;line-height:1.55;padding:10px 0}#networkSecurityCard details{margin-top:12px;border-top:1px solid var(--line,#36463f);padding-top:12px}#networkSecurityCard summary{cursor:pointer;font-size:14px}
      #networkSecurityCard .bad{color:#ffb4a8}#networkSecurityCard a{color:#a8dfca}#networkSecurityCard [hidden]{display:none!important}
    `;document.head.appendChild(style);
    const card=document.createElement('div');card.id='networkSecurityCard';card.className='card';card.hidden=true;
    card.innerHTML=`
      <h3>Подключение к Лешему: HTTP и HTTPS</h3>
      <p class="hint">HTTP подходит для обычного чата. Для микрофона на телефоне и планшете нужен доверенный HTTPS. Параметры меняет подтверждённый хозяин этого сервера.</p>
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
      <details><summary>Бесплатный сертификат и автопродление</summary>
        <p class="hint">Леший подтвердит владение доменом через HTTP-01. Центр сертификации обращается к внешнему порту 80; перенаправление на HTTP-порт Лешего нужно настроить на Вашем роутере или сервере. Порты приложения можно выбирать отдельно.</p>
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
    $('networkRefresh').onclick=()=>refresh(false);
    $('networkSave').onclick=savePorts;$('networkImport').onclick=importCertificate;
    $('networkAcmeSave').onclick=()=>saveAcme(false);$('networkAcmeIssue').onclick=()=>saveAcme(true);
    return true;
  }

  function listenerText(name,listener,enabled,configuredPort){
    if(listener?.listening===true)return name+': запущен, порт '+listener.port;
    if(listener?.listening===false)return name+': не запущен'+(listener.error?' — '+listener.error:'');
    return name+': состояние запуска не подтверждено; сохранён '+(enabled?'порт '+configuredPort:'выключенный режим');
  }

  function render(data,fill=false){
    if(!ensure())return;if(!data.settings)throw new Error('Сервер не вернул настройки подключения');
    lastStatus=data;const settings=data.settings,runtime=data.runtime||{},cert=data.certificate||{},acme=data.acme||{};
    $('networkSecurityCard').hidden=false;
    if(fill||!portsDirty){
      $('networkHttpEnabled').checked=settings.http_enabled===true;$('networkHttpsEnabled').checked=settings.https_enabled===true;
      $('networkHttpPort').value=String(settings.http_port??'');$('networkHttpsPort').value=String(settings.https_port??'');$('networkHostname').value=settings.hostname||'';portsDirty=false;
    }
    $('networkRuntimeStatus').textContent=[listenerText('HTTP',runtime.http,settings.http_enabled,settings.http_port),listenerText('HTTPS',runtime.https,settings.https_enabled,settings.https_port),runtime.restart_required?'Сохранённые настройки отличаются от применённых. Нужен перезапуск Лешего.':''].filter(Boolean).join('\n');
    const certNode=$('networkCertificateStatus');certNode.classList.toggle('bad',cert.configured===true&&cert.valid!==true);
    certNode.textContent=!cert.configured?'Сертификат пока не установлен. HTTPS без подходящего сертификата не запускается.':[
      cert.valid===true?'Сертификат прошёл локальную проверку.':'Сохранённый сертификат нельзя использовать: '+(cert.error||'срок или содержимое не прошли проверку'),
      'Имена: '+(Array.isArray(cert.names)?cert.names.join(', '):'не подтверждены')+'. Срок всей цепочки: '+(cert.valid_until||cert.not_after||'не подтверждён'),
      cert.active_for_new_connections===true?'Применён для новых HTTPS-соединений.':'Применение для новых HTTPS-соединений не подтверждено.',
      cert.fingerprint_sha256?'Отпечаток SHA-256: '+cert.fingerprint_sha256:'',
      'Доверие сертификату на Вашем устройстве проверяется браузером.',
    ].filter(Boolean).join('\n');
    $('networkImportHelp').textContent=secureUpload()?'Ключ отправляется только этому серверу и не возвращается в ответе API.':'Для загрузки закрытого ключа откройте HTTPS или localhost на самом сервере.';
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
    for(const id of ['networkSave','networkImport','networkAcmeSave','networkAcmeIssue']){
      let disabled=pendingActions.has(id);
      if(id==='networkImport')disabled=disabled||!secureUpload();
      if(id==='networkAcmeIssue')disabled=disabled||lastStatus?.acme?.in_progress===true||$('networkAcmeTerms').hidden;
      $(id).disabled=disabled;
    }
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
      if(!secureUpload())throw new Error('Загрузка закрытого ключа доступна через HTTPS или localhost');
      const certificate=$('networkCertificateFile').files[0],key=$('networkKeyFile').files[0];
      if(!certificate||!key)throw new Error('Выберите сертификат и соответствующий закрытый ключ');
      if(certificate.size>256*1024||key.size>64*1024)throw new Error('Сертификат ограничен 256 КБ, закрытый ключ — 64 КБ');
      try{
        await api('/v1/web-security/certificate/import',{certificate_pem:await certificate.text(),private_key_pem:await key.text(),key_password:$('networkKeyPassword').value,confirm:true});
        await refresh(false);note('Сертификат проверен и сохранён. Фактическое состояние HTTPS показано выше.');
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
      await refresh(false);note(issue?'Запрос выпуска передан. Дождитесь фактического результата в состоянии сертификата.':'Параметры сохранены. Проверьте ссылку на актуальные условия перед выпуском.');
    });
  }

  function boot(){
    if(!ensure()){const observer=new MutationObserver(()=>{if(ensure()){observer.disconnect();boot()}});observer.observe(document.documentElement,{childList:true,subtree:true});return}
    refresh();const pane=$('pane-settings');new MutationObserver(()=>{if(pane.classList.contains('active'))refresh()}).observe(pane,{attributes:true,attributeFilter:['class']});
    document.addEventListener('visibilitychange',()=>{clearTimeout(pollTimer);if(!document.hidden&&pane.classList.contains('active'))refresh()});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
