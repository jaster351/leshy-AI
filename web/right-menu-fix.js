(function(){
  'use strict';
  const $=id=>document.getElementById(id);

  function hideLegacyPanel(){
    const panel=$('contextPanel')||document.querySelector('.context');
    if(panel){panel.classList.remove('open');panel.setAttribute('aria-hidden','true');panel.style.setProperty('display','none','important')}
    document.body.classList.remove('leshy-context-open');
    const legacyOpen=$('contextOpen');if(legacyOpen)legacyOpen.style.setProperty('display','none','important');
  }

  function style(){
    if($('leshyRightMenuFixStyle'))return;
    const s=document.createElement('style');s.id='leshyRightMenuFixStyle';s.textContent=`
      body.full .context,body.full .context.open,body.full.leshy-context-open .context,body.full.leshy-context-open .context.open{display:none!important;visibility:hidden!important;pointer-events:none!important;transform:none!important;box-shadow:none!important}
      body.full .workspace,body.full.leshy-context-open .workspace{grid-template-columns:260px minmax(0,1fr)!important}
      #chatMoreMenu{position:fixed!important;width:230px!important;max-width:calc(100vw - 20px)!important;border-radius:12px!important;z-index:1000!important}
      @media(max-width:820px){body.full .workspace,body.full.leshy-context-open .workspace{grid-template-columns:1fr!important}}
    `;document.head.appendChild(s);
  }

  function bindMenu(){
    const menu=$('chatMoreMenu'),button=$('chatMoreButton');
    if(!menu||!button)return false;
    const status=[...menu.querySelectorAll('button')].find(b=>b.textContent.trim()==='Состояние Лешего');
    if(status&&status.dataset.dropdownFixed!=='1'){
      status.dataset.dropdownFixed='1';
      status.onclick=()=>{
        hideLegacyPanel();menu.classList.remove('open');button.classList.remove('open');
        if(typeof window.showSection==='function')window.showSection('system');
        else document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('active',p.id==='pane-system'));
      };
    }
    if(button.dataset.dropdownPositionBound!=='1'){
      button.dataset.dropdownPositionBound='1';
      button.addEventListener('click',()=>setTimeout(()=>{
        hideLegacyPanel();if(!menu.classList.contains('open'))return;
        const r=button.getBoundingClientRect(),width=Math.min(230,window.innerWidth-20);
        menu.style.width=width+'px';menu.style.right='auto';menu.style.left=Math.max(10,Math.min(window.innerWidth-width-10,r.right-width))+'px';menu.style.top=Math.min(window.innerHeight-menu.offsetHeight-10,r.bottom+7)+'px';
      },0),true);
    }
    return true;
  }

  function boot(){
    style();hideLegacyPanel();
    let tries=0;const timer=setInterval(()=>{tries++;hideLegacyPanel();if(bindMenu()||tries>=12)clearInterval(timer)},250);bindMenu();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
