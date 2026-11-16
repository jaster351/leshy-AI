(function () {
  'use strict';

  const find = (id) => document.getElementById(id);
  const labels = {
    idle: 'Ожидание', queued: 'В очереди', checking: 'Проверка источника',
    current: 'Рабочая модель актуальна в канале stable',
    available: 'Найдена модель для загрузки', downloading: 'Загрузка модели',
    pending_quality_check: 'Модель загружена, проверка качества ещё не проведена',
    error: 'Ошибка обновления'
  };

  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {
      credentials: 'same-origin'
    } : {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'HTTP ' + response.status);
    return payload;
  }

  function attach() {
    if (find('modelUpdateCard')) return true;
    const pane = find('pane-settings');
    if (!pane) return false;
    const card = document.createElement('section');
    card.className = 'card';
    card.id = 'modelUpdateCard';
    card.style.marginTop = '12px';
    card.innerHTML = [
      '<h3>Автоматическое обновление языковой модели</h3>',
      '<div class="muted">Проверка официального канала stable каждые 24 часа. ',
      'Новая модель не заменяет рабочую до независимой проверки её качества.</div>',
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">',
      '<select class="field" id="modelUpdateMode" aria-label="Режим обновления">',
      '<option value="manual">Только вручную</option>',
      '<option value="notify">Проверять и сообщать</option>',
      '<option value="auto">Автоматически скачивать кандидат</option>',
      '</select>',
      '<button class="button secondary" id="modelUpdateCheck" type="button">Проверить сейчас</button>',
      '</div>',
      '<div id="modelUpdateStatus" class="model-meta" style="white-space:pre-wrap;margin-top:10px;font-size:12px">',
      'Загружаю состояние обновлений…</div>',
      '<div class="hint" style="margin-top:8px">Автозагрузка не означает автоматическое переключение. ',
      'Пока отсутствует независимая оценка русского языка, фактической точности и работы инструментов, ',
      'новая GGUF остаётся кандидатом. Существующая модель не удаляется.</div>',
      '<section style="margin-top:18px;border-top:1px solid var(--line);padding-top:14px">',
      '<h3>Поиск моделей в репозиториях разработчиков</h3>',
      '<p class="muted">Поиск по реальному каталогу Hugging Face Hub, а не по заранее записанному списку. ',
      'Специализация определяется только по доступным названиям и тегам.</p>',
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px">',
      '<label>Название модели<input class="field" id="modelRepoName" type="search" maxlength="120" ',
      'placeholder="Например, Qwen3.5 или Coder" autocomplete="off" style="width:100%"></label>',
      '<label>Автор / организация<input class="field" id="modelRepoAuthor" maxlength="80" ',
      'placeholder="Например, Qwen или google" autocomplete="off" style="width:100%"></label>',
      '<label>Специализация<select class="field" id="modelRepoSpecialty" style="width:100%">',
      '<option value="all">Все направления</option>',
      '<option value="programming">Программирование</option>',
      '<option value="legal">Юриспруденция</option>',
      '<option value="medicine">Медицина</option>',
      '<option value="finance">Финансы</option>',
      '<option value="science">Наука</option>',
      '<option value="education">Образование</option>',
      '</select></label>',
      '<label>Дата публикации от<input class="field" id="modelRepoFrom" type="date" style="width:100%"></label>',
      '<label>Дата публикации до<input class="field" id="modelRepoTo" type="date" style="width:100%"></label>',
      '</div>',
      '<button class="button secondary" id="modelRepoSearch" type="button" style="margin-top:12px">',
      'Найти модели</button>',
      '<div id="modelRepoResults" aria-live="polite" style="margin-top:12px"></div>',
      '</section>'
    ].join('');
    const reference = find('modelLibraryCard') || find('localModelCard');
    if (reference && reference.parentNode) reference.insertAdjacentElement('afterend', card);
    else pane.appendChild(card);
    find('modelUpdateMode').onchange = save;
    find('modelUpdateCheck').onclick = check;
    find('modelRepoSearch').onclick = searchRepositories;
    find('modelRepoName').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); searchRepositories(); }
    });
    return true;
  }

  async function searchRepositories() {
    const button = find('modelRepoSearch');
    const container = find('modelRepoResults');
    if (!button || !container) return;
    const params = new URLSearchParams({
      name: find('modelRepoName').value.trim(),
      author: find('modelRepoAuthor').value.trim(),
      specialty: find('modelRepoSpecialty').value,
      from: find('modelRepoFrom').value,
      to: find('modelRepoTo').value,
      limit: '30'
    });
    button.disabled = true;
    container.textContent = 'Получаю модели из каталога Hugging Face…';
    try {
      const data = await api('/v1/model-repositories?' + params.toString());
      container.replaceChildren();
      const info = document.createElement('p');
      info.className = 'muted';
      info.textContent = 'Найдено: ' + data.count + ' · просмотрено записей: ' + data.scanned
        + '. Данные: ' + data.source + '.';
      container.appendChild(info);
      if (!data.items || !data.items.length) {
        const empty = document.createElement('p');
        empty.textContent = 'Под заданные условия модели не найдены в просмотренной части каталога.';
        container.appendChild(empty);
      }
      for (const item of data.items || []) {
        const entry = document.createElement('article');
        entry.style.cssText = 'padding:10px 0;border-bottom:1px solid var(--line)';
        const link = document.createElement('a');
        // Only verified canonical Hub repository URL shapes may leave the UI.
        if (!/^https:\/\/huggingface\.co\/[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/.test(item.url)) {
          continue;
        }
        link.href = item.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = item.id;
        const details = document.createElement('div');
        details.className = 'muted';
        details.style.fontSize = '12px';
        details.textContent = 'Опубликована: ' + (item.created_at || 'нет даты')
          + ' · Изменена: ' + (item.last_modified || 'неизвестно')
          + ' · Скачиваний: ' + item.downloads
          + ' · ' + (item.gguf_tag ? 'GGUF по тегам' : 'GGUF не подтверждён');
        entry.append(link, details);
        container.appendChild(entry);
      }
      const foot = document.createElement('p');
      foot.className = 'hint';
      foot.textContent = data.note || '';
      if (data.scan_limited) foot.textContent += ' Поиск охватывает ограниченную выдачу; более старые записи могут не попасть в неё.';
      container.appendChild(foot);
    } catch (error) {
      container.textContent = 'Не удалось выполнить поиск: ' + (error.message || String(error));
    } finally {
      button.disabled = false;
    }
  }

  let busy = false;
  function render(data) {
    if (!attach()) return;
    find('modelUpdateMode').value = data.mode || 'notify';
    find('modelUpdateCheck').disabled = Boolean(data.busy);
    const rows = [
      'Состояние: ' + (labels[data.state] || data.state || '—'),
      'Рабочая модель: ' + (data.active_model || '—'),
      'В канале stable: ' + (data.available_model || '—'),
      'Версия: ' + (data.available_version || '—'),
      'Скачанный кандидат: ' + (data.candidate || 'нет'),
      'Проверка качества: обязательна, ещё не автоматизирована',
      'Автоматическое переключение: выключено'
    ];
    if (data.state === 'downloading' && data.total) {
      rows.push('Загрузка: ' + Math.round(100 * Number(data.downloaded || 0) / Number(data.total)) + '%');
    }
    if (data.error || data.last_error) rows.push('Ошибка: ' + (data.error || data.last_error));
    find('modelUpdateStatus').textContent = rows.join('\n');
  }

  async function refresh() {
    if (busy || !attach()) return;
    try {
      render(await api('/v1/model-updates'));
    } catch (error) {
      const status = find('modelUpdateStatus');
      if (status) status.textContent = error.message || String(error);
    }
  }

  async function save() {
    const select = find('modelUpdateMode');
    if (!select || busy) return;
    busy = true;
    select.disabled = true;
    try {
      render(await api('/v1/model-updates/config', {mode: select.value}));
    } catch (error) {
      find('modelUpdateStatus').textContent = 'Не удалось сохранить режим: ' + error.message;
    } finally {
      busy = false;
      select.disabled = false;
    }
  }

  async function check() {
    if (busy) return;
    busy = true;
    find('modelUpdateCheck').disabled = true;
    try {
      render(await api('/v1/model-updates/check', {download: true}));
    } catch (error) {
      find('modelUpdateStatus').textContent = 'Проверка не запущена: ' + error.message;
    } finally {
      busy = false;
      find('modelUpdateCheck').disabled = false;
    }
  }

  function init() {
    if (!attach()) return;
    refresh();
    // The page can stay open while the background download changes states.
    setInterval(refresh, 30000);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, {once: true});
  } else {
    init();
  }
})();
