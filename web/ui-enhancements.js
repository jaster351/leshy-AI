(function(){
  'use strict';

  const style=document.createElement('style');
  style.textContent=`
    .think-block{margin:10px 0;border:1px solid rgba(151,218,177,.14);border-radius:11px;background:rgba(255,255,255,.025);overflow:hidden}
    .think-block summary{cursor:pointer;user-select:none;padding:8px 10px;color:#91a89a;font-size:11px;list-style:none}
    .think-block summary::-webkit-details-marker{display:none}
    .think-block summary:before{content:'▸';display:inline-block;margin-right:7px;transition:.15s}
    .think-block[open] summary:before{transform:rotate(90deg)}
    .think-content{padding:0 10px 10px;color:#aabdb1;font-size:12px;line-height:1.48;white-space:pre-wrap}
    .image-job{margin-top:12px;border:1px solid rgba(151,218,177,.14);border-radius:14px;background:rgba(12,29,21,.72);padding:12px;overflow:hidden}
    .image-job-head{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:12px}
    .image-job-stage{color:#c7d8cd}
    .image-job-pct{color:#91a89a;font-variant-numeric:tabular-nums}
    .image-progress{height:7px;margin-top:9px;border-radius:99px;background:rgba(255,255,255,.06);overflow:hidden}
    .image-progress>span{display:block;height:100%;width:0;background:linear-gradient(90deg,#4f9f71,#93ddb1);transition:width .25s ease}
    .generated-image{display:block;max-width:min(100%,640px);max-height:70vh;border-radius:12px;margin-top:12px;border:1px solid rgba(151,218,177,.16);cursor:zoom-in}
    .image-error{margin-top:9px;color:#ff9e9e;font-size:12px;white-space:pre-wrap}
    .image-meta{margin-top:7px;color:#91a89a;font-size:10px}
  `;
  document.head.appendChild(style);

  function splitThink(value){
    let visible=String(value==null?'':value);
    const thoughts=[];
    visible=visible.replace(/<think>([\s\S]*?)<\/think>/gi,function(_,inner){
      const clean=String(inner||'').trim();
      if(clean) thoughts.push(clean);
      return '';
    });
    const lower=visible.toLowerCase();
    const openIndex=lower.indexOf('<think>');
    if(openIndex>=0){
      const remainder=visible.slice(openIndex+7).replace(/<\/think>/gi,'').trim();
      if(remainder) thoughts.push(remainder);
      visible=visible.slice(0,openIndex);
    }
    visible=visible.replace(/<\/think>/gi,'').trim();
    return {visible,thoughts};
  }

  function appendSources(box,sources){
    if(!sources||!sources.length)return;
    const src=document.createElement('div');
    src.className='sources';
    src.textContent='Источники:';
    for(const s of sources){
      const a=document.createElement('a');
      a.href=s.url;
      a.target='_blank';
      a.rel='noopener noreferrer';
      a.textContent=s.title||s.url;
      src.appendChild(a);
    }
    box.appendChild(src);
  }

  addMessage=function(role,text,sources=[]){
    const box=document.createElement('div');
    box.className='msg '+role;
    const who=document.createElement('div');
    who.className='who';
    who.textContent=role==='user'?'Вы':'Леший';
    box.appendChild(who);

    if(role==='assistant'){
      const parsed=splitThink(text);
      for(const thought of parsed.thoughts){
        const details=document.createElement('details');
        details.className='think-block';
        const summary=document.createElement('summary');
        summary.textContent='Рассуждение';
        const content=document.createElement('div');
        content.className='think-content';
        content.textContent=thought;
        details.append(summary,content);
        box.appendChild(details);
      }
      if(parsed.visible){
        const body=document.createElement('div');
        body.textContent=parsed.visible;
        box.appendChild(body);
      }
    }else{
      const body=document.createElement('div');
      body.textContent=String(text==null?'':text);
      box.appendChild(body);
    }

    appendSources(box,sources);
    $('messages').appendChild(box);
    $('messages').scrollTop=$('messages').scrollHeight;
    return box;
  };

  function wait(ms){return new Promise(resolve=>setTimeout(resolve,ms));}

  async function watchImageJob(box,initial){
    const card=document.createElement('div');
    card.className='image-job';
    const head=document.createElement('div');
    head.className='image-job-head';
    const stage=document.createElement('span');
    stage.className='image-job-stage';
    const pct=document.createElement('span');
    pct.className='image-job-pct';
    const progress=document.createElement('div');
    progress.className='image-progress';
    const fill=document.createElement('span');
    progress.appendChild(fill);
    const meta=document.createElement('div');
    meta.className='image-meta';
    head.append(stage,pct);
    card.append(head,progress,meta);
    box.appendChild(card);
    $('messages').scrollTop=$('messages').scrollHeight;

    let job=initial;
    while(job){
      const n=Math.max(0,Math.min(100,Number(job.progress||0)));
      stage.textContent=job.stage||job.state||'Генерация изображения';
      pct.textContent=Math.round(n)+'%';
      fill.style.width=n+'%';
      meta.textContent=(job.backend?'Backend: '+job.backend+' · ':'')+(job.width&&job.height?job.width+'×'+job.height:'');

      if(job.state==='done'){
        $('chatState').textContent='Готов';
        if(job.image_url){
          const img=document.createElement('img');
          img.className='generated-image';
          img.alt='Сгенерированное Лешим изображение';
          img.src=job.image_url+'?t='+Date.now();
          img.onclick=function(){window.open(job.image_url,'_blank','noopener');};
          card.appendChild(img);
          $('messages').scrollTop=$('messages').scrollHeight;
        }
        return;
      }
      if(job.state==='error'){
        $('chatState').textContent='Ошибка генерации';
        const err=document.createElement('div');
        err.className='image-error';
        err.textContent=job.error||'Не удалось сгенерировать изображение.';
        card.appendChild(err);
        return;
      }

      $('chatState').textContent='Генерирую изображение…';
      await wait(1100);
      try{
        job=await api('/v1/image/job?job_id='+encodeURIComponent(job.id));
      }catch(e){
        await wait(1800);
      }
    }
  }

  sendFrom=async function(input){
    const text=input.value.trim();
    if(!text)return;
    input.value='';
    showSection('chat');
    addMessage('user',text);
    $('chatState').textContent='Думаю…';
    try{
      const r=await api('/v1/chat',{method:'POST',body:JSON.stringify({message:text,session_id:sessionId,owner_id:ownerId})});
      sessionId=r.session_id;
      localStorage.setItem(sessionKey,sessionId);
      const box=addMessage('assistant',r.answer,r.sources||[]);
      if(r.image_job){
        $('chatState').textContent='Генерирую изображение…';
        watchImageJob(box,r.image_job);
      }else{
        $('chatState').textContent='Готов';
      }
      await Promise.all([refreshStatus(),loadSessions()]);
    }catch(e){
      addMessage('assistant','Ошибка: '+e.message);
      $('chatState').textContent='Ошибка';
    }
  };
})();
