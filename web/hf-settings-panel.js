(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  async function api(path,options={}){const init=Object.assign({},options||{});init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});const r=await fetch(path,init);const d=await r.json().catch(()=>({error:'Некорректный ответ сервера'}));if(!r.ok)throw new Error(d.error||('HTTP '+r.status));return d}
  function install(){
    if($('hfSettingsCard'))return;const pane=$('pane-settings');if(!pane)return;
    const card=document.createElement('div');card.id='hfSettingsCard';card.className='card';card.style.marginTop='12px';card.innerHTML=`
      <h3>Hugging Face</h3>
      <div class="muted">Необязательный токен повышает лимиты Hugging Face Hub. Xet ускоряет загрузку моделей из репозиториев с Xet Storage.</div>
      <label style="display:grid;gap:5px;margin-top:10px;font-size:11px;color:var(--muted)">HF Token
        <input class="field" id="hfToken" type="password" autocomplete="new-password" placeholder="hf_… · оставьте пустым, чтобы не менять сохранённый токен">
      </label>
      <div class="toolbar" style="margin-top:10px">
        <button class="button" id="hfSave" type="button">Сохранить токен</button>
        <button class="button secondary" id="hfClear" type="button">Удалить токен</button>
      </div>
      <div class="hint" id="hfStatus" style="margin-top:8px">Проверяю…</div>`;
    const nas=$('nasSettingsCard');if(nas&&nas.parentNode)nas.insertAdjacentElement('afterend',card);else pane.appendChild(card);
    $('hfSave').onclick=save;$('hfClear').onclick=clear;load();
  }
  function render(d){if(!$('hfStatus'))return;$('hfToken').value='';$('hfStatus').textContent='Токен: '+(d.token_saved?'сохранён защищённо':'не задан · используются анонимные лимиты')+'\nXet Storage: '+(d.xet_enabled?'ускорение доступно':'hf_xet отсутствует в текущей сборке')+'\nHF_TOKEN: '+(d.environment_ready?'активен в текущем процессе':'не задан')}
  async function load(){try{render(await api('/v1/huggingface/status'))}catch(e){if($('hfStatus'))$('hfStatus').textContent='Ошибка: '+e.message}}
  async function save(){const b=$('hfSave'),token=$('hfToken').value.trim();if(!token){$('hfStatus').textContent='Введите токен или используйте «Удалить токен».';return}b.disabled=true;try{render(await api('/v1/huggingface/config',{method:'POST',body:JSON.stringify({token})}));$('hfStatus').textContent+='\nТокен сохранён.'}catch(e){$('hfStatus').textContent='Ошибка: '+e.message}finally{b.disabled=false}}
  async function clear(){if(!confirm('Удалить сохранённый Hugging Face token? Анонимные загрузки продолжат работать.'))return;const b=$('hfClear');b.disabled=true;try{render(await api('/v1/huggingface/config',{method:'POST',body:JSON.stringify({clear:true})}));$('hfStatus').textContent+='\nТокен удалён.'}catch(e){$('hfStatus').textContent='Ошибка: '+e.message}finally{b.disabled=false}}
  function boot(){install();new MutationObserver(install).observe(document.documentElement,{childList:true,subtree:true})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
