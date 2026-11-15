(function(){
  'use strict';

  const previousFetch=window.fetch.bind(window);
  const TERMINAL_EVENTS=new Set(['done','error','duplicate']);

  function streamUrl(input){
    try{return typeof input==='string'?input:String((input&&input.url)||'')}
    catch(_e){return ''}
  }

  function isChatStream(input){
    const url=streamUrl(input).split('?',1)[0];
    return url==='/v1/chat/stream'||url.endsWith('/v1/chat/stream');
  }

  function eventType(block){
    for(const line of String(block||'').split('\n')){
      if(line.startsWith('event:'))return line.slice(6).trim().toLowerCase();
    }
    return '';
  }

  function wrapTerminalResponse(response){
    if(!response||!response.body||typeof ReadableStream==='undefined')return response;

    const reader=response.body.getReader();
    const decoder=new TextDecoder();
    const encoder=new TextEncoder();
    let buffer='';
    let terminal=false;

    const body=new ReadableStream({
      async pull(controller){
        if(terminal){controller.close();return}
        try{
          const part=await reader.read();
          if(part.done){
            buffer+=decoder.decode();
            if(buffer)controller.enqueue(encoder.encode(buffer));
            controller.close();
            return;
          }

          buffer+=decoder.decode(part.value,{stream:true});
          let boundary=-1;
          while((boundary=buffer.indexOf('\n\n'))>=0){
            const block=buffer.slice(0,boundary+2);
            buffer=buffer.slice(boundary+2);
            controller.enqueue(encoder.encode(block));

            const type=eventType(block);
            if(TERMINAL_EVENTS.has(type)){
              terminal=true;
              try{
                window.dispatchEvent(new CustomEvent('leshy:stream-transport-terminal',{detail:{type}}));
              }catch(_e){}
              // Логическое SSE-событие done/error уже доставлено клиенту. Дальше
              // нельзя ждать физический EOF keep-alive соединения: это оставляло
              // предыдущий чат "активным" и следующий вопрос вместо отправки
              // останавливал старый stream. Закрываем только клиентское чтение.
              try{await reader.cancel('leshy logical stream terminal')}catch(_e){}
              controller.close();
              return;
            }
          }
        }catch(error){
          controller.error(error);
        }
      },
      cancel(reason){
        try{return reader.cancel(reason)}catch(_e){return undefined}
      }
    });

    return new Response(body,{
      status:response.status,
      statusText:response.statusText,
      headers:new Headers(response.headers)
    });
  }

  window.fetch=async function(input,init){
    const response=await previousFetch(input,init);
    if(!isChatStream(input))return response;
    return wrapTerminalResponse(response);
  };
})();
