(function () {
  'use strict';
  const byId = (id) => document.getElementById(id);

  async function api(path, method, payload) {
    const options = {
      method: method || 'GET',
      headers: {'Content-Type': 'application/json', 'X-Leshy-UI': 'torrent-parser'}
    };
    if (method === 'POST') options.body = JSON.stringify(payload || {});
    const r = await fetch(path, options);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    return d;
  }

  function install() {
    const pane = byId('pane-settings');
    if (!pane || byId('torrentParserCard')) return;
    const card = document.createElement('div');
    card.id = 'torrentParserCard';
    card.className = 'card';
    card.style.marginTop = '12px';
    card.innerHTML = [
      '<h3>Parser for Plex — фильмы и музыка</h3>',
      '<p class="muted">Леший отправляет команды на NAS. Парсер сохраняет .torrent для отдельного торрент-клиента.</p>',
      '<label for="torrentParserAddress" style="display:block;margin:9px 0 5px">Адрес парсера</label>',
      '<input id="torrentParserAddress" class="field" type="url"',
      ' value="http://192.168.0.19:8766" autocomplete="off"',
      ' placeholder="http://192.168.0.19:8766/alice" style="width:100%">',
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">',
      '<button id="torrentParserSave" class="button" type="button">Сохранить адрес</button>',
      '<button id="torrentParserTest" class="button secondary" type="button">Проверить связь</button>',
      '<button id="torrentParserPair" class="button secondary" type="button">Привязать один раз</button>',
      '</div>',
      '<p id="torrentParserState" class="hint" aria-live="polite">Загружаю настройки…</p>',
      '<p class="hint">Первую привязку выполняют один раз в течение 15 минут после первого запуска парсера.',
      ' Ключ сохраняется в защищённом хранилище Лешего и не показывается на странице.',
      ' Повторная привязка возможна только после команды reset на NAS.</p>'
    ].join('');
    const nasCard = byId('nasSettingsCard');
    if (nasCard && nasCard.parentNode === pane) nasCard.insertAdjacentElement('afterend', card);
    else pane.appendChild(card);

    const state = byId('torrentParserState');
    const address = byId('torrentParserAddress');
    const render = (data) => {
      if (data.address) address.value = data.address;
      state.textContent = [
        data.configured ? 'Адрес настроен' : 'Адрес не сохранён',
        data.paired ? 'Привязка подтверждена' : 'Привязка не выполнена'
      ].join(' · ');
    };
    const onAction = async (buttonId, callback, busyText) => {
      const button = byId(buttonId);
      button.disabled = true;
      state.textContent = busyText;
      try { await callback(); }
      catch (e) { state.textContent = 'Ошибка: ' + e.message; }
      finally { button.disabled = false; }
    };
    byId('torrentParserSave').onclick = () => onAction('torrentParserSave', async () => {
      const data = await api('/v1/torrent-parser/config', 'POST', {address: address.value.trim()});
      render(data);
      state.textContent += ' · Сохранено';
    }, 'Сохраняю адрес…');
    byId('torrentParserTest').onclick = () => onAction('torrentParserTest', async () => {
      const data = await api('/v1/torrent-parser/test', 'POST');
      state.textContent = data.connected ? 'NAS отвечает · ' + (data.paired ? 'Привязано' : 'Требуется привязка')
                                         : 'NAS доступен, но сервис не подтвердил готовность';
    }, 'Проверяю подключение…');
    byId('torrentParserPair').onclick = () => onAction('torrentParserPair', async () => {
      const data = await api('/v1/torrent-parser/pair', 'POST');
      render(data);
      state.textContent = data.paired ? 'Привязка выполнена. Теперь можно попросить Лешего скачать фильм или музыку.' :
                                        'Не удалось привязаться';
    }, 'Получаю одноразовый ключ от NAS…');
    api('/v1/torrent-parser/config').then(render)
      .catch((e) => { state.textContent = 'Ошибка настроек: ' + e.message; });
  }

  function boot() {
    install();
    const observer = new MutationObserver(install);
    observer.observe(document.documentElement, {childList: true, subtree: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();