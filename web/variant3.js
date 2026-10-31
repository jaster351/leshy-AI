(function(){
  'use strict';

  function $(id){ return document.getElementById(id); }
  const runtimeState={learning:null};

  async function request(path, options){
    if(typeof window.api==='function') return window.api(path, options||{});
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok) throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function setUiState(text,kind){
    const node=$('chatState');
    if(!node) return;
    node.textContent=text;
    node.classList.toggle('bad',kind==='error');
    node.classList.toggle('warn',kind==='warn');
    node.classList.toggle('good',kind==='ok');
  }

  function safe(name,fn){
    try{return fn();}
    catch(error){
      console.error('[Leshy UI]',name,error);
      setUiState('Ошибка интерфейса: '+(error&&error.message?error.message:String(error)),'error');
      return null;
    }
  }

  function forceProfessionalMode(){
    try{ localStorage.setItem('leshy.ui.mode','full'); }catch(_e){}
    document.body.classList.add('full');
    const minimal=$('minimalMode');
    if(minimal){ minimal.style.display='none'; minimal.disabled=true; }
    try{
      if(typeof window.showSection==='function'){
        const section=(typeof window.currentSection!=='undefined'&&window.currentSection)||localStorage.getItem('leshy.ui.section')||'overview';
        window.showSection(section,false);
      }
    }catch(_e){}
  }

  function showSectionSafe(section){
    if(typeof window.showSection==='function'){
      try{ window.showSection(section); return; }
      catch(error){ console.error('[Leshy UI] showSection failed',error); }
    }
    document.body.classList.add('full');
    document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('active',p.id==='pane-'+section));
    document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.section===section));
  }

  async function fallbackSend(input){
    const text=(input&&input.value||'').trim();
    if(!text) return;
    input.value='';
    showSectionSafe('chat');
    const messages=$('messages');
    function append(role,value){
      if(!messages) return;
      const box=document.createElement('div');
      box.className='msg '+role;
      const who=document.createElement('div');
      who.className='who';
      who.textContent=role==='user'?'Вы':'Леший';
      const body=document.createElement('div');
      body.textContent=value;
      box.append(who,body);
      messages.appendChild(box);
      messages.scrollTop=messages.scrollHeight;
    }
    append('user',text);
    setUiState('Думаю…','warn');
    try{
      const sessionKey='leshy.session';
      const sessionId=localStorage.getItem(sessionKey)||'';
      const result=await request('/v1/chat',{method:'POST',body:JSON.stringify({message:text,session_id:sessionId,owner_id:'web:local'})});
      if(result.session_id) localStorage.setItem(sessionKey,result.session_id);
      append('assistant',result.answer||'Ответ пуст.');
      setUiState('Готов','ok');
    }catch(error){
      append('assistant','Ошибка: '+error.message);
      setUiState('Ошибка','error');
    }
  }

  function sendSafe(input){
    if(typeof window.sendFrom==='function'){
      try{ return window.sendFrom(input); }
      catch(error){ console.error('[Leshy UI] sendFrom failed',error); }
    }
    return fallbackSend(input);
  }

  function updateLearningControls(enabled){
    runtimeState.learning=!!enabled;
    const switcher=$('learningSwitch');
    if(switcher){
      switcher.classList.toggle('on',!!enabled);
      switcher.setAttribute('aria-pressed',enabled?'true':'false');
      switcher.title=enabled?'Выключить самообучение':'Включить самообучение';
    }
    const quick=$('learningQuickToggle');
    if(quick){
      quick.textContent='Самообучение: '+(enabled?'ВКЛ':'ВЫКЛ');
      quick.classList.toggle('learning-on',!!enabled);
      quick.classList.toggle('learning-off',!enabled);
    }
  }

  async function refreshLearningState(){
    try{
      const status=await request('/v1/status');
      updateLearningControls(!!status.learning_enabled);
      return status;
    }catch(_error){
      const quick=$('learningQuickToggle');
      if(quick) quick.textContent='Самообучение: сервер недоступен';
      return null;
    }
  }

  async function toggleLearning(){
    const next=runtimeState.learning===null ? true : !runtimeState.learning;
    const buttons=[$('learningSwitch'),$('learningQuickToggle')].filter(Boolean);
    buttons.forEach(b=>b.disabled=true);
    try{
      const result=await request('/v1/learning',{method:'POST',body:JSON.stringify({enabled:next})});
      updateLearningControls(typeof result.enabled==='boolean'?result.enabled:next);
      const state=$('learningState');
      if(state) state.textContent=runtimeState.learning
        ? 'Самообучение включено. Леший самостоятельно исследует интернет и проверяет знания.'
        : 'Самообучение выключено пользователем.';
      setUiState(runtimeState.learning?'Самообучение включено':'Самообучение выключено','ok');
    }catch(error){
      alert('Не удалось изменить самообучение: '+error.message);
      setUiState('Ошибка самообучения','error');
    }finally{
      buttons.forEach(b=>b.disabled=false);
    }
  }

  function installVisibleLearningButton(){
    if($('learningQuickToggle')) return;
    const host=document.querySelector('.head-actions') || document.querySelector('.main-head');
    if(!host) return;
    const button=document.createElement('button');
    button.id='learningQuickToggle';
    button.className='button small secondary learning-off';
    button.type='button';
    button.textContent='Самообучение: …';
    button.title='Включить или выключить автономное обучение из интернета';
    button.addEventListener('click',toggleLearning);
    host.insertBefore(button,host.firstChild);
  }

  function makeTelegramAccessUi(){
    const users=$('tgUsers');
    if(!users || $('tgOwner')) return;
    const card=users.closest('.card');
    if(!card) return;

    const block=document.createElement('div');
    block.className='telegram-access-block';

    const owner=document.createElement('input');
    owner.className='field';
    owner.id='tgOwner';
    owner.placeholder='Telegram ID хозяина';
    owner.inputMode='numeric';

    const checkLabel=document.createElement('label');
    checkLabel.className='telegram-check';
    const check=document.createElement('input');
    check.type='checkbox';
    check.id='tgAllowAll';
    const checkText=document.createElement('span');
    checkText.textContent='Разрешить боту отвечать всем';
    checkLabel.append(check,checkText);

    const note=document.createElement('div');
    note.className='telegram-owner-note';
    note.textContent='Хозяин всегда имеет доступ и административные права. Остальные пользователи могут общаться с Лешим, но не могут включать обучение или менять административные настройки.';

    block.append(owner,checkLabel,note);
    users.insertAdjacentElement('afterend',block);

    const save=$('tgSave');
    if(save){
      save.onclick=async function(){
        const token=($('tgToken')&&$('tgToken').value)||'';
        const allowed=(users.value||'').split(/[\s,;]+/).filter(Boolean);
        const ownerId=(owner.value||'').trim();
        const allowAll=check.checked;
        if(allowAll && !ownerId){
          alert('Для режима «отвечать всем» укажите Telegram ID хозяина.');
          owner.focus();
          return;
        }
        try{
          const data=await request('/v1/telegram/config',{
            method:'POST',
            body:JSON.stringify({token:token,allowed_users:allowed,allow_all:allowAll,owner_id:ownerId})
          });
          if($('tgToken')) $('tgToken').value='';
          applyTelegramStatus(data);
          try{ if(typeof window.refreshStatus==='function') await window.refreshStatus(); }catch(_e){}
        }catch(e){ alert(e.message); }
      };
    }
  }

  function applyTelegramStatus(tg){
    if(!tg) return;
    const owner=$('tgOwner');
    const allow=$('tgAllowAll');
    if(owner && document.activeElement!==owner) owner.value=tg.owner_id||'';
    if(allow) allow.checked=!!tg.allow_all;
    const state=$('telegramState');
    if(state){
      let text=tg.running?'работает':(tg.configured?'настроен':'не настроен');
      if(tg.allow_all) text+=' · отвечает всем';
      else if(tg.allowed_users) text+=' · доступ по списку';
      state.textContent=text;
    }
  }

  async function refreshTelegramAccess(){
    try{
      const data=await request('/v1/telegram/status');
      applyTelegramStatus(data);
    }catch(_e){}
  }

  function bindCoreControls(){
    document.querySelectorAll('.nav-item').forEach(button=>{
      button.onclick=()=>showSectionSafe(button.dataset.section||'overview');
    });
    document.querySelectorAll('[data-start]').forEach(button=>{
      button.onclick=()=>{
        const target=button.dataset.start||'overview';
        if(target==='search'){
          showSectionSafe('chat');
          const input=$('chatInput');
          if(input){input.value='/search ';input.focus();}
        }else showSectionSafe(target);
      };
    });

    const heroSend=$('heroSend');
    if(heroSend) heroSend.onclick=()=>sendSafe($('heroInput'));
    const chatSend=$('chatSend');
    if(chatSend) chatSend.onclick=()=>sendSafe($('chatInput'));
    ['heroInput','chatInput'].forEach(id=>{
      const input=$(id);
      if(!input || input.dataset.recoveryEnter==='1') return;
      input.dataset.recoveryEnter='1';
      input.addEventListener('keydown',event=>{
        if(event.key==='Enter'&&!event.shiftKey){ event.preventDefault(); sendSafe(input); }
      });
    });

    const learning=$('learningSwitch');
    if(learning) learning.onclick=toggleLearning;

    const research=$('researchQueue');
    if(research) research.onclick=async()=>{
      const input=$('researchTopic');
      const query=(input&&input.value||'').trim();
      if(!query) return;
      research.disabled=true;
      try{
        await request('/v1/research/queue',{method:'POST',body:JSON.stringify({query})});
        if(input) input.value='';
        if(typeof window.loadLearning==='function') await window.loadLearning();
        setUiState('Тема добавлена в обучение','ok');
      }catch(error){ alert(error.message); }
      finally{ research.disabled=false; }
    };

    const modelDownload=$('modelDownload');
    if(modelDownload) modelDownload.onclick=async()=>{
      modelDownload.disabled=true;
      try{
        await request('/v1/model/download',{method:'POST',body:'{}'});
        const progress=$('modelProgress');
        if(progress) progress.textContent='Загрузка модели запущена…';
      }catch(error){ alert(error.message); }
      finally{ setTimeout(()=>{modelDownload.disabled=false;},1200); }
    };

    const knowledgeRefresh=$('knowledgeRefresh');
    if(knowledgeRefresh) knowledgeRefresh.onclick=()=>{
      if(typeof window.loadKnowledge==='function') return window.loadKnowledge();
      showSectionSafe('knowledge');
    };
    const tasksRefresh=$('tasksRefresh');
    if(tasksRefresh) tasksRefresh.onclick=()=>{
      if(typeof window.loadTasks==='function') return window.loadTasks();
      showSectionSafe('tasks');
    };
    const logsRefresh=$('logsRefresh');
    if(logsRefresh) logsRefresh.onclick=()=>{
      if(typeof window.loadLogs==='function') return window.loadLogs();
      location.reload();
    };

    const contextOpen=$('contextOpen');
    if(contextOpen) contextOpen.onclick=()=>{ const panel=$('contextPanel'); if(panel) panel.classList.add('open'); };
    const contextClose=$('contextClose');
    if(contextClose) contextClose.onclick=()=>{ const panel=$('contextPanel'); if(panel) panel.classList.remove('open'); };
  }

  function installRecoveryStyle(){
    if($('leshyRecoveryStyle')) return;
    const style=document.createElement('style');
    style.id='leshyRecoveryStyle';
    style.textContent=`
      #learningQuickToggle{white-space:nowrap}
      #learningQuickToggle.learning-on{border-color:rgba(147,221,177,.55);background:rgba(80,180,120,.18);color:#cffff0}
      #learningQuickToggle.learning-off{border-color:rgba(241,207,141,.32);background:rgba(241,207,141,.07);color:#efdba8}
      @media(max-width:720px){#learningQuickToggle{font-size:11px;padding:7px 9px}}
    `;
    document.head.appendChild(style);
  }

  function boot(){
    safe('professional mode',forceProfessionalMode);
    safe('recovery styles',installRecoveryStyle);
    safe('learning button',installVisibleLearningButton);
    safe('telegram controls',makeTelegramAccessUi);
    safe('core controls',bindCoreControls);
    refreshTelegramAccess();
    refreshLearningState();
    setInterval(refreshTelegramAccess,5000);
    setInterval(refreshLearningState,5000);

    window.addEventListener('error',event=>{
      console.error('[Leshy UI] uncaught error',event.error||event.message);
      setUiState('Ошибка интерфейса — кнопки переподключены','warn');
      safe('rebind after error',bindCoreControls);
    });
    window.addEventListener('unhandledrejection',event=>{
      console.error('[Leshy UI] unhandled promise rejection',event.reason);
    });
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',boot,{once:true});
  else boot();
})();
