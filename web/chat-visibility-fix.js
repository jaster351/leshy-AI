(function(){
  'use strict';
  const $=id=>document.getElementById(id);

  function installCss(){
    if($('leshyChatVisibilityFixStyle'))return;
    const s=document.createElement('style');
    s.id='leshyChatVisibilityFixStyle';
    s.textContent=`
      :root{--leshy-chat-safe-bottom:190px}
      body.full #pane-chat .messages{padding-bottom:calc(var(--leshy-chat-safe-bottom) + 28px)!important;scroll-padding-bottom:calc(var(--leshy-chat-safe-bottom) + 28px)!important}
      body.full #messages>.msg:last-child{scroll-margin-bottom:calc(var(--leshy-chat-safe-bottom) + 24px)}
      @media(max-width:820px){body.full #pane-chat .messages{padding-bottom:calc(var(--leshy-chat-safe-bottom) + 20px)!important;scroll-padding-bottom:calc(var(--leshy-chat-safe-bottom) + 20px)!important}}
    `;
    document.head.appendChild(s);
  }

  function messages(){return $('messages')}
  function composer(){return document.querySelector('#pane-chat .chat-compose')}
  function nearBottom(root){return !root||root.scrollHeight-root.scrollTop-root.clientHeight<240}
  function updateSafeBottom(){
    const c=composer();
    if(!c)return;
    const rect=c.getBoundingClientRect();
    const height=Math.max(120,Math.ceil(rect.height||c.offsetHeight||0));
    document.documentElement.style.setProperty('--leshy-chat-safe-bottom',height+'px');
  }
  function keepLatestVisible(force){
    const root=messages();if(!root)return;
    if(!force&&!nearBottom(root))return;
    requestAnimationFrame(()=>{root.scrollTop=root.scrollHeight});
  }

  function boot(){
    installCss();updateSafeBottom();
    const c=composer();
    if(c&&window.ResizeObserver){const ro=new ResizeObserver(()=>{updateSafeBottom();keepLatestVisible(false)});ro.observe(c);window.addEventListener('beforeunload',()=>ro.disconnect(),{once:true})}
    else window.addEventListener('resize',updateSafeBottom);
    window.addEventListener('resize',()=>{updateSafeBottom();keepLatestVisible(false)});
    window.addEventListener('leshy:activity',()=>keepLatestVisible(false));
    window.addEventListener('leshy:delta',()=>keepLatestVisible(false));
    window.addEventListener('leshy:done',()=>{updateSafeBottom();keepLatestVisible(true)});
    const root=messages();
    if(root&&window.MutationObserver){const mo=new MutationObserver(()=>keepLatestVisible(false));mo.observe(root,{childList:true,subtree:true});window.addEventListener('beforeunload',()=>mo.disconnect(),{once:true})}
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
