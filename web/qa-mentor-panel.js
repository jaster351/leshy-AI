/* Журнал реальных QA/mentor обращений к Лешему. Никаких демонстрационных данных. */
(() => {
  "use strict";
  const paneId = "pane-qa-mentor";
  const $ = id => document.getElementById(id);
  let refreshing = false;

  function elm(tag, cls, value) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined) node.textContent = String(value);
    return node;
  }

  function setup() {
    const main = document.querySelector("main.main");
    if (!main) return;
    if (!$("qaMentorStyle")) {
      const css = elm("style");
      css.id = "qaMentorStyle";
      css.textContent = [
        "#pane-qa-mentor{background:#212121;color:#eee}",
        "#pane-qa-mentor .qa-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:14px 0}",
        "#pane-qa-mentor .qa-button{border:1px solid #555;background:#333;color:#fff;padding:9px 13px;border-radius:10px;cursor:pointer}",
        "#pane-qa-mentor .qa-hint{color:#aaa;font-size:13px;line-height:1.5}",
        "#pane-qa-mentor .qa-items{max-width:840px;display:flex;flex-direction:column;gap:15px}",
        "#pane-qa-mentor .qa-item{padding:14px;border-radius:16px;border:1px solid #444}",
        "#pane-qa-mentor .qa-bubble{width:fit-content;max-width:92%;padding:12px 16px;border-radius:16px;margin:8px 0}",
        "#pane-qa-mentor .qa-ask{background:#353535;margin-right:auto}",
        "#pane-qa-mentor .qa-reply{background:#1c3b2b;margin-left:auto}",
        "#pane-qa-mentor .qa-who{font-size:12px;font-weight:700;margin-bottom:6px}",
        "#pane-qa-mentor .qa-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.5}",
        "#pane-qa-mentor .qa-meta{font-size:11px;color:#aaa;overflow-wrap:anywhere;margin-top:8px}",
        "#pane-qa-mentor .qa-fail{color:#ffaaa7}",
        "#pane-qa-mentor .qa-empty{padding:25px;border:1px dashed #666;text-align:center;border-radius:12px}",
        "@media(max-width:680px){#pane-qa-mentor .qa-bubble{max-width:100%}}"
      ].join("\n");
      document.head.appendChild(css);
    }
    if (!$(paneId)) {
      const pane = elm("section", "pane");
      pane.id = paneId;
      pane.appendChild(elm("h2", "section-title", "QA и наставник"));
      pane.appendChild(elm("p", "qa-hint", "Настоящие сообщения от AI-наставника и QA-тестировщика, ответы Лешего и статус API. Без примеров и симуляций."));
      const toolbar = elm("div", "qa-actions");
      const button = elm("button", "qa-button", "Обновить журнал");
      button.type = "button";
      button.onclick = refresh;
      const status = elm("span", "qa-hint", "Ожидание подтверждённых событий");
      status.id = "qaMentorStatus";
      toolbar.append(button, status);
      pane.appendChild(toolbar);
      const list = elm("div", "qa-items");
      list.id = "qaMentorEvents";
      pane.appendChild(list);
      main.appendChild(pane);
    }
    // Сохраняем обычный чат нетронутым, включая обновление истории и контекста.
    if (typeof titles !== "undefined") titles["qa-mentor"] = ["QA и наставник", "живой журнал"];
    for (const [menuId, buttonId] of [["profileMenu", "qaMentorProfile"], ["chatMoreMenu", "qaMentorMore"]]) {
      const menu = $(menuId);
      if (!menu || $(buttonId)) continue;
      const button = elm("button", "", "QA и наставник");
      button.id = buttonId;
      button.type = "button";
      button.onclick = open;
      menu.appendChild(button);
    }
    const nav = document.querySelector(".sidebar nav");
    if (nav && !$("qaMentorNav")) {
      const button = elm("button", "nav-item", "QA и наставник");
      button.id = "qaMentorNav";
      button.type = "button";
      button.onclick = open;
      nav.appendChild(button);
    }
  }

  function open() {
    setup();
    if (typeof showSection === "function") showSection("qa-mentor");
    else {
      document.body.classList.add("full");
      document.querySelectorAll(".pane").forEach(item => item.classList.toggle("active", item.id === paneId));
      const heading = $("pageTitle");
      if (heading) heading.textContent = "QA и наставник";
    }
    refresh();
  }

  function date(value) {
    const stamp = Date.parse(value || "");
    return Number.isFinite(stamp) ? new Date(stamp).toLocaleString("ru-RU") : "время неизвестно";
  }

  function row(event, n) {
    const card = elm("article", "qa-item");
    const role = event.role === "qa" ? "QA-тестировщик" : "AI-наставник";
    const question = elm("div", "qa-bubble qa-ask");
    question.append(elm("div", "qa-who", role + " · № " + n),
                    elm("div", "qa-text", event.request || ""),
                    elm("div", "qa-meta", date(event.started_at)));
    const response = elm("div", "qa-bubble qa-reply");
    response.append(elm("div", "qa-who", "Леший · " + (event.status === "answered" ? "ответ" : "ошибка связи")),
                    elm("div", event.status === "answered" ? "qa-text" : "qa-text qa-fail",
                       event.status === "answered" ? (event.answer || "(пустой ответ)") :
                       ("Запрос завершился ошибкой: " + (event.error_type || "неизвестно"))));
    card.append(question, response,
      elm("div", "qa-meta",
        "API: " + (event.status === "answered" ? "получен ответ" : "ошибка") +
        " · QA-вердикт: не оценено" +
        " · " + Number(event.elapsed_ms || 0) + " мс" +
        " · сессия: " + (event.session_id || "не указана") +
        " · " + date(event.finished_at) +
        " · ID " + String(event.id || "")));
    return card;
  }

  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      const res = await fetch("/v1/qa-mentor/events?limit=100", {
        method: "GET", credentials: "same-origin", cache: "no-store",
        headers: {"Accept": "application/json"}
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(
        [401,403].includes(res.status) ? "Нужна авторизация владельца" :
        (data.error || "HTTP " + res.status));
      const items = Array.isArray(data.items) ?
        data.items.filter(item => item.source === "live_api") : [];
      $("qaMentorStatus").textContent =
        (data.chat_enabled ? "Приём сообщений разрешён" : "Приём сообщений не включён") +
        " · фактических вызовов: " + items.length +
        (data.last_activity_at ? " · последний: " + date(data.last_activity_at) : "");
      $("qaMentorEvents").replaceChildren(...(
        items.length ? items.map((event, i) => row(event, i + 1)) :
        [elm("div", "qa-empty", "Реальных диалогов пока нет. Ничего не симулируется.")]
      ));
    } catch (error) {
      $("qaMentorStatus").textContent = "Нет подтверждённого доступа: " + error.message;
      $("qaMentorEvents").replaceChildren(
        elm("div", "qa-empty", "Журнал недоступен. Данные тестирования не выдумываются."));
    } finally { refreshing = false; }
  }

  function boot() {
    setup();
    // Профиль и меню верхней панели создаются после загрузки основной оболочки.
    let attempts = 0;
    const task = setInterval(() => { setup(); if (++attempts >= 24) clearInterval(task); }, 500);
    setInterval(() => {
      if (!document.hidden && $(paneId)?.classList.contains("active")) refresh();
    }, 5000);
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", boot, {once: true});
  else boot();
})();