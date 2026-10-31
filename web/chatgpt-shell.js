(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const svg=p=>`<svg viewBox="0 0 24 24" aria-hidden="true">${p}</svg>`;
  const I={
    plus:svg('<path d="M12 5v14M5 12h14"/>'),
    mic:svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4M9 21h6"/>'),
    send:svg('<path d="m5 12 14-7-4 14-3-5-7-2Z"/><path d="m12 14 7-9"/>'),
    stop:svg('<rect x="7" y="7" width="10" height="10" rx="1"/>'),
    chevron:svg('<path d="m8 10 4 4 4-4"/>')
  };

  function css(){
    if($('leshyModernChatShell')) return;
    const s=document.createElement('style');s.id='leshyModernChatShell';s.textContent=`
      :root{--chat-main:#171717;--chat-sidebar:#0f0f0f;--chat-hover:#212121;--chat-border:#2f2f2f;--chat-text:#ececec;--chat-muted:#a8a8a8;--chat-composer:#2f2f2f}
      body.full .workspace{grid-template-columns:260px minmax(0,1fr)!important;background:var(--chat-main)!important;gap:0!important}
      body.full .sidebar{background:var(--chat-sidebar)!important;border-right:0!important}
      body.full .main{background:var(--chat-main)!important}
      body.full .main-head{height:52px!important;background:var(--chat-main)!important;border-bottom:0!important;padding:0 12px 0 18px!important;position:relative;z-index:20}
      body.full .head-title{gap:4px!important;align-items:center!important;cursor:default}
      body.full .head-title strong{font-size:17px!important;font-weight:650!important;color:var(--chat-text)!important}
      body.full .head-title strong:after{content:'⌄';font-size:14px;color:#b7b7b7;margin-left:6px;vertical-align:2px}
      body.full .head-title span{display:none!important}
      #leshyTopActions{gap:2px!important}#leshyTopActions button{border-radius:8px!important;color:#d9d9d9!important}#leshyTopActions button:hover{background:#2a2a2a!important}
      body.full .context{display:none!important}
      body.full.leshy-context-open .workspace{grid-template-columns:260px minmax(0,1fr)!important}
      body.full.leshy-context-open .context.open{display:block!important;position:fixed!important;right:12px!important;top:60px!important;bottom:12px!important;width:min(360px,calc(100vw - 24px))!important;height:auto!important;z-index:500!important;border:1px solid #373737!important;border-radius:14px!important;background:#1d1d1d!important;box-shadow:0 18px 60px rgba(0,0,0,.55)!important}
      #pane-chat{background:var(--chat-main)!important}
      body.full .messages{padding:30px max(20px,calc((100vw - 260px - 820px)/2)) 150px!important;scrollbar-gutter:stable}
      #messages .msg{max-width:780px!important;width:100%!important;margin-left:auto!important;margin-right:auto!important}
      #messages .msg.user{width:fit-content!important;max-width:min(70%,680px)!important;margin-left:auto!important;margin-right:max(0px,calc((100% - 780px)/2))!important;background:#303030!important;border:0!important;border-radius:20px!important;padding:10px 15px!important;color:var(--chat-text)!important}
      #messages .msg.user .who{display:none!important}
      #messages .msg.assistant{background:transparent!important;border:0!important;padding:4px 0 18px!important;color:var(--chat-text)!important}
      #messages .msg.assistant>.who{font-size:11px!important;text-transform:none!important;letter-spacing:0!important;color:#bdbdbd!important;margin-bottom:7px!important;font-weight:600}
      #messages .msg.assistant .chat-rich-text,#messages .msg.assistant p{font-size:15px!important;line-height:1.62!important}
      body.full .chat-compose{position:absolute!important;left:0;right:0;bottom:0;padding:10px max(20px,calc((100vw - 260px - 820px)/2)) 22px!important;border-top:0!important;background:linear-gradient(180deg,rgba(23,23,23,0),#171717 25%,#171717 100%)!important;z-index:15}
      body.full .chat-compose>.composer{position:relative;display:grid!important;grid-template-columns:36px minmax(0,1fr) auto auto!important;grid-template-rows:auto 36px!important;align-items:center!important;gap:5px!important;min-height:92px!important;border:1px solid #464646!important;background:var(--chat-composer)!important;border-radius:26px!important;padding:10px 10px 9px!important;box-shadow:none!important;max-width:820px!important;margin:0 auto!important}
      body.full .chat-compose>.composer:focus-within{border-color:#5a5a5a!important}
      #chatInput{grid-column:1/-1!important;grid-row:1!important;min-height:42px!important;max-height:160px!important;padding:8px 10px 4px!important;font-size:15px!important;line-height:1.45!important;color:#f3f3f3!important;background:transparent!important}
      #chatInput::placeholder{color:#a7a7a7!important}
      #chatAttachButton,#chatMicButton,#chatSend{width:36px!important;height:36px!important;min-width:36px!important;padding:0!important;border-radius:50%!important;border:0!important;display:grid!important;place-items:center!important}
      #chatAttachButton{grid-column:1!important;grid-row:2!important;background:transparent!important;color:#d4d4d4!important;border:1px solid #555!important}
      #chatAttachButton:hover,#chatMicButton:hover{background:#3b3b3b!important}
      #chatMicButton{grid-column:3!important;grid-row:2!important;background:transparent!important;color:#e0e0e0!important}
      #chatSend{grid-column:4!important;grid-row:2!important;background:#f4f4f4!important;color:#171717!important}
      #chatSend:hover{background:#fff!important}#chatSend.streaming{background:#f4f4f4!important;color:#171717!important;min-width:36px!important}
      #chatAttachButton svg,#chatMicButton svg,#chatSend svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      #chatSend svg.stop-fill{fill:currentColor;stroke:none;width:14px;height:14px}
      #chatComposerHint{max-width:820px;margin:7px auto 0;text-align:center;font-size:10px;color:#767676}
      #chatAttachMenu{position:absolute;left:8px;bottom:50px;z-index:700;width:220px;background:#252525;border:1px solid #3a3a3a;border-radius:12px;padding:6px;box-shadow:0 16px 48px rgba(0,0,0,.5);display:none}
      #chatAttachMenu.open{display:block}#chatAttachMenu button{width:100%;height:38px;border:0;background:transparent;color:#e7e7e7;border-radius:8px;text-align:left;padding:0 10px;cursor:pointer}#chatAttachMenu button:hover{background:#333}
      #chatVoiceState{position:absolute;right:55px;bottom:52px;padding:5px 8px;border-radius:8px;background:#232323;border:1px solid #393939;color:#cfcfcf;font-size:10px;display:none}#chatVoiceState.show{display:block}
      #chatUploadInput{display:none}
      #sidebarHistory #sessionList .leshy-session-item{border-radius:8px!important}#sidebarHistory #sessionList .leshy-session-item:hover{background:#212121!important}
      #profileMenu,#chatMoreMenu,#leshySessionMenu{background:#2b2b2b!important;border-color:#414141!important;color:#ececec!important}
      @media(max-width:820px){body.full .messages{padding:20px 14px 145px!important}body.full .chat-compose{padding:8px 10px 14px!important}#messages .msg.user{max-width:85%!important;margin-right:0!important}body.full .chat-compose>.composer{min-height:86px!important}.shareText{display:none!important}}
    `;document.head.appendChild(s);
  }

  function installComposer(){
    const composer=document.querySelector('#pane-chat .chat-compose>.composer');if(!composer||$('chatAttachButton'))return;
    const input=$('chatInput'),send=$('chatSend');if(!input||!send)return;
    const attach=document.createElement('button');attach.id='chatAttachButton';attach.type='button';attach.title='Добавить файл';attach.innerHTML=I.plus;
    const mic=document.createElement('button');mic.id='chatMicButton';mic.type='button';mic.title='Голосовой ввод';mic.innerHTML=I.mic;
    const upload=document.createElement('input');upload.id='chatUploadInput';upload.type='file';upload.multiple=true;upload.accept='image/*,.txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.csv,.json,.log,.py,.js,.ts,.cs,.cpp,.c,.h,.java,.go,.rs,.sh,.ps1,.yaml,.yml,.xml';
    const menu=document.createElement('div');menu.id='chatAttachMenu';menu.innerHTML='<button type="button" data-upload="any">Добавить файл</button><button type="button" data-upload="image">Добавить изображение</button>';
    const voice=document.createElement('div');voice.id='chatVoiceState';
    composer.insertBefore(attach,input);composer.insertBefore(mic,send);composer.append(upload,menu,voice);
    send.innerHTML=I.send;send.title='Отправить';
    attach.onclick=e=>{e.stopPropagation();menu.classList.toggle('open')};
    menu.onclick=e=>{const b=e.target.closest('button');if(!b)return;upload.accept=b.dataset.upload==='image'?'image/*':'image/*,.txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.csv,.json,.log,.py,.js,.ts,.cs,.cpp,.c,.h,.java,.go,.rs,.sh,.ps1,.yaml,.yml,.xml';upload.click();menu.classList.remove('open')};
    upload.onchange=()=>{const files=[...upload.files||[]];if(!files.length)return;const names=files.map(f=>f.name).join(', ');voice.textContent='Выбрано: '+names+' · загрузка вложений будет подключена к multimodal-контуру';voice.classList.add('show');setTimeout(()=>voice.classList.remove('show'),3500);upload.value=''};
    mic.onclick=()=>startVoice(input,voice,mic);
    document.addEventListener('click',e=>{if(!e.target.closest('#chatAttachMenu')&&!e.target.closest('#chatAttachButton'))menu.classList.remove('open')});
    const hint=document.createElement('div');hint.id='chatComposerHint';hint.textContent='Леший может ошибаться. Важную информацию лучше перепроверять.';composer.parentElement.appendChild(hint);
  }

  function updateSendButton(){
    const send=$('chatSend');if(!send)return;
    const streaming=send.classList.contains('streaming');send.innerHTML=streaming?I.stop:I.send;send.title=streaming?'Остановить ответ':'Отправить';
  }

  function observeSend(){
    const send=$('chatSend');if(!send||send.dataset.modernObserved==='1')return;send.dataset.modernObserved='1';new MutationObserver(updateSendButton).observe(send,{attributes:true,attributeFilter:['class'],childList:true,subtree:true});updateSendButton();
  }

  function startVoice(input,state,button){
    const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
    if(!SR){state.textContent='Голосовой ввод не поддерживается этим браузером';state.classList.add('show');setTimeout(()=>state.classList.remove('show'),2500);return}
    if(button.dataset.listening==='1'){try{button._recognition&&button._recognition.stop()}catch(_e){}return}
    const r=new SR();button._recognition=r;r.lang='ru-RU';r.interimResults=true;r.continuous=false;button.dataset.listening='1';button.style.background='#444';state.textContent='Слушаю…';state.classList.add('show');
    let base=input.value.trim();r.onresult=e=>{let final='',interim='';for(let i=e.resultIndex;i<e.results.length;i++){const t=e.results[i][0].transcript;if(e.results[i].isFinal)final+=t;else interim+=t}input.value=(base+(base?' ':'')+final+interim).trimStart();if(final)base=input.value.trim()};
    r.onerror=e=>{state.textContent='Микрофон: '+(e.error||'ошибка');setTimeout(()=>state.classList.remove('show'),2200)};
    r.onend=()=>{delete button.dataset.listening;button.style.background='';state.textContent='Готово';setTimeout(()=>state.classList.remove('show'),900);input.focus()};
    try{r.start()}catch(e){delete button.dataset.listening;state.textContent='Не удалось включить микрофон';state.classList.add('show')}
  }

  function chatTitle(){
    const title=$('pageTitle');if(!title)return;const observer=new MutationObserver(()=>{if(document.querySelector('#pane-chat.active'))title.textContent='Леший'});observer.observe(document.querySelector('.main')||document.body,{subtree:true,attributes:true,attributeFilter:['class']});if(document.querySelector('#pane-chat.active'))title.textContent='Леший';
  }

  function boot(){css();installComposer();observeSend();chatTitle();setInterval(()=>{installComposer();observeSend()},1200)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
