(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  let indicator=null;
  let activityText='';
  let activityKind='';

  function style(){
    if($('leshyThinkingIndicatorStyle'))return;
    const s=document.createElement('style');
    s.id='leshyThinkingIndicatorStyle';
    s.textContent=`
      #leshyThinkingIndicator{display:none;align-items:center;gap:8px;max-width:780px;margin:4px auto 12px;padding:0 2px;color:#9ea8b5;font-size:12px;line-height:22px;user-select:none}
      #leshyThinkingIndicator.show{display:flex}
      #leshyThinkingIndicator.searching{color:#a9bed2}
      #leshyThinkingIndicator .activity-icon{display:none;width:15px;height:15px;flex:0 0 15px}
      #leshyThinkingIndicator.searching .activity-icon{display:block}
      #leshyThinkingIndicator .activity-icon svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
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
    indicator.innerHTML='<span class="activity-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></svg></span><span id="leshyThinkingText">Леший думает</span><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>';
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
    const stateRaw=String($('chatState')?.textContent||'').trim();
    const state=stateRaw.toLowerCase();
    const active=state.includes('отвечает')||state.includes('думает')||state.includes('генер')||!!activityText;
    if(!active){node.classList.remove('show','searching');return}
    const label=$('leshyThinkingText');
    if(activityText&&!lastAssistantHasText()){
      if(label)label.textContent=activityText;
      node.classList.toggle('searching',activityKind==='web_search');
    }else{
      if(label)label.textContent=lastAssistantHasText()?'Леший отвечает':'Леший думает';
      node.classList.remove('searching');
      if(lastAssistantHasText()){activityText='';activityKind=''}
    }
    node.classList.add('show');
    const messages=$('messages');if(messages){messages.appendChild(node);messages.scrollTop=messages.scrollHeight}
  }

  function boot(){
    style();ensure();update();
    window.addEventListener('leshy:activity',event=>{
      const detail=event.detail||{};
      activityText=String(detail.text||'Леший работает…');
      activityKind=String(detail.kind||'');
      update();
    });
    window.addEventListener('leshy:delta',()=>{if(lastAssistantHasText()){activityText='';activityKind=''}update()});
    window.addEventListener('leshy:done',()=>{activityText='';activityKind='';update()});
    const observer=new MutationObserver(update);
    observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
