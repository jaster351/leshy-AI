(function(){
  'use strict';
  const KEY='leshy.voice.profile.v1';
  const $=id=>document.getElementById(id);
  const read=()=>{try{return JSON.parse(localStorage.getItem(KEY)||'{}')}catch(_e){return {}}};
  const write=p=>{try{localStorage.setItem(KEY,JSON.stringify(p))}catch(_e){}};
  const voices=()=>('speechSynthesis' in window?window.speechSynthesis.getVoices():[]);
  function profile(){const p=read();return {gender:p.gender==='female'?'female':'male',voice:p.voice||'',rate:Number(p.rate)||1}}
  function suitable(list,gender){
    const hints=gender==='female'?['female','жен','alena','irina','svetlana','katya','anna','zira','aria','jenny']:['male','муж','pavel','dmitry','maxim','alexander','guy','david','mark'];
    const preferred=list.filter(v=>hints.some(h=>String(v.name).toLowerCase().includes(h)));
    return preferred.length?preferred:list;
  }
  function populate(){
    const select=$('leshyVoiceSelect');if(!select)return;
    const p=profile(),list=suitable(voices(),p.gender),old=p.voice;
    select.textContent='';
    if(!list.length){const o=document.createElement('option');o.textContent='Системные голоса не найдены';o.value='';select.appendChild(o);return}
    list.forEach(v=>{const o=document.createElement('option');o.value=v.name;o.textContent=v.name+(v.lang?' · '+v.lang:'');select.appendChild(o)});
    if(list.some(v=>v.name===old))select.value=old;else{select.value=list[0].name;save()}
  }
  function save(){const select=$('leshyVoiceSelect'),male=$('leshyVoiceMale');const p={gender:male&&male.classList.contains('on')?'male':'female',voice:select?select.value:'',rate:1};write(p);window.dispatchEvent(new CustomEvent('leshy:voice-profile-changed',{detail:p}))}
  function setGender(g){const m=$('leshyVoiceMale'),f=$('leshyVoiceFemale');if(!m||!f)return;m.classList.toggle('on',g==='male');f.classList.toggle('on',g==='female');m.setAttribute('aria-pressed',String(g==='male'));f.setAttribute('aria-pressed',String(g==='female'));write(Object.assign(profile(),{gender:g,voice:''}));populate();save()}
  function preview(){
    if(!('speechSynthesis' in window)){const s=$('leshyVoiceStatus');if(s)s.textContent='В этом WebView нет Speech Synthesis';return}
    const p=profile(),list=voices(),voice=list.find(v=>v.name===p.voice);window.speechSynthesis.cancel();
    const u=new SpeechSynthesisUtterance(p.gender==='male'?'Здравствуйте. Я Леший. Так будет звучать мой голос.':'Здравствуйте. Я Леший. Так будет звучать мой голос.');u.lang=voice&&voice.lang||'ru-RU';if(voice)u.voice=voice;u.rate=p.rate;
    const s=$('leshyVoiceStatus');if(s)s.textContent='Прослушивание…';u.onend=()=>{if(s)s.textContent='Готово'};u.onerror=()=>{if(s)s.textContent='Не удалось воспроизвести голос'};window.speechSynthesis.speak(u);
  }
  function install(){
    const pane=$('pane-settings');if(!pane||$('leshyVoiceSettings'))return;
    const card=document.createElement('div');card.className='card';card.id='leshyVoiceSettings';card.style.marginTop='12px';
    card.innerHTML='<h3>Голос Лешего</h3><div class="muted" style="margin-bottom:10px">Выберите мужской или женский голос и тембр. Выбор сохраняется на этом устройстве.</div><div class="toolbar"><button class="button secondary" id="leshyVoiceMale" type="button">Мужской</button><button class="button secondary" id="leshyVoiceFemale" type="button">Женский</button></div><label class="muted" for="leshyVoiceSelect">Голос / тембр</label><select class="field" id="leshyVoiceSelect" style="margin-top:6px"></select><div class="toolbar" style="margin-top:10px"><button class="button" id="leshyVoicePreview" type="button">Прослушать</button><span class="muted" id="leshyVoiceStatus"></span></div>';
    pane.appendChild(card);const p=profile();setGender(p.gender);$('leshyVoiceSelect').addEventListener('change',save);$('leshyVoiceMale').addEventListener('click',()=>setGender('male'));$('leshyVoiceFemale').addEventListener('click',()=>setGender('female'));$('leshyVoicePreview').addEventListener('click',preview);
    if('speechSynthesis' in window)window.speechSynthesis.onvoiceschanged=populate;
  }
  window.LeshyVoiceProfile={get:profile,preview};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
