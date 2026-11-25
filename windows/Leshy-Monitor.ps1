param(
    [string]$Repository,
    [string]$BrainRoot,
    [ValidateRange(2,30)][int]$Interval = 3,
    [switch]$JsonLines
)
$ErrorActionPreference = 'Stop'
if ($JsonLines) { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); $ProgressPreference='SilentlyContinue' }
if (-not $Repository) { $Repository = Split-Path -Parent $PSScriptRoot }
$repo = [IO.Path]::GetFullPath($Repository)
if (-not $BrainRoot) { $BrainRoot = Join-Path $repo 'dist\Leshy\LeshyBrain' }
$brain = [IO.Path]::GetFullPath($BrainRoot)
$profile = Join-Path $repo '.autobuild\logs\build-performance-latest.log'
$stagePath = Join-Path $repo '.autobuild\profile-stage.tmp'
$taskName = 'Leshy AI - Git Build (15 min)'
$invariant = [Globalization.CultureInfo]::InvariantCulture
try { $Host.UI.RawUI.WindowTitle = 'Леший — монитор сборки и ИИ' } catch {}
$stages = @{'preflight'='Подготовка';'local-module-preflight'='Проверка модулей';'pip-dependencies'='Зависимости Python';'brand-icon'='Создание значка';'workspace-preparation'='Подготовка сборки';'pyinstaller'='Компиляция EXE (PyInstaller)';'frozen-manifest-preflight'='Проверка состава EXE';'knowledge-copy'='Копирование базы знаний';'frozen-smoke-test'='Проверка запуска';'inno-setup'='Создание установщика';'publish-or-stage'='Подготовка / публикация сборки'}
$previous = @{}
$speedSamples = New-Object 'System.Collections.Generic.Queue[System.Double]'
$cpuSamples = New-Object 'System.Collections.Generic.Queue[System.Double]'
$gpuSamples = New-Object 'System.Collections.Generic.Queue[System.Double]'
$rateTokens = 0.0; $rateSeconds = 0.0; $peakRate = $null
$completed = @(); $lastLogRead = [datetime]::MinValue
$smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $smi) {
    $fallback = Join-Path $env:ProgramFiles 'NVIDIA Corporation\NVSMI\nvidia-smi.exe'
    if (Test-Path -LiteralPath $fallback -PathType Leaf) { $smi = [pscustomobject]@{Source=$fallback} }
}
function Number($value) {
    if ($null -eq $value) { return $null }
    $n = 0.0
    if ([double]::TryParse(([string]$value).Trim().Replace(',','.'), [Globalization.NumberStyles]::Float, $invariant, [ref]$n) -and -not [double]::IsNaN($n) -and -not [double]::IsInfinity($n)) { return $n }
    return $null
}
function Metric($name,$value,$color='Cyan') {
    if ($null -eq $value) { Write-Host ('{0,-20} —' -f $name) -ForegroundColor DarkGray; return }
    $v = [Math]::Max(0,[Math]::Min(100,[double]$value)); $n=[int][Math]::Round($v/2.5)
    Write-Host ('{0,-20}' -f $name) -NoNewline
    Write-Host ('█' * $n) -ForegroundColor $color -NoNewline
    Write-Host ('░' * (40-$n)) -ForegroundColor DarkGray -NoNewline
    Write-Host (' {0,6:N1}%' -f $v)
}
function Fmt($value,$format='N1') { if ($null -eq $value) { return '—' }; return ([double]$value).ToString($format) }
function Add-Sample($queue,$value) {
    if ($null -eq $value) { $queue.Enqueue(-1.0) } else { $queue.Enqueue([double]$value) }
    while ($queue.Count -gt 30) { [void]$queue.Dequeue() }
}
function Spark($queue,$maximum) {
    if ($queue.Count -eq 0) { return '—' }
    $chars = '▁▂▃▄▅▆▇█'; $scale=[Math]::Max(1,[double]$maximum)
    return (-join @($queue.ToArray() | ForEach-Object { if ($_ -lt 0) {'·'} else {$chars[[int][Math]::Min(7,[Math]::Max(0,[Math]::Floor(8*$_/$scale)))]} }))
}
function Get-LocalSlots([int]$port) {
    $request = [Net.HttpWebRequest]::Create(('http://127.0.0.1:{0}/slots' -f $port))
    $request.Proxy=$null; $request.AllowAutoRedirect=$false; $request.Timeout=1500; $request.ReadWriteTimeout=1500
    $response=$request.GetResponse()
    try {
        if ([int]$response.StatusCode -ne 200) { throw 'slots unavailable' }
        $stream=$response.GetResponseStream(); $buffer=New-Object byte[] 8192; $memory=New-Object IO.MemoryStream
        try {
            while (($n=$stream.Read($buffer,0,$buffer.Length)) -gt 0) {
                if ($memory.Length+$n -gt 1048576) { throw 'slots limit' }
                $memory.Write($buffer,0,$n)
            }
            $json=[Text.Encoding]::UTF8.GetString($memory.ToArray()) | ConvertFrom-Json
            foreach ($slot in @($json)) {
                if ($null -ne $slot -and $slot.is_processing -is [bool]) {
                    $count=$slot.n_decoded
                    if ($null -eq $count -and $null -ne $slot.next_token) { $count=$slot.next_token.n_decoded }
                    [pscustomobject]@{id=$slot.id;task=$slot.id_task;active=$slot.is_processing;decoded=(Number $count)}
                }
            }
        } finally { $memory.Dispose(); $stream.Dispose() }
    } finally { $response.Dispose() }
}
function Get-CompletedTimings {
    $dir=Join-Path $brain 'logs'
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { return }
    foreach ($file in @(Get-ChildItem -LiteralPath $dir -Filter '*-server.log' -File -ErrorAction SilentlyContinue | Where-Object {$_.Name -match '^(vulkan|openvino|cpu|gpu|npu|llama)[a-z0-9_-]*-server\.log$'})) {
        $prompt=$null
        foreach ($line in @(Get-Content -LiteralPath $file.FullName -Tail 2000 -ErrorAction SilentlyContinue)) {
            if ($line -match '\bprompt eval time\s*=\s*([\d.,]+)\s*ms\s*/\s*(\d+)\s*tokens?') { $prompt=[long]$Matches[2]; continue }
            if ($line -match '(?<!prompt )\beval time\s*=\s*([\d.,]+)\s*ms\s*/\s*(\d+)\s*tokens?') {
                $ms=Number $Matches[1]; $count=[long]$Matches[2]
                if ($null -ne $ms -and $ms -gt 0) { [pscustomobject]@{Input=$prompt;Output=$count;Seconds=$ms/1000.0;Speed=$count/($ms/1000.0)} }
                $prompt=$null
            }
        }
    }
}
while ($true) {
    $now=Get-Date; $taskState='Не удалось прочитать'; $stage='—'; $buildAge=$null
    try { $taskState=[string](Get-ScheduledTask -TaskName $taskName).State } catch {}
    try {
        $rawStage=Get-Content -LiteralPath $stagePath -TotalCount 1 -ErrorAction Stop
        if ($stages.ContainsKey($rawStage)) {$stage=$stages[$rawStage]} else {$stage=$rawStage}
        $sample=@(Get-Content -LiteralPath $profile -Tail 20 | Where-Object {$_ -match 'SAMPLE stage='}) | Select-Object -Last 1
        if ($sample -and $sample -match '^([^ ]+) SAMPLE ') { $buildAge=($now-[DateTimeOffset]::Parse($Matches[1]).LocalDateTime).TotalSeconds }
    } catch {}
    $cpu=$null; $disk=$null; $ramFree=$null; $ramTotal=$null; $diskMbps=$null
    try { $cpu=Number (Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'").PercentProcessorTime } catch {}
    try { $d=Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -Filter "Name='_Total'"; $disk=Number $d.PercentDiskTime; $diskMbps=[double]$d.DiskBytesPersec/1MB } catch {}
    try { $os=Get-CimInstance Win32_OperatingSystem; $ramFree=[double]$os.FreePhysicalMemory/1MB; $ramTotal=[double]$os.TotalVisibleMemorySize/1MB } catch {}
    Add-Sample $cpuSamples $cpu
    $gpus=@()
    if ($smi) {
        try {
            $raw=@(& $smi.Source '--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu' '--format=csv,noheader,nounits' 2>$null)
            if ($LASTEXITCODE -eq 0) { $gpus=@($raw | ConvertFrom-Csv -Header Index,Name,Load,Used,Total,Temp) }
        } catch {}
    }
    if ($gpus.Count -gt 0) { Add-Sample $gpuSamples (Number $gpus[0].Load) } else { Add-Sample $gpuSamples $null }
    $active=0; $known=0; $tokensNow=0; $readOK=0; $servers=@(); $rates=@(); $newPrevious=@{}
    try {
        $prefix=$brain.TrimEnd('\')+'\'
        $servers=@(Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) -and $_.CommandLine -match '--host\s+"?127\.0\.0\.1"?(?:\s|$)' })
        foreach ($server in $servers) {
            if ($server.CommandLine -notmatch '--port\s+"?(\d{1,5})"?(?:\s|$)') { continue }
            $port=[int]$Matches[1]; if ($port -lt 1 -or $port -gt 65535) { continue }
            try {
                $slots=@(Get-LocalSlots $port); if ($slots.Count -eq 0) {continue}; $readOK++
                $measuredAt=[datetime]::UtcNow
                foreach ($slot in $slots) {
                    if (-not $slot.active) {continue}; $active++
                    if ($null -eq $slot.decoded -or $slot.decoded -lt 0) {continue}
                    $known++; $tokensNow+=$slot.decoded
                    if ($null -eq $slot.task -or $null -eq $slot.id) { continue }
                    $key='{0}:{1}:{2}:{3}' -f $server.ProcessId,$server.CreationDate,$slot.id,$slot.task
                    if ($previous.ContainsKey($key)) {
                        $prev=$previous[$key]; $seconds=($measuredAt-$prev.At).TotalSeconds; $delta=$slot.decoded-$prev.Count
                        if ($seconds -gt 0 -and $seconds -le 60 -and $delta -ge 0) {
                            $rates+=($delta/$seconds)
                            if ($delta -gt 0) { $rateTokens+=$delta; $rateSeconds+=$seconds }
                        }
                    }
                    $newPrevious[$key]=@{Count=$slot.decoded;At=$measuredAt}
                }
            } catch {}
        }
    } catch {}
    $previous=$newPrevious
    $liveRate=$null
    if ($readOK -gt 0 -and $readOK -eq $servers.Count) {
        if ($active -eq 0) { $liveRate=0.0 }
        elseif ($rates.Count -eq $active) { $liveRate=($rates | Measure-Object -Sum).Sum; if ($liveRate -gt 0 -and ($null -eq $peakRate -or $liveRate -gt $peakRate)) {$peakRate=$liveRate} }
    }
    Add-Sample $speedSamples $liveRate
    $averageRate=$null; if ($rateSeconds -gt 0) { $averageRate=$rateTokens/$rateSeconds }
    if (($now-$lastLogRead).TotalSeconds -ge 12) {
        try { $completed=@(Get-CompletedTimings); $lastLogRead=$now } catch { $completed=@(); $lastLogRead=$now }
    }
    if ($JsonLines) {
        $gpuMetrics=@(foreach ($g in $gpus) {
            [pscustomobject]@{index=$g.Index.Trim();name=$g.Name.Trim();load=(Number $g.Load);used=(Number $g.Used);total=(Number $g.Total);temperature=(Number $g.Temp)}
        })
        $models=@(foreach ($s in $servers) {
            $name=$null; $backend=$null
            if ($s.CommandLine -match '(?:^|\s)-m\s+(?:"([^"]+)"|(\S+))') {
                $modelPath=$Matches[1]; if (-not $modelPath) {$modelPath=$Matches[2]}
                $name=[IO.Path]::GetFileName($modelPath)
            }
            if ($s.ExecutablePath -match '(?i)[\\/](vulkan|openvino|cpu|cuda)(?:[\\/_-]|$)') {$backend=$Matches[1]}
            [pscustomobject]@{model=$name;backend=$backend}
        })
        $summary=$null
        if ($completed.Count -gt 0) {
            $outs=$completed | Measure-Object -Property Output -Sum -Average -Maximum
            $secs=($completed | Measure-Object -Property Seconds -Sum).Sum
            $inputs=@($completed | Where-Object {$null -ne $_.Input})
            $inTokens=$null; $allTokens=$null; $finishedRate=$null
            if ($inputs.Count -eq $completed.Count) {$inTokens=($inputs | Measure-Object -Property Input -Sum).Sum;$allTokens=$inTokens+$outs.Sum}
            if ($secs -gt 0) {$finishedRate=$outs.Sum/$secs}
            $summary=@{count=$completed.Count;output=$outs.Sum;input=$inTokens;total=$allTokens;mean_output=$outs.Average;max_output=$outs.Maximum;mean_speed=$finishedRate;max_speed=($completed|Measure-Object -Property Speed -Maximum).Maximum}
        }
        $displayNow=$null; if ($active -gt 0 -and $known -eq $active -and $readOK -eq $servers.Count) {$displayNow=$tokensNow}
        $ramPercent=$null; if ($null -ne $ramTotal -and $ramTotal -gt 0) {$ramPercent=100*($ramTotal-$ramFree)/$ramTotal}
        $state='unavailable';if ($readOK -gt 0) {if ($active -gt 0) {$state='generating'} else {$state='idle'};if ($readOK -lt $servers.Count) {$state='partial'}}
        $sample=@{timestamp=(Get-Date).ToUniversalTime().ToString('o');cpu=$cpu;disk=$disk;disk_mib_s=$diskMbps;ram_free_gib=$ramFree;ram_total_gib=$ramTotal;ram_percent=$ramPercent;gpus=$gpuMetrics;models=$models;task=$taskState;stage=$stage;build_age=$buildAge;generation_state=$state;tokens_now=$displayNow;rate=$liveRate;rate_mean=$averageRate;rate_peak=$peakRate;completed=$summary}
        [Console]::WriteLine(($sample | ConvertTo-Json -Depth 6 -Compress))
        [Console]::Out.Flush()
        Start-Sleep -Seconds $Interval
        continue
    }
    Clear-Host
    Write-Host '══════════════════════════════════════════════════════════════════' -ForegroundColor Cyan
    Write-Host '                  ЛЕШИЙ — МОНИТОР СБОРКИ И ИИ' -ForegroundColor Cyan
    Write-Host '══════════════════════════════════════════════════════════════════' -ForegroundColor Cyan
    $taskLabels=@{Running='Работает';Ready='Ожидает запуска';Disabled='Отключён';Queued='В очереди'}
    $taskDisplay=$taskState; if ($taskLabels.ContainsKey($taskState)) {$taskDisplay=$taskLabels[$taskState]}
    Write-Host ('Время: {0:HH:mm:ss}   |   Обновлятор: {1}' -f $now,$taskDisplay)
    if ($taskState -eq 'Running') { Write-Host ('Этап: '+$stage) -ForegroundColor Yellow } else { Write-Host ('Последний этап: '+$stage) -ForegroundColor DarkGray }
    if ($null -ne $buildAge -and $taskState -eq 'Running') { Write-Host ('Возраст записи профиля: {0:N0} сек.' -f $buildAge) -ForegroundColor DarkGray }
    Write-Host "`nРЕСУРСЫ СЕРВЕРА" -ForegroundColor Cyan
    Metric 'Процессор' $cpu Cyan; Metric 'Диск' $disk Green
    if ($null -ne $ramTotal -and $ramTotal -gt 0) { Metric 'Оперативная память' (100*($ramTotal-$ramFree)/$ramTotal) Magenta; Write-Host ('Свободно RAM: {0:N1} из {1:N1} ГБ' -f $ramFree,$ramTotal) }
    if ($null -ne $diskMbps) { Write-Host ('Скорость диска: {0:N1} МиБ/с' -f $diskMbps) }
    Write-Host "`nВИДЕОКАРТЫ NVIDIA" -ForegroundColor Cyan
    if ($gpus.Count -eq 0) { Write-Host 'nvidia-smi: показатели недоступны' -ForegroundColor DarkGray }
    foreach ($gpu in $gpus) {
        Write-Host ('GPU {0}: {1}' -f $gpu.Index.Trim(),$gpu.Name.Trim()) -ForegroundColor White
        $used=Number $gpu.Used; $total=Number $gpu.Total
        Metric 'Нагрузка GPU' (Number $gpu.Load) Green
        if ($null -ne $used -and $null -ne $total -and $total -gt 0) { Metric 'Видеопамять' (100*$used/$total) Yellow; Write-Host ('VRAM: {0:N2} / {1:N2} ГБ' -f ($used/1024),($total/1024)) }
        Write-Host ('Температура: '+(Fmt (Number $gpu.Temp) 'N0')+' °C')
    }
    Write-Host "`nЛЕШИЙ — ТОКЕНЫ" -ForegroundColor Cyan
    $state='Нет доступных счётчиков llama-server'
    if ($readOK -gt 0) { if ($active -gt 0) {$state='Генерация / обработка запроса'} else {$state='Простой'}; if ($readOK -lt $servers.Count) {$state+=' (часть счётчиков недоступна)'} }
    Write-Host ('Состояние: '+$state) -ForegroundColor Yellow
    $displayNow=$null; if ($active -gt 0 -and $known -eq $active -and $readOK -eq $servers.Count) {$displayNow=$tokensNow}
    Write-Host ('Токенов текущих ответов:  '+(Fmt $displayNow 'N0'))
    Write-Host ('Скорость сейчас:         '+(Fmt $liveRate)+' токен/с')
    Write-Host ('Средняя сессии монитора: '+(Fmt $averageRate)+' токен/с (интервалы с новыми токенами на слот)')
    Write-Host ('Макс. суммарная скорость: '+(Fmt $peakRate)+' токен/с (с запуска монитора)')
    if ($completed.Count -gt 0) {
        $outs=$completed | Measure-Object -Property Output -Sum -Average -Maximum
        $secs=($completed | Measure-Object -Property Seconds -Sum).Sum
        $max=($completed | Measure-Object -Property Speed -Maximum).Maximum
        $inputs=@($completed | Where-Object {$null -ne $_.Input})
        Write-Host ('Завершённых в хвостах журналов: '+$completed.Count)
        Write-Host ('Выходных токенов: {0:N0} | среднее/ответ: {1:N0} | максимум: {2:N0}' -f $outs.Sum,$outs.Average,$outs.Maximum)
        if ($inputs.Count -eq $completed.Count) { $in=($inputs | Measure-Object -Property Input -Sum).Sum; Write-Host ('Входных: {0:N0} | входных + выходных: {1:N0}' -f $in,($in+$outs.Sum)) } else { Write-Host 'Входные токены / полный расход: неполные данные' -ForegroundColor DarkGray }
        if ($secs -gt 0) { Write-Host ('По завершённым: средняя {0:N1} | макс. {1:N1} токен/с' -f ($outs.Sum/$secs),$max) }
    } else { Write-Host 'Точные итоги завершённых запросов: пока нет подходящих записей.' -ForegroundColor DarkGray }
    Write-Host "`nИСТОРИЯ НАГРУЗКИ (до 30 измерений)" -ForegroundColor Cyan
    Write-Host ('CPU   '+(Spark $cpuSamples 100)) -ForegroundColor Cyan
    Write-Host ('GPU 0 '+(Spark $gpuSamples 100)) -ForegroundColor Green
    $chartMax=1; if ($speedSamples.Count -gt 0) {$chartMax=[Math]::Max(1,($speedSamples.ToArray()|Measure-Object -Maximum).Maximum)}
    Write-Host ('Т/с   '+(Spark $speedSamples $chartMax)+'  шкала до '+(Fmt $chartMax)) -ForegroundColor Yellow
    Write-Host "`nCtrl+C или закрытие окна — завершить только монитор." -ForegroundColor DarkGray
    Start-Sleep -Seconds $Interval
}
