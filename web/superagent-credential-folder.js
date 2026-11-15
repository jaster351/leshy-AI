(function () {
  'use strict';
  // Only the directory is configured here. Never ask for passwords in the UI.
  const get = id => document.getElementById(id);
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const css = el('style');
  css.textContent = [
    '#saCredentialFolderCard{max-width:880px;margin:12px 0 18px;padding:16px;border:1px solid #3c5550;border-radius:12px;background:#132026;color:#f0f6f3}',
    '#saCredentialFolderCard h3{margin:0 0 8px;font-size:16px}',
    '#saCredentialFolderCard p{font-size:12px;color:#bac9c3;line-height:1.55}',
    '#saCredentialFolderCard label{font-size:13px;display:block;margin-bottom:6px}',
    '#saCredentialFolderPath{width:100%;max-width:100%;box-sizing:border-box;background:#0d151b;color:#fff;border:1px solid #526560;border-radius:8px;padding:10px;font-size:14px}',
    '#saCredentialFolderCard .sa-folder-buttons{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}',
    '#saCredentialFolderCard button{border:1px solid #526560;border-radius:8px;padding:9px 13px;background:#234239;color:#fff;cursor:pointer}',
    '#saCredentialFolderCard button:disabled{opacity:.55;cursor:wait}',
    '#saCredentialFolderStatus{margin-top:10px;overflow-wrap:anywhere;font-size:12px}',
    '#saCredentialFolderStatus[aria-invalid="true"]{color:#ffc3bd}'
  ].join('');
  document.head.append(css);
  const headers = { 'X-Leshy-UI': 'superagent' };
  const api = async (method, body) => {
    const options = { method, headers: { ...headers }, credentials: 'same-origin', cache: 'no-store' };
    if (method === 'POST') {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch('/v1/superagent/credential-folder', options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.error || 'HTTP ' + response.status);
    return data;
  };
  function install() {
    const settings = get('pane-settings');
    if (!settings || get('saCredentialFolderCard')) return;
    const card = el('section');
    card.id = 'saCredentialFolderCard';
    card.append(el('h3', 'SuperAgent · папка с файлами доступа'));
    card.append(el('p', 'Укажите абсолютный путь к папке на компьютере, где запущен Леший. Он не будет искать файлы доступа за её пределами. Путь принадлежит только Вашей Web-сессии.'));
    const label = el('label', 'Путь к папке');
    label.htmlFor = 'saCredentialFolderPath';
    card.append(label);
    const input = el('input');
    input.id = 'saCredentialFolderPath';
    input.type = 'text';
    input.placeholder = 'Например: C:\\Leshy-AI\\Accounts или /srv/leshy/accounts';
    input.maxLength = 2048;
    input.autocomplete = 'off';
    input.spellcheck = false;
    card.append(input);
    const controls = el('div', undefined, 'sa-folder-buttons');
    const save = el('button', 'Сохранить');
    const clear = el('button', 'Сбросить');
    save.type = clear.type = 'button';
    controls.append(save, clear);
    card.append(controls);
    const status = el('div', 'Загрузка настройки…');
    status.id = 'saCredentialFolderStatus';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    card.append(status);
    card.append(el('p', 'Пароли сюда не вводятся и не копируются в Knowledge. Настройка папки сама по себе не включает подключение по SSH или RDP.'));
    const subtitle = settings.querySelector('.section-subtitle');
    if (subtitle) subtitle.after(card);
    else settings.prepend(card);
    const state = (message, error) => {
      status.textContent = message;
      status.setAttribute('aria-invalid', error ? 'true' : 'false');
    };
    let busy = false;
    const setBusy = value => {
      busy = value;
      save.disabled = value;
      clear.disabled = value;
      input.disabled = value;
    };
    const load = async () => {
      try {
        const data = await api('GET');
        input.value = data.path || '';
        state(data.configured ? (data.available ? 'Папка настроена и доступна.' : 'Сохранённая папка сейчас недоступна.') : 'Папка пока не настроена.', !data.available && data.configured);
      } catch (error) {
        state('Не удалось загрузить настройку: ' + error.message, true);
      }
    };
    save.onclick = async () => {
      if (busy) return;
      setBusy(true);
      state('Проверка папки…');
      try {
        const data = await api('POST', { path: input.value });
        input.value = data.path || '';
        state(data.configured ? 'Папка сохранена и проверена.' : 'Настройка очищена.');
      } catch (error) {
        state('Не удалось сохранить: ' + error.message, true);
      } finally { setBusy(false); }
    };
    clear.onclick = async () => {
      if (busy || !window.confirm('Убрать папку с файлами доступа из настроек SuperAgent?')) return;
      setBusy(true);
      try {
        await api('POST', { path: '' });
        input.value = '';
        state('Папка больше не настроена.');
      } catch (error) {
        state('Не удалось сбросить: ' + error.message, true);
      } finally { setBusy(false); }
    };
    const superagent = get('pane-superagent');
    if (superagent) {
      const shortcut = el('button', 'Настроить папку с учётными данными');
      shortcut.type = 'button';
      shortcut.className = 'adm-link';
      shortcut.addEventListener('click', () => {
        if (typeof window.showSection === 'function') window.showSection('settings');
        input.focus();
        card.scrollIntoView({ block: 'start', behavior: 'smooth' });
      });
      superagent.querySelector('.section-subtitle')?.after(shortcut);
    }
    void load();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
})();
