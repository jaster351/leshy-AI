(function(){
  'use strict';

  const $=id=>document.getElementById(id);

  function installHelp(){
    const pane=$('pane-tasks');
    if(!pane || $('tasksHelpCard')) return;
    const subtitle=pane.querySelector('.section-subtitle');
    const card=document.createElement('div');
    card.id='tasksHelpCard';
    card.className='card';
    card.style.marginBottom='12px';
    card.innerHTML=`
      <h3>Как пользоваться задачами</h3>
      <div class="item-body" style="margin-top:0">
        Это локальный план/трекер дел Лешего и пользователя. Здесь можно сохранить задачу, описание, приоритет и срок, а затем менять её состояние.
      </div>
      <div class="divider"></div>
      <div class="row"><span>Открыта</span><b>ещё не начата</b></div>
      <div class="row"><span>В работе</span><b>выполняется</b></div>
      <div class="row"><span>Готово</span><b>завершена</b></div>
      <div class="row"><span>Отменено</span><b>больше не требуется</b></div>
      <div class="hint" style="margin-top:9px">
        Сейчас этот раздел хранит и отслеживает задачи, но сам по себе ещё не является автономным исполнителем. Команду Лешему на реальное выполнение пока давайте в чате; дальнейшая цель — связать задачи с автономным планировщиком и инструментами.
      </div>
      <div class="hint" style="margin-top:7px">
        Пример: «Проверить NAS после обновления» → описание «проверить SMB и папку torrent-watch» → высокий приоритет → срок → «В работу» → «Готово».
      </div>`;
    if(subtitle) subtitle.insertAdjacentElement('afterend',card);
    else pane.insertBefore(card,pane.firstChild);
  }

  function installDueField(){
    const title=$('taskTitle');
    if(!title || $('taskDueAt')) return;
    const form=title.closest('.task-form');
    if(!form) return;

    const due=document.createElement('input');
    due.className='field';
    due.id='taskDueAt';
    due.type='datetime-local';
    due.title='Необязательный срок задачи';
    due.setAttribute('aria-label','Срок задачи');

    const priority=$('taskPriority');
    if(priority) priority.insertAdjacentElement('afterend',due);
    else title.insertAdjacentElement('afterend',due);

    form.style.gridTemplateColumns='minmax(0,1fr) 150px minmax(180px,220px)';
  }

  function bindCreate(){
    const button=$('taskCreate');
    if(!button || button.dataset.tasksEnhanced==='1') return;
    button.dataset.tasksEnhanced='1';
    button.onclick=async()=>{
      const title=($('taskTitle')&&$('taskTitle').value||'').trim();
      if(!title) return;
      button.disabled=true;
      try{
        const dueLocal=($('taskDueAt')&&$('taskDueAt').value||'').trim();
        const dueAt=dueLocal ? new Date(dueLocal).toISOString() : '';
        await api('/v1/tasks',{
          method:'POST',
          body:JSON.stringify({
            owner_id:typeof ownerId!=='undefined'?ownerId:'web:local',
            title,
            details:($('taskDetails')&&$('taskDetails').value)||'',
            priority:Number(($('taskPriority')&&$('taskPriority').value)||0),
            due_at:dueAt
          })
        });
        $('taskTitle').value='';
        if($('taskDetails')) $('taskDetails').value='';
        if($('taskPriority')) $('taskPriority').value='0';
        if($('taskDueAt')) $('taskDueAt').value='';
        if(typeof loadTasks==='function') await loadTasks();
        if(typeof loadOverview==='function') await loadOverview();
      }catch(error){
        alert(error.message||String(error));
      }finally{
        button.disabled=false;
      }
    };
  }

  installHelp();
  installDueField();
  bindCreate();
})();
