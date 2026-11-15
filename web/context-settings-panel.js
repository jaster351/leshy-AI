/* Общий регулятор контекста Web, Telegram, SuperAgent и других вызовов LLM. */
(function(){
  "use strict";
  const byId = id => document.getElementById(id);
  let saving = false;

  const style = document.createElement("style");
  style.textContent = [
    "#leshyContextSettings{margin-top:12px}",
    "#leshyContextSettings .ctx-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}",
    "#leshyContextSettings .ctx-value{font-weight:700;font-size:14px}",
    "#leshyContextSettings input[type=range]{width:100%;accent-color:var(--accent,#93ddb1)}",
    "#leshyContextSettings .ctx-labels{display:flex;justify-content:space-between;gap:12px;color:var(--muted,#91a89a);font-size:12px}",
    "#leshyContextSettings .ctx-explain{line-height:1.5;font-size:12px;color:var(--muted,#91a89a);margin-top:10px}",
    "#leshyContextSettings .ctx-feedback{min-height:20px;font-size:12px;margin-top:8px}"
  ].join("\n");
  document.head.appendChild(style);

  async function request(path, options) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", ...((options && options.headers) || {})},
      ...(options || {})
    });
    const data = await response.json().catch(() => ({error:"Некорректный ответ сервера"}));
    if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
    return data;
  }

  function ensurePanel(){
    if (byId("leshyContextSettings")) return true;
    const pane = byId("pane-settings");
    if (!pane) return false;
    const card = document.createElement("section");
    card.className = "card";
    card.id = "leshyContextSettings";
    card.innerHTML = [
      '<h3>Память диалога и контекст модели</h3>',
      '<div class="ctx-row"><label for="leshyContextMode">Режим</label>',
      '<select class="field" id="leshyContextMode" style="max-width:220px">',
      '<option value="auto">Авто (рекомендуется)</option>',
      '<option value="manual">Вручную</option></select>',
      '<strong class="ctx-value" id="leshyContextValue">Авто</strong></div>',
      '<div style="margin-top:12px"><input id="leshyContextLevel"',
      ' type="range" min="0" max="100" step="1" value="50"',
      ' aria-label="Сколько памяти использовать для контекста">',
      '<div class="ctx-labels"><span>Минимум</span><span>Безлимит ∞</span></div></div>',
      '<div class="ctx-explain">',
      'Авто подбирает бюджет по возможностям модели. При значении «Безлимит» ',
      'доступна вся сохранённая история, но в каждом запросе используется ',
      'ограниченное техническими возможностями модели окно. Ничего не удаляется.',
      '</div><div class="ctx-explain"><strong>Общая настройка:</strong> ',
      'Web, Telegram, SuperAgent. На автоматическое самообучение ',
      'и сохранение Knowledge не влияет.</div>',
      '<div class="ctx-explain" id="leshyContextBackendInfo"></div>',
      '<button id="leshyContextSave" class="button" type="button" style="margin-top:12px">Сохранить</button>',
      '<div class="ctx-feedback" id="leshyContextFeedback" aria-live="polite"></div>'
    ].join("");
    pane.appendChild(card);
    byId("leshyContextMode").addEventListener("change", updateView);
    byId("leshyContextLevel").addEventListener("input", updateView);
    byId("leshyContextSave").addEventListener("click", save);
    return true;
  }

  function updateView(){
    const mode = byId("leshyContextMode").value;
    const level = Number(byId("leshyContextLevel").value);
    byId("leshyContextLevel").disabled = mode === "auto";
    byId("leshyContextValue").textContent = mode === "auto"
      ? "Авто" : level === 100 ? "Безлимит ∞" : level === 0 ? "Минимум" : level + "%";
  }

  function render(data){
    byId("leshyContextMode").value = data.mode === "manual" ? "manual" : "auto";
    byId("leshyContextLevel").value = Number.isInteger(data.level) ? data.level : 50;
    updateView();
    const details = Array.isArray(data.backend_contexts) ? data.backend_contexts : [];
    const parts = details.map(entry =>
      entry.backend + ": " + entry.selected_tokens + " из " +
      entry.physical_tokens + " токенов за запрос"
    );
    byId("leshyContextBackendInfo").textContent = parts.join(" · ");
  }

  async function load(){
    if (!ensurePanel()) return;
    try {
      render(await request("/v1/context/settings"));
      byId("leshyContextFeedback").textContent = "";
    } catch(error){
      byId("leshyContextFeedback").textContent =
        "Не удалось загрузить настройки. Требуется вход владельца: " + error.message;
    }
  }

  async function save(){
    if (saving) return;
    saving = true;
    const button = byId("leshyContextSave");
    button.disabled = true;
    try {
      const data = await request("/v1/context/settings", {
        method: "POST",
        headers: {"X-Leshy-UI": "context-settings"},
        body: JSON.stringify({
          mode: byId("leshyContextMode").value,
          level: Number(byId("leshyContextLevel").value)
        })
      });
      render(data);
      byId("leshyContextFeedback").textContent =
        "Сохранено. Настройка применяется к новым запросам всех интерфейсов.";
    } catch(error){
      byId("leshyContextFeedback").textContent = "Не удалось сохранить: " + error.message;
    } finally {
      saving = false;
      button.disabled = false;
    }
  }

  function init(){
    if (ensurePanel()) load();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once:true});
  else init();
})();
