(function(){
  'use strict';
  if(window.__leshyActivityFetchBridge)return;
  window.__leshyActivityFetchBridge=true;
  const originalFetch=window.fetch.bind(window);

  function dispatch(type,data){
    try{window.dispatchEvent(new CustomEvent('leshy:'+type,{detail:data||{}}))}catch(_e){}
    if(type==='activity'&&data&&data.text){const s=document.getElementById('chatState');if(s)s.textContent=String(data.text)}
  }

  async function watch(response){
    try{
      if(!response.body)return;
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
      while(true){
        const part=await reader.read();if(part.done)break;
        buffer+=decoder.decode(part.value,{stream:true});
        let boundary;
        while((boundary=buffer.indexOf('\n\n'))>=0){
          const block=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
          let type='message',raw='';
          for(const line of block.split('\n')){
            if(line.startsWith('event:'))type=line.slice(6).trim();
            else if(line.startsWith('data:'))raw+=(raw?'\n':'')+line.slice(5).trim();
          }
          if(!raw)continue;
          let data={};try{data=JSON.parse(raw)}catch(_e){continue}
          if(type==='activity')dispatch('activity',data);
          else if(type==='delta')dispatch('delta',data);
          else if(type==='done')dispatch('done',data);
          else if(type==='error')dispatch('done',{error:data.error||'Ошибка'});
        }
      }
    }catch(_e){}
  }

  window.fetch=async function(input,init){
    const response=await originalFetch(input,init);
    try{
      const url=typeof input==='string'?input:(input&&input.url)||'';
      if(String(url).includes('/v1/chat/stream'))watch(response.clone());
    }catch(_e){}
    return response;
  };
})();
