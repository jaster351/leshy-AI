# Леший AI

**Леший** — самостоятельный локальный AI-ассистент с LLM, долговременной памятью, Web UI, Telegram, интернет-поиском, Browser Agent, расширениями и управляемым самообучением.

Автор: **Тихонов Михаил Викторович**.

Проект развивается независимо от CineMax и «Гуслей». Интеграция с другими системами должна выполняться через стабильный публичный API/SDK или отдельные модули.

## Версия

Канонический номер версии хранится только в корневом файле [`VERSION`](VERSION). README не является источником номера версии.

---

# Справочник внутри Лешего

Чтобы узнать возможности и правила работы, напишите в чате:

```text
help
/help
помощь
справка
что ты умеешь
что ты можешь
как с тобой работать
как тобой пользоваться
```

Леший выдаст встроенный справочник без обращения к LLM.

Полная инструкция по работе, установке, развёртыванию и сборке:

**[`docs/HELP_AND_INSTALLATION.md`](docs/HELP_AND_INSTALLATION.md)**

---

# Что Леший умеет сейчас

## Локальный чат

- локальная LLM;
- история диалогов;
- отдельные пользовательские сессии;
- изоляция данных пользователей;
- Web UI;
- доступ по LAN при соответствующей настройке host/firewall/token.

## Долговременная память

```text
/remember текст
/memories
```

Память хранится локально и подмешивается только в контекст соответствующего пользователя.

## Интернет и актуальная информация

Явный поиск:

```text
/search запрос
```

Для новых знаний используется путь:

```text
research -> candidate -> verification -> trusted
```

Содержимое страницы или поискового сниппета не становится доверенным знанием автоматически.

## Управляемое самообучение

В Web UI есть постоянный переключатель **Самообучение ВКЛ/ВЫКЛ**.

- `ВЫКЛ` — автономное фоновое исследование запрещено;
- `ВКЛ` — фоновый worker может обрабатывать очередь исследований и учебный план;
- прямой пользовательский поиск разрешён независимо от фонового режима.

## Telegram

Telegram работает через long polling и подходит для установки за NAT.

Поддерживаются:

- Bot Token;
- разрешённые Telegram ID;
- отдельные пользовательские пространства;
- `/status`;
- `/learning_on`;
- `/learning_off`.

## Browser Platform

Browser Platform объединяет Browser Use и Playwright под единым Browser Agent и политикой безопасности.

- Browser Use выполняет интеллектуальное исследование динамических сайтов;
- Playwright выполняет детерминированные browser-workflow;
- запросы на покупку или бронирование сначала исследуют и сравнивают варианты;
- `mutate`, `commit` и `financial` проходят Human Approval Gate;
- оплата требует отдельного подтверждения суммы и валюты;
- browser state/cookies сохраняются отдельно;
- CAPTCHA, challenge и 2FA не обходятся.

Подробности: [`docs/BROWSER_AGENT.md`](docs/BROWSER_AGENT.md).

## Computer Use

Есть отдельный desktop-модуль управления компьютером. Он развивается и пока не считается полностью завершённым для всех приложений и сценариев.

## Документы

Document Intelligence использует MarkItDown и работает с поддерживаемыми форматами, включая:

- PDF;
- DOCX;
- XLSX;
- PPTX.

## Marketplace, MCP и Skills

В проекте есть:

- Extension Marketplace;
- MCP v2 Tools Gateway;
- система модулей/Skills;
- отдельные venv для расширений;
- проверка разрешений;
- проверка лицензии и версии;
- health-check и rollback.

## Голос

В проекте присутствуют ASR, TTS, голосовые настройки и голосовой UI. Голосовые функции продолжают развиваться и могут отличаться по готовности между сборками.

## Изображения

Есть локальный модуль генерации изображений. Модель генерации может загружаться отдельно при первом использовании.

## Аппаратное ускорение

Интерактивный runtime использует порядок:

```text
GPU/Vulkan -> NPU/OpenVINO -> CPU
```

При ошибке ускорителя работает автоматический fallback.

## Диагностика

Есть:

- централизованные runtime-логи;
- отдельный журнал проблем;
- redaction токенов, паролей, Bearer-заголовков и других секретов;
- smoke-test сборок;
- диагностика hardware/runtime.

## Экспериментально: Аквариум Лешего

Отдельно разрабатывается «Аквариум Лешего» — виртуальный питомец и симулятор пресноводной/морской экосистемы. До слияния соответствующей ветки/PR эта функция может отсутствовать в стабильной `main`.

---

# Как с Лешим работать

Пишите обычным человеческим языком. Специальный синтаксис нужен только для отдельных команд.

Примеры:

```text
Найди актуальную информацию о новой версии программы.
```

```text
Сравни три варианта и объясни, какой подходит лучше.
```

```text
Открой сайт, найди подходящие варианты, но ничего не покупай без моего подтверждения.
```

```text
Запомни, что этот сервер используется только для тестов.
```

Для опасных, финансовых или необратимых действий Леший должен запросить подтверждение.

---

# Основная модель

Основная модель первой рабочей сборки:

- репозиторий: `Qwen/Qwen3-8B-GGUF`;
- файл: `Qwen3-8B-Q4_K_M.gguf`;
- лицензия: Apache-2.0.

Модель не включается в Git. В Web UI предусмотрена её отдельная загрузка с проверкой SHA-256.

---

# Быстрый запуск из исходников

Требуется Python **3.11+**.

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
python -m pip install "llama-cpp-python>=0.3.27,<0.4"
python -m leshy.web_server
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
python -m pip install "llama-cpp-python>=0.3.27,<0.4" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
python -m leshy.web_server
```

После запуска Web UI обычно доступен по адресу:

```text
http://127.0.0.1:8765
```

Подробные команды для Debian/Ubuntu, Fedora/RHEL, Arch, openSUSE, Alpine, macOS Intel/Apple Silicon и Windows находятся в [`docs/HELP_AND_INSTALLATION.md`](docs/HELP_AND_INSTALLATION.md).

---

# Тестовый запуск без модели

Linux/macOS:

```bash
LESHY_LLM_BACKEND=echo python -m leshy.web_server
```

Windows:

```powershell
$env:LESHY_LLM_BACKEND="echo"
python -m leshy.web_server
```

---

# Доступ по локальной сети

Леший может слушать LAN-интерфейс:

```text
LESHY_HOST=0.0.0.0
LESHY_PORT=8765
```

Если сеть не полностью доверенная, используйте firewall и `LESHY_API_TOKEN`. Для внешнего доступа предпочтительны reverse proxy + TLS и/или VPN/Zero Trust.

---

# Сборка Windows

Официальный Windows build pipeline:

```powershell
.\windows\build.ps1
```

Он:

1. устанавливает build-зависимости;
2. собирает `Leshy.exe` через PyInstaller;
3. добавляет Web UI и VERSION;
4. запускает frozen smoke-test `/v1/status`;
5. запускает frozen smoke-test `/v1/chat`;
6. сохраняет `LeshyBrain` при публикации новой сборки;
7. собирает установщик Inno Setup.

Результат:

```text
dist\Leshy\Leshy.exe
dist\LeshySetup-<version>.exe
```

Для создания установщика требуется Inno Setup 6 (`ISCC.exe`).

---

# Сборка Linux и macOS

На Linux/macOS используется PyInstaller. Сборка должна выполняться на той ОС и архитектуре, для которой нужен результат.

Базово:

```bash
python -m pip install -e ".[build]"
python -m pip install "llama-cpp-python>=0.3.27,<0.4"
```

Далее выполняется PyInstaller-сборка с включением `web`, `VERSION` и runtime-зависимостей. Полная готовая команда приведена в [`docs/HELP_AND_INSTALLATION.md`](docs/HELP_AND_INSTALLATION.md).

PyInstaller не является полноценным cross-compiler: Windows-сборку следует делать на Windows, Linux-сборку — на Linux, macOS-сборку — на macOS.

---

# LeshyBrain

`LeshyBrain` — каноническое локальное пользовательское хранилище.

Минимальная структура:

```text
LeshyBrain/
  data/leshy.sqlite3
  models/
  runtime/
  secrets/
  logs/
  workspace/
```

Не удаляйте `LeshyBrain` при обычном обновлении, если хотите сохранить память, модели, настройки, Telegram token и другие локальные данные.

---

# Обновление

Общий порядок:

1. остановить Лешего;
2. сделать резервную копию `LeshyBrain`;
3. выполнить `git pull`;
4. активировать venv;
5. выполнить `python -m pip install -e .`;
6. запустить тесты;
7. перезапустить runtime.

Для frozen-сборки после обновления исходников нужно собрать приложение заново.

---

# Quality Gate

Готовая сборка должна пройти как минимум:

- Python import/compile checks;
- unit/regression tests;
- Web JavaScript checks;
- UI regression checks;
- frozen `/v1/status` smoke-test;
- frozen `/v1/chat` smoke-test;
- проверку итогового исполняемого пакета.

Сбой GitHub Actions до назначения runner (`runner_id=0`, без реально выполненных steps) является инфраструктурным сбоем, а не результатом тестирования кода.

---

# Документация

Основные документы:

- [`docs/HELP_AND_INSTALLATION.md`](docs/HELP_AND_INSTALLATION.md) — справочник, Linux/macOS/Windows, сборка и обновление;
- [`docs/BROWSER_AGENT.md`](docs/BROWSER_AGENT.md) — Browser Agent;
- [`docs/VOICE_UI.md`](docs/VOICE_UI.md) — голосовой UI;
- [`docs/VOICE_ENGINES.md`](docs/VOICE_ENGINES.md) — голосовые движки;
- [`docs/MCP_TOOLS_GATEWAY.md`](docs/MCP_TOOLS_GATEWAY.md) — MCP Tools Gateway;
- [`docs/DOCUMENT_INTELLIGENCE.md`](docs/DOCUMENT_INTELLIGENCE.md) — документы;
- [`docs/WINDOWS_TRAY_AUTOSTART_DATA_ROOT.md`](docs/WINDOWS_TRAY_AUTOSTART_DATA_ROOT.md) — Windows runtime и Data Root;
- [`docs/LOGGING.md`](docs/LOGGING.md) — логирование.

---

# Лицензия

Код репозитория распространяется по **GNU General Public License v2.0**, если для конкретного компонента явно не указано иное. Сторонние библиотеки и модели сохраняют собственные лицензии.
