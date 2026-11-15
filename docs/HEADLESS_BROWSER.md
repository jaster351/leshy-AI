# Браузер Лешего: Ubuntu Server без GUI и Windows

Леший использует локальный Chromium, без облака. На Ubuntu/CentOS без X11/Wayland браузер работает в **headless**-режиме; на Windows при наличии рабочего стола по умолчанию запускается **видимое окно**.

## Ubuntu 24.04/26.04

В активном virtualenv:

```bash
python -m playwright install --with-deps chromium
python -c 'from leshy.system_browser import ensure_system_browser; print(ensure_system_browser(auto_install=False).executable)'
```

Chromium от Playwright находится обычно в `~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome` (либо `chrome-linux/chrome`). Он не обязан быть в `PATH`. При `PLAYWRIGHT_BROWSERS_PATH=/other/path` ищем там.

Для проверки CDP без запуска Лешего:

```bash
python - <<'PY'
from playwright.sync_api import sync_playwright
from leshy.system_browser import ensure_system_browser
from leshy.browser_launch_policy import chromium_launch_policy
browser = ensure_system_browser(auto_install=False)
policy = chromium_launch_policy()
print("Browser:", browser.executable, "headless:", policy.headless)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=browser.executable, headless=policy.headless,
                           chromium_sandbox=policy.chromium_sandbox, args=list(policy.args))
    print("CDP: OK", b.version)
    b.close()
PY
```

При запуске от `root` Chromium не умеет пользоваться стандартным sandbox, поэтому Леший добавляет исключение `--no-sandbox` **только для Linux/root**. Это снижает изоляцию от потенциально опасных страниц; для обычной работы, особенно с аккаунтами, рекомендуется запускать Лешего под отдельным непривилегированным пользователем.

На headless Ubuntu **нельзя открыть видимое окно для первичной SMS/2FA-авторизации «Ленты»**. Для интерактивного входа потребуется графическая сессия (например, временный удалённый рабочий стол); скрипт корректно сообщает об этом вместо CDP-ошибки. Автоматизированное чтение страниц работает headless, но оформление заказа по-прежнему требует разрешённых действий и подтверждений пользователя.

## Windows

Используется установленный Edge, Chrome или Chromium. По умолчанию браузер видимый; `LESHY_BROWSER_HEADLESS=1` включает скрытый режим. Текущие механизмы профилей и подтверждения финансовых действий сохранены.

## Сборка

`linux/build.sh` сам устанавливает Playwright Chromium перед PyInstaller; отключить скачивание для офлайн-сборки можно `LESHY_SKIP_BROWSER_INSTALL=1 bash linux/build.sh`. Запуск и поддержка системного Chromium не включены в «нейросетевую логику» Лешего. Каждый кластерный узел настраивает браузер локально и независимо.
