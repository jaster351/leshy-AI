(function(){
  'use strict';

  function $(id){ return document.getElementById(id); }

  function forceProfessionalMode(){
    try{ localStorage.setItem('leshy.ui.mode','full'); }catch(_e){}
    document.body.classList.add('full');
    const minimal=$('minimalMode');
    if(minimal){ minimal.style.display='none'; minimal.disabled=true; }
    try{
      if(typeof showSection==='function'){
        const section=(typeof currentSection!=='undefined'&&currentSection)||localStorage.getItem('leshy.ui.section')||'overview';
        showSection(section,false);
      }
    }catch(_e){}
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
          const res=await fetch('/v1/telegram/config',{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({token:token,allowed_users:allowed,allow_all:allowAll,owner_id:ownerId})
          });
          const data=await res.json().catch(()=>({}));
          if(!res.ok) throw new Error(data.error||('HTTP '+res.status));
          if($('tgToken')) $('tgToken').value='';
          applyTelegramStatus(data);
          try{ if(typeof refreshStatus==='function') await refreshStatus(); }catch(_e){}
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
      const res=await fetch('/v1/telegram/status',{cache:'no-store'});
      if(!res.ok) return;
      applyTelegramStatus(await res.json());
    }catch(_e){}
  }

  function boot(){
    forceProfessionalMode();
    makeTelegramAccessUi();
    refreshTelegramAccess();
    setInterval(refreshTelegramAccess,5000);
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',boot,{once:true});
  else boot();
})();
