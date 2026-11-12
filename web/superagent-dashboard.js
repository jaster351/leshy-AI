(function () {
  'use strict';
  // This panel is deliberately a small addition to the existing Tasks section.
  const $ = (id) => document.getElementById(id);
  const statusLabels = {
    queued: 'В очереди', working: 'Выполняется', waiting: 'Ждёт ответа',
    ready: 'Завершено', blocked: 'Требует внимания', failed: 'Ошибка', stopped: 'Остановлено'
  };
  const examples = [
    'Изучи последние ошибки в логах Лешего и предложи способы исправления.',
    'Проанализируй архитектуру проекта и подготовь отчёт с приоритетами улучшений.',
    'Исследуй надёжные подходы к долговременной памяти AI и сравни их.'
  ];
  let pending = false;
  let showArchived = false;
  let lastSignature = "";
  const element = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  const say = (message, error) => {
    const target = $('saNotice');
    if (!target) return;
    target.textContent = message;
    target.className = 'sa-notice ' + (error ? 'sa-error' : '');
  };
  const styles = `
    .sa-dashboard{margin:14px 0 18px;padding:16px;border:1px solid var(--line);background:rgba(13,30,22,.88);border-radius:16px}
    .sa-dashboard h3{margin:0 0 7px;font-size:17px}.sa-help{font-size:12px;color:var(--muted);line-height:1.55}
    .sa-dashboard textarea{width:100%;min-height:83px;margin:10px 0 7px;padding:10px;border:1px solid var(--line);border-radius:10px;background:#07110d;color:var(--text);resize:vertical}
    .sa-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.sa-row button,.sa-controls button{min-height:34px;padding:7px 11px;border:1px solid var(--line);border-radius:9px;background:rgba(96,190,133,.13);color:var(--text);cursor:pointer}
    .sa-row button:hover,.sa-controls button:hover{background:rgba(116,213,155,.22)}.sa-row button:disabled,.sa-controls button:disabled{opacity:.45;cursor:default}
    .sa-dashboard .sa-danger{border-color:rgba(255,140,140,.45);color:var(--danger)}
    .sa-list{display:grid;gap:9px;margin-top:12px}.sa-task{padding:12px;border:1px solid var(--line);border-radius:12px;background:rgba(5,16,10,.78);min-width:0}
    .sa-task h4{font-size:13px;margin:7px 0;font-weight:650;overflow-wrap:anywhere}
    .sa-meta{font-size:11px;color:var(--muted);overflow-wrap:anywhere}.sa-state{display:inline-block;padding:3px 8px;border:1px solid var(--line);border-radius:20px;font-size:11px}
    .sa-controls{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}.sa-notice{font-size:12px;min-height:20px;margin-top:9px;color:var(--accent)}.sa-error{color:var(--danger)}
    .sa-dashboard details{margin-top:10px;padding:10px;border:1px solid var(--line);border-radius:10px}.sa-dashboard summary{cursor:pointer;font-size:13px}
    .sa-dialog{display:grid;gap:8px;margin-top:9px}.sa-message{padding:9px 11px;border:1px solid var(--line);border-radius:11px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.sa-user{margin-left:8%;background:rgba(96,190,133,.11)}.sa-agent{margin-right:8%;background:rgba(100,140,180,.10)}.sa-answer{margin-top:10px;padding:11px;border:1px solid var(--line);border-radius:11px}.sa-answer textarea{min-height:90px}.sa-answer .sa-help{margin:0 0 6px}
    .sa-dashboard code{display:block;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;margin:7px 0;color:var(--accent)}
    .sa-plan{margin-top:10px;padding:10px;border:1px dashed var(--line);border-radius:10px}.sa-plan textarea{min-height:75px}
    @media(max-width:720px){.sa-dashboard{padding:12px}.sa-row button,.sa-controls button{flex:1;min-width:110px}}
  `;
  async function api(path, options = {}) {
    const headers = { 'X-Leshy-UI': 'superagent', ...options.headers };
    if (options.method === 'POST') headers['Content-Type'] = 'application/json';
    const response = await fetch(path, { ...options, headers, credentials: 'same-origin', cache: 'no-store' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.error || `Ошибка сервера: ${response.status}`);
    return data;
  }
  function makeButton(text, action, disabled, className) {
    const btn = element('button', className || '', text);
    btn.type = 'button';
    btn.disabled = !!disabled;
    btn.addEventListener('click', action);
    return btn;
  }
  async function perform(payload, confirmed = false) {
    if (pending) return;
    const action = payload.action;
    if (!confirmed && action === 'stop' && !window.confirm('Остановить это задание? Новые действия больше не начнутся.')) return;
    if (!confirmed && action === 'plan_cancel' && !window.confirm('Отменить все незавершённые этапы плана?')) return;
    if (!confirmed && action === 'close' && !window.confirm('Закрыть задание? Незавершённая работа будет остановлена, а отчёт сохранится в архиве.')) return;
    pending = true;
    say('Выполняю команду…', false);
    try {
      await api('/v1/superagent/task', { method: 'POST', body: JSON.stringify(payload) });
      say('Готово. Список обновлён.', false);
    } catch (error) {
      say(error.message, true);
    } finally {
      pending = false;
      await refresh(true);
    }
  }
  function renderDetails(host, detail, taskId) {
    host.replaceChildren();
    const dialogue = element('div', 'sa-dialog');
    (detail.conversation || []).forEach(message => dialogue.append(element('div', 'sa-message sa-user', message.text)));
    if (detail.state === 'waiting' && detail.attention) dialogue.append(element('div', 'sa-message sa-agent', detail.attention));
    if (detail.latest_report) dialogue.append(element('div', 'sa-message sa-agent', detail.latest_report));
    if (dialogue.childNodes.length) host.append(dialogue);
    if (detail.conversation && detail.conversation.length) host.append(element('p', 'sa-help', 'История ранее отправленных сообщений сохранена; старые вопросы агента могут отсутствовать.'));
    const steps = Array.isArray(detail.steps) ? detail.steps : [];
    if (steps.length) {
      host.append(element('strong', 'sa-help', 'Недавние действия'));
      steps.forEach(item => host.append(element('p', 'sa-meta', `${item.tool} — ${item.status}`)));
    }
    if (detail.plan) {
      const plan = detail.plan;
      host.append(element('strong', 'sa-help', 'План · ' + plan.state));
      (plan.steps || []).forEach(step => host.append(element('p', 'sa-meta', `${step.title} — ${step.state}`)));
      const group = element('div', 'sa-controls');
      if (!detail.archived && plan.state === 'active') group.append(makeButton('Пауза плана', () => perform({ action: 'plan_pause', id: taskId }), false));
      if (!detail.archived && plan.state === 'paused') group.append(makeButton('Продолжить план', () => perform({ action: 'plan_resume', id: taskId }), false));
      if (!detail.archived && ['active', 'paused'].includes(plan.state)) group.append(makeButton('Отменить план', () => perform({ action: 'plan_cancel', id: taskId }), false, 'sa-danger'));
      host.append(group);
      host.append(element('p', 'sa-help', 'План сохраняет последовательность этапов; сам по себе не запускает их автоматическое выполнение.'));
    } else if (!['ready', 'failed', 'stopped'].includes(detail.state)) {
      const planArea = element('div', 'sa-plan');
      planArea.append(element('strong', '', 'Создать план из этапов'));
      planArea.append(element('p', 'sa-help', 'По одному этапу в строке. Следующий этап зависит от предыдущего. Это планирование, а не запуск команд.'));
      const stepsInput = element('textarea', '', 'Изучить исходные данные\nПодготовить решение\nПроверить результат');
      stepsInput.setAttribute('aria-label', 'Этапы плана');
      planArea.append(stepsInput);
      planArea.append(makeButton('Сохранить план', async () => {
        const titles = stepsInput.value.split('\n').map(x => x.trim()).filter(Boolean);
        if (!titles.length || titles.length > 32 || titles.some(x => x.length > 300)) {
          say('Нужно от 1 до 32 этапов, каждый до 300 символов.', true); return;
        }
        const steps = titles.map((title, index) => ({ id: `step_${index + 1}`, title,
          depends_on: index ? [`step_${index}`] : [] }));
        await perform({ action: 'plan_create', id: taskId, steps });
      }, false));
      host.append(planArea);
    }
    if (!detail.latest_report && !detail.attention && !steps.length && !detail.plan) {
      host.prepend(element('p', 'sa-help', 'Подробных результатов пока нет.'));
    }
  }
  function taskCard(task) {
    const card = element('article', 'sa-task');
    const top = element('div', 'sa-row');
    top.append(element('span', 'sa-state', task.state_label || statusLabels[task.state] || task.state));
    top.append(element('span', 'sa-meta', `Попыток: ${task.attempt || 0} · ${task.id}`));
    card.append(top);
    card.append(element('h4', '', task.title || 'Без названия'));
    if (task.latest_report) card.append(element('p', 'sa-help', task.latest_report.slice(0, 500)));
    if (task.state === 'waiting' && task.attention) card.append(element('p', 'sa-help', 'Вопрос SuperAgent: ' + task.attention.slice(0, 1200)));
    const actions = element('div', 'sa-controls');
    const details = element('div', 'sa-detail');
    details.hidden = true;
    actions.append(makeButton('Подробнее', async () => {
      if (!details.hidden) { details.hidden = true; return; }
      details.hidden = false;
      details.textContent = 'Загрузка…';
      try {
        const response = await api('/v1/superagent/tasks?id=' + encodeURIComponent(task.id));
        renderDetails(details, response.task, task.id);
      } catch (error) { details.textContent = error.message; }
    }));
    if (task.can_restore) actions.append(makeButton('Вернуть из архива', () => perform({ action: 'restore', id: task.id })));
    if (task.can_stop) actions.append(makeButton('Остановить', () => perform({ action: 'stop', id: task.id }), false, 'sa-danger'));
    if (task.can_retry) actions.append(makeButton('Повторить', () => perform({ action: 'retry', id: task.id })));
    if (task.can_close) {
      const label = task.state === 'ready' ? 'Завершить и закрыть' :
        ['queued', 'working', 'waiting', 'blocked'].includes(task.state) ? 'Отменить и закрыть' : 'Закрыть в архив';
      actions.append(makeButton(label, () => perform({ action: 'close', id: task.id }), false, 'sa-danger'));
    }
    card.append(actions, details);
    if (task.can_answer) {
      const reply = element('div', 'sa-answer');
      reply.append(element('p', 'sa-help', 'Ваш ответ для этого задания:'));
      const input = element('textarea');
      input.placeholder = 'Введите ответ на вопрос SuperAgent…';
      input.maxLength = 12000;
      input.setAttribute('aria-label', 'Ответ SuperAgent: ' + (task.title || 'задание'));
      reply.append(input);
      reply.append(makeButton('Отправить ответ и продолжить', () => {
        const value = input.value.trim();
        if (!value) { say('Сначала введите ответ.', true); input.focus(); return; }
        perform({ action: 'answer', id: task.id, text: value });
      }));
      card.append(reply);
    }
    return card;
  }
  async function refresh(force = false) {
    const list = $('saTaskList');
    if (!list) return;
    try {
      const data = await api('/v1/superagent/tasks?archived=' + (showArchived ? '1' : '0'));
      const signature = JSON.stringify([showArchived, data.tasks]);
      if (signature === lastSignature && !force) return;
      const activeReply = document.activeElement?.closest?.('.sa-answer');
      if (activeReply && activeReply.querySelector('textarea')?.value.trim() && !force) return;
      lastSignature = signature;
      list.replaceChildren();
      if (!data.tasks.length) list.append(element('div', 'sa-help', showArchived ? 'В архиве пока нет заданий.' : 'Активных заданий пока нет. Опишите задачу выше и нажмите «Запустить».'));
      (data.tasks || []).forEach(item => list.append(taskCard(item)));
      const stamp = $('saUpdated');
      if (stamp) stamp.textContent = 'Обновлено: ' + new Date().toLocaleTimeString('ru-RU');
    } catch (error) {
      list.replaceChildren(element('div', 'sa-help', 'Не удалось получить задания: ' + error.message));
    }
  }
  function install() {
    if ($('saDashboard')) return;
    const pane = $('pane-superagent') || $('pane-tasks');
    if (!pane) return;
    const css = element('style'); css.textContent = styles; document.head.append(css);
    const panel = element('section', 'sa-dashboard'); panel.id = 'saDashboard';
    panel.append(element('h3', '', 'Суперагент · автономные задания'));
    panel.append(element('p', 'sa-help', 'Здесь можно создавать, останавливать и повторять задания без команд и идентификаторов. Действия применяются только к Вашим заданиям.'));
    const input = element('textarea'); input.id = 'saNewTask';
    input.placeholder = 'Что нужно сделать? Например: изучи логи и предложи исправления';
    input.setAttribute('aria-label', 'Описание новой задачи SuperAgent');
    panel.append(input);
    const row = element('div', 'sa-row');
    row.append(makeButton('Запустить задачу', () => {
      const text = input.value.trim();
      if (!text) { say('Опишите задачу перед запуском.', true); return; }
      perform({ action: 'create', text });
      input.value = '';
    }));
    row.append(makeButton('Обновить список', () => refresh(true)));
    const toggleArchive = makeButton('Показать архив', async () => {
      showArchived = !showArchived;
      toggleArchive.textContent = showArchived ? 'Показать активные' : 'Показать архив';
      lastSignature = '';
      await refresh(true);
    });
    row.append(toggleArchive);
    panel.append(row);
    const tips = element('details');
    const label = element('summary', '', 'Что делают кнопки и команды? Примеры');
    tips.append(label);
    [
      ['Запустить задачу', 'Создаёт новое автономное задание. Не даёт дополнительных разрешений на опасные действия.', '/agent Изучи причину ошибки запуска и подготовь отчёт'],
      ['Остановить', 'Останавливает дальнейшие действия задания; уже начатый ответ модели может завершиться.', '/agent stop SA-...'],
      ['Повторить', 'Заново ставит в очередь задание после ошибки или ожидания.', '/agent retry SA-...'],
      ['Ответить', 'Передаёт ответ, когда SuperAgent ожидает решения.', '/agent answer SA-... Да, продолжай'],
      ['Подробнее', 'Показывает отчёт, недавние инструментальные шаги и этапы плана.', '/agent status SA-...'],
      ['Отправить ответ и продолжить', 'Появляется прямо под вопросом, когда агент ожидает ответа. После отправки задача возобновляется.', 'Ответ вводится в карточке задания'],
      ['Завершить и закрыть', 'Переносит завершённое задание в архив, сохраняя отчёт и историю.', 'Кнопка в карточке задания'],
      ['Отменить и закрыть', 'Останавливает незаконченное задание и переносит его в архив.', 'Кнопка в карточке задания'],
      ['Показать архив', 'Показывает закрытые задачи с возможностью вернуть их в список.', 'Кнопка над списком'],
      ['Пауза / продолжение плана', 'Меняет состояние сохранённого плана. Само создание плана пока не запускает этапы.', '/agent plan status SA-...']
    ].forEach(([name, description, command]) => {
      tips.append(element('p', 'sa-help', name + ' — ' + description));
      tips.append(element('code', '', command));
    });
    tips.append(element('p', 'sa-help', 'Примеры новых задач — нажмите на пример, затем «Запустить»:'));
    examples.forEach(example => tips.append(makeButton(example, () => { input.value = example; input.focus(); }, false, '')));
    panel.append(tips);
    const notice = element('div', 'sa-notice'); notice.id = 'saNotice'; notice.setAttribute('role', 'status');
    panel.append(notice);
    const list = element('div', 'sa-list'); list.id = 'saTaskList'; panel.append(list);
    const updated = element('div', 'sa-meta'); updated.id = 'saUpdated'; panel.append(updated);
    const subtitle = pane.querySelector('.section-subtitle');
    if (subtitle) subtitle.after(panel);
    else pane.prepend(panel);
    refresh();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
  // Refresh when opening the Tasks page, and while it is actively visible.
  document.addEventListener('click', event => {
    if (event.target.closest('[data-section="tasks"], [data-start="tasks"], [data-admin-route="superagent"]')) setTimeout(refresh, 100);
  });
  setInterval(() => {
    if (document.visibilityState === 'visible' && ($('pane-superagent')?.classList.contains('active') || $('pane-tasks')?.classList.contains('active')) && !pending) refresh();
  }, 15000);
})();
