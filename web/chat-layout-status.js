(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  let hideTimer=0;

  function installCss(){
    if($('leshyChatLayoutStatusStyle'))return;
    const style=document.createElement('style');
    style.id='leshyChatLayoutStatusStyle';
    style.textContent=`
      :root{--leshy-composer-inset:170px}
      body.full #pane-chat{overflow:hidden!important}
      body.full #messages{padding-bottom:calc(var(--leshy-composer-inset) + 28px)!important;scroll-padding-bottom:calc(var(--leshy-composer-inset) + 28px)!important}
      #sessionArchiveToggle{display:none!important}
      #leshyWorkStatus{position:absolute;left:50%;bottom:calc(var(--leshy-composer-inset) - 8px);transform:translateX(-50%);z-index:34;display:none;align-items:center;gap:8px;max-width:min(760px,calc(100% - 40px));padding:6px 10px;border:1px solid #3d3d3d;background:rgba(33,33,33,.94);backdrop-filter:blur(10px);border-radius:10px;color:#bdbdbd;font-size:12px;line-height:1.35;box-shadow:0 8px 24px rgba(0,0,0,.2);pointer-events:none}
      #leshyWorkStatus.show{display:flex}
      #leshyWorkStatus .dot{width:7px;height:7px;border-radius:50%;background:#a8c8ff;box-shadow:0 0 0 3px rgba(168,200,255,.08);animation:leshyWorkStatusPulse 1.15s ease-in-out infinite;flex:0 0 7px}
      #leshyWorkStatus .text{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      @keyframes leshyWorkStatusPulse{50%{opacity:.35;transform:scale(.75)}}
      @media(max-width:820px){body.full #messages{padding-bottom:calc(var(--leshy-composer-inset) + 20px)!important}#leshyWorkStatus{max-width:calc(100% - 20px);bottom:calc(var(--leshy-composer-inset) - 5px)}}
    `;
    document.head.appendChild(style);
  }

  function ensureStatus(){
    let node=$('leshyWorkStatus');
    if(node)return node;
    const pane=$('pane-chat');if(!pane)return null;
    node=document.createElement('div');node.id='leshyWorkStatus';node.setAttribute('role','status');node.setAttribute('aria-live','polite');
    node.innerHTML='<span class="dot" aria-hidden="true"></span><span class="text">Леший думает…</span>';
    pane.appendChild(node);return node;
  }

  function keepBottomVisible(){
    const messages=$('messages');if(!messages)return;
    const gap=messages.scrollHeight-messages.scrollTop-messages.clientHeight;
    if(gap<260)messages.scrollTop=messages.scrollHeight;
  }

  function measureComposer(){
    const pane=$('pane-chat'),composer=pane&&pane.querySelector('.chat-compose');
    if(!pane||!composer)return;
    const rect=composer.getBoundingClientRect();
    const paneRect=pane.getBoundingClientRect();
    const overlap=Math.max(0,paneRect.bottom-Math.max(paneRect.top,rect.top));
    const inset=Math.max(130,Math.ceil(overlap+12));
    document.documentElement.style.setProperty('--leshy-composer-inset',inset+'px');
    keepBottomVisible();
  }

  function show(text){
    clearTimeout(hideTimer);const node=ensureStatus();if(!node)return;
    const label=node.querySelector('.text');if(label)label.textContent=String(text||'Леший работает…');
    node.classList.add('show');measureComposer();
  }
  function hide(delay=220){
    clearTimeout(hideTimer);hideTimer=setTimeout(()=>{const node=$('leshyWorkStatus');if(node)node.classList.remove('show')},delay);
  }

  function bind(){
    const pane=$('pane-chat'),composer=pane&&pane.querySelector('.chat-compose');
    if(!pane||!composer)return false;
    measureComposer();
    if('ResizeObserver' in window){const ro=new ResizeObserver(measureComposer);ro.observe(composer);ro.observe(pane);window.__leshyChatLayoutObserver=ro}
    window.addEventListener('resize',measureComposer,{passive:true});
    window.addEventListener('leshy:activity',e=>show(e.detail&&e.detail.text||'Леший работает…'));
    window.addEventListener('leshy:delta',()=>hide(80));
    window.addEventListener('leshy:done',()=>hide(100));
    window.addEventListener('leshy:session-changed',()=>{hide(0);setTimeout(measureComposer,50)});
    document.addEventListener('input',e=>{if(e.target&&e.target.id==='chatInput')requestAnimationFrame(measureComposer)});
    return true;
  }

  function boot(){installCss();ensureStatus();if(!bind())setTimeout(boot,120)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
