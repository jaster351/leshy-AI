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
.\windows\Prepare-InklingOriginal.ps1 -SsdRoot 'D:\AI-SSD' -Download
```

Скачивает весь оригинальный snapshot Hugging Face (веса, конфигурацию, процессор, токенизатор) в D:\AI-SSD\LeshyModels\Inkling-2-Mini-original. Поддерживается продолжение прерванной загрузки через HF cache. Манифест завершения записывается только в конце. GGUF остаётся активной моделью.

Статус без повторной загрузки:

```powershell
.\windows\Prepare-InklingOriginal.ps1 -SsdRoot 'D:\AI-SSD'
```

## 2. Экспериментальный original-format SSD offload

```powershell
.\windows\Prepare-InklingOriginal.ps1 -SsdRoot 'D:\AI-SSD' -InstallInference
$env:LESHY_INKLING_MODEL_DIR = 'D:\AI-SSD\LeshyModels\Inkling-2-Mini-original'
$env:LESHY_SSD_OFFLOAD_DIR = 'D:\AI-SSD\LeshyOffload\Inkling-2-Mini'
$env:LESHY_INKLING_RAM_GIB = '8'
$env:LESHY_INKLING_VRAM_GIB = '6'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
py -3.11 -m leshy.inkling_original ask --question 'Как Вас зовут?'
```

Проверка не гарантирует успешное размещение mixed NVFP4/MXFP8 оригинала на 8 ГБ VRAM; при нехватке памяти или неподдерживаемом формате должна выводиться реальная ошибка. Только после успешного вывода можно пробовать LESHY_LLM_BACKEND=inkling-original. В сборке PyInstaller дополнительные torch/transformers не включены: до интеграции внешнего Python-процесса использовать данный backend в сборке нельзя. Исходная ветка GGUF не меняется.

## 3. Реальный InferSSD для поддерживаемых GGUF MoE

Установите официальный Windows CLI из releases/README https://github.com/wici-ai/inferssd. Для выбранной GGUF-модели из каталога Лешего, поддерживаемой установленным InferSSD, можно включить:

```powershell
[Environment]::SetEnvironmentVariable('LESHY_LLM_BACKEND', 'inferssd', 'User')
```

По желанию укажите LESHY_INFERSSD_EXECUTABLE как полный путь к inferssd.exe. Леший запускает процесс inferssd serve только локально на 127.0.0.1, использует совместимый с OpenAI API протокол и при недоступности движка возвращается к штатному llama.cpp. Журнал: LeshyBrain/logs/inferssd-local.log.

InferSSD выбирает экспертов и организует SSD/RAM/VRAM-кэши сам: Python-код не имитирует этот механизм. Реальная скорость на RTX 3070 Laptop зависит от NVMe, PCIe, поддерживаемой GGUF модели и расхода памяти. При выключении режима верните LESHY_LLM_BACKEND=native и перезапустите Лешего.
