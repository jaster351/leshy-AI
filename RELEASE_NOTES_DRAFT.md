# Черновик примечаний к выпуску Leshy 1.10.12

## Browser Platform

- Добавлен универсальный Browser Agent: Browser Use для семантического веб-исследования и Playwright для детерминированных действий.
- Родная локальная LLM Лешего подключена к Browser Use без обязательного платного API.
- Read-only режим Browser Use теперь технически отключает click/input/send_keys/select_dropdown, произвольный JavaScript и локальный file I/O, а не полагается только на prompt.
- Добавлен Human Approval Gate: одноразовое подтверждение `mutate`, `commit` и `financial` действий; оплата требует суммы/валюты и отдельного подтверждения.
- Web UI показывает `Подтвердить` / `Отмена` и отдельную кнопку подтверждения оплаты.
- Результаты поиска вариантов сохраняются в SQLite и восстанавливаются после перезапуска.
- Browser state/cookies сохраняются в отдельном профиле; CAPTCHA/challenge/2FA не обходятся.
- Усилена доменная изоляция: явный allowlist нельзя расширить стартовым URL.

## Инструменты и документы

- Добавлен MCP v2 Tools Gateway с реестром permissions и approval для опасных инструментов.
- Добавлен изолированный MCP/Skills Extension Marketplace: pinned package, лицензия, HTTPS repository, отдельный venv, health-check и rollback неудачной установки.
- Добавлен Document Intelligence на MarkItDown с локальными fallback для PDF/текста.
- Existing Computer Use представлен отдельным модулем в настройках.

## Проверки

- Добавлены regression-тесты intent routing, approvals, restart recovery, read-only tool registry, domain allowlist, Document Intelligence, MCP Registry/Marketplace и локального Browser Use LLM bridge.
- CI дополнительно проверяет JavaScript Browser Approval, Marketplace и Module Settings.
