(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  let hideTimer=0;
  let layoutFrame=0;
  const mobileViewport='(max-width:1024px) and (pointer:coarse)';

  function installCss(){
    if($('leshyChatLayoutStatusStyle'))return;
    const style=document.createElement('style');
    style.id='leshyChatLayoutStatusStyle';
    style.textContent=`
      :root{--leshy-composer-inset:170px;--leshy-viewport-height:100vh;--leshy-viewport-offset-top:0px}
      @supports(height:100dvh){:root{--leshy-viewport-height:100dvh}}
      body.full #pane-chat{overflow:hidden!important}
      body.full #messages{padding-bottom:calc(var(--leshy-composer-inset) + 28px)!important;scroll-padding-bottom:calc(var(--leshy-composer-inset) + 28px)!important}
      body.full #messages .chat-code-block{min-width:0!important;max-width:100%!important}
      body.full #profileMenu{box-sizing:border-box;max-height:calc(var(--leshy-viewport-height) - 80px - env(safe-area-inset-top,0px) - env(safe-area-inset-bottom,0px));overflow-y:auto;overscroll-behavior:contain}
      #sessionArchiveToggle{display:none!important}
      #leshyWorkStatus{position:absolute;left:50%;bottom:calc(var(--leshy-composer-inset) - 8px);transform:translateX(-50%);z-index:34;display:none;align-items:center;gap:8px;max-width:min(760px,calc(100% - 40px));padding:6px 10px;border:1px solid #3d3d3d;background:rgba(33,33,33,.94);backdrop-filter:blur(10px);border-radius:10px;color:#bdbdbd;font-size:12px;line-height:1.35;box-shadow:0 8px 24px rgba(0,0,0,.2);pointer-events:none}
      #leshyWorkStatus.show{display:flex}
      #leshyWorkStatus .dot{width:7px;height:7px;border-radius:50%;background:#a8c8ff;box-shadow:0 0 0 3px rgba(168,200,255,.08);animation:leshyWorkStatusPulse 1.15s ease-in-out infinite;flex:0 0 7px}
      #leshyWorkStatus .text{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      @keyframes leshyWorkStatusPulse{50%{opacity:.35;transform:scale(.75)}}
      @media(max-width:1024px){body.full .row{flex-wrap:wrap}body.full [data-compute-mode]{min-width:0!important;max-width:100%!important;flex:1 1 230px}}
      @media(max-width:820px),(max-width:1024px) and (pointer:coarse){
        body.full .workspace{position:relative!important;top:var(--leshy-viewport-offset-top)!important;height:var(--leshy-viewport-height)!important}
        body.full .main-head{height:calc(54px + env(safe-area-inset-top,0px))!important;flex-shrink:0;padding-top:env(safe-area-inset-top,0px)!important}
        body.full .chat-compose{padding-bottom:calc(18px + env(safe-area-inset-bottom,0px))!important}
        body.full #chatInput{max-height:clamp(42px,calc(var(--leshy-viewport-height) / 4),160px)!important}
      }
      @media(max-width:820px){
        body.full .sidebar{top:var(--leshy-viewport-offset-top)!important;bottom:auto!important;height:var(--leshy-viewport-height)!important;padding-top:env(safe-area-inset-top,0px)!important;padding-bottom:env(safe-area-inset-bottom,0px)!important}
        body.full .chat-compose{padding-bottom:calc(14px + env(safe-area-inset-bottom,0px))!important}
      }
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
    setLayoutValue('--leshy-composer-inset',inset+'px');
    keepBottomVisible();
  }

  function setLayoutValue(name,value){
    const style=document.documentElement.style;
    if(style.getPropertyValue(name)!==value)style.setProperty(name,value);
  }

  function measureViewport(){
    const viewport=window.visualViewport,style=document.documentElement.style;
    if(!window.matchMedia(mobileViewport).matches){
      style.removeProperty('--leshy-viewport-height');
      style.removeProperty('--leshy-viewport-offset-top');
      return;
    }
    // Масштабирование должно увеличивать страницу без перестройки оболочки.
    if(!viewport||Math.abs(viewport.scale-1)>.01)return;
    if(Number.isFinite(viewport.height)&&viewport.height>0){
      setLayoutValue('--leshy-viewport-height',Math.round(viewport.height)+'px');
      setLayoutValue('--leshy-viewport-offset-top',Math.max(0,Math.round(viewport.offsetTop))+'px');
    }
  }

  function scheduleLayout(){
    if(layoutFrame)return;
    layoutFrame=requestAnimationFrame(()=>{layoutFrame=0;measureViewport();measureComposer()});
  }

  function show(text){
    clearTimeout(hideTimer);const node=ensureStatus();if(!node)return;
    const label=node.querySelector('.text');if(label)label.textContent=String(text||'Леший работает…');
    node.classList.add('show');scheduleLayout();
  }
  function hide(delay=220){
    clearTimeout(hideTimer);hideTimer=setTimeout(()=>{const node=$('leshyWorkStatus');if(node)node.classList.remove('show')},delay);
  }

  function bind(){
    const pane=$('pane-chat'),composer=pane&&pane.querySelector('.chat-compose');
    if(!pane||!composer)return false;
    measureViewport();measureComposer();
    if('ResizeObserver' in window){const ro=new ResizeObserver(scheduleLayout);ro.observe(composer);ro.observe(pane);window.__leshyChatLayoutObserver=ro}
    window.addEventListener('resize',scheduleLayout,{passive:true});
    if(window.visualViewport){window.visualViewport.addEventListener('resize',scheduleLayout,{passive:true});window.visualViewport.addEventListener('scroll',scheduleLayout,{passive:true})}
    window.addEventListener('leshy:activity',e=>show(e.detail&&e.detail.text||'Леший работает…'));
    window.addEventListener('leshy:delta',()=>hide(80));
    window.addEventListener('leshy:done',()=>hide(100));
    window.addEventListener('leshy:session-changed',()=>{hide(0);if(window.matchMedia('(max-width:820px)').matches)document.querySelector('.sidebar')?.classList.remove('mobile-open');setTimeout(scheduleLayout,50)});
    document.addEventListener('input',e=>{if(e.target&&e.target.id==='chatInput')scheduleLayout()});
    return true;
  }

  function boot(){installCss();ensureStatus();if(!bind())setTimeout(boot,120)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
