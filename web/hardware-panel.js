(function(){
  'use strict';

  async function request(path, options){
    if(typeof api==='function') return api(path, options);
    const response=await fetch(path,Object.assign({headers:{'Content-Type':'application/json'}},options||{}));
    const body=await response.json();
    if(!response.ok) throw new Error(body.error||('HTTP '+response.status));
    return body;
  }

  const style=document.createElement('style');
  style.textContent=`
    .compute-card{margin-top:12px}
    .compute-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:10px 0}
    .compute-chip{border:1px solid var(--line);border-radius:11px;padding:10px;background:rgba(255,255,255,.025)}
    .compute-chip b{display:block;font-size:13px;margin-bottom:3px}.compute-chip span{font-size:10px;color:var(--muted);word-break:break-word}
    .compute-ready b{color:var(--accent)}.compute-off b{color:var(--muted)}
    .compute-note{margin-top:8px;padding:9px;border-radius:10px;background:rgba(147,221,177,.06);font-size:11px;color:#b7cbbf;line-height:1.45}
    @media(max-width:720px){.compute-grid{grid-template-columns:1fr}}
  `;
  document.head.appendChild(style);

  function el(tag, attrs, text){
    const node=document.createElement(tag);
    if(attrs) Object.entries(attrs).forEach(([k,v])=>{if(k==='class')node.className=v;else node.setAttribute(k,v);});
    if(text!=null) node.textContent=text;
    return node;
  }

  function names(values, fallback){return Array.isArray(values)&&values.length?values.join(', '):fallback;}

  async function refresh(card){
    const status=await request('/v1/hardware');
    const caps=status.capabilities||{};
    const plan=status.plan||{};
    const cfg=status.config||{};
    const select=card.querySelector('[data-compute-mode]');
    if(select) select.value=cfg.mode||'auto';
    const chips=card.querySelector('[data-compute-chips]');
    chips.innerHTML='';
    [
      ['CPU',true,caps.cpu_name||'CPU'],
      ['GPU',!!caps.gpu,names(caps.gpu_names,'Не обнаружен')],
      ['NPU',!!caps.npu,names(caps.npu_names,'Не обнаружен')]
    ].forEach(([title,ready,detail])=>{
      const chip=el('div',{class:'compute-chip '+(ready?'compute-ready':'compute-off')});
      chip.append(el('b',null,title+(ready?' · доступен':' · нет')),el('span',null,detail));
      chips.appendChild(chip);
    });
    const summary=card.querySelector('[data-compute-summary]');
    summary.textContent='Активный план: '+(plan.effective_mode||'—')+' · основной backend: '+(plan.chat_backend||'—')+' · фон: '+((plan.background_devices||[]).join(' + ')||'—');
    const warnings=card.querySelector('[data-compute-warnings]');
    const list=[...(caps.notes||[]),...(plan.warnings||[])];
    warnings.textContent=list.length?list.join(' · '):'Леший автоматически выбирает доступные ускорители и сохраняет CPU как резерв для системных задач и fallback.';
  }

  function install(){
    const pane=document.getElementById('pane-settings');
    if(!pane||document.getElementById('computeResourceCard')) return;
    const card=el('div',{class:'card compute-card',id:'computeResourceCard'});
    card.innerHTML=`
      <h3>Производительность</h3>
      <div class="muted">Управление CPU, GPU и NPU. Режим «Все ресурсы» включает максимально доступную комбинацию ускорителей.</div>
      <div class="compute-grid" data-compute-chips></div>
      <div class="row"><span>Режим вычислений</span><select class="field" style="width:auto;min-width:230px" data-compute-mode>
        <option value="auto">Автоматически</option>
        <option value="cpu_gpu_npu">Все ресурсы: CPU + GPU + NPU</option>
        <option value="cpu_npu">CPU + NPU</option>
        <option value="cpu_gpu">CPU + GPU</option>
        <option value="gpu_npu">GPU + NPU</option>
        <option value="gpu">Только GPU</option>
        <option value="npu">Только NPU</option>
        <option value="cpu">Только CPU</option>
      </select></div>
      <div class="compute-note" data-compute-summary>Загрузка…</div>
      <div class="hint" data-compute-warnings style="margin-top:8px"></div>
      <button class="button" style="width:100%;margin-top:10px" data-compute-save>Применить режим</button>
    `;
    pane.appendChild(card);
    card.querySelector('[data-compute-save]').addEventListener('click',async function(){
      const button=this;button.disabled=true;button.textContent='Применяю…';
      try{
        await request('/v1/hardware/config',{method:'POST',body:JSON.stringify({mode:card.querySelector('[data-compute-mode]').value,prefer_npu_for_background:true})});
        await refresh(card);
        button.textContent='Сохранено · применяется после перезапуска runtime';
      }catch(e){button.textContent='Ошибка: '+e.message;}
      finally{setTimeout(()=>{button.disabled=false;button.textContent='Применить режим';},2400);}
    });
    refresh(card).catch(e=>{card.querySelector('[data-compute-summary]').textContent='Не удалось получить аппаратный статус: '+e.message;});
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',install); else install();
})();
