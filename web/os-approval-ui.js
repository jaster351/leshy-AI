(function(){
  'use strict';
  const OWNER='web:local';
  const KEY='leshy.session';
  let lastSession='';
  let known=new Set();

  async function api(path,options={}){
    const init=Object.assign({},options||{});init.headers=Object.assign({'Content-Type':'application/json'},init.headers||{});
    const r=await fetch(path,init);const d=await r.json().catch(()=>({error:'Некорректный ответ сервера'}));
    if(!r.ok)throw new Error(d.error||('HTTP '+r.status));return d;
  }

  function style(){if(document.getElementById('osApprovalStyle'))return;const s=document.createElement('style');s.id='osApprovalStyle';s.textContent=`
    .os-approval-card{max-width:780px;margin:8px auto 18px;padding:12px 13px;border:1px solid #4a3e31;border-radius:12px;background:#211c17;color:#ece7df}
    .os-approval-title{font-size:12px;font-weight:700;margin-bottom:5px}.os-approval-desc{font-size:12px;line-height:1.45;color:#d5cec4;word-break:break-word}
    .os-approval-actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}.os-approval-actions button{height:34px;border-radius:9px;padding:0 12px;cursor:pointer;border:1px solid #4c5158;background:#2a2f35;color:#edf0f4}
    .os-approval-actions .yes{background:#8b2f2f;border-color:#a44747}.os-approval-actions button:hover{filter:brightness(1.08)}.os-approval-actions button:disabled{opacity:.55;cursor:default}
    .os-approval-result{margin-top:8px;font-size:11px;color:#b9c2cc}
  `;document.head.appendChild(s)}

  function session(){return localStorage.getItem(KEY)||''}
  function host(){return document.getElementById('messages')}

  async function resolve(card,id,allow){
    const buttons=[...card.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
    const result=card.querySelector('.os-approval-result');result.textContent=allow?'Выполняю удаление…':'Отменяю…';
    try{
      const d=await api('/v1/os/approval',{method:'POST',body:JSON.stringify({owner_id:OWNER,id,allow})});
      result.textContent=d.message||'Готово';card.dataset.resolved='1';
      setTimeout(()=>{const sid=session();if(sid&&typeof window.loadHistory==='function')window.loadHistory(sid)},350);
    }catch(e){result.textContent='Ошибка: '+e.message;buttons.forEach(b=>b.disabled=false)}
  }

  function render(item){
    if(known.has(item.id)||document.querySelector('[data-os-approval="'+CSS.escape(item.id)+'"]'))return;
    known.add(item.id);const root=host();if(!root)return;
    const card=document.createElement('div');card.className='os-approval-card';card.dataset.osApproval=item.id;
    const title=document.createElement('div');title.className='os-approval-title';title.textContent='Требуется подтверждение владельца';
    const desc=document.createElement('div');desc.className='os-approval-desc';desc.textContent=item.description||'Удалить выбранный объект?';
    const actions=document.createElement('div');actions.className='os-approval-actions';
    const yes=document.createElement('button');yes.type='button';yes.className='yes';yes.textContent=item.yes_label||'Да, удалить';
    const no=document.createElement('button');no.type='button';no.textContent=item.no_label||'Нет, отменить';
    const result=document.createElement('div');result.className='os-approval-result';result.textContent='Без Вашего подтверждения удаление не выполняется.';
    yes.onclick=()=>resolve(card,item.id,true);no.onclick=()=>resolve(card,item.id,false);actions.append(yes,no);card.append(title,desc,actions,result);root.appendChild(card);root.scrollTop=root.scrollHeight;
  }

  async function poll(){
    const sid=session();if(!sid)return;
    if(sid!==lastSession){lastSession=sid;known=new Set();document.querySelectorAll('.os-approval-card').forEach(x=>x.remove())}
    try{const d=await api('/v1/os/approvals?owner_id='+encodeURIComponent(OWNER)+'&session_id='+encodeURIComponent(sid));for(const item of d.items||[])render(item)}catch(_e){}
  }

  function boot(){style();poll();setInterval(poll,900);window.addEventListener('storage',poll)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
