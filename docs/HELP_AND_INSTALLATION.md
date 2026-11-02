# Леший: справочник, установка, развёртывание и сборка

Этот документ отвечает сразу на четыре вопроса:

1. Что Леший умеет сейчас.
2. Как с ним работать.
3. Как развернуть Лешего на Linux, macOS и Windows.
4. Как собрать собственную исполняемую версию.

> Важно про формулировку «любая версия ОС»: проект требует Python 3.11+. Поэтому буквально все исторические версии Linux/macOS/Windows поддержать невозможно. Универсальная инструкция ниже подходит для современных версий ОС, на которых можно установить Python 3.11+ и зависимости проекта. Для слишком старых систем рекомендуется контейнер, виртуальная машина или обновление ОС.

---

## 1. Быстрая справка внутри Лешего

В чате можно написать любую из команд/фраз:

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

Леший должен вернуть встроенный справочник без обращения к LLM.

---

## 2. Что Леший умеет сейчас

Ниже перечислены функции, которые присутствуют в текущем проекте. Отдельно отмечено, что считается стабильным, что развивается и что пока экспериментально.

### 2.1. Локальный чат

- локальная LLM;
- история диалогов;
- отдельные сессии;
- изоляция данных пользователей;
- Web UI;
- работа через локальную сеть при соответствующей настройке host/firewall/token.

### 2.2. Долговременная память

Команды:

```text
/remember текст
/memories
```

Память хранится локально и привязана к текущему пользователю.

### 2.3. Интернет-поиск и исследование

Явный запрос:

```text
/search ваш запрос
```

Леший также может сам определить, что запрос требует актуальных данных. Новые сведения не становятся доверенными автоматически. Используется цепочка:

```text
research -> candidate -> verification -> trusted
```

### 2.4. Управляемое самообучение

Самообучение можно включать и выключать. В выключенном состоянии фоновое автономное исследование запрещено, но прямой поиск пользователя остаётся доступен.

### 2.5. Telegram

Поддерживается Telegram long polling, поэтому бот может работать даже за NAT без входящего публичного порта.

Есть отдельные пользовательские пространства, команды статуса и переключения самообучения.

### 2.6. Browser Agent

Browser Platform использует:

- Browser Use — интеллектуальная работа с сайтами;
- Playwright — детерминированное выполнение браузерных сценариев.

Покупка, бронирование, оплата и другие необратимые операции должны проходить Human Approval Gate.

CAPTCHA, 2FA и другие защитные механизмы не обходятся.

### 2.7. Computer Use

В проекте есть отдельный модуль управления компьютером. Он развивается и не считается полностью завершённым для абсолютно всех приложений и сценариев.

### 2.8. Документы

Document Intelligence умеет работать с поддерживаемыми форматами, включая:

- PDF;
- DOCX;
- XLSX;
- PPTX;
- другие форматы, поддерживаемые MarkItDown.

### 2.9. Marketplace, MCP и Skills

Есть:

- Extension Marketplace;
- MCP v2 Tools Gateway;
- система Skills/модулей;
- изолированная установка расширений;
- проверка разрешений;
- проверка версии и лицензии;
- health-check.

### 2.10. Голос

В проекте есть ASR, TTS, голосовые настройки и голосовой UI. Голосовые возможности продолжают развиваться и могут зависеть от конкретной сборки и наличия микрофона/аудиоустройств.

### 2.11. Генерация изображений

Есть локальный модуль генерации изображений. Модель генерации может загружаться отдельно при первом использовании.

### 2.12. Аппаратное ускорение

Приоритет backend:

```text
GPU/Vulkan -> NPU/OpenVINO -> CPU
```

Если ускоритель не работает, предусмотрен fallback.

### 2.13. Диагностика

Есть:

- централизованные логи;
- отдельный журнал проблем;
- redaction токенов, паролей и других секретов;
- smoke-test сборки;
- runtime-диагностика.

### 2.14. Экспериментально: Аквариум Лешего

Аквариум разрабатывается отдельной веткой/PR. Это виртуальный питомец и симуляция пресноводной/морской экосистемы. Пока функция может отсутствовать в стабильной `main`-сборке.

---

## 3. Как правильно работать с Лешим

Лешию не нужны специальные формулировки. Пишите обычным языком.

Хорошо:

```text
Найди актуальную версию программы и сравни с установленной.
```

```text
Открой сайт, найди три подходящих варианта, но ничего не покупай без моего подтверждения.
```

```text
Запомни, что этот сервер используется только для тестов.
```

```text
Разбери этот документ и кратко перечисли риски.
```

Если действие опасное, финансовое, необратимое или меняет внешнюю систему, Леший должен запросить подтверждение.

---

# 4. Общие требования для всех ОС

Минимально требуется:

- 64-битная ОС;
- Python 3.11 или новее;
- Git;
- доступ в интернет для первой установки зависимостей и моделей;
- минимум несколько гигабайт свободного места;
- системный Edge/Chrome/Chromium для браузерных функций;
- достаточно RAM для выбранной LLM.

Основная модель первой рабочей сборки хранится отдельно от Git.

Рекомендуется использовать виртуальное окружение Python.

---

# 5. Linux

Инструкция подходит для большинства современных дистрибутивов. Отличается только способ установки системных пакетов.

## 5.1. Debian / Ubuntu / Linux Mint

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip build-essential cmake pkg-config
```

Если системный Python старее 3.11, установите Python 3.11+ через репозиторий дистрибутива, `pyenv`, пакет производителя или контейнер.

Проверка:

```bash
python3 --version
git --version
```

## 5.2. Fedora / RHEL / Rocky / AlmaLinux

```bash
sudo dnf install -y git python3 python3-pip gcc gcc-c++ make cmake pkgconf-pkg-config
```

Для старых RHEL-подобных систем может потребоваться отдельный Python 3.11+.

## 5.3. Arch / Manjaro

```bash
sudo pacman -S --needed git python python-pip base-devel cmake pkgconf
```

## 5.4. openSUSE

```bash
sudo zypper install git python311 python311-pip python311-devel gcc gcc-c++ make cmake pkg-config
```

## 5.5. Alpine Linux

Alpine использует musl вместо glibc, поэтому часть Python/LLM-зависимостей может собираться из исходников или не иметь готовых wheel.

```bash
sudo apk add git python3 py3-pip py3-virtualenv build-base cmake pkgconf linux-headers
```

Для максимально предсказуемой установки на сервере предпочтительнее Debian/Ubuntu/Fedora-подобная система.

## 5.6. Клонирование проекта

```bash
git clone https://github.com/jaster351/leshy-AI.git
cd leshy-AI
```

Для приватного репозитория GitHub должен быть настроен Ваш SSH key или токен.

## 5.7. Виртуальное окружение

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

## 5.8. Установка Лешего из исходников

```bash
python -m pip install -e .
```

CPU runtime:

```bash
python -m pip install "llama-cpp-python>=0.3.27,<0.4"
```

Если готового wheel нет, pip может собрать пакет из исходников. Тогда необходимы C/C++ compiler и CMake.

## 5.9. Тестовый запуск без LLM

```bash
export LESHY_LLM_BACKEND=echo
python -m leshy.web_server
```

Открыть:

```text
http://127.0.0.1:8765
```

Проверка API:

```bash
curl http://127.0.0.1:8765/v1/status
```

После проверки:

```bash
unset LESHY_LLM_BACKEND
```

## 5.10. Запуск с доступом из локальной сети

```bash
export LESHY_HOST=0.0.0.0
export LESHY_PORT=8765
python -m leshy.web_server
```

Если компьютер доступен не только в доверенной LAN, обязательно используйте firewall и API token.

## 5.11. systemd

Пример `/etc/systemd/system/leshy.service`:

```ini
[Unit]
Description=Leshy AI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=leshy
WorkingDirectory=/opt/leshy-AI
Environment=LESHY_HOST=0.0.0.0
Environment=LESHY_PORT=8765
ExecStart=/opt/leshy-AI/.venv/bin/python -m leshy.web_server
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Создание отдельного пользователя:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin leshy
```

После размещения файлов:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now leshy
sudo systemctl status leshy
```

Логи:

```bash
journalctl -u leshy -f
```

## 5.12. Обновление на Linux

```bash
cd /opt/leshy-AI
git pull
source .venv/bin/activate
python -m pip install -e .
sudo systemctl restart leshy
```

Перед обновлением рекомендуется сделать резервную копию `LeshyBrain`.

---

# 6. macOS

Поддерживаются Intel Mac и Apple Silicon при наличии Python 3.11+ и совместимых зависимостей.

## 6.1. Установка Command Line Tools

```bash
xcode-select --install
```

## 6.2. Установка Homebrew

Если Homebrew уже установлен, этот шаг пропускается.

После установки Homebrew:

```bash
brew update
brew install git python@3.12 cmake pkg-config
```

Проверка:

```bash
python3 --version
git --version
```

## 6.3. Клонирование

```bash
git clone https://github.com/jaster351/leshy-AI.git
cd leshy-AI
```

## 6.4. Виртуальное окружение

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
```

CPU runtime:

```bash
python -m pip install "llama-cpp-python>=0.3.27,<0.4"
```

## 6.5. Apple Silicon и Metal

На Apple Silicon можно собирать llama-cpp-python с Metal. Конкретные флаги зависят от версии llama.cpp/llama-cpp-python, поэтому перед нестандартной сборкой необходимо сверять актуальную документацию используемой версии.

Базовый CPU вариант остаётся самым универсальным.

## 6.6. Тестовый запуск

```bash
export LESHY_LLM_BACKEND=echo
python -m leshy.web_server
```

Открыть:

```text
http://127.0.0.1:8765
```

## 6.7. Запуск по сети

```bash
export LESHY_HOST=0.0.0.0
export LESHY_PORT=8765
python -m leshy.web_server
```

Не забудьте разрешить входящее соединение в macOS Firewall только для доверенной сети.

## 6.8. Автозапуск через launchd

Создайте `~/Library/LaunchAgents/ru.leshy.ai.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ru.leshy.ai</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/USERNAME/leshy-AI/.venv/bin/python</string>
    <string>-m</string>
    <string>leshy.web_server</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/USERNAME/leshy-AI</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LESHY_HOST</key><string>0.0.0.0</string>
    <key>LESHY_PORT</key><string>8765</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/leshy.out.log</string>
  <key>StandardErrorPath</key><string>/tmp/leshy.err.log</string>
</dict>
</plist>
```

Замените `USERNAME` и пути на реальные.

Загрузка:

```bash
launchctl load ~/Library/LaunchAgents/ru.leshy.ai.plist
```

Остановка:

```bash
launchctl unload ~/Library/LaunchAgents/ru.leshy.ai.plist
```

## 6.9. Обновление macOS

```bash
cd ~/leshy-AI
git pull
source .venv/bin/activate
python -m pip install -e .
```

После этого перезапустите LaunchAgent или запущенный процесс.

---

# 7. Windows

Подходит для современных 64-битных Windows, на которых устанавливается Python 3.11+.

## 7.1. Требования

Установите:

- Git;
- Python 3.11+;
- при сборке установщика — Inno Setup 6;
- Microsoft Edge/Chrome/Chromium для browser-функций.

При наличии `winget`:

```powershell
winget install Git.Git
winget install Python.Python.3.12
winget install JRSoftware.InnoSetup
```

После установки закройте и снова откройте PowerShell.

Проверка:

```powershell
python --version
git --version
```

## 7.2. Клонирование

```powershell
git clone https://github.com/jaster351/leshy-AI.git
cd leshy-AI
```

## 7.3. Виртуальное окружение

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
```

CPU runtime:

```powershell
python -m pip install "llama-cpp-python>=0.3.27,<0.4" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

## 7.4. Если PowerShell запрещает Activate.ps1

Для текущего пользователя:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Или можно не активировать venv, а всегда использовать:

```powershell
.\.venv\Scripts\python.exe -m leshy.web_server
```

## 7.5. Тестовый запуск

```powershell
$env:LESHY_LLM_BACKEND="echo"
python -m leshy.web_server
```

Открыть:

```text
http://127.0.0.1:8765
```

Очистить тестовую переменную:

```powershell
Remove-Item Env:LESHY_LLM_BACKEND -ErrorAction SilentlyContinue
```

## 7.6. Доступ по LAN

```powershell
$env:LESHY_HOST="0.0.0.0"
$env:LESHY_PORT="8765"
python -m leshy.web_server
```

Порт открывайте только в доверенной сети. Для недоверенной сети используйте firewall и `LESHY_API_TOKEN`.

---

# 8. Компиляция / сборка Windows

Windows имеет официальный проектный build pipeline.

Из корня репозитория:

```powershell
.\windows\build.ps1
```

Скрипт автоматически:

1. обновляет pip;
2. устанавливает проект;
3. устанавливает PyInstaller;
4. устанавливает CPU llama-cpp-python;
5. собирает `Leshy.exe`;
6. добавляет Web UI и VERSION;
7. выполняет frozen smoke-test `/v1/status`;
8. выполняет frozen smoke-test `/v1/chat`;
9. сохраняет пользовательский `LeshyBrain`;
10. собирает установщик Inno Setup.

Результат:

```text
dist\Leshy\Leshy.exe
dist\LeshySetup-<version>.exe
```

Если Inno Setup не установлен, финальный этап установщика завершится ошибкой.

---

# 9. Компиляция / сборка Linux

На Linux «компиляция» Python-проекта означает создание frozen-пакета с PyInstaller. PyInstaller собирает программу для той ОС и архитектуры, на которой он запущен.

Нельзя надёжно собрать Linux binary на Windows обычным PyInstaller. Собирать нужно на Linux или внутри Linux CI/container/VM.

## 9.1. Build-зависимости

Активируйте venv и установите:

```bash
python -m pip install -e ".[build]"
python -m pip install "llama-cpp-python>=0.3.27,<0.4"
```

## 9.2. Сборка

```bash
python -m PyInstaller --noconfirm --clean --onedir --console \
  --name Leshy \
  --paths src \
  --collect-all llama_cpp \
  --collect-all ddgs \
  --collect-all tzdata \
  --collect-all faster_whisper \
  --collect-all ctranslate2 \
  --collect-all av \
  --collect-all tokenizers \
  --collect-all huggingface_hub \
  --collect-all hf_xet \
  --collect-all playwright \
  --collect-all browser_use \
  --collect-all markitdown \
  --collect-all mcp \
  --collect-all mss \
  --collect-all pyautogui \
  --collect-all pypdf \
  --add-data "web:web" \
  --add-data "VERSION:." \
  windows/launcher.py
```

Результат:

```text
dist/Leshy/Leshy
```

Запуск:

```bash
./dist/Leshy/Leshy --host 127.0.0.1 --port 8765
```

Для LAN:

```bash
./dist/Leshy/Leshy --host 0.0.0.0 --port 8765
```

## 9.3. Проверка frozen-сборки

```bash
LESHY_LLM_BACKEND=echo ./dist/Leshy/Leshy --host 127.0.0.1 --port 8765 --no-browser
```

В другом терминале:

```bash
curl http://127.0.0.1:8765/v1/status
```

После этого проверьте чат через Web UI.

## 9.4. Перенос на другой Linux

PyInstaller не гарантирует переносимость между любыми версиями glibc. Для широкой совместимости рекомендуется собирать на достаточно старой поддерживаемой glibc-базе или делать отдельные сборки для целевых семейств дистрибутивов.

---

# 10. Компиляция / сборка macOS

Собирать macOS приложение нужно на macOS соответствующей архитектуры.

Intel и Apple Silicon желательно собирать отдельно.

## 10.1. Build-зависимости

```bash
source .venv/bin/activate
python -m pip install -e ".[build]"
python -m pip install "llama-cpp-python>=0.3.27,<0.4"
```

## 10.2. PyInstaller

```bash
python -m PyInstaller --noconfirm --clean --onedir --console \
  --name Leshy \
  --paths src \
  --collect-all llama_cpp \
  --collect-all ddgs \
  --collect-all tzdata \
  --collect-all faster_whisper \
  --collect-all ctranslate2 \
  --collect-all av \
  --collect-all tokenizers \
  --collect-all huggingface_hub \
  --collect-all hf_xet \
  --collect-all playwright \
  --collect-all browser_use \
  --collect-all markitdown \
  --collect-all mcp \
  --collect-all mss \
  --collect-all pyautogui \
  --collect-all pypdf \
  --add-data "web:web" \
  --add-data "VERSION:." \
  windows/launcher.py
```

Результат:

```text
dist/Leshy/Leshy
```

Запуск:

```bash
./dist/Leshy/Leshy --host 127.0.0.1 --port 8765
```

## 10.3. Подпись и notarization

Для локального использования подпись обычно не обязательна. Для распространения приложения другим пользователям macOS потребуются Developer ID, codesign и notarization Apple. Это отдельный release-процесс.

---

# 11. Проверка исходников перед сборкой

Для любой ОС:

```bash
python -m compileall -q src tests windows
python -m unittest discover -s tests -v
```

Если используется pytest:

```bash
pytest -q
```

Web JavaScript рекомендуется проверять через Node.js `node --check` для соответствующих файлов.

---

# 12. Где хранятся данные

Каноническое локальное хранилище — `LeshyBrain`.

Оно содержит:

```text
LeshyBrain/
  data/
  models/
  runtime/
  secrets/
  logs/
  workspace/
```

Не удаляйте `LeshyBrain` при обычном обновлении, если хотите сохранить память, настройки, модели, Telegram token и другие локальные данные.

---

# 13. Резервное копирование

Минимально необходимо сохранять `LeshyBrain`.

Linux/macOS:

```bash
tar -czf leshybrain-backup.tar.gz LeshyBrain
```

Windows PowerShell:

```powershell
Compress-Archive -Path .\LeshyBrain -DestinationPath .\LeshyBrain-backup.zip
```

Перед крупным обновлением рекомендуется остановить Лешего и только потом копировать данные.

---

# 14. Обновление из Git

Универсальный порядок:

```text
1. Остановить Лешего.
2. Сделать резервную копию LeshyBrain.
3. Выполнить git pull.
4. Активировать venv.
5. Выполнить pip install -e .
6. Запустить тесты.
7. Перезапустить Лешего.
```

Если используется frozen-сборка, после `git pull` её необходимо собрать заново.

---

# 15. Типичные ошибки

## Python старее 3.11

Проверьте:

```bash
python --version
```

или:

```bash
python3 --version
```

Нужен Python 3.11+.

## `ModuleNotFoundError: leshy`

Обычно проект не установлен в текущий venv:

```bash
python -m pip install -e .
```

## Не открывается порт 8765

Проверьте:

- запущен ли процесс;
- `LESHY_HOST`;
- `LESHY_PORT`;
- firewall;
- не занят ли порт другой программой.

## Web UI работает локально, но не по LAN

Убедитесь, что используется:

```text
LESHY_HOST=0.0.0.0
```

и firewall разрешает входящие подключения на выбранный порт только из нужной сети.

## Не работает браузерный модуль

Проверьте наличие Edge/Chrome/Chromium.

## Не загружается LLM

Проверьте свободное место, RAM, целостность модели и логи в `LeshyBrain/logs`.

## Ошибка сборки llama-cpp-python

Убедитесь, что установлены compiler, CMake и системные build tools. На Windows проще использовать официальный CPU wheel, указанный в проектной инструкции.

---

# 16. Безопасность

Никогда не публикуйте наружу порт Лешего без необходимости.

Для доступа не только из доверенной LAN используйте:

- firewall;
- `LESHY_API_TOKEN`;
- reverse proxy;
- TLS;
- VPN или Zero Trust доступ.

Не храните Telegram token, пароли и API keys в Git.

---

# 17. Краткая шпаргалка

Запуск из исходников:

```bash
python -m pip install -e .
python -m leshy.web_server
```

Тест без LLM:

Linux/macOS:

```bash
LESHY_LLM_BACKEND=echo python -m leshy.web_server
```

Windows:

```powershell
$env:LESHY_LLM_BACKEND="echo"
python -m leshy.web_server
```

Web UI:

```text
http://127.0.0.1:8765
```

Справочник в чате:

```text
help
```

или:

```text
помощь
```
