# Леший: справочник, установка, развёртывание и сборка

Этот документ отвечает на четыре вопроса:

1. Что Леший умеет сейчас.
2. Как с ним работать.
3. Как развернуть Лешего на Linux, macOS и Windows.
4. Как собрать собственную исполняемую версию.

> Важно про формулировку «любая версия ОС»: проект требует Python 3.11+. Поэтому буквально все исторические версии Linux/macOS/Windows поддержать невозможно. Инструкция ниже универсальна для систем, на которых доступен Python 3.11+ и зависимости проекта. Для слишком старых ОС используйте VM/container с современной ОС или обновите систему.

---

## 1. Быстрая справка внутри Лешего

В чате можно написать:

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

Леший возвращает встроенный справочник без обращения к LLM.

---

## 2. Что Леший умеет сейчас

### 2.1. Локальный чат

- локальная LLM;
- история диалогов;
- отдельные сессии;
- изоляция данных пользователей;
- Web UI;
- работа по LAN при соответствующей настройке host/firewall/token.

### 2.2. Долговременная память

```text
/remember текст
/memories
```

### 2.3. Интернет-поиск и исследование

```text
/search ваш запрос
```

Новые сведения проходят цепочку:

```text
research -> candidate -> verification -> trusted
```

### 2.4. Управляемое самообучение

Самообучение можно включать и выключать. В выключенном состоянии автономное фоновое исследование не запускается, но прямой поиск пользователя остаётся доступен.

### 2.5. Telegram

Поддерживается Telegram long polling, отдельные пользовательские пространства, статус и управление самообучением.

### 2.6. Browser Agent

Browser Platform использует Browser Use + Playwright. Покупка, бронирование, оплата и другие необратимые операции проходят Human Approval Gate. CAPTCHA и 2FA не обходятся.

### 2.7. Computer Use

Есть отдельный модуль управления компьютером. Он развивается и пока не считается полностью завершённым для всех приложений и сценариев.

### 2.8. Документы

Document Intelligence работает с поддерживаемыми форматами, включая PDF, DOCX, XLSX и PPTX.

### 2.9. Marketplace, MCP и Skills

Есть Extension Marketplace, MCP v2 Tools Gateway, система Skills/модулей, изолированная установка расширений, проверка разрешений, лицензии, версии и health-check.

### 2.10. Голос

В проекте есть ASR, TTS, голосовые настройки и голосовой UI. Готовность отдельных голосовых функций зависит от конкретной сборки.

### 2.11. Генерация изображений

Есть локальный модуль генерации изображений. Модель может загружаться отдельно при первом использовании.

### 2.12. Аппаратное ускорение

```text
GPU/Vulkan -> NPU/OpenVINO -> CPU
```

При отказе ускорителя предусмотрен fallback.

### 2.13. Диагностика

Есть централизованные логи, журнал проблем, redaction секретов, smoke-test сборки и runtime-диагностика.

### 2.14. Экспериментально: Аквариум Лешего

Аквариум разрабатывается отдельной веткой/PR как виртуальный питомец и симуляция пресноводной/морской экосистемы. До слияния он может отсутствовать в стабильной `main`.

---

## 3. Как правильно работать с Лешим

Пишите обычным языком.

Примеры:

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

# 4. Общие требования

Минимально требуется:

- 64-битная ОС;
- Python 3.11+;
- Git;
- интернет для первой установки зависимостей/моделей;
- несколько гигабайт свободного места;
- Edge/Chrome/Chromium для browser-функций;
- RAM, достаточная для выбранной LLM.

Рекомендуется отдельное виртуальное окружение Python.

---

# 5. Linux

## 5.1. Debian / Ubuntu / Linux Mint

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip build-essential cmake pkg-config
```

## 5.2. Fedora / RHEL / Rocky / AlmaLinux

```bash
sudo dnf install -y git python3 python3-pip gcc gcc-c++ make cmake pkgconf-pkg-config
```

## 5.3. Arch / Manjaro

```bash
sudo pacman -S --needed git python python-pip base-devel cmake pkgconf
```

## 5.4. openSUSE

```bash
sudo zypper install git python311 python311-pip python311-devel gcc gcc-c++ make cmake pkg-config
```

## 5.5. Alpine Linux

```bash
sudo apk add git python3 py3-pip py3-virtualenv build-base cmake pkgconf linux-headers
```

Alpine использует musl, поэтому часть Python/LLM-зависимостей может собираться из исходников. Для наиболее предсказуемого сервера предпочтительнее Debian/Ubuntu/Fedora-подобная система.

## 5.6. Проверка Python

```bash
python3 --version
```

Нужен Python 3.11+. Если системный Python старее, используйте пакет репозитория, `pyenv`, контейнер или VM.

## 5.7. Клонирование

```bash
git clone https://github.com/jaster351/leshy-AI.git
cd leshy-AI
```

Для приватного репозитория настройте GitHub SSH key или token.

## 5.8. Виртуальное окружение

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

## 5.9. Установка из исходников

```bash
python -m pip install -e .
python -m pip install "llama-cpp-python>=0.3.27,<0.4"
```

Если wheel недоступен, понадобятся C/C++ compiler и CMake.

## 5.10. Тест без LLM

```bash
export LESHY_LLM_BACKEND=echo
python -m leshy.web_server
```

Открыть:

```text
http://127.0.0.1:8765
```

Проверка:

```bash
curl http://127.0.0.1:8765/v1/status
```

После проверки:

```bash
unset LESHY_LLM_BACKEND
```

## 5.11. Доступ из LAN

```bash
export LESHY_HOST=0.0.0.0
export LESHY_PORT=8765
python -m leshy.web_server
```

Для недоверенной сети используйте firewall и `LESHY_API_TOKEN`.

## 5.12. systemd

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

Создание пользователя:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin leshy
```

Запуск:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now leshy
sudo systemctl status leshy
```

Логи:

```bash
journalctl -u leshy -f
```

## 5.13. Обновление Linux

```bash
cd /opt/leshy-AI
git pull
source .venv/bin/activate
python -m pip install -e .
sudo systemctl restart leshy
```

Перед обновлением сохраните `LeshyBrain`.

---

# 6. macOS

Поддерживаются Intel Mac и Apple Silicon, если ОС позволяет установить Python 3.11+ и зависимости проекта.

## 6.1. Command Line Tools

```bash
xcode-select --install
```

## 6.2. Homebrew

После установки Homebrew:

```bash
brew update
brew install git python@3.12 cmake pkg-config
```

## 6.3. Проверка

```bash
python3 --version
git --version
```

## 6.4. Клонирование и venv

```bash
git clone https://github.com/jaster351/leshy-AI.git
cd leshy-AI
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
python -m pip install "llama-cpp-python>=0.3.27,<0.4"
```

## 6.5. Apple Silicon / Metal

На Apple Silicon можно использовать Metal-сборку llama.cpp. Флаги зависят от версии `llama-cpp-python`, поэтому перед нестандартной Metal-сборкой сверяйте документацию именно установленной версии. CPU-вариант является наиболее универсальным fallback.

## 6.6. Тестовый запуск

```bash
export LESHY_LLM_BACKEND=echo
python -m leshy.web_server
```

## 6.7. LAN

```bash
export LESHY_HOST=0.0.0.0
export LESHY_PORT=8765
python -m leshy.web_server
```

Разрешайте входящие подключения в macOS Firewall только для нужной сети.

## 6.8. launchd

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

Замените `USERNAME` и пути.

Загрузить:

```bash
launchctl load ~/Library/LaunchAgents/ru.leshy.ai.plist
```

Остановить:

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

Затем перезапустите LaunchAgent/процесс.

---

# 7. Windows

Подходит для современных 64-битных Windows, на которых доступен Python 3.11+.

## 7.1. Требования

- Git;
- Python 3.11+;
- Inno Setup 6 для создания установщика;
- Edge/Chrome/Chromium.

При наличии `winget`:

```powershell
winget install Git.Git
winget install Python.Python.3.12
winget install JRSoftware.InnoSetup
```

Перезапустите PowerShell и проверьте:

```powershell
python --version
git --version
```

## 7.2. Клонирование и venv

```powershell
git clone https://github.com/jaster351/leshy-AI.git
cd leshy-AI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
python -m pip install "llama-cpp-python>=0.3.27,<0.4" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

Если PowerShell блокирует `Activate.ps1`:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Либо запускайте Python напрямую:

```powershell
.\.venv\Scripts\python.exe -m leshy.web_server
```

## 7.3. Тест без LLM

```powershell
$env:LESHY_LLM_BACKEND="echo"
python -m leshy.web_server
```

После проверки:

```powershell
Remove-Item Env:LESHY_LLM_BACKEND -ErrorAction SilentlyContinue
```

## 7.4. LAN

```powershell
$env:LESHY_HOST="0.0.0.0"
$env:LESHY_PORT="8765"
python -m leshy.web_server
```

---

# 8. Официальная сборка Windows

Из корня репозитория:

```powershell
.\windows\build.ps1
```

Скрипт:

1. обновляет pip;
2. устанавливает проект;
3. устанавливает PyInstaller;
4. устанавливает CPU `llama-cpp-python`;
5. собирает `Leshy.exe`;
6. включает Web UI и `VERSION`;
7. выполняет frozen smoke-test `/v1/status`;
8. выполняет frozen smoke-test `/v1/chat`;
9. сохраняет пользовательский `LeshyBrain`;
10. создаёт Inno Setup installer.

Результат:

```text
dist\Leshy\Leshy.exe
dist\LeshySetup-<version>.exe
```

---

# 9. Сборка Linux

PyInstaller собирает пакет для той ОС/архитектуры, на которой запущен. Linux binary следует собирать на Linux или Linux CI/VM/container.

## 9.1. Build-зависимости

```bash
source .venv/bin/activate
python -m pip install -e ".[build]"
python -m pip install "llama-cpp-python>=0.3.27,<0.4"
```

## 9.2. Frozen-сборка

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
  packaging/launcher.py
```

Результат:

```text
dist/Leshy/Leshy
```

Запуск:

```bash
./dist/Leshy/Leshy --host 127.0.0.1 --port 8765
```

LAN:

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

## 9.4. Переносимость Linux

PyInstaller не гарантирует абсолютную совместимость между всеми версиями glibc. Для широкого распространения собирайте на достаточно старой поддерживаемой glibc-базе либо выпускайте отдельные артефакты для основных семейств дистрибутивов.

---

# 10. Сборка macOS

Собирать приложение нужно на macOS. Intel и Apple Silicon желательно собирать отдельно.

## 10.1. Build-зависимости

```bash
source .venv/bin/activate
python -m pip install -e ".[build]"
python -m pip install "llama-cpp-python>=0.3.27,<0.4"
```

## 10.2. Frozen-сборка

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
  packaging/launcher.py
```

Результат:

```text
dist/Leshy/Leshy
```

Запуск:

```bash
./dist/Leshy/Leshy --host 127.0.0.1 --port 8765
```

Для публичного распространения macOS-сборки потребуются Developer ID, `codesign` и notarization Apple.

---

# 11. Очень старые версии ОС

Если ОС настолько старая, что Python 3.11+ или зависимости проекта не устанавливаются:

### Linux

Используйте современный container/VM. На старом ядре Docker тоже может быть недоступен — тогда VM является более надёжным вариантом.

### macOS

Используйте VM с современной Linux/macOS-средой либо обновите macOS. Современный Python/LLM stack не гарантирует работу на давно снятых с поддержки версиях macOS.

### Windows

Для старых Windows, где Python 3.11+ не поддерживается, используйте VM с современной Windows/Linux или обновите ОС.

Это честнее и надёжнее, чем обещать совместимость, которую зависимости проекта физически не обеспечивают.

---

# 12. Проверка исходников перед сборкой

```bash
python -m compileall -q src tests windows packaging
python -m unittest discover -s tests -v
```

При необходимости:

```bash
pytest -q
```

Web JavaScript проверяется через Node.js `node --check` для соответствующих файлов.

---

# 13. LeshyBrain

Каноническое локальное хранилище:

```text
LeshyBrain/
  data/
  models/
  runtime/
  secrets/
  logs/
  workspace/
```

Не удаляйте `LeshyBrain` при обычном обновлении.

---

# 14. Резервное копирование

Перед крупным обновлением остановите Лешего.

Linux/macOS:

```bash
tar -czf leshybrain-backup.tar.gz LeshyBrain
```

Windows:

```powershell
Compress-Archive -Path .\LeshyBrain -DestinationPath .\LeshyBrain-backup.zip
```

---

# 15. Обновление из Git

Общий порядок:

```text
1. Остановить Лешего.
2. Сделать backup LeshyBrain.
3. git pull.
4. Активировать venv.
5. python -m pip install -e .
6. Запустить тесты.
7. Перезапустить Лешего.
```

Frozen-сборку после обновления исходников нужно собрать заново.

---

# 16. Типичные ошибки

## Python старее 3.11

```bash
python --version
```

или:

```bash
python3 --version
```

## `ModuleNotFoundError: leshy`

```bash
python -m pip install -e .
```

## Порт 8765 недоступен

Проверьте процесс, `LESHY_HOST`, `LESHY_PORT`, firewall и занятость порта.

## Web UI работает локально, но не из LAN

Используйте:

```text
LESHY_HOST=0.0.0.0
```

и разрешите порт в firewall только для доверенной сети.

## Browser Agent не запускается

Проверьте наличие Edge/Chrome/Chromium.

## LLM не загружается

Проверьте свободное место, RAM, модель и `LeshyBrain/logs`.

## `llama-cpp-python` не собирается

Проверьте compiler, CMake и build tools. На Windows предпочтительнее CPU wheel из инструкции выше.

---

# 17. Безопасность

Не публикуйте порт Лешего напрямую в интернет без необходимости.

Для внешнего доступа используйте:

- firewall;
- `LESHY_API_TOKEN`;
- reverse proxy;
- TLS;
- VPN/Zero Trust.

Не храните Telegram token, пароли и API keys в Git.

---

# 18. Краткая шпаргалка

Исходники:

```bash
python -m pip install -e .
python -m leshy.web_server
```

Web UI:

```text
http://127.0.0.1:8765
```

Справочник:

```text
help
```

или:

```text
помощь
```
