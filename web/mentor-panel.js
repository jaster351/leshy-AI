/* Owner-only mentorship view: all data come from the Leshy backend, never GitHub directly. */
(function () {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const make = (tag, cls, value) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined) node.textContent = value;
    return node;
  };
  let available = false;
  let busy = false;
  let lastFingerprint = "";

  function mount() {
    const nav = document.querySelector(".sidebar nav");
    const main = document.querySelector(".workspace .main");
    if (!nav || !main || byId("pane-mentor")) return;
    const button = make("button", "nav-item", "Наставник");
    button.type = "button";
    button.dataset.section = "mentor";
    button.id = "mentorNav";
    button.hidden = true;
    const before = nav.querySelector('[data-section="tasks"]');
    nav.insertBefore(button, before || null);

    const pane = make("section", "pane");
    pane.id = "pane-mentor";
    pane.appendChild(make("h2", "section-title", "Диалог с наставником"));
    pane.appendChild(make(
      "div", "section-subtitle",
      "Реальная переписка ChatGPT и локальной модели «Леший». Только для подтверждённого владельца."
    ));
    const bar = make("div", "toolbar");
    const status = make("span", "badge", "Проверка подключения…");
    status.id = "mentorStatus";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const refresh = make("button", "button secondary small", "Обновить");
    refresh.type = "button";
    refresh.addEventListener("click", () => void load());
    const source = make("a", "button secondary small", "Закрытый журнал");
    source.href = "https://github.com/jaster351/AI-mentor/issues/2";
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    bar.append(status, refresh, source);
    pane.appendChild(bar);
    const hint = make(
      "div", "hint",
      "Сообщения появляются только после фактической доставки через приватный канал. "
      + "Без ответа от модели переписка не считается состоявшейся."
    );
    hint.style.marginBottom = "15px";
    pane.appendChild(hint);
    const messages = make("div", "list");
    messages.id = "mentorMessages";
    messages.setAttribute("aria-live", "polite");
    pane.appendChild(messages);
    const system = byId("pane-system");
    main.insertBefore(pane, system || null);
    if (typeof titles !== "undefined") {
      titles.mentor = ["Наставник", "реальная переписка с ChatGPT"];
    }
    button.addEventListener("click", () => {
      if (!available) return;
      if (typeof showSection === "function") showSection("mentor");
      else {
        document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p === pane));
        document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("active", n === button));
      }
      void load();
    });
  }

  function render(result) {
    const status = byId("mentorStatus");
    const messages = byId("mentorMessages");
    if (!status || !messages) return;
    const states = {
      connected: "Канал подключён",
      not_configured: "Нет разрешённого GitHub-доступа",
      disabled: "Канал отключён",
      error: "Сбой обмена",
    };
    status.textContent = states[result.state] || "Нет данных";
    status.className = "badge " + (result.state === "connected" ? "good" : "warn");
    if (result.state === "error") status.title = result.error || "Ошибка получения ответов";
    else status.removeAttribute("title");
    const list = Array.isArray(result.messages) ? result.messages : [];
    const fingerprint = JSON.stringify([result.state, result.checked_at, list]);
    if (lastFingerprint === fingerprint) return;
    lastFingerprint = fingerprint;
    messages.replaceChildren();
    if (!list.length) {
      const empty = make("div", "empty", "Подтверждённых сообщений пока нет.");
      messages.append(empty);
      return;
    }
    for (const entry of list) {
      if (!entry || !["mentor", "leshy"].includes(entry.role)) continue;
      const item = make("article", "item");
      const label = entry.role === "leshy" ? "Леший" : "ChatGPT · наставник";
      item.appendChild(make("div", "item-title", label));
      let at = "";
      if (typeof entry.at === "string") {
        const d = new Date(entry.at);
        if (Number.isFinite(d.getTime())) at = d.toLocaleString();
      }
      item.appendChild(make("div", "item-meta", at));
      item.appendChild(make("div", "item-body", String(entry.text || "")));
      messages.appendChild(item);
    }
  }

  async function load() {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch("/v1/mentor/messages", {
        credentials: "same-origin", cache: "no-store",
        headers: { "Accept": "application/json" }
      });
      if (response.status === 401 || response.status === 403) {
        available = false;
        const nav = byId("mentorNav");
        if (nav) nav.hidden = true;
        return;
      }
      if (!response.ok) throw new Error("HTTP " + response.status);
      const payload = await response.json();
      available = true;
      const nav = byId("mentorNav");
      if (nav) nav.hidden = false;
      render(payload);
    } catch (_) {
      const status = byId("mentorStatus");
      if (status) status.textContent = "Леший или канал недоступен";
    } finally {
      busy = false;
    }
  }

  const init = () => {
    mount();
    void load();
    window.setInterval(() => {
      if (byId("pane-mentor")?.classList.contains("active")) void load();
    }, 20000);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once:true});
  else init();
})();
