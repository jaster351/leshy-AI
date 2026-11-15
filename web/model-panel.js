(function(){
  'use strict';

  const byId=id=>document.getElementById(id);

  async function requestJson(path,options){
    if(typeof window.api==='function') return window.api(path,options||{});
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok) throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function bytes(value){
    let n=Number(value||0);
    const units=['Б','КБ','МБ','ГБ','ТБ'];
    let i=0;
    while(n>=1024&&i<units.length-1){n/=1024;i++;}
    const digits=i===0?0:(n>=100?0:(n>=10?1:2));
    return n.toFixed(digits)+' '+units[i];
  }

  function ensureStyle(){
    if(byId('leshyModelPanelStyle')) return;
    const style=document.createElement('style');
    style.id='leshyModelPanelStyle';
    style.textContent=`
      #localModelCard{margin-top:12px}
      #localModelCard .model-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}
      #localModelCard .model-actions button{flex:1 1 180px}
      #localModelCard .model-meta{margin-top:8px;color:var(--muted,#8e9aa8);font-size:11px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
      #localModelCard .model-file{display:none}
      #localModelCard .upload-progress{display:none;height:7px;margin-top:9px;border-radius:999px;overflow:hidden;background:rgba(255,255,255,.07)}
      #localModelCard .upload-progress span{display:block;height:100%;width:0;background:linear-gradient(90deg,#4a9be8,#67b7ff);transition:width .15s linear}
      #localModelCard .model-row{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;margin-top:9px}
      #tasksHelpCard{margin:0 0 12px}
      #tasksHelpCard .task-help-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:10px}
      #tasksHelpCard .task-help-state{border:1px solid var(--line);border-radius:10px;padding:9px;background:rgba(255,255,255,.025)}
      #tasksHelpCard .task-help-state b{display:block;font-size:12px;margin-bottom:3px}
      #tasksHelpCard .task-help-state span{font-size:11px;color:var(--muted)}
      @media(max-width:900px){#tasksHelpCard .task-help-grid{grid-template-columns:1fr 1fr}}
      @media(max-width:720px){#localModelCard .model-row{grid-template-columns:1fr}#tasksHelpCard .task-help-grid{grid-template-columns:1fr}.task-form{grid-template-columns:1fr!important}}
    `;
    document.head.appendChild(style);
  }

  function ensurePanel(){
    ensureStyle();
    if(byId('localModelCard')) return byId('localModelCard');
    const pane=byId('pane-settings');
    if(!pane) return null;

    const oldDownload=byId('modelDownload');
    if(oldDownload){
      oldDownload.textContent='Скачать рекомендованную Qwen3-8B Q4_K_M';
      // The built-in button uses Python urllib and may fail when this PC
      // cannot verify the download server's HTTPS certificate.
      // A regular link opens in the user's browser instead, without
      // weakening TLS verification or trying to launch a browser on the server.
      if(!byId('modelBrowserDownload')){
        const browserLink=document.createElement('a');
        browserLink.id='modelBrowserDownload';
        browserLink.className='button secondary';
        browserLink.href='https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf';
        browserLink.target='_blank';
        browserLink.rel='noopener noreferrer';
        browserLink.textContent='Скачать через браузер';
        browserLink.style.cssText='display:block;text-align:center;margin-top:8px;';
        oldDownload.insertAdjacentElement('afterend',browserLink);
        const hint=document.createElement('div');
        hint.className='hint';
        hint.textContent='Если загрузили файл через браузер, нажмите «Загрузить GGUF с диска» ниже и выберите скачанную модель. Леший сохранит её в LeshyBrain\\models.';
        browserLink.insertAdjacentElement('afterend',hint);
      }
    }

    const card=document.createElement('div');
    card.className='card';
    card.id='localModelCard';
    card.innerHTML=`
      <h3>Выбор локальной LLM-модели</h3>
      <div class="muted">Леший сканирует <span class="mono">LeshyBrain\\models</span> и показывает все локальные файлы GGUF.</div>
      <div class="model-row">
        <select class="field" id="localModelSelect" aria-label="Локальная GGUF-модель"></select>
        <button class="button" id="localModelApply" type="button">Сделать активной</button>
      </div>
      <div class="model-meta" id="localModelMeta">Загрузка списка моделей…</div>
      <div class="model-actions">
        <button class="button secondary" id="localModelChoose" type="button">Загрузить GGUF с диска</button>
        <button class="button secondary" id="localModelRefresh" type="button">Обновить список</button>
      </div>
      <input class="model-file" id="localModelFile" type="file" accept=".gguf,application/octet-stream">
      <div class="upload-progress" id="localModelUploadProgress"><span></span></div>
      <div class="hint" id="localModelState" style="margin-top:8px">Выбор сохраняется и восстанавливается после перезапуска Лешего.</div>
    `;

    const brainCard=byId('brainDir')?.closest('.card');
    if(brainCard && brainCard.parentNode) brainCard.parentNode.insertBefore(card,brainCard);
    else pane.appendChild(card);

    byId('localModelRefresh').onclick=loadModels;
    byId('localModelApply').onclick=applyModel;
    byId('localModelChoose').onclick=()=>byId('localModelFile').click();
    byId('localModelFile').onchange=uploadModel;
    byId('localModelSelect').onchange=renderMeta;
    return card;
  }

  let snapshot=null;

  function renderMeta(){
    const select=byId('localModelSelect');
    const meta=byId('localModelMeta');
    if(!select||!meta||!snapshot) return;
    const item=(snapshot.items||[]).find(x=>x.name===select.value);
    if(!item){meta.textContent='Папка моделей: '+(snapshot.models_dir||'—');return;}
    const rows=[
      (item.active?'Активная модель':'Доступная модель'),
      'Размер: '+bytes(item.size),
      'GGUF: '+(item.valid_gguf?'да':'нет')+(item.gguf_version!=null?' · версия '+item.gguf_version:''),
      'Файл: '+item.name,
      'Папка: '+(snapshot.models_dir||'—')
    ];
    meta.textContent=rows.join('\n');
    const apply=byId('localModelApply');
    if(apply){apply.disabled=!!item.active||!item.valid_gguf;apply.textContent=item.active?'Уже активна':'Сделать активной';}
  }

  async function loadModels(preferred){
    ensurePanel();
    const select=byId('localModelSelect');
    const state=byId('localModelState');
    if(!select) return;
    try{
      const data=await requestJson('/v1/models');
      snapshot=data;
      select.textContent='';
      const items=data.items||[];
      if(!items.length){
        const option=document.createElement('option');
        option.value='';option.textContent='GGUF-модели не найдены';select.appendChild(option);
        if(state) state.textContent='Положите .gguf в '+data.models_dir+' или загрузите файл кнопкой ниже.';
        renderMeta();
        return;
      }
      for(const item of items){
        const option=document.createElement('option');
        option.value=item.name;
        option.textContent=item.name+(item.active?' · активна':'')+(!item.valid_gguf?' · повреждена/не GGUF':'');
        select.appendChild(option);
      }
      const target=preferred||data.active_model;
      if(target && items.some(x=>x.name===target)) select.value=target;
      if(state) state.textContent='Найдено моделей: '+items.length+'. Активная: '+(data.active_model||'—');
      renderMeta();
    }catch(error){
      if(state) state.textContent='Ошибка списка моделей: '+error.message;
    }
  }

  async function applyModel(){
    const select=byId('localModelSelect');
    const button=byId('localModelApply');
    const state=byId('localModelState');
    const name=(select&&select.value||'').trim();
    if(!name) return;
    button.disabled=true;
    if(state) state.textContent='Переключаю модель… Текущий backend будет перезапущен.';
    try{
      const data=await requestJson('/v1/models/select',{method:'POST',body:JSON.stringify({name})});
      snapshot=data;
      if(state) state.textContent='Активная модель: '+data.active_model+'. Выбор сохранён.';
      await loadModels(data.active_model);
      try{if(typeof window.refreshStatus==='function') await window.refreshStatus();}catch(_e){}
    }catch(error){
      if(state) state.textContent='Не удалось переключить модель: '+error.message;
      button.disabled=false;
    }
  }

  function uploadModel(){
    const input=byId('localModelFile');
    const file=input&&input.files&&input.files[0];
    if(!file) return;
    if(!file.name.toLowerCase().endsWith('.gguf')){alert('Выберите файл с расширением .gguf');input.value='';return;}

    const state=byId('localModelState');
    const progress=byId('localModelUploadProgress');
    const fill=progress&&progress.querySelector('span');
    const choose=byId('localModelChoose');
    if(progress) progress.style.display='block';
    if(fill) fill.style.width='0%';
    if(choose) choose.disabled=true;
    if(state) state.textContent='Загружаю '+file.name+' · '+bytes(file.size);

    const xhr=new XMLHttpRequest();
    xhr.open('POST','/v1/models/upload',true);
    xhr.setRequestHeader('Content-Type','application/octet-stream');
    xhr.setRequestHeader('X-Leshy-Filename',encodeURIComponent(file.name));
    xhr.upload.onprogress=function(event){
      if(event.lengthComputable && fill){fill.style.width=Math.max(0,Math.min(100,event.loaded*100/event.total))+'%';}
      if(state && event.lengthComputable) state.textContent='Загрузка '+file.name+': '+Math.round(event.loaded*100/event.total)+'% · '+bytes(event.loaded)+' / '+bytes(event.total);
    };
    xhr.onerror=function(){if(state)state.textContent='Ошибка сети при загрузке модели.';if(choose)choose.disabled=false;};
    xhr.onload=async function(){
      if(choose) choose.disabled=false;
      if(input) input.value='';
      let data={};try{data=JSON.parse(xhr.responseText||'{}');}catch(_e){}
      if(xhr.status<200||xhr.status>=300){if(state)state.textContent='Ошибка загрузки: '+(data.error||('HTTP '+xhr.status));return;}
      if(fill) fill.style.width='100%';
      const uploaded=data.uploaded&&data.uploaded.name?data.uploaded.name:file.name;
      if(state) state.textContent='Модель '+uploaded+' загружена. Теперь её можно сделать активной.';
      await loadModels(uploaded);
    };
    xhr.send(file);
  }

  function installTasksHelp(){
    ensureStyle();
    const pane=byId('pane-tasks');
    if(!pane || byId('tasksHelpCard')) return;
    const subtitle=pane.querySelector('.section-subtitle');
    const card=document.createElement('div');
    card.id='tasksHelpCard';
    card.className='card';
    card.innerHTML=`
      <h3>Как пользоваться разделом «Задачи»</h3>
      <div class="item-body" style="margin-top:0">Это локальный план и трекер дел. Создайте задачу, добавьте описание, выберите приоритет и при необходимости срок. Затем меняйте её состояние по мере работы.</div>
      <div class="task-help-grid">
        <div class="task-help-state"><b>Открыта</b><span>ещё не начата</span></div>
        <div class="task-help-state"><b>В работе</b><span>выполняется сейчас</span></div>
        <div class="task-help-state"><b>Готово</b><span>задача завершена</span></div>
        <div class="task-help-state"><b>Отменено</b><span>больше не требуется</span></div>
      </div>
      <div class="hint" style="margin-top:10px"><b>Важно:</b> сейчас этот раздел хранит и отслеживает задачи, но ещё не является автономным исполнителем. Если нужно, чтобы Леший реально выполнил действие, дайте команду в чате. В дальнейшем задачи будут связаны с автономным планировщиком и инструментами.</div>
      <div class="hint" style="margin-top:7px">Пример: «Проверить NAS после обновления» → описание «проверить SMB и папку torrent-watch» → высокий приоритет → срок → «В работу» → «Готово».</div>`;
    if(subtitle) subtitle.insertAdjacentElement('afterend',card); else pane.insertBefore(card,pane.firstChild);
  }

  function installTaskDueField(){
    const title=byId('taskTitle');
    if(!title || byId('taskDueAt')) return;
    const form=title.closest('.task-form');
    if(!form) return;
    const due=document.createElement('input');
    due.className='field';
    due.id='taskDueAt';
    due.type='datetime-local';
    due.title='Необязательный срок задачи';
    due.setAttribute('aria-label','Срок задачи');
    const priority=byId('taskPriority');
    if(priority) priority.insertAdjacentElement('afterend',due); else title.insertAdjacentElement('afterend',due);
    form.style.gridTemplateColumns='minmax(0,1fr) 150px minmax(180px,220px)';
  }

  function bindTaskCreate(){
    const button=byId('taskCreate');
    if(!button || button.dataset.tasksEnhanced==='1') return;
    button.dataset.tasksEnhanced='1';
    button.onclick=async()=>{
      const title=(byId('taskTitle')&&byId('taskTitle').value||'').trim();
      if(!title) return;
      button.disabled=true;
      try{
        const raw=(byId('taskDueAt')&&byId('taskDueAt').value||'').trim();
        const dueAt=raw?new Date(raw).toISOString():'';
        await requestJson('/v1/tasks',{method:'POST',body:JSON.stringify({
          owner_id:typeof window.ownerId!=='undefined'?window.ownerId:'web:local',
          title,
          details:(byId('taskDetails')&&byId('taskDetails').value)||'',
          priority:Number((byId('taskPriority')&&byId('taskPriority').value)||0),
          due_at:dueAt
        })});
        byId('taskTitle').value='';
        if(byId('taskDetails')) byId('taskDetails').value='';
        if(byId('taskPriority')) byId('taskPriority').value='0';
        if(byId('taskDueAt')) byId('taskDueAt').value='';
        if(typeof window.loadTasks==='function') await window.loadTasks();
        if(typeof window.loadOverview==='function') await window.loadOverview();
      }catch(error){alert(error.message||String(error));}
      finally{button.disabled=false;}
    };
  }

  function init(){
    ensurePanel();
    loadModels();
    installTasksHelp();
    installTaskDueField();
    bindTaskCreate();
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init,{once:true});
  else init();
})();
