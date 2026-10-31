(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  let indicator=null;
  let currentSession=localStorage.getItem('leshy.session')||'';
  let state={active:false,text:'',kind:''};

  function style(){
    if($('leshyThinkingIndicatorStyle'))return;
    const s=document.createElement('style');s.id='leshyThinkingIndicatorStyle';s.textContent=`
      #leshyThinkingIndicator{display:none;align-items:center;gap:8px;max-width:780px;margin:4px auto 12px;padding:0 2px;color:#9ea8b5;font-size:12px;line-height:22px;user-select:none}
      #leshyThinkingIndicator.show{display:flex}#leshyThinkingIndicator.searching{color:#a9bed2}
      #leshyThinkingIndicator .activity-icon{display:none;width:15px;height:15px;flex:0 0 15px}#leshyThinkingIndicator.searching .activity-icon{display:block}
      #leshyThinkingIndicator .activity-icon svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
      #leshyThinkingIndicator .dots{display:inline-flex;gap:3px;align-items:center;height:14px}#leshyThinkingIndicator .dots i{display:block;width:4px;height:4px;border-radius:50%;background:currentColor;opacity:.35;animation:leshyThinkingDot 1.15s infinite ease-in-out}#leshyThinkingIndicator .dots i:nth-child(2){animation-delay:.16s}#leshyThinkingIndicator .dots i:nth-child(3){animation-delay:.32s}
      @keyframes leshyThinkingDot{0%,60%,100%{transform:translateY(0);opacity:.28}30%{transform:translateY(-3px);opacity:1}}
    `;document.head.appendChild(s);
  }
  function ensure(){
    if(indicator&&indicator.isConnected)return indicator;const messages=$('messages');if(!messages)return null;
    indicator=document.createElement('div');indicator.id='leshyThinkingIndicator';indicator.innerHTML='<span class="activity-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></svg></span><span id="leshyThinkingText">Леший думает</span><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>';messages.appendChild(indicator);return indicator;
  }
  function render(){
    const node=ensure();if(!node)return;if(!state.active){node.classList.remove('show','searching');return}
    const label=$('leshyThinkingText');if(label)label.textContent=state.text||'Леший думает';node.classList.toggle('searching',state.kind==='web_search');node.classList.add('show');const messages=$('messages');if(messages)messages.appendChild(node);
  }
  function isCurrent(detail){const s=String(detail&&detail.session_id||'');return !s||!currentSession||s===currentSession}
  function boot(){
    style();ensure();render();
    window.addEventListener('leshy:session-changed',e=>{currentSession=String(e.detail?.session_id||'');state={active:false,text:'',kind:''};render()});
    window.addEventListener('leshy:activity',e=>{if(!isCurrent(e.detail))return;state={active:true,text:String(e.detail?.text||'Леший думает…'),kind:String(e.detail?.kind||'')};render()});
    window.addEventListener('leshy:delta',e=>{if(!isCurrent(e.detail))return;state={active:true,text:'Леший отвечает…',kind:''};render()});
    window.addEventListener('leshy:done',e=>{if(!isCurrent(e.detail))return;state={active:false,text:'',kind:''};render()});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
