(function(){
  'use strict';

  const OWNER='web:local';
  const SESSION_KEY='leshy.session';
  const STALL_MS=90000;
  let active=null;
  let stallTimer=null;

  const $=id=>document.getElementById(id);
  const sid=()=>{try{return localStorage.getItem(SESSION_KEY)||''}catch(_e){return ''}};
  function setSid(value){
    const id=String(value||'');
    try{if(id)localStorage.setItem(SESSION_KEY,id);else localStorage.removeItem(SESSION_KEY)}catch(_e){}
    try{window.sessionId=id}catch(_e){}
    try{window.dispatchEvent(new CustomEvent('leshy:session-changed',{detail:{session_id:id}}))}catch(_e){}
  }
  function state(text){const n=$('chatState');if(n)n.textContent=text}
  function emit(type,detail){try{window.dispatchEvent(new CustomEvent('leshy:'+type,{detail:detail||{}}))}catch(_e){}}
  function rid(){try{return crypto.randomUUID()}catch(_e){return Date.now()+'-'+Math.random().toString(16).slice(2)}}

  function clearStall(){if(stallTimer){clearTimeout(stallTimer);stallTimer=null}}
  function armStall(req){
    clearStall();
    stallTimer=setTimeout(()=>{
      if(active!==req)return;
      stop(req,'timeout');
      state('Ответ остановлен: превышено время ожидания');
      if(typeof window.addMessage==='function')window.addMessage('assistant','Ответ не пришёл за 90 секунд без прогресса. Поток остановлен; запрос можно отправить повторно.');
      emit('stream-stalled',{session_id:req.sessionId||sid(),request_id:req.requestId,timeout_ms:STALL_MS});
    },STALL_MS);
  }

  function makeBox(){
    const root=$('messages');if(!root)return null;
    const box=document.createElement('div');box.className='msg assistant leshy-canonical-stream';
    const who=document.createElement('div');who.className='who';who.textContent='Леший';
    const body=document.createElement('div');body.className='leshy-stream-plain';
    box.append(who,body);root.appendChild(box);root.scrollTop=root.scrollHeight;
    return {box,body};
  }
  function render(req){
    if(!req.ui||!req.ui.body||!req.ui.body.isConnected)return;
    req.ui.body.textContent=req.raw;
    const root=$('messages');if(root&&root.scrollHeight-root.scrollTop-root.clientHeight<180)root.scrollTop=root.scrollHeight;
  }
  function finalRender(req,answer,sources){
    if(req.ui&&req.ui.box&&req.ui.box.isConnected)req.ui.box.remove();
    if(typeof window.addMessage==='function')window.addMessage('assistant',answer||'',sources||[]);
    else if(req.ui&&req.ui.body)req.ui.body.textContent=answer||'';
  }

  function parse(buffer,onEvent){
    let pos;
    while((pos=buffer.indexOf('\n\n'))>=0){
      const block=buffer.slice(0,pos);buffer=buffer.slice(pos+2);
      let type='message',data='';
      for(const line of block.split('\n')){
        if(line.startsWith('event:'))type=line.slice(6).trim();
        else if(line.startsWith('data:'))data+=(data?'\n':'')+line.slice(5).trim();
      }
      if(data){try{onEvent(type,JSON.parse(data))}catch(_e){}}
    }
    return buffer;
  }

  async function stop(req,reason='user'){
    if(!req||req.terminal)return;
    req.terminal=true;clearStall();
    if(active===req)active=null;
    try{fetch('/v1/chat/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:req.requestId}),keepalive:true})}catch(_e){}
    try{req.controller.abort()}catch(_e){}
    state('Готов');
    emit('done',{session_id:req.sessionId||sid(),request_id:req.requestId,stopped:true,reason});
  }
  window.stopCurrentLeshyStream=()=>stop(active,'user');

  async function send(input){
    if(active&&!active.terminal)return stop(active,'user');
    const text=String(input&&input.value||'').trim();if(!text)return null;
    input.value='';
    try{if(typeof window.showSection==='function')window.showSection('chat')}catch(_e){}
    if(typeof window.addMessage==='function')window.addMessage('user',text);

    const req={requestId:rid(),sessionId:sid(),controller:new AbortController(),raw:'',terminal:false,ui:makeBox(),done:null};
    active=req;state('Леший думает…');armStall(req);
    emit('activity',{session_id:req.sessionId,request_id:req.requestId,text:'Леший думает…'});

    try{
      const response=await fetch('/v1/chat/stream',{
        method:'POST',headers:{'Content-Type':'application/json'},signal:req.controller.signal,
        body:JSON.stringify({message:text,session_id:req.sessionId,owner_id:OWNER,request_id:req.requestId})
      });
      if(!response.ok)throw new Error('HTTP '+response.status);
      if(!response.body)throw new Error('Браузер не поддерживает потоковые ответы');
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';

      while(!req.terminal){
        const part=await reader.read();
        if(part.done)break;
        buffer+=decoder.decode(part.value,{stream:true});
        let terminalEvent=false;
        buffer=parse(buffer,(type,data)=>{
          if(type==='start'){
            if(data.session_id){req.sessionId=String(data.session_id);setSid(req.sessionId)}
            armStall(req);
          }else if(type==='activity'){
            const textState=String(data.text||'Леший работает…');state(textState);armStall(req);emit('activity',Object.assign({session_id:req.sessionId,request_id:req.requestId},data));
          }else if(type==='delta'){
            req.raw+=String(data.text||'');render(req);state('Леший отвечает…');armStall(req);emit('delta',{session_id:req.sessionId,request_id:req.requestId,text:data.text||''});
          }else if(type==='done'){
            req.done=data||{};req.terminal=true;terminalEvent=true;
            if(active===req)active=null;
            clearStall();
            const answer=typeof req.done.answer==='string'?req.done.answer:req.raw;req.raw=answer;
            finalRender(req,answer,req.done.sources||[]);state('Готов');emit('done',{session_id:req.sessionId,request_id:req.requestId,answer});
          }else if(type==='duplicate'){
            req.terminal=true;terminalEvent=true;if(active===req)active=null;clearStall();state('Готов');emit('done',{session_id:req.sessionId,request_id:req.requestId,duplicate:true});
          }else if(type==='error'){
            throw new Error(data.error||'Ошибка генерации');
          }
        });
        if(terminalEvent||req.terminal){try{await reader.cancel('terminal SSE received')}catch(_e){}break}
      }

      if(!req.terminal){
        req.terminal=true;if(active===req)active=null;clearStall();
        if(req.raw){finalRender(req,req.raw,[]);state('Готов');emit('done',{session_id:req.sessionId,request_id:req.requestId,answer:req.raw,eof:true})}
        else throw new Error('Поток завершился без ответа');
      }
      try{if(typeof window.loadSessions==='function')window.loadSessions()}catch(_e){}
      return {session_id:req.sessionId,answer:req.raw,request_id:req.requestId};
    }catch(error){
      if(active===req)active=null;clearStall();
      if(error&&error.name==='AbortError')return {session_id:req.sessionId,answer:req.raw,stopped:true,request_id:req.requestId};
      req.terminal=true;if(req.ui&&req.ui.box&&req.ui.box.isConnected)req.ui.box.remove();
      if(typeof window.addMessage==='function')window.addMessage('assistant','Ошибка: '+String(error&&error.message||error));
      state('Ошибка');emit('done',{session_id:req.sessionId,request_id:req.requestId,error:String(error&&error.message||error)});
      return {session_id:req.sessionId,answer:'',error:String(error&&error.message||error),request_id:req.requestId};
    }
  }

  function newChat(){
    if(active&&!active.terminal)stop(active,'new-chat');
    setSid('');const root=$('messages');if(root)root.textContent='';state('Готов');
    try{if(typeof window.showSection==='function')window.showSection('chat')}catch(_e){}
    const input=$('chatInput');if(input){input.disabled=false;input.focus()}
  }

  function install(){
    window.sendFrom=send;window.newChat=newChat;
    const chatSend=$('chatSend'),heroSend=$('heroSend'),newButton=$('newChat');
    if(chatSend)chatSend.onclick=e=>{e.preventDefault();send($('chatInput'))};
    if(heroSend)heroSend.onclick=e=>{e.preventDefault();send($('heroInput'))};
    if(newButton)newButton.onclick=e=>{e.preventDefault();newChat()};

    document.addEventListener('keydown',event=>{
      if(event.key!=='Enter'||event.shiftKey)return;
      const target=event.target;if(!target||!(target.id==='chatInput'||target.id==='heroInput'))return;
      event.preventDefault();event.stopImmediatePropagation();send(target);
    },true);
    try{window.dispatchEvent(new CustomEvent('leshy:canonical-chat-controller-ready'))}catch(_e){}
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
