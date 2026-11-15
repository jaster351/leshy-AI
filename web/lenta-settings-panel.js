(function(){
  'use strict';

  const OWNER_KEY='leshy.owner_id';
  const ownerId=()=>String(localStorage.getItem(OWNER_KEY)||'web:local').trim().slice(0,200)||'web:local';
  const esc=value=>String(value==null?'':value).replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));

  async function api(path,options={}){
    const init=Object.assign({},options||{});
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function selectHtml(id,label,value,items){
    return `<label style="display:grid;gap:5px;font-size:12px;color:#c7c7c7">${esc(label)}
      <select id="${id}" style="background:#242424;color:#ececec;border:1px solid #454545;border-radius:8px;padding:8px 9px">
        ${items.map(item=>`<option value="${esc(item[0])}" ${item[0]===value?'selected':''}>${esc(item[1])}</option>`).join('')}
      </select>
    </label>`;
  }

  function render(data){
    const host=document.getElementById('leshyLentaSettings');
    if(!host)return;
    const prefs=data.preferences||{};
    const account=data.authenticated?'Вход выполнен':'Вход не подтверждён';
    const accountClass=data.authenticated?'good':'warn';
    const store=data.store_id?`Магазин: ${esc(data.store_id)}`:'Магазин будет взят из личного кабинета';
    host.innerHTML=`
      <div style="height:1px;background:#363636;margin:10px 0"></div>
      <div class="item-title" style="font-size:13px">Настройки «Ленты»</div>
      <div class="item-meta ${accountClass}" style="margin-top:5px">${account}</div>
      <div class="item-meta">${store}</div>
      <div style="display:flex;gap:7px;flex-wrap:wrap;margin:9px 0 10px">
        <button type="button" class="btn" id="lentaOpenAccount">Открыть аккаунт</button>
        <button type="button" class="btn" id="lentaMarkAuthenticated">Я вошёл</button>
      </div>
      <div style="display:grid;gap:8px">
        ${selectHtml('lentaDelivery','Получение',prefs.delivery_mode||'account',[
          ['account','Как выбрано в личном кабинете'],['delivery','Доставка'],['pickup','Самовывоз']
        ])}
        ${selectHtml('lentaPayment','Оплата по умолчанию',prefs.payment_mode||'account',[
          ['account','Как выбрано в личном кабинете'],['self','Я завершу оплату сам'],['card_on_delivery','Картой при получении'],['cash','Наличными при получении'],['card_online','Картой онлайн']
        ])}
        ${selectHtml('lentaSubstitution','Если товара нет',prefs.substitution_policy||'automatic',[
          ['automatic','Подобрать близкий аналог и сообщить'],['ask','Сначала спросить меня'],['none','Не заменять']
        ])}
      </div>
      <div class="item-meta good" style="margin-top:9px">Подтверждение перед финальным заказом: всегда включено</div>
      <div class="item-meta" style="margin-top:4px">Пароль, SMS/3DS, CVV и реквизиты карты Леший здесь не хранит.</div>
      <div class="hint" id="lentaSettingsStatus" style="margin-top:7px"></div>`;

    document.getElementById('lentaOpenAccount')?.addEventListener('click',openAccount);
    document.getElementById('lentaMarkAuthenticated')?.addEventListener('click',markAuthenticated);
    ['lentaDelivery','lentaPayment','lentaSubstitution'].forEach(id=>{
      document.getElementById(id)?.addEventListener('change',save);
    });
  }

  function status(text){
    const node=document.getElementById('lentaSettingsStatus');
    if(node)node.textContent=text||'';
  }

  async function load(){
    status('Загружаю настройки…');
    try{
      const data=await api('/v1/lenta/settings?owner_id='+encodeURIComponent(ownerId()));
      render(data);
      status('Настройки относятся только к Вашему профилю «Ленты».');
    }catch(error){status('Ошибка: '+error.message);}
  }

  async function save(){
    status('Сохраняю…');
    try{
      const body={
        owner_id:ownerId(),
        delivery_mode:document.getElementById('lentaDelivery')?.value||'account',
        payment_mode:document.getElementById('lentaPayment')?.value||'account',
        substitution_policy:document.getElementById('lentaSubstitution')?.value||'automatic'
      };
      const data=await api('/v1/lenta/settings',{method:'POST',body:JSON.stringify(body)});
      render(data);
      status('Сохранено.');
    }catch(error){status('Ошибка сохранения: '+error.message);}
  }

  async function openAccount(){
    status('Открываю персональный профиль браузера…');
    try{
      const data=await api('/v1/lenta/open-account',{method:'POST',body:JSON.stringify({owner_id:ownerId()})});
      render(data);
      status('Окно открыто. Войдите на сайте; пароль и SMS вводите только там.');
    }catch(error){status('Ошибка: '+error.message);}
  }

  async function markAuthenticated(){
    status('Сохраняю состояние входа…');
    try{
      const data=await api('/v1/lenta/mark-authenticated',{method:'POST',body:JSON.stringify({owner_id:ownerId()})});
      render(data);
      status('Профиль отмечен как авторизованный.');
    }catch(error){status('Ошибка: '+error.message);}
  }

  function install(){
    const row=document.querySelector('[data-module="lenta"]');
    if(!row||row.querySelector('#leshyLentaSettings'))return;
    const host=document.createElement('div');
    host.id='leshyLentaSettings';
    host.innerHTML='<div class="item-meta">Загрузка настроек «Ленты»…</div>';
    row.appendChild(host);
    load();
  }

  function boot(){
    install();
    new MutationObserver(install).observe(document.documentElement,{childList:true,subtree:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
