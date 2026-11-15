(function(){
  'use strict';

  const $=id=>document.getElementById(id);

  async function api(path,options={}){
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok) throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function style(){
    if($('leshyModelLibraryStyle')) return;
    const node=document.createElement('style');
    node.id='leshyModelLibraryStyle';
    node.textContent=`
      #modelLibraryCard{margin-top:12px}
      #modelLibraryRuntime{display:grid;grid-template-columns:minmax(0,240px) 1fr;gap:10px;align-items:center;margin:10px 0 14px;padding:10px;border:1px solid var(--line);border-radius:9px;background:rgba(255,255,255,.02)}
      #modelLibraryRuntime .runtime-note{font-size:11px;color:var(--muted);line-height:1.45}
      #modelLibraryGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
      .model-library-item{border:1px solid var(--line);border-radius:10px;background:rgba(255,255,255,.025);padding:12px;display:grid;gap:8px}
      .model-library-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
      .model-library-title{font-weight:700;font-size:13px;line-height:1.35}
      .model-library-badges{display:flex;gap:5px;flex-wrap:wrap;margin-top:5px}
      .model-library-badge{font-size:10px;padding:2px 6px;border:1px solid var(--line);border-radius:999px;color:var(--muted)}
      .model-library-badge.vision{color:#75bdf9;border-color:#315a7c}
      .model-library-badge.installed{color:#57c996;border-color:#32614e}
      .model-library-description{font-size:11px;line-height:1.5;color:#d3dbe4}
      .model-library-note{font-size:10.5px;line-height:1.45;color:var(--muted)}
      .model-library-label{color:#8ca0b3;font-weight:650}
      .model-library-actions{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:auto}
      .model-library-actions .button{flex:1 1 130px}
      .model-library-vision{display:flex;align-items:center;gap:6px;font-size:10.5px;color:var(--muted);width:100%}
      #modelLibraryProgress{margin-top:10px;border:1px solid var(--line);border-radius:9px;padding:9px 10px;font-size:11px;color:var(--muted);white-space:pre-wrap}
      #modelLibraryProgress.active{color:#dce8f3;border-color:#31516f;background:rgba(74,155,232,.06)}
      @media(max-width:950px){#modelLibraryGrid{grid-template-columns:1fr}}
      @media(max-width:720px){#modelLibraryRuntime{grid-template-columns:1fr}}
    `;
    document.head.appendChild(node);
  }

  function ensure(){
    style();
    if($('modelLibraryCard')) return $('modelLibraryCard');
    const pane=$('pane-settings');
    if(!pane) return null;
    const card=document.createElement('div');
    card.className='card';
    card.id='modelLibraryCard';
    card.innerHTML=`
      <h3>Каталог рекомендуемых моделей</h3>
      <div class="muted">Готовые модели для Лешего. У каждой указано, что это за модель, для каких задач она подходит и насколько тяжела для памяти.</div>
      <div id="modelLibraryRuntime">
        <select class="field" id="runtimeMode">
          <option value="auto">Авто — рекомендовано</option>
          <option value="modern">Современный runtime</option>
          <option value="legacy">Legacy runtime</option>
        </select>
        <div class="runtime-note" id="runtimeModeNote">Загрузка режима…</div>
      </div>
      <div id="modelLibraryGrid"></div>
      <div id="modelLibraryProgress">Каталог загружается…</div>
    `;
    const local=$('localModelCard');
    if(local && local.parentNode) local.parentNode.insertBefore(card,local.nextSibling);
    else pane.appendChild(card);
    $('runtimeMode').onchange=saveRuntimeMode;
    return card;
  }

  function runtimeText(data){
    const mode=data&&data.mode||'auto';
    const descriptions=data&&data.modes||{};
    let text=descriptions[mode]||'';
    if(data&&data.model_requires_modern) text+=(text?'\n':'')+'Текущая модель относится к новой архитектуре: в режиме «Авто» будет проверен свежий llama.cpp.';
    const backs=data&&data.backends||{};
    const summary=Object.entries(backs).map(([name,x])=>name+': '+(x.current_tag||'не установлен')+(x.legacy?(' · legacy '+(x.legacy_tag||'есть')):'')).join(' | ');
    if(summary) text+=(text?'\n':'')+summary;
    return text||'Режим runtime не определён.';
  }

  async function loadRuntime(){
    try{
      const data=await api('/v1/runtime-policy');
      $('runtimeMode').value=data.mode||'auto';
      $('runtimeModeNote').textContent=runtimeText(data);
    }catch(error){$('runtimeModeNote').textContent='Ошибка runtime: '+error.message;}
  }

  async function saveRuntimeMode(){
    const select=$('runtimeMode');
    select.disabled=true;
    try{
      const data=await api('/v1/runtime-policy',{method:'POST',body:JSON.stringify({mode:select.value})});
      $('runtimeModeNote').textContent=runtimeText(data)+'\nНастройка сохранена.';
    }catch(error){alert('Не удалось сохранить режим runtime: '+error.message);}
    finally{select.disabled=false;}
  }

  function cardFor(model){
    const item=document.createElement('div');
    item.className='model-library-item';
    const badges=[
      '<span class="model-library-badge">'+model.quant+'</span>',
      '<span class="model-library-badge">'+model.size_label+'</span>',
      model.vision?'<span class="model-library-badge vision">Vision / Video</span>':'',
      model.installed?'<span class="model-library-badge installed">Установлена</span>':''
    ].join('');
    item.innerHTML=`
      <div class="model-library-head"><div><div class="model-library-title"></div><div class="model-library-badges">${badges}</div></div></div>
      <div class="model-library-description"></div>
      <div class="model-library-note"><span class="model-library-label">Для чего:</span> <span class="best-for"></span></div>
      <div class="model-library-note"><span class="model-library-label">Память:</span> <span class="memory-note"></span></div>
      <div class="model-library-note"><span class="model-library-label">Runtime:</span> <span class="runtime-note-model"></span></div>
      <div class="model-library-actions"></div>
    `;
    item.querySelector('.model-library-title').textContent=model.title;
    item.querySelector('.model-library-description').textContent=model.description;
    item.querySelector('.best-for').textContent=model.best_for;
    item.querySelector('.memory-note').textContent=model.memory_note;
    item.querySelector('.runtime-note-model').textContent=model.runtime==='modern'?'нужен свежий llama.cpp; «Авто» обновит его при запуске модели':'совместима со стабильным/legacy runtime';
    const actions=item.querySelector('.model-library-actions');
    if(model.vision){
      const label=document.createElement('label');
      label.className='model-library-vision';
      const check=document.createElement('input');
      check.type='checkbox';check.checked=true;check.dataset.visionFor=model.id;
      const text=document.createElement('span');
      text.textContent='Скачать также vision-проектор mmproj'+(model.mmproj_installed?' · уже установлен':'');
      label.append(check,text);actions.appendChild(label);
    }
    const download=document.createElement('button');
    download.className='button secondary small';
    download.type='button';
    download.textContent=model.installed?'Проверить / докачать':'Скачать';
    download.onclick=()=>downloadModel(model.id,model.vision);
    actions.appendChild(download);

    // Use the client's default browser when the built-in Python downloader
    // cannot verify HTTPS certificates. Never disable TLS verification.
    if(typeof model.url==='string' && /^https:\/\/huggingface\.co\//i.test(model.url)){
      const browser=document.createElement('a');
      browser.className='button secondary small';
      browser.href=model.url;
      browser.target='_blank';
      browser.rel='noopener noreferrer';
      browser.textContent='Скачать через браузер';
      actions.appendChild(browser);
      if(model.vision && model.mmproj_url && /^https:\/\/huggingface\.co\//i.test(model.mmproj_url)){
        const projector=document.createElement('a');
        projector.className='button secondary small';
        projector.href=model.mmproj_url;
        projector.target='_blank';
        projector.rel='noopener noreferrer';
        projector.textContent='Скачать mmproj через браузер';
        actions.appendChild(projector);
      }
    }

    if(model.installed){
      const activate=document.createElement('button');
      activate.className='button small';activate.type='button';activate.textContent='Сделать активной';
      activate.onclick=async()=>{
        activate.disabled=true;
        try{
          await api('/v1/models/select',{method:'POST',body:JSON.stringify({name:model.filename})});
          if(typeof window.refreshStatus==='function') await window.refreshStatus();
          await loadAll();
        }catch(error){alert('Не удалось включить модель: '+error.message);}
        finally{activate.disabled=false;}
      };
      actions.appendChild(activate);
    }
    return item;
  }

  async function downloadModel(id,vision){
    const include=vision?!!document.querySelector('input[data-vision-for="'+id+'"]')?.checked:false;
    try{
      const data=await api('/v1/model-library/download',{method:'POST',body:JSON.stringify({model_id:id,include_vision:include})});
      renderProgress(data.job||{});
    }catch(error){alert('Не удалось начать загрузку: '+error.message);}
  }

  function renderProgress(job){
    const box=$('modelLibraryProgress');
    if(!box) return;
    const state=job&&job.state||'idle';
    box.classList.toggle('active',['queued','downloading_model','downloading_vision'].includes(state));
    if(state==='idle'){box.textContent='Загрузки сейчас нет.';return;}
    if(state==='error'){box.textContent='Ошибка загрузки: '+(job.error||'неизвестная ошибка');return;}
    if(state==='ready'){box.textContent='Загрузка завершена. Модель появилась в списке локальных GGUF.';return;}
    const total=Number(job.total||0),done=Number(job.downloaded||0);
    const pct=total?Math.min(100,done*100/total):0;
    const part=state==='downloading_vision'?'vision-проектор':'модель';
    box.textContent='Скачивается '+part+': '+(job.current_file||job.model_id||'')+(total?' · '+pct.toFixed(1)+'%':'')+'\nФайл сохраняется в LeshyBrain\\models.';
  }

  async function loadLibrary(){
    ensure();
    try{
      const data=await api('/v1/model-library');
      const grid=$('modelLibraryGrid');grid.textContent='';
      for(const model of data.items||[]) grid.appendChild(cardFor(model));
      if(!(data.items||[]).length) grid.textContent='Каталог пуст.';
      renderProgress(data.job||{});
      return data;
    }catch(error){$('modelLibraryProgress').textContent='Ошибка каталога: '+error.message;return null;}
  }

  async function loadAll(){
    ensure();
    await Promise.all([loadRuntime(),loadLibrary()]);
  }

  function boot(){
    ensure();
    loadAll();
    setInterval(()=>{ if($('modelLibraryCard')) loadLibrary(); },2000);
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',boot,{once:true});
  else boot();
})();

(function(){
  'use strict';
  const $=id=>document.getElementById(id);

  function installLayoutFix(){
    if($('leshyDesktopContextFix')) return;
    const style=document.createElement('style');
    style.id='leshyDesktopContextFix';
    style.textContent=`
      @media(min-width:1100px){
        body.full.leshy-context-open .workspace{grid-template-columns:272px minmax(0,1fr) 360px!important}
        body.full .context{position:static!important;right:auto!important;top:auto!important;bottom:auto!important;width:auto!important;height:100vh!important;z-index:auto!important;box-shadow:none!important;transform:none!important;display:none!important;grid-column:3!important;grid-row:1!important}
        body.full.leshy-context-open .context.open{display:block!important}
        body.full.leshy-context-open .main{grid-column:2!important;min-width:0!important}
      }
    `;
    document.head.appendChild(style);
  }

  function syncContext(){
    const panel=$('contextPanel')||document.querySelector('.context');
    document.body.classList.toggle('leshy-context-open',!!(panel&&panel.classList.contains('open')));
  }

  function watchContext(){
    const panel=$('contextPanel')||document.querySelector('.context');
    if(!panel||panel.dataset.desktopFixBound==='1') return;
    panel.dataset.desktopFixBound='1';
    new MutationObserver(syncContext).observe(panel,{attributes:true,attributeFilter:['class']});
    syncContext();
  }

  async function request(path,options={}){
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
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
    installLayoutFix();
    watchContext();
    enhanceNasTest();
    const observer=new MutationObserver(()=>{watchContext();enhanceNasTest();});
    observer.observe(document.documentElement,{childList:true,subtree:true});
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',boot,{once:true});
  else boot();
})();
