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
      'новая GGUF остаётся кандидатом. Существующая модель не удаляется.</div>'
    ].join('');
    const reference = find('modelLibraryCard') || find('localModelCard');
    if (reference && reference.parentNode) reference.insertAdjacentElement('afterend', card);
    else pane.appendChild(card);
    find('modelUpdateMode').onchange = save;
    find('modelUpdateCheck').onclick = check;
    return true;
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
