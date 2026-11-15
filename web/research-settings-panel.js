/* Shared internet-search tuning: auto/manual 0.00..10.00 with decision weights. */
(function(){
  'use strict';
  const id = x => document.getElementById(x);
  const reasons = {
    explicit_request:'Явная просьба проверить интернет',
    offline_request:'Пользователь запретил интернет',
    local_dialogue:'Диалог или локальные данные',
    fresh_cache:'Свежие подтверждённые сведения уже получены',
    trusted_source:'Предоставлен конкретный первоисточник',
    strict_freshness:'Требуется актуальная внешняя проверка',
    manual_score:'Сработал ручной уровень поиска',
    auto_semantic:'Решение локального классификатора',
    auto_heuristic:'Автоматическое правило',
    auto_local:'Локальный ответ',
    model_ask:'Сначала требуется уточнение',
  };
  const styles = document.createElement('style');
  styles.textContent = [
    '#leshyResearchPanel{margin-top:12px}',
    '#leshyResearchPanel .research-row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}',
    '#leshyResearchPanel input[type="range"]{width:100%;accent-color:var(--accent,#93ddb1)}',
    '#leshyResearchPanel .research-help{font-size:12px;color:var(--muted,#91a89a);line-height:1.5;margin-top:8px}',
    '#leshyResearchPanel .research-entry{border-top:1px solid #52665955;padding:7px 0;font-size:12px;overflow-wrap:anywhere}',
    '#leshyResearchPanel .research-weights{white-space:pre-wrap;line-height:1.4;color:var(--muted,#91a89a)}'
  ].join('\n');
  document.head.appendChild(styles);

  async function api(path, opts){
    const response=await fetch(path,{
      ...opts,credentials:'same-origin',
      headers:{'Content-Type':'application/json',...(opts&&opts.headers||{})}
    });
    const data=await response.json().catch(()=>({error:'Некорректный ответ'}));
    if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
    return data;
  }
  function create(){
    if(id('leshyResearchPanel'))return true;
    const pane=id('pane-settings');
    if(!pane)return false;
    const card=document.createElement('section');
    card.id='leshyResearchPanel';
    card.className='card';
    card.innerHTML=[
      '<h3>Интернет-поиск · регулятор</h3>',
      '<div class="research-row"><label for="leshyResearchMode">Режим</label>',
      '<select class="field" id="leshyResearchMode" style="max-width:200px">',
      '<option value="auto">Авто</option><option value="manual">Вручную</option></select>',
      '<label for="leshyResearchNumber">Уровень</label>',
      '<input id="leshyResearchNumber" type="number" min="0" max="10" step="0.01" value="5.00" style="width:90px"></div>',
      '<input id="leshyResearchSlider" type="range" min="0" max="10" step="0.01" value="5.00" aria-label="Чувствительность интернет-поиска">',
      '<div class="research-row research-help" style="justify-content:space-between"><span>0,00 · минимум</span><span>10,00 · максимум</span></div>',
      '<div class="research-help">Авто выбирает поиск по контексту. Ручной уровень влияет на спорные случаи. ',
      'Прямой запрос веб-проверки, запрет интернета и обязательная актуальная проверка имеют приоритет.</div>',
      '<button class="button" type="button" id="leshyResearchSave">Сохранить</button> ',
      '<button class="button small" type="button" id="leshyResearchRefresh">Обновить диагностику</button>',
      '<div class="research-help" id="leshyResearchFeedback" aria-live="polite"></div>',
      '<h4>Последние решения</h4><div class="research-help">',
      'Веса и уровень зафиксированы на момент запроса. В журнал не записываются тексты, ссылки или персональные данные.</div>',
      '<div id="leshyResearchDecisions"></div>'
    ].join('');
    pane.appendChild(card);
    id('leshyResearchMode').addEventListener('change',view);
    id('leshyResearchSlider').addEventListener('input',()=>{
      id('leshyResearchNumber').value=Number(id('leshyResearchSlider').value).toFixed(2);view();
    });
    id('leshyResearchNumber').addEventListener('change',()=>{
      const n=Number(id('leshyResearchNumber').value);
      if(!Number.isFinite(n)||n<0||n>10)return;
      id('leshyResearchSlider').value=n.toFixed(2);view();
    });
    id('leshyResearchSave').addEventListener('click',save);
    id('leshyResearchRefresh').addEventListener('click',load);
    return true;
  }
  function view(){
    const active=id('leshyResearchMode').value==='manual';
    id('leshyResearchSlider').disabled=!active;
    id('leshyResearchNumber').disabled=!active;
  }
  function render(data){
    id('leshyResearchMode').value=data.mode==='manual'?'manual':'auto';
    const level=Number(data.level||0).toFixed(2);
    id('leshyResearchNumber').value=level;
    id('leshyResearchSlider').value=level;
    view();
    const root=id('leshyResearchDecisions');root.replaceChildren();
    const decisions=Array.isArray(data.decisions)?data.decisions.slice().reverse():[];
    if(!decisions.length){root.textContent='Пока нет решений поиска';return;}
    for(const item of decisions){
      const box=document.createElement('div');box.className='research-entry';
      const head=document.createElement('div');
      head.textContent=[
        item.timestamp||'',item.search?'Интернет':'Локально',
        item.mode==='manual'?'Вручную':'Авто',
        'уровень '+String(item.level||''),
      ].join(' · ');
      const why=document.createElement('div');
      why.textContent=reasons[item.reason_code]||'Причина решения записана';
      const weights=document.createElement('div');weights.className='research-weights';
      weights.textContent=Object.entries(item.weights||{}).map(([k,v])=>k+': '+String(v)).join(' | ');
      box.append(head,why,weights);root.appendChild(box);
    }
  }
  async function load(){
    if(!create())return;
    try{render(await api('/v1/research/settings'));id('leshyResearchFeedback').textContent='';}
    catch(e){id('leshyResearchFeedback').textContent=e.message;}
  }
  async function save(){
    const button=id('leshyResearchSave');
    const mode=id('leshyResearchMode').value;
    const level=Number(id('leshyResearchNumber').value);
    if(!Number.isFinite(level)||level<0||level>10||Math.round(level*100)!==level*100 && Math.abs(Math.round(level*100)-level*100)>1e-8){
      id('leshyResearchFeedback').textContent='Используйте значение 0,00–10,00 с шагом 0,01';return;
    }
    button.disabled=true;
    try{
      const data=await api('/v1/research/settings',{
        method:'POST',body:JSON.stringify({mode,level:level.toFixed(2)})
      });
      render(data);
      id('leshyResearchFeedback').textContent='Сохранено. Применяется к новым запросам Web и Telegram.';
    }catch(e){id('leshyResearchFeedback').textContent='Ошибка сохранения: '+e.message;}
    finally{button.disabled=false;}
  }
  if(document.readyState==='loading')
    document.addEventListener('DOMContentLoaded',load,{once:true});
  else load();
})();
