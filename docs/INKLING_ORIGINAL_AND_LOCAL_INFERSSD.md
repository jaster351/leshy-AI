# Inkling 2 Mini (оригинал) и локальная технология InferSSD

Оригинал: https://huggingface.co/thinkingmachines/Inkling-2-Mini
Технология SSD streaming: https://github.com/wici-ai/inferssd

## Важно: это два разных механизма

- Оригинальный Inkling 2 Mini распространяется в формате Safetensors (NVFP4/MXFP8). Для него создан отдельный загрузчик полного checkpoint и экспериментальная попытка Transformers/Accelerate disk-offload. Это не InferSSD, совместимость на RTX 3070 Laptop не подтверждена.
- WiCi InferSSD — механизм индексирования, выборочной загрузки и предвыборки MoE-экспертов из SSD в RAM/VRAM. Его движок работает с поддерживаемыми GGUF MoE, а не с оригинальным checkpoint Safetensors. Inkling 2 Mini пока нет в официальном списке протестированных GGUF InferSSD.
- Использование удалённой видеокарты и вычисления по Wi-Fi категорически не предусмотрены.

## 1. Скачать оригинал на SSD (Windows)

В корне репозитория Лешего выполните:

```powershell
.\windows\Prepare-InklingOriginal.ps1 -Download
```

Скачивает оригинальный snapshot Hugging Face (веса, конфигурацию, процессор, токенизатор) рядом с установленным Лешим в `dist\Leshy\LeshyBrain\models\Inkling-2-Mini-original`, а файлы SSD offload размещает в `dist\Leshy\LeshyBrain\offload\Inkling-2-Mini`. Если установленная сборка отсутствует, используется `LeshyBrain` в корне репозитория. Для нестандартной установки можно явно указать `-BrainDir 'C:\путь\к\LeshyBrain'`. Старые пути `LESHY_INKLING_MODEL_DIR` и `LESHY_SSD_OFFLOAD_DIR` в окружении пользователя переписываются на эти каталоги. Прерванная загрузка использует HF cache; активная GGUF не заменяется.

При региональном `403` или сетевой ошибке загрузчик сначала пробует прямое соединение, затем существующие пользовательские прокси HTTP CONNECT `vpn.nidr.cyberhost.biz:7777` и `vpn.usa.cyberhost.biz:7777`. При необходимости пользователь может указать `LESHY_HF_PROXY_URL` (полный HTTP(S) proxy URL), который будет первым резервным вариантом. Прокси применяются **только к дочернему процессу скачивания Hugging Face**, включая hf_xet; локальный чат/InferSSD остаются на loopback. Если ни один маршрут не позволяет загрузить модель, инструмент сообщает о реальном `403`, а не помечает модель установленной.

Скрипт создаёт отдельную виртуальную среду `LeshyBrain/runtime/inkling-original-venv` (Python 3.11) и устанавливает туда зависимости. Он больше не обновляет глобальные `click`, `huggingface_hub` и другие пакеты, необходимые браузеру Лешего. Пакеты, ранее изменённые в глобальном Python старым скриптом, автоматически не откатываются.

Статус без повторной загрузки:

```powershell
.\windows\Prepare-InklingOriginal.ps1
```

## 2. Экспериментальный original-format SSD offload

```powershell
.\windows\Prepare-InklingOriginal.ps1 -InstallInference
$env:LESHY_INKLING_RAM_GIB = '8'
$env:LESHY_INKLING_VRAM_GIB = '6'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
py -3.11 -m leshy.inkling_original ask --question 'Как Вас зовут?'
```

Проверка не гарантирует успешное размещение mixed NVFP4/MXFP8 оригинала на 8 ГБ VRAM; при нехватке памяти или неподдерживаемом формате должна выводиться реальная ошибка. После успешного вывода можно включить экспериментальный backend: `.\windows\Prepare-InklingOriginal.ps1 -Activate`. Windows-сборка запускает отдельный локальный Python sidecar: torch/transformers не требуется в самом PyInstaller EXE. Препаратор ставит только Python-модуль Лешего в внешний Python 3.11; зависимости ML устанавливаются флагом -InstallInference. При отказе оригинала fallback GGUF остаётся. Работа на RTX 3070 не подтверждена.

## 3. Реальный InferSSD для поддерживаемых GGUF MoE

Установите официальный Windows CLI из releases/README https://github.com/wici-ai/inferssd. Для выбранной GGUF-модели из каталога Лешего, поддерживаемой установленным InferSSD, можно включить:

```powershell
[Environment]::SetEnvironmentVariable('LESHY_LLM_BACKEND', 'inferssd', 'User')
```

По желанию укажите LESHY_INFERSSD_EXECUTABLE как полный путь к inferssd.exe. Леший запускает процесс inferssd serve только локально на 127.0.0.1, использует совместимый с OpenAI API протокол и при недоступности движка возвращается к штатному llama.cpp. Журнал: LeshyBrain/logs/inferssd-local.log.

InferSSD выбирает экспертов и организует SSD/RAM/VRAM-кэши сам: Python-код не имитирует этот механизм. Реальная скорость на RTX 3070 Laptop зависит от NVMe, PCIe, поддерживаемой GGUF модели и расхода памяти. При выключении режима верните LESHY_LLM_BACKEND=native и перезапустите Лешего.
