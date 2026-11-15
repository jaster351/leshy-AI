(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  let mounted=false;

  async function api(path,options={}){
    const init=Object.assign({},options);
    init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const response=await fetch(path,init);
    const data=await response.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function esc(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

  function addStyle(){
    if($('githubPluginStyle'))return;
    const style=document.createElement('style');style.id='githubPluginStyle';style.textContent=`
      .github-plugin-config{margin-top:12px;border:1px solid #3c3c3c;background:#202020;border-radius:12px;padding:13px;display:grid;gap:10px}
      .github-plugin-head{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}.github-plugin-title{font-weight:650;font-size:13px}.github-plugin-status{font-size:10px;color:#aaa}.github-plugin-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}.github-plugin-config textarea{min-height:72px;resize:vertical}.github-plugin-note{font-size:10px;color:#999;line-height:1.5}.github-plugin-actions{display:flex;gap:7px;flex-wrap:wrap}.github-plugin-ok{color:#9fd7b6}.github-plugin-error{color:#efaaaa;white-space:pre-wrap}@media(max-width:760px){.github-plugin-grid{grid-template-columns:1fr}}
    `;document.head.appendChild(style);
  }

  function host(){return $('marketplaceCard')||$('pane-settings')}

  async function refresh(){
    const status=$('githubPluginStatus');
    try{
      const data=await api('/v1/marketplace/github/status');
      const repos=$('githubPluginRepos'),def=$('githubPluginDefault'),write=$('githubPluginWrite');
      if(repos)repos.value=(data.repositories||[]).join('\n');
      if(def)def.value=data.default_repository||'';
      if(write)write.checked=!!data.write_enabled;
      if(status){status.className='github-plugin-status github-plugin-ok';status.textContent='Подключён · '+(data.token_present?'token сохранён локально':'публичный доступ без token')+' · режим чата: только чтение';}
    }catch(error){if(status){status.className='github-plugin-status github-plugin-error';status.textContent='GitHub: '+error.message}}
  }

  async function save(){
    const button=$('githubPluginSave');if(button)button.disabled=true;
    const status=$('githubPluginStatus');
    try{
      const repos=String($('githubPluginRepos')?.value||'').split(/[\n,]+/).map(x=>x.trim()).filter(Boolean);
      const body={repositories:repos,default_repository:String($('githubPluginDefault')?.value||'').trim(),token:String($('githubPluginToken')?.value||'').trim(),write_enabled:!!$('githubPluginWrite')?.checked};
      await api('/v1/marketplace/github/config',{method:'POST',body:JSON.stringify(body)});
      if($('githubPluginToken'))$('githubPluginToken').value='';
      if(status){status.className='github-plugin-status github-plugin-ok';status.textContent='Настройки GitHub сохранены.'}
      await refresh();
    }catch(error){if(status){status.className='github-plugin-status github-plugin-error';status.textContent='Не удалось сохранить: '+error.message}}
    finally{if(button)button.disabled=false}
  }

  async function clearToken(){
    const status=$('githubPluginStatus');
    try{
      const repos=String($('githubPluginRepos')?.value||'').split(/[\n,]+/).map(x=>x.trim()).filter(Boolean);
      await api('/v1/marketplace/github/config',{method:'POST',body:JSON.stringify({repositories:repos,default_repository:String($('githubPluginDefault')?.value||'').trim(),clear_token:true,write_enabled:!!$('githubPluginWrite')?.checked})});
      if(status){status.className='github-plugin-status github-plugin-ok';status.textContent='GitHub token удалён из LeshyBrain.'}
      await refresh();
    }catch(error){if(status){status.className='github-plugin-status github-plugin-error';status.textContent='Не удалось удалить token: '+error.message}}
  }

  function install(){
    if(mounted||$('githubPluginConfig'))return !!$('githubPluginConfig');
    const parent=host();if(!parent)return false;addStyle();
    const box=document.createElement('div');box.id='githubPluginConfig';box.className='github-plugin-config';box.innerHTML=`
      <div class="github-plugin-head"><div><div class="github-plugin-title">GitHub · официальный плагин</div><div class="github-plugin-note">Леший может читать свой код, commits, Issues и Pull Requests только в разрешённых репозиториях.</div></div><div class="github-plugin-status" id="githubPluginStatus">Проверяю…</div></div>
      <div class="github-plugin-grid"><label><div class="github-plugin-note">Разрешённые репозитории · owner/repo, по одному в строке</div><textarea class="field" id="githubPluginRepos" spellcheck="false"></textarea></label><label><div class="github-plugin-note">Репозиторий по умолчанию</div><input class="field" id="githubPluginDefault" value="jaster351/leshy-AI" spellcheck="false"><div class="github-plugin-note" style="margin-top:8px">GitHub token · нужен для private repo и повышенного API-limit</div><input class="field" id="githubPluginToken" type="password" autocomplete="new-password" placeholder="github_pat_… или ghp_…"></label></div>
      <label class="github-plugin-note"><input id="githubPluginWrite" type="checkbox"> Разрешение на запись подготовить в настройках. <b>Чатовый GitHub-инструмент всё равно остаётся read-only</b>, пока запись не будет реализована отдельными подтверждаемыми действиями.</label>
      <div class="github-plugin-note">Token сохраняется только в <code>LeshyBrain/secrets</code>. На Windows используется DPAPI. В репозиторий и в ответы Лешего token не передаётся.</div>
      <div class="github-plugin-actions"><button class="button" id="githubPluginSave" type="button">Сохранить GitHub</button><button class="button secondary" id="githubPluginRefresh" type="button">Проверить</button><button class="button secondary" id="githubPluginClearToken" type="button">Удалить token</button></div>`;
    parent.appendChild(box);mounted=true;
    $('githubPluginSave').onclick=save;$('githubPluginRefresh').onclick=refresh;$('githubPluginClearToken').onclick=clearToken;refresh();return true;
  }

  function boot(){let tries=0;const timer=setInterval(()=>{tries++;if(install()||tries>30)clearInterval(timer)},250);install();document.addEventListener('click',event=>{const b=event.target?.closest?.('button');if(b&&/Marketplace|Магазин/i.test(b.textContent||''))setTimeout(()=>{install();refresh()},160)})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
