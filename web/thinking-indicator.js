(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  let indicator=null;

  function style(){
    if($('leshyThinkingIndicatorStyle'))return;
    const s=document.createElement('style');
    s.id='leshyThinkingIndicatorStyle';
    s.textContent=`
      #leshyThinkingIndicator{display:none;align-items:center;gap:8px;max-width:780px;margin:4px auto 12px;padding:0 2px;color:#9ea8b5;font-size:12px;line-height:22px;user-select:none}
      #leshyThinkingIndicator.show{display:flex}
      #leshyThinkingIndicator .dots{display:inline-flex;gap:3px;align-items:center;height:14px}
      #leshyThinkingIndicator .dots i{display:block;width:4px;height:4px;border-radius:50%;background:currentColor;opacity:.35;animation:leshyThinkingDot 1.15s infinite ease-in-out}
      #leshyThinkingIndicator .dots i:nth-child(2){animation-delay:.16s}#leshyThinkingIndicator .dots i:nth-child(3){animation-delay:.32s}
      @keyframes leshyThinkingDot{0%,60%,100%{transform:translateY(0);opacity:.28}30%{transform:translateY(-3px);opacity:1}}
    `;
    document.head.appendChild(s);
  }

  function ensure(){
    if(indicator&&indicator.isConnected)return indicator;
    const messages=$('messages');if(!messages)return null;
    indicator=document.createElement('div');indicator.id='leshyThinkingIndicator';
    indicator.innerHTML='<span id="leshyThinkingText">Леший думает</span><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>';
    messages.appendChild(indicator);
    return indicator;
  }

  function lastAssistantHasText(){
    const messages=$('messages');if(!messages)return false;
    const list=[...messages.querySelectorAll('.msg.assistant')];
    if(!list.length)return false;
    const last=list[list.length-1];
    const clone=last.cloneNode(true);clone.querySelectorAll('.who,.chat-stream-caret,.sources').forEach(x=>x.remove());
    return clone.textContent.trim().length>0;
  }

  function update(){
    const node=ensure();if(!node)return;
    const state=String($('chatState')?.textContent||'').trim().toLowerCase();
    const active=state.includes('отвечает')||state.includes('думает')||state.includes('генер');
    if(!active){node.classList.remove('show');return}
    const label=$('leshyThinkingText');
    if(label)label.textContent=lastAssistantHasText()?'Леший отвечает':'Леший думает';
    node.classList.add('show');
    const messages=$('messages');if(messages){messages.appendChild(node);messages.scrollTop=messages.scrollHeight}
  }

  function boot(){
    style();ensure();update();
    const observer=new MutationObserver(update);
    observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
