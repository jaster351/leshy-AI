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
  const expandedTasks = new Set();
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
    .sa-remote{margin-top:14px;padding:12px;border:1px solid var(--line);border-radius:12px}
    .sa-remote h4{font-size:14px;margin:12px 0 7px}
    .sa-remote-card{padding:10px 0;border-bottom:1px solid var(--line)}
    .sa-remote-card:last-child{border-bottom:0}
    .sa-remote-note{font-size:12px;line-height:1.5;color:var(--muted);margin:8px 0}
    .sa-remote .sa-danger{border-color:rgba(255,140,140,.45);color:var(--danger)}
    .sa-dashboard code{display:block;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;margin:7px 0;color:var(--accent)}
    .sa-plan{margin-top:10px;padding:10px;border:1px dashed var(--line);border-radius:10px}.sa-plan textarea{min-height:75px}.sa-plan select{width:100%;max-width:460px;margin:6px 0 10px;padding:9px;border:1px solid var(--line);border-radius:8px;background:#07110d;color:var(--text)}
    .sa-stage-result{padding:7px 9px;margin:5px 0;border-left:2px solid var(--line);overflow-wrap:anywhere}.sa-evidence{font-size:12px;color:var(--accent);margin:4px 0}.sa-proof{font-size:12px;margin:4px 0 7px}.sa-proof summary{cursor:pointer}.sa-stage{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin:8px 0;padding:9px;border:1px solid var(--line);border-radius:9px}
    .sa-stage select{width:auto;max-width:100%;flex:1 1 150px;margin:0}.sa-stage input{min-width:130px;flex:2 1 180px;padding:9px;border:1px solid var(--line);border-radius:8px;background:#07110d;color:var(--text)}
    .sa-stage input[hidden]{display:none}.sa-stage button{min-height:34px}.sa-progress{font-size:12px;margin:6px 0;color:var(--muted)}
    .sa-dashboard details.sa-advanced{margin-top:10px}.sa-dashboard button:focus-visible,.sa-dashboard input:focus-visible,.sa-dashboard select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
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
      const response = await api('/v1/superagent/task', { method: 'POST', body: JSON.stringify(payload) });
      if (action === 'plan_run' && response.plan)
        say('Проверок выполнено: ' + response.plan.completed + '. Ошибок: ' + response.plan.errors + '.', !!response.plan.errors);
      else if (action === 'plan_auto') say('Автоматические проверки включены для этого плана.', false);
      else if (action === 'plan_manual') say('Автоматические проверки выключены.', false);
      else say('Готово. Список обновлён.', false);
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
      const visibleSteps = plan.steps || [];
      const total = Number.isInteger(plan.total_steps) ? plan.total_steps : visibleSteps.length;
      host.append(element('strong', 'sa-help', `План · ${plan.state} · ${total} этапов`));
      if (plan.state_counts) {
        const counts = Object.entries(plan.state_counts).map(([state, count]) => `${statusLabels[state] || state}: ${count}`);
        host.append(element('p', 'sa-help', counts.join(' · ')));
      }
      const stageLabels = {queued:'Ожидает',running:'Выполняется',done:'Готово',
        failed:'Ошибка',blocked:'Заблокировано',cancelled:'Отменено'};
      const finished = Number(plan.state_counts?.done || 0);
      host.append(element('p', 'sa-progress', `Готово: ${finished} из ${total}. ` +
        (plan.auto_run ? 'Автоматические чтения включены.' : 'Автоматические чтения выключены.')));
      visibleSteps.forEach(step => {
        const item = element('div', 'sa-stage-result');
        item.append(element('p', 'sa-meta', `${step.title} — ${stageLabels[step.state] || step.state}`));
        if (step.evidence) {
          const evidence = step.evidence;
          const title = evidence.status === 'recorded' ? 'Проверено при выполнении' :
            evidence.status === 'legacy' ? 'Историческое выполнение' : 'Подтверждение недоступно';
          item.append(element('p', 'sa-evidence', title + '. ' + (evidence.description || '')));
          if (evidence.summary) item.append(element('p', 'sa-help', evidence.summary));
          if (evidence.source_review) {
            const review = evidence.source_review;
            const reviewText = {
              empty: 'Совпадений в базе знаний нет.',
              missing_sources: 'У записей нет подходящих ссылок на источники.',
              limited_sources: 'Источников недостаточно для независимого сопоставления.',
              multiple_hosts: 'Найдены ссылки на несколько разных сайтов.'
            };
            const label = element('p', 'sa-evidence',
              'Проверка источников: ' + (reviewText[review.status] || 'Требуется проверка.'));
            item.append(label);
            item.append(element('p', 'sa-help',
              'Записей: ' + review.items + ', со ссылкой: ' + review.linked_items +
              ', сайтов: ' + review.distinct_hosts + ', повторов: ' + review.repeated_claims + '.'));
            item.append(element('p', 'sa-help',
              'Ссылки проверены только на наличие и разнообразие. Содержимое сайтов не проверялось; совпадение ссылок не доказывает истинность сведений.'));
          } else if (evidence.source_review_status === 'unavailable') {
            item.append(element('p', 'sa-help', 'Проверку ссылок нельзя подтвердить по сохранённым данным.'));
          }
          if (evidence.result_truncated)
            item.append(element('p', 'sa-help', 'Полный результат не сохранён: доступен только его отпечаток.'));
          if (evidence.status === 'recorded') {
            const proof = element('details', 'sa-proof');
            proof.append(element('summary', '', 'Техническое подтверждение'));
            proof.append(element('p', 'sa-meta', 'Квитанция: ' + (evidence.receipt_id || 'неизвестно')));
            proof.append(element('p', 'sa-meta', 'SHA-256 результата: ' + (evidence.result_sha256 || 'неизвестен')));
            if (evidence.recorded_at) proof.append(element('p', 'sa-meta', 'Выполнено: ' + evidence.recorded_at));
            proof.append(element('p', 'sa-help',
              'Подтверждено выполнение инструмента, а не достоверность внешних сведений. После перезапуска криптографическая проверка локальной базы не выполняется.'));
            item.append(proof);
          }
        }
        host.append(item);
      });
      if (total > visibleSteps.length) host.append(element('p', 'sa-help', `Показано ${visibleSteps.length} из ${total} этапов. Общий прогресс — выше.`));
      const group = element('div', 'sa-controls');
      if (!detail.archived && plan.state === 'active') group.append(makeButton('Пауза плана', () => perform({ action: 'plan_pause', id: taskId }), false));
      if (!detail.archived && plan.state === 'paused') group.append(makeButton('Продолжить план', () => perform({ action: 'plan_resume', id: taskId }), false));
      const liveTicket = !['ready', 'failed', 'blocked', 'stopped'].includes(detail.state) && !detail.archived;
      const hasReadSteps = visibleSteps.some(step => !!step.tool) || (plan.read_steps_total || 0) > 0;
      if (liveTicket && plan.state === 'active' && hasReadSteps)
        group.append(makeButton('Выполнить доступные проверки', () => perform({action:'plan_run', id:taskId}), false));
      if (liveTicket && ['active', 'paused'].includes(plan.state) && hasReadSteps) {
        group.append(makeButton(plan.auto_run ? 'Выключить автозапуск проверок' : 'Включить автозапуск проверок',
          () => perform({action:plan.auto_run ? 'plan_manual':'plan_auto', id:taskId}), false));
      }
      if (!detail.archived && ['active', 'paused'].includes(plan.state))
        group.append(makeButton('Отменить план', () => perform({ action: 'plan_cancel', id: taskId }), false, 'sa-danger'));
      host.append(group);
      host.append(element('p', 'sa-help',
        hasReadSteps
          ? 'Автоматически доступны только безопасные проверки: версия, сведения о системе и поиск в проверенных знаниях. Действия с файлами и ОС не запускаются.'
          : 'В этом плане только ручные этапы. Для автоматических проверок создайте план с доступными действиями.'));
    } else if (!['ready', 'failed', 'stopped'].includes(detail.state)) {
      const planArea = element('div', 'sa-plan');
      planArea.append(element('strong', '', 'Составить план задания'));
      planArea.append(element('p', 'sa-help',
        'Добавьте действия кнопками. Проверки выполняются Лешим; ручные этапы остаются напоминаниями, а не командами ОС.'));
      const modeLabel = element('label', 'sa-help', 'Как выполнять этапы');
      const modeSelect = element('select');
      modeSelect.setAttribute('aria-label', 'Порядок этапов');
      [['parallel', 'Независимо (параллельно)'], ['sequential', 'По очереди']].forEach(([value, text]) => {
        const option = element('option', '', text); option.value = value; modeSelect.append(option);
      });
      planArea.append(modeLabel, modeSelect);

      const stageRows = element('div', 'sa-stage-list');
      const toolOptions = [
        ['manual', 'Ручной этап', 'Опишите этап'],
        ['runtime.version', 'Проверить версию Лешего', 'Проверить версию Лешего'],
        ['runtime.platform', 'Посмотреть систему Лешего', 'Посмотреть систему Лешего'],
        ['knowledge.search', 'Найти в проверенных знаниях', 'Найти сведения в знаниях']
      ];
      const addStage = (initialTool) => {
        if (stageRows.children.length >= 64) {
          say('Для удобства редактор поддерживает не более 64 этапов за раз.', true); return;
        }
        const item = element('div', 'sa-stage');
        const select = element('select');
        select.setAttribute('aria-label', 'Действие этапа');
        toolOptions.forEach(([value, name]) => {
          const option = element('option', '', name); option.value = value; select.append(option);
        });
        select.value = initialTool;
        const title = element('input', 'sa-stage-title');
        title.type = 'text'; title.maxLength = 300;
        title.setAttribute('aria-label', 'Название этапа');
        const query = element('input', 'sa-stage-query');
        query.type = 'text'; query.maxLength = 300;
        query.placeholder = 'Что искать? Например: источники информации о памяти агентов';
        query.setAttribute('aria-label', 'Запрос для поиска в знаниях');
        const update = () => {
          query.hidden = select.value !== 'knowledge.search';
          const suggested = toolOptions.find(x => x[0] === select.value)?.[2] || '';
          if (!title.value || toolOptions.some(x => x[2] === title.value)) title.value = select.value === 'manual' ? '' : suggested;
          title.placeholder = select.value === 'manual' ? 'Что должен сделать человек?' : suggested;
        };
        select.addEventListener('change', update);
        update();
        const remove = makeButton('Убрать', () => item.remove());
        remove.setAttribute('aria-label', 'Удалить этап');
        item.append(select, title, query, remove);
        stageRows.append(item);
        if (initialTool === 'knowledge.search') query.focus();
        else if (initialTool === 'manual') title.focus();
      };
      const choices = element('div', 'sa-row');
      [
        ['Проверить версию', 'runtime.version'],
        ['Сведения о системе', 'runtime.platform'],
        ['Поиск в знаниях', 'knowledge.search'],
        ['Ручной этап', 'manual']
      ].forEach(([name, kind]) => choices.append(makeButton('+ ' + name, () => addStage(kind))));
      planArea.append(choices, stageRows);

      const planPayload = (steps) => {
        const payload = {action: 'plan_create', id: taskId, steps};
        if (new TextEncoder().encode(JSON.stringify(payload)).length > 262144) {
          say('Слишком много данных (максимум 256 КБ). Разделите план на несколько задач.', true);
          return null;
        }
        return payload;
      };
      const createBuiltPlan = async () => {
        const rows = Array.from(stageRows.children);
        if (!rows.length) { say('Сначала добавьте хотя бы одно действие.', true); return; }
        const steps = [];
        for (const [index, item] of rows.entries()) {
          const tool = item.querySelector('select').value;
          const title = item.querySelector('.sa-stage-title').value.trim();
          const query = item.querySelector('.sa-stage-query').value.trim();
          if (!title) { say('У каждого этапа должно быть понятное название.', true); return; }
          if (tool === 'knowledge.search' && (query.length < 3 || query.length > 300)) {
            say('Введите запрос для поиска в знаниях — от 3 до 300 символов.', true);
            item.querySelector('.sa-stage-query').focus(); return;
          }
          const step = {id: 'step_' + (index + 1), title,
            depends_on: modeSelect.value === 'sequential' && index ? ['step_' + index] : []};
          if (tool !== 'manual') {
            step.tool = tool;
            step.arguments = tool === 'knowledge.search' ? {query} : {};
          }
          steps.push(step);
        }
        const payload = planPayload(steps);
        if (payload) await perform(payload);
      };
      planArea.append(makeButton('Сохранить план из действий', createBuiltPlan));
      planArea.append(element('p', 'sa-help',
        'После сохранения включите «Автозапуск проверок» для выполнения в фоне. Это не включает управление файлами или системой.'));

      const simple = element('details', 'sa-advanced');
      simple.append(element('summary', '', 'Другой способ: перечислить ручные этапы строками'));
      simple.append(element('p', 'sa-help', 'Напишите каждый этап с новой строки. Эти этапы не выполняются автоматически.'));
      const stepsInput = element('textarea');
      stepsInput.value = 'Изучить исходные данные\nПодготовить решение\nПроверить результат';
      stepsInput.setAttribute('aria-label', 'Ручные этапы плана');
      simple.append(stepsInput);
      simple.append(makeButton('Сохранить ручные этапы', async () => {
        const titles = stepsInput.value.split('\n').map(x => x.trim()).filter(Boolean);
        if (!titles.length || titles.some(x => x.length > 300)) {
          say('Нужен хотя бы один этап, каждый до 300 символов.', true); return;
        }
        const steps = titles.map((title, index) => ({
          id: 'step_' + (index + 1), title,
          depends_on: modeSelect.value === 'sequential' && index ? ['step_' + index] : []
        }));
        const payload = planPayload(steps);
        if (payload) await perform(payload);
      }));
      planArea.append(simple);
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
    const loadDetails = async () => {
      details.textContent = 'Загрузка…';
      try {
        const response = await api('/v1/superagent/tasks?id=' + encodeURIComponent(task.id));
        if (expandedTasks.has(task.id)) renderDetails(details, response.task, task.id);
      } catch (error) { details.textContent = error.message; }
    };
    actions.append(makeButton('Подробнее', () => {
      if (expandedTasks.has(task.id)) {
        expandedTasks.delete(task.id);
        details.hidden = true;
      } else {
        expandedTasks.add(task.id);
        details.hidden = false;
        loadDetails();
      }
    }));
    if (expandedTasks.has(task.id)) {
      details.hidden = false;
      loadDetails();
    }
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
      const editing = document.activeElement?.closest?.('.sa-answer, .sa-plan');
      if (editing && !force) return;
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
  function installRemoteAccess(panel) {
    const section = element('details', 'sa-remote');
    const heading = element('summary', '', 'Удалённые компьютеры и разрешения');
    section.append(heading);
    section.append(element('p', 'sa-remote-note',
      'Показывает зарегистрированные компьютеры и разрешения, которые можно отозвать. ' +
      'SSH-проверка ОС доступна только для ранее подтверждённых серверов. ' +
      'Удалённый рабочий стол и выполнение произвольных команд не поддерживаются.'));
    const status = element('p', 'sa-notice');
    status.setAttribute('role', 'status');
    const container = element('div', 'sa-remote-list');
    const reload = makeButton('Обновить разрешения', () => load());
    section.append(reload, status, container);
    panel.append(section);

    const display = (value) => String(value || '').slice(0, 200);
    const readableTime = (value) => {
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? 'неизвестно' : date.toLocaleString('ru-RU');
    };
    const capabilities = {
      inspect: 'Просмотр состояния', execute: 'Выполнение команд',
      update: 'Обновления', reboot: 'Перезагрузка', rdp: 'Удалённый экран'
    };
    let requestInFlight = false;
    const addInspection = (row, node, credentials) => {
      if (!credentials || !credentials.files?.length) {
        row.append(element('p', 'sa-remote-note',
          'Для проверки ОС выберите папку с файлами доступа в Настройках SuperAgent. ' +
          'Пока она не задана, SSH не запускается.'));
        const shortcut = makeButton('Открыть настройки файлов доступа', () => {
          if (typeof window.showSection === 'function') window.showSection('settings');
          $('saCredentialFolderCard')?.scrollIntoView({block:'start', behavior:'smooth'});
        });
        row.append(shortcut);
        return;
      }
      const label = element('label', 'sa-remote-note', 'Файл доступа для проверки по SSH');
      const select = element('select', 'sa-ssh-select');
      select.setAttribute('aria-label', 'Файл доступа к компьютеру ' + node.node_id);
      credentials.files.forEach(name => {
        const option = element('option', '', name); option.value = name; select.append(option);
      });
      const result = element('p', 'sa-remote-note');
      result.setAttribute('role', 'status');
      const inspect = makeButton('Проверить операционную систему (SSH)', async () => {
        if (requestInFlight || !select.value) return;
        if (!window.confirm('Запустить только чтение ОС на компьютере ' + node.node_id +
          '? Будет использован выбранный файл доступа. Команды пользователя не выполняются.')) return;
        requestInFlight = true;
        inspect.disabled = true;
        result.textContent = 'Устанавливаю защищённое SSH-соединение и проверяю подлинность сервера…';
        try {
          const response = await api('/v1/superagent/ssh/inspect', {
            method:'POST', body:JSON.stringify({
              node_id:node.node_id, credential_file:select.value, confirm:true
            })
          });
          const observed = response.inspection || {};
          const known = observed.verified === true;
          const name = observed.os_family === 'Windows' ? 'Windows' :
            ['Linux','FreeBSD','OpenBSD','NetBSD','Darwin'].includes(observed.os_family)
              ? observed.os_family : 'Не определена';
          result.textContent = known
            ? 'Подлинность SSH-сервера подтверждена. Операционная система: ' + name + '.'
            : 'Подлинность SSH-сервера подтверждена, но ОС определить не удалось.';
          result.className = 'sa-remote-note';
        } catch (error) {
          result.textContent = 'SSH-проверка не выполнена: ' + error.message;
          result.className = 'sa-remote-note sa-error';
        } finally {
          requestInFlight = false;
          inspect.disabled = false;
        }
      });
      row.append(label, select, inspect, result);
      if (credentials.truncated) row.append(element('p', 'sa-remote-note',
        'Показаны первые 100 файлов доступа.'));
    };
    async function load() {
      if (requestInFlight || !section.open) return;
      requestInFlight = true;
      reload.disabled = true;
      status.className = 'sa-notice';
      status.textContent = 'Проверяю разрешения…';
      try {
        const data = await api('/v1/superagent/remote-access');
        container.replaceChildren();
        status.textContent = data.remote_admin_enabled
          ? 'SSH-диагностика доступна только для подтверждённых компьютеров при наличии файла доступа и SSH-модуля. RDP и управление ОС выключены.'
          : 'Удалённое администрирование выключено. Доступные разрешения можно просмотреть и отозвать.';
        let credentials = null;
        if (data.remote_admin_enabled && data.nodes.length) {
          try {
            credentials = await api('/v1/superagent/ssh/credentials');
          } catch (error) {
            container.append(element('p', 'sa-remote-note',
              'Файлы доступа недоступны: ' + error.message));
          }
        }
        if (!data.nodes.length) {
          container.append(element('p', 'sa-remote-note',
            'Подтверждённых компьютеров пока нет. Новый компьютер не добавляется по одному IP или паролю: ' +
            'нужно отдельное подтверждение владения и подлинности.'));
        } else {
          container.append(element('h4', '', 'Мои компьютеры'));
          data.nodes.forEach(node => {
            const row = element('div', 'sa-remote-card');
            row.append(element('strong', '', display(node.node_id)));
            row.append(element('p', 'sa-meta', 'Зарегистрирован: ' + readableTime(node.registered_at)));
            row.append(element('p', 'sa-remote-note',
              'Наличие записи не означает, что компьютер сейчас доступен или его ключ повторно проверен.'));
            if (data.remote_admin_enabled && credentials) addInspection(row, node, credentials);
            container.append(row);
          });
        }
        if (data.nodes_truncated) container.append(element('p', 'sa-remote-note', 'Показаны первые 64 компьютера.'));
        if (data.active_grants.length) {
          container.append(element('h4', '', 'Кому разрешён доступ'));
          data.active_grants.forEach(grant => {
            const row = element('div', 'sa-remote-card');
            row.append(element('strong', '',
              (capabilities[grant.capability] || 'Другое разрешение') +
              ' · ' + display(grant.node_id)));
            row.append(element('p', 'sa-meta',
              'Пользователь: ' + display(grant.grantee_id) +
              ' · до: ' + readableTime(grant.expires_at)));
            const revoke = makeButton('Отозвать это разрешение', async () => {
              if (!window.confirm('Отозвать право «' +
                (capabilities[grant.capability] || grant.capability) +
                '» для выбранного пользователя?')) return;
              if (requestInFlight) return;
              requestInFlight = true;
              revoke.disabled = true;
              status.textContent = 'Отзываю разрешение…';
              let revoked = false;
              try {
                await api('/v1/superagent/remote-access/revoke', {
                  method: 'POST',
                  body: JSON.stringify({
                    node_id: grant.node_id,
                    grantee_id: grant.grantee_id,
                    capability: grant.capability
                  })
                });
                revoked = true;
                row.remove();
                // Reload the whole view to avoid showing stale grants.
              } catch (error) {
                status.textContent = 'Не удалось отозвать разрешение: ' + error.message;
                status.className = 'sa-notice sa-error';
                revoke.disabled = false;
              } finally {
                requestInFlight = false;
                await load();
                if (revoked) {
                  status.className = 'sa-notice';
                  status.textContent = 'Разрешение отозвано. Список обновлён.';
                }
              }
            }, false, 'sa-danger');
            row.append(revoke);
            container.append(row);
          });
        } else {
          container.append(element('p', 'sa-remote-note',
            'Активных разрешений для других пользователей нет.'));
        }
        if (data.grants_truncated) container.append(element('p', 'sa-remote-note', 'Показаны первые 200 разрешений.'));
      } catch (error) {
        status.textContent = error.message || 'Не удалось прочитать разрешения';
        container.replaceChildren(element('p', 'sa-remote-note',
          'Для этого раздела нужен действующий вход через подтверждённого провайдера.'));
      } finally {
        requestInFlight = false;
        reload.disabled = false;
      }
    }
    section.addEventListener('toggle', () => {
      if (section.open) load();
    });
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
      expandedTasks.clear();
      toggleArchive.textContent = showArchived ? 'Показать активные' : 'Показать архив';
      lastSignature = '';
      await refresh(true);
    });
    row.append(toggleArchive);
    panel.append(row);
    const tips = element('details');
    const label = element('summary', '', 'Подсказки и примеры');
    tips.append(label);
    [
      ['Запустить задачу', 'Опишите обычными словами, что должен сделать Леший.'],
      ['Подробнее', 'В карточке доступны история, отчёт, этапы, подтверждения выполнения и управление проверками.'],
      ['Составить план', 'Добавьте готовые действия кнопками. Можете выбрать параллельное выполнение или порядок этапов.'],
      ['Выполнить доступные проверки', 'Однократно запускает готовые безопасные чтения.'],
      ['Включить автозапуск проверок', 'Позволяет Лешему выполнять разрешённые чтения в фоне. Можно выключить в любой момент.'],
      ['Пауза и отмена', 'Останавливают новые шаги, сохраняя уже полученные результаты.'],
      ['Ответить на вопрос', 'Напишите ответ прямо в карточке, без специальных команд и идентификаторов.'],
      ['Архив', 'Завершённые задания можно закрывать и возвращать из архива.']
    ].forEach(([name, description]) => tips.append(element('p', 'sa-help', name + ' — ' + description)));
    tips.append(element('p', 'sa-help', 'Примеры новых задач — нажмите на пример, затем «Запустить»:'));
    examples.forEach(example => tips.append(makeButton(example, () => { input.value = example; input.focus(); }, false, '')));
    panel.append(tips);
    installRemoteAccess(panel);
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
