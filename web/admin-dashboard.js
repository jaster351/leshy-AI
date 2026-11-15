(function () {
  'use strict';
  // This module only changes administrative panes; the chat and its transport remain untouched.
  const get = id => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text);
    return n;
  };
  const go = id => {
    if (typeof window.showSection === 'function') window.showSection(id);
    else document.querySelectorAll('.pane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + id));
    get('profileMenu')?.classList.remove('open');
    if (id === 'overview') void refresh();
  };
  const style = el('style');
  style.textContent = '#profileMenu .adm-label{padding:9px 12px 3px;font-size:10px;color:#91a89a;text-transform:uppercase;letter-spacing:.08em}' +
    '#profileMenu .adm-separator{border-top:1px solid rgba(151,218,177,.17);margin:6px}' +
    '#profileMenu button:focus-visible,.adm-link:focus-visible{outline:2px solid #93ddb1;outline-offset:2px}' +
    '#pane-overview .adm-root{display:grid;gap:12px;color:#edf7f1;font-family:Inter,Segoe UI,Arial,sans-serif}' +
    '#pane-overview .adm-top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}' +
    '#pane-overview .adm-title{font-size:22px;font-weight:700;margin:0}' +
    '#pane-overview .adm-muted{font-size:12px;color:#a3b7ad}' +
    '#pane-overview .adm-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}' +
    '#pane-overview .adm-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}' +
    '#pane-overview .adm-card{min-width:0;background:#111c25;border:1px solid #29403e;border-radius:12px;padding:14px}' +
    '#pane-overview .adm-card h3{font-size:15px;margin:0 0 10px}' +
    '#pane-overview .adm-big{font-size:25px;font-weight:700;font-variant-numeric:tabular-nums;padding-top:7px}' +
    '#pane-overview .adm-line{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid #29403e;font-size:12px}' +
    '#pane-overview .adm-line:last-child{border-bottom:0}' +
    '#pane-overview .adm-line b{overflow-wrap:anywhere;text-align:right}' +
    '.adm-link,.adm-subnav button{cursor:pointer;border:1px solid #36594a;border-radius:9px;background:#172b2a;color:#e8f5ee;padding:8px 12px}' +
    '.adm-subnav{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}' +
    '@media(max-width:900px){#pane-overview .adm-cards{grid-template-columns:repeat(2,minmax(0,1fr))}}' +
    '@media(max-width:620px){#pane-overview .adm-cards,#pane-overview .adm-grid{grid-template-columns:1fr}}';
  document.head.appendChild(style);
  const metric = (title, key) => {
    const card = el('div', 'adm-card');
    card.append(el('div', 'adm-muted', title));
    const value = el('div', 'adm-big', 'Нет данных');
    value.dataset.adm = key;
    card.append(value); return card;
  };
  const details = (title, items) => {
    const card = el('div','adm-card'); card.append(el('h3','',title));
    items.forEach(([label,key]) => {
      const line=el('div','adm-line');
      line.append(el('span','',label));
      const value=el('b','', 'Нет данных'); value.dataset.adm=key;
      line.append(value); card.append(line);
    });
    return card;
  };
  const set = (key, value) => {
    const e = document.querySelector('[data-adm="' + key + '"]');
    if (e) e.textContent = value == null ? 'Нет данных' : String(value);
  };
  const fmt = value => value === null || value === undefined || !Number.isFinite(Number(value)) ? 'Нет данных' : Number(value).toLocaleString('ru-RU');
  const giga = value => Number(value)>0 ? (Number(value)/1073741824).toLocaleString('ru-RU',{maximumFractionDigits:1})+' ГБ' : 'Нет данных';
  function setupOverview(){
    const pane=get('pane-overview');
    if(!pane || get('leshyAdminOverview'))return;
    Array.from(pane.children).forEach(child=>{child.style.display='none'});
    const root=el('div','adm-root');root.id='leshyAdminOverview';
    const header=el('div','adm-top');
    const title=el('div');
    title.append(el('h2','adm-title','Леший — Центр управления'));
    title.append(el('div','adm-muted','Сведения из локальных API. Отсутствующие измерения не подменяются цифрами.'));
    header.append(title);
    const buttons=el('div');
    [['Обновить','overview'],['Задачи','tasks'],['SuperAgent','superagent']].forEach(([label,route])=>{
      const b=el('button','adm-link',label);b.type='button';b.onclick=()=>go(route);buttons.append(b);
    });
    header.append(buttons);root.append(header);
    const summary=el('div','adm-cards');
    [['Диалоги','sessions'],['Сообщения','messages'],['Проверенные знания','trusted'],['Открытые задачи','tasks'],['Узлы LAN','lan'],['Рой: узлы (заявлено)','swarm']].forEach(x=>summary.append(metric(...x)));
    root.append(summary);
    const grid=el('div','adm-grid');
    grid.append(details('Текущий компьютер',[['CPU','cpu'],['GPU','gpu'],['RAM всего','ram'],['RAM свободно','free'],['VRAM','vram'],['Токен/с','tps']]));
    grid.append(details('Локальный кластер',[['Состояние','clusterState'],['Обнаружено узлов','lanDetail'],['Выполняется задач','inflight'],['RAM всех узлов','lanRam'],['VRAM всех узлов','lanVram'],['Сетевая скорость','lanNetwork']]));
    grid.append(details('Глобальный рой',[['Состояние','swarmState'],['Заявлено узлов','swarmDetail'],['CPU всех узлов','swarmCpu'],['RAM всех узлов','swarmRam'],['VRAM всех узлов','swarmVram'],['Скорость генерации','swarmTps']]));
    grid.append(details('Производительность',[['Текущая, токен/с','currentTps'],['Максимальная, токен/с','maxTps'],['Средняя, токен/с','avgTps'],['Минимальная, токен/с','minTps'],['Метод измерения','tpsSource']]));
    grid.append(details('География запусков',[['Местоположения','locations'],['Точки на карте','points']]));
    grid.append(details('Обновление',[['Проверено','updated'],['Источники','sources']]));
    root.append(grid);pane.append(root);
  }
  const fetchJSON = async path => {
    const r = await fetch(path,{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    return r.json();
  };
  let busy=false;
  async function refresh(){
    if(busy || !get('pane-overview')?.classList.contains('active'))return;
    busy=true;
    const owner=localStorage.getItem('leshy.owner_id') || 'web:local';
    try{
      const paths=['/v1/overview?owner_id='+encodeURIComponent(owner),'/v1/hardware','/v1/cluster/diagnostics','/v1/swarm/status'];
      const settled=await Promise.allSettled(paths.map(fetchJSON));
      const result=settled.map(s=>s.status==='fulfilled'?s.value:null);
      const [overview,hardware,cluster,swarm]=result;
      if(overview){
        const stats=overview.status?.stats || {};
        set('sessions',fmt(stats.sessions));set('messages',fmt(stats.messages));set('trusted',fmt(stats.trusted));
        const tasks=overview.tasks || {};
        set('tasks',fmt((Number(tasks.open)||0)+(Number(tasks.in_progress)||0)));
      }
      if(hardware){
        const caps=hardware.capabilities||{};
        const mem=hardware.memory?.system||{};
        set('cpu',caps.cpu_name||'Нет данных');
        set('gpu',Array.isArray(caps.gpu_names)&&caps.gpu_names.length?caps.gpu_names.join(', '):'Нет данных');
        set('ram',giga(mem.ram_total_bytes));set('free',giga(mem.ram_free_bytes));
      }
      if(cluster){
        set('lan',fmt(cluster.peer_count));set('lanDetail',fmt(cluster.peer_count));
        set('clusterState',cluster.enabled?'Включён':'Выключен');
        const inflight=Object.values(cluster.inflight_by_node||{}).reduce((s,n)=>s+(Number(n)||0),0);
        set('inflight',fmt(inflight));
      }
      if(swarm){
        set('swarm',fmt(swarm.peer_count_reported));set('swarmDetail',fmt(swarm.peer_count_reported));
        set('swarmState',swarm.state||'Нет данных');
      }
      set('locations','Нет подтверждённых координат');
      set('points','Не отображаются без согласия владельцев');
      set('updated',new Date().toLocaleTimeString('ru-RU'));
      set('sources',settled.every(s=>s.status==='fulfilled')?'Все API ответили':'Часть API недоступна');
    }catch(e){set('sources','Ошибка: '+String(e?.message||e));}
    finally{busy=false;}
  }
  function setupAgentPane(){
    if(get('pane-superagent'))return;
    const pane=el('section','pane');pane.id='pane-superagent';
    pane.append(el('h2','section-title','SuperAgent'));
    pane.append(el('div','section-subtitle','Автономная работа, вопросы, планы и управление заданиями'));
    document.querySelector('.main')?.append(pane);
  }
  function subnav(section,links){
    const pane=get('pane-'+section);
    if(!pane || pane.querySelector('.adm-subnav'))return;
    const bar=el('div','adm-subnav');
    links.forEach(([label,route,target])=>{
      const b=el('button','',label);
      b.type='button';
      b.onclick=()=>{go(route);if(target)setTimeout(()=>get(target)?.scrollIntoView({behavior:'smooth',block:'start'}),100)};
      bar.append(b);
    });
    pane.querySelector('.section-subtitle')?.after(bar);
  }
  function setupMenu(){
    const menu=get('profileMenu');if(!menu)return false;
    if(menu.dataset.adminReady)return true;
    menu.dataset.adminReady='1';menu.replaceChildren();
    const group=(title,items)=>{
      menu.append(el('div','adm-label',title));
      items.forEach(([label,route,target])=>{
        const b=el('button','',label);b.type='button';b.dataset.adminRoute=route;
        b.onclick=()=>{go(route);if(target)setTimeout(()=>get(target)?.scrollIntoView({behavior:'smooth',block:'start'}),100)};
        menu.append(b);
      });
      menu.append(el('div','adm-separator'));
    };
    group('Работа',[['Обзор','overview'],['Задачи','tasks'],['SuperAgent','superagent'],['Знания и обучение','knowledge']]);
    group('Управление',[['Инструменты','tools'],['Система','system'],['Настройки','settings']]);
    group('Быстрый доступ',[['Профиль','settings','ownerIdentityCard'],['Обучение','learning'],['Логи','logs'],['Магазин расширений','settings','marketplaceCard']]);
    return true;
  }
  function install(){
    setupAgentPane();setupOverview();
    subnav('knowledge',[['Знания','knowledge'],['Обучение','learning']]);
    subnav('system',[['Состояние','system'],['Логи','logs'],['Кластер','settings','leshyClusterDiagnostics']]);
    subnav('tools',[['Инструменты','tools'],['Магазин','settings','marketplaceCard']]);
    if(!setupMenu()){
      let n=0;const timer=setInterval(()=>{n++;if(setupMenu()||n>=20)clearInterval(timer)},250);
    }
    void refresh();
    setInterval(()=>{if(document.visibilityState==='visible')void refresh()},10000);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});
  else install();
})();