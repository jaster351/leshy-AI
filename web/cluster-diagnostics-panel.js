(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const txt=(id,value)=>{const el=$(id);if(el)el.textContent=String(value==null?'—':value);};
  const stamp=value=>{
    if(!value)return '—';
    const d=new Date(Number(value)*1000);
    return Number.isNaN(d.getTime())?'—':d.toLocaleString('ru-RU');
  };
  const switchState=(button,on)=>{
    if(!button)return;
    button.classList.toggle('on',!!on);
    button.setAttribute('aria-checked',on?'true':'false');
  };
  async function api(url,options){
    const response=await fetch(url,{headers:{'Content-Type':'application/json'},...(options||{})});
    const data=await response.json().catch(()=>({error:'Ответ API не является JSON'}));
    if(!response.ok)throw Error(data.error||('HTTP '+response.status));
    return data;
  }
  let polling=false,latest=null;
  function draw(data){
    latest=data;
    txt('leshyClusterDiagVersion',data.version);
    txt('leshyClusterDiagId',data.cluster_id);
    txt('leshyClusterDiagNode',data.node_id);
    txt('leshyClusterDiagEndpoint',data.endpoint);
    txt('leshyClusterDiagPeers',data.peer_count);
    txt('leshyClusterDiagInFlight',Object.values(data.inflight_by_node||{}).reduce((s,n)=>s+(Number(n)||0),0));
    txt('leshyClusterDiagUdp',data.udp&&data.udp.listener_running?'UDP '+data.udp.port_udp+' · прослушивается':'UDP '+(data.udp?.port_udp||38765)+' · не работает');
    txt('leshyClusterDiagTcp',data.tcp_port);
    txt('leshyClusterDiagLast',stamp(data.udp&&data.udp.last_accepted_at));
    switchState($('leshyClusterModuleToggle'),!!data.enabled);
    switchState($('leshyClusterDiscoveryToggle'),!!data.autodiscovery_enabled);
    const counts=data.udp?.counts||{};
    txt('leshyClusterDiagPackets',
      'Принято: '+(counts.accepted||0)+' · другой кластер: '+(counts.other_cluster||0)+
      ' · подпись неверна: '+(counts.invalid_signature||0)+
      ' · одинаковый node_id: '+(counts.same_node_id||0)+
      ' · повреждены: '+(counts.invalid_packet||0));
    const pairHelp=$('leshyClusterPairingHelp');
    if(pairHelp)pairHelp.style.display=(Number(counts.other_cluster||0)>0 && Number(data.peer_count||0)<2)?'block':'none';
    const hints=$('leshyClusterDiagHints');
    if(hints)hints.textContent=Array.isArray(data.hints)&&data.hints.length?data.hints.join(' '):
      'Ошибок автообнаружения не зарегистрировано. Наличие узлов не гарантирует распределение каждой задачи.';
    const peers=$('leshyClusterDiagPeerList');
    if(peers){
      peers.replaceChildren();
      for(const peer of (data.remote_peers||[])){
        if(!peer||peer.node_id===data.node_id)continue;
        const item=document.createElement('div');
        item.className='item';
        const title=document.createElement('div');title.className='item-title';
        title.textContent=peer.endpoint||'Удалённый узел';
        const meta=document.createElement('div');meta.className='item-meta';
        meta.textContent='Узел '+String(peer.node_id||'').slice(0,12)+
          ' · CPU: '+(peer.cpu_threads||0)+
          ' · GPU: '+(peer.gpu_names||[]).length+
          ' · NPU: '+(peer.npu_names||[]).length+
          ' · Задачи: '+(peer.queue_depth||0)+
          ' · Последний ответ: '+stamp(peer.last_seen);
        item.append(title,meta);peers.append(item);
      }
      if(!peers.childElementCount){
        const msg=document.createElement('div');msg.className='item-meta';
        msg.textContent='Другие доверенные компьютеры пока не обнаружены.';
        peers.append(msg);
      }
    }
  }
  async function refresh(){
    if(polling||!$('leshyClusterDiagnostics'))return;
    polling=true;
    try{
      draw(await api('/v1/cluster/diagnostics'));
      try{
        const sync=await api('/v1/cluster/knowledge-sync/status');
        const state=({waiting:'ожидание соседних ПК',synchronized:'подключено',disabled:'выключено',error:'ошибка'}[sync.state]||sync.state||'неизвестно');
        txt('leshyClusterKnowledgeSync',
          'Состояние: '+state+' · Получено записей: '+(sync.received_total||0)+
          ' · Отдано: '+(sync.sent_total||0)+
          ' · Последний обмен: '+(sync.last_sync||'ещё не было')+
          (sync.last_error?' · Ошибка: '+sync.last_error:''));
      }catch(error){
        txt('leshyClusterKnowledgeSync','Нет данных синхронизации: '+error.message);
      }
      txt('leshyClusterDiagUpdated','Обновлено '+new Date().toLocaleTimeString('ru-RU'));
    }catch(error){
      txt('leshyClusterDiagUpdated','Ошибка диагностики: '+error.message);
    }finally{polling=false;}
  }
  async function toggleDiscovery(){
    const button=$('leshyClusterDiscoveryToggle');
    if(!button||!latest)return;
    const next=!latest.autodiscovery_enabled;
    button.disabled=true;
    txt('leshyClusterDiagUpdated',(next?'Включаю':'Выключаю')+' автообнаружение…');
    try{
      draw(await api('/v1/cluster/autodiscovery',{method:'POST',body:JSON.stringify({enabled:next})}));
      await refresh();
    }catch(error){
      txt('leshyClusterDiagUpdated','Ошибка сохранения: '+error.message);
    }finally{button.disabled=false;}
  }
  function toggleCluster(){
    const button=document.querySelector('[data-module-toggle="distributed-cluster"]');
    if(!button){txt('leshyClusterDiagUpdated','Переключатель модуля не найден.');return;}
    button.click();
  }
  function install(){
    if($('leshyClusterDiagnostics'))return;
    const existing=$('leshyClusterCard');
    if(!existing)return;
    const area=document.createElement('div');
    area.id='leshyClusterDiagnostics';
    area.style.marginTop='14px';
    area.innerHTML=[
      '<h3>Диагностика кластера</h3>',
      '<div class="muted">Данные доступны только в настройках этого экземпляра. Не публикуются в общей статистике.</div>',
      '<div class="list" style="margin-top:10px">',
      '<div class="item"><div class="row">',
      '<div style="flex:1"><div class="item-title">Кластер</div><div class="item-meta">Обмен вычислительными задачами доверенных узлов</div></div>',
      '<button id="leshyClusterModuleToggle" type="button" class="switch" role="switch" aria-label="Включить или выключить кластер" aria-checked="false"></button>',
      '</div></div>',
      '<div class="item"><div class="row">',
      '<div style="flex:1"><div class="item-title">Автообнаружение LAN</div><div class="item-meta">Поиск через UDP 38765; настройка переживает перезапуск</div></div>',
      '<button id="leshyClusterDiscoveryToggle" type="button" class="switch" role="switch" aria-label="Включить или выключить автообнаружение" aria-checked="false"></button>',
      '</div></div>',
      '<div class="item"><div class="item-title">Локальная диагностика</div>',
      '<div class="item-meta">Версия: <b id="leshyClusterDiagVersion">—</b> · Компьютеров: <b id="leshyClusterDiagPeers">—</b> · Задач в работе: <b id="leshyClusterDiagInFlight">—</b></div>',
      '<div class="item-meta">cluster_id: <code id="leshyClusterDiagId">—</code></div>',
      '<div class="item-meta">node_id: <code id="leshyClusterDiagNode">—</code></div>',
      '<div class="item-meta">Локальный адрес: <code id="leshyClusterDiagEndpoint">—</code></div>',
      '<div class="item-meta"><span id="leshyClusterDiagUdp">—</span> · HTTP TCP: <b id="leshyClusterDiagTcp">—</b></div>',
      '<div class="item-meta">Последнее обнаружение: <b id="leshyClusterDiagLast">—</b></div>',
      '<div class="item-meta" id="leshyClusterDiagPackets">—</div>',
      '</div>',
      '<div class="item"><div class="item-title">Синхронизация общих знаний</div>',
      '<div class="item-meta">Данные передаются между доверенными узлами зашифрованно каждые 5 секунд. Личная переписка, пароли и токены не копируются.</div>',
      '<div class="item-meta" id="leshyClusterKnowledgeSync">Ожидание статуса…</div></div>',
      '<div class="item"><div class="item-title">Другие компьютеры</div><div class="list" id="leshyClusterDiagPeerList" style="margin-top:8px"></div></div>',
      '</div>',
      '<div class="hint" id="leshyClusterDiagHints" role="status" style="margin-top:10px"></div>',
      '<div class="item" id="leshyClusterPairingHelp" style="display:none;margin-top:9px">',
      '<div class="item-title">Найден другой кластер — требуется первичное сопряжение</div>',
      '<div class="item-meta">Автопоиск уже работает. На каждом новом ПК Леший создаёт отдельный секрет. Однократно перенесите cluster-secret.txt с доверенного компьютера на этот узел (например, через USB), затем запустите windows\\Join-LeshyCluster.ps1 -SecretFile E:\\cluster-secret.txt и перезапустите Лешего. После этого обнаружение и повторное подключение автоматические. Уникальный node-id не меняйте. Секрет не пересылайте в чат.</div>',
      '</div>',
      '<div class="row" style="margin-top:10px;gap:10px">',
      '<button type="button" id="leshyClusterDiagRefresh">Проверить сейчас</button>',
      '<span class="item-meta" id="leshyClusterDiagUpdated">Загрузка диагностики…</span>',
      '</div>'
    ].join('');
    existing.appendChild(area);
    $('leshyClusterModuleToggle').addEventListener('click',toggleCluster);
    $('leshyClusterDiscoveryToggle').addEventListener('click',toggleDiscovery);
    $('leshyClusterDiagRefresh').addEventListener('click',refresh);
    refresh();
  }
  function boot(){
    install();
    new MutationObserver(install).observe(document.documentElement,{childList:true,subtree:true});
    window.addEventListener('leshy:module-changed',event=>{
      if(event?.detail?.name==='distributed-cluster')refresh();
    });
    setInterval(()=>{
      if(document.visibilityState==='visible'&&$('leshyClusterDiagnostics')?.getClientRects().length)refresh();
    },15000);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});
  else boot();
})();
