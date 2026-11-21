# Separate, on-demand Scheduled Task: prepare large model assets without blocking builds.
# ASCII-only for Windows PowerShell 5.1. All state remains alongside Leshy.exe.
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$brain = Join-Path $repo 'dist\Leshy\LeshyBrain'
$root = Join-Path $brain 'runtime\model-provision'
$logDir = Join-Path $brain 'logs'
New-Item -ItemType Directory -Force -Path $root, $logDir | Out-Null
$logFile = Join-Path $logDir 'model-auto-provision.log'
$statePath = Join-Path $root 'state.json'
$mutexName = 'Local\LeshyModelProvision-' + ([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($repo))).Replace('=','').Replace('/','_').Replace('+','-')
if ($mutexName.Length -gt 240) { $mutexName = $mutexName.Substring(0,240) }
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$owned = $false
$transcript = $false

function Save-State([string]$Step,[string]$Outcome,[string]$Details) {
    $payload = [ordered]@{
        schema = 1
        step = $Step
        outcome = $Outcome
        details = $Details.Substring(0,[Math]::Min(700,$Details.Length))
        updated_at = (Get-Date).ToUniversalTime().ToString('o')
        model_path = (Join-Path $brain 'models\Inkling-2-Mini-original')
        offload_path = (Join-Path $brain 'offload\Inkling-2-Mini')
    }
    $temp = Join-Path $root ([Guid]::NewGuid().ToString('N')+'.tmp')
    $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temp -Encoding UTF8
    Move-Item -LiteralPath $temp -Destination $statePath -Force
    $stepFile = Join-Path $root ($Step+'.json')
    $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temp -Encoding UTF8
    Move-Item -LiteralPath $temp -Destination $stepFile -Force
}
function Is-RecentFailure([string]$Step) {
    $stepFile = Join-Path $root ($Step+'.json')
    if (-not (Test-Path -LiteralPath $stepFile -PathType Leaf)) { return $false }
    try {
        $prior = Get-Content -LiteralPath $stepFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($prior.step -ne $Step -or $prior.outcome -ne 'failed') { return $false }
        $stamp = [DateTimeOffset]::Parse([string]$prior.updated_at)
        return (([DateTimeOffset]::UtcNow - $stamp.ToUniversalTime()).TotalHours -lt 4)
    } catch { return $false }
}
function Has-OriginalCheckpoint {
    $model = Join-Path $brain 'models\Inkling-2-Mini-original'
    $manifestPath = Join-Path $model '.leshy-original.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath (Join-Path $model 'config.json') -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath (Join-Path $model 'tokenizer_config.json') -PathType Leaf)) { return $false }
    if (@(Get-ChildItem -LiteralPath $model -Filter '*.safetensors' -File -ErrorAction SilentlyContinue).Count -eq 0) { return $false }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$manifest.repository -ne 'thinkingmachines/Inkling-2-Mini') { return $false }
        $files = @($manifest.files)
        if ($files.Count -eq 0) { return $false }
        $modelRoot = [IO.Path]::GetFullPath($model).TrimEnd('\') + '\'
        foreach ($item in $files) {
            $candidate = [IO.Path]::GetFullPath((Join-Path $model ([string]$item.path)))
            if (-not $candidate.StartsWith($modelRoot,[StringComparison]::OrdinalIgnoreCase)) { return $false }
            $entry = Get-Item -LiteralPath $candidate -ErrorAction Stop
            if ([long]$entry.Length -ne [long]$item.bytes) { return $false }
        }
        return $true
    } catch { return $false }
}
function Has-InferSSD {
    $homes = @((Join-Path $brain 'runtime\inferssd'))
    if ($env:INFERSSD_HOME) { $homes += $env:INFERSSD_HOME }
    if ($env:LOCALAPPDATA) { $homes += (Join-Path $env:LOCALAPPDATA 'inferssd') }
    foreach ($home in $homes) {
        if (Test-Path -LiteralPath (Join-Path $home 'current\bin\inferssd.exe') -PathType Leaf) { return $true }
    }
    return [bool](Get-Command 'inferssd.exe' -ErrorAction SilentlyContinue)
}
try {
    try { $owned = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { return }
    if ((Test-Path -LiteralPath $logFile -PathType Leaf) -and (Get-Item -LiteralPath $logFile).Length -gt 10MB) {
        Move-Item -LiteralPath $logFile -Destination (Join-Path $logDir 'model-auto-provision.previous.log') -Force
    }
    Start-Transcript -LiteralPath $logFile -Append | Out-Null
    $transcript = $true
    Write-Host ('AUTO_MODEL_PREPARE ' + (Get-Date -Format o))

    # InferSSD official release installer is pinned. It does not switch the
    # current dense Qwen3 to InferSSD and does not download giant MoE GGUFs.
    if (-not (Has-InferSSD) -and -not (Is-RecentFailure 'inferssd')) {
        try {
            Save-State 'inferssd' 'running' 'Installing native Windows InferSSD from pinned upstream release.'
            $installer = Join-Path $root 'inferssd-install.ps1'
            $uri = 'https://github.com/wici-ai/inferssd/releases/download/261119.309/install.ps1'
            Invoke-WebRequest -Uri $uri -UseBasicParsing -OutFile $installer -TimeoutSec 120
            if ((Get-Item -LiteralPath $installer).Length -lt 500) { throw 'Upstream installer response too short' }
            $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            # Persist the official engine beside Leshy.exe. Environment
            # overrides are scoped to this task, never user-wide.
            $env:INFERSSD_HOME = Join-Path $brain 'runtime\inferssd'
            $env:INFERSSD_BIN_DIR = Join-Path $env:INFERSSD_HOME 'bin'
            & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer
            if ($LASTEXITCODE -ne 0) { throw ('Official InferSSD installer returned '+$LASTEXITCODE) }
            if (-not (Has-InferSSD)) { throw 'InferSSD executable not found after installation' }
            Save-State 'inferssd' 'installed' 'Native InferSSD installed. A verified supported MoE model is still required.'
            Write-Host 'INFERSSD_INSTALLED (no model switch)'
        } catch {
            Save-State 'inferssd' 'failed' ([string]$_.Exception.Message)
            Write-Warning ('INFERSSD_INSTALL_FAILED: '+$_.Exception.Message)
        }
    }

    # Only stage the requested Inkling original in the installed brain.
    # Keep 12 GiB of free disk after its ~20 GiB snapshot, plus transient cache.
    if (Has-OriginalCheckpoint) {
        Write-Host 'INKLING_MANIFEST_PRESENT: skip repeated large download.'
    } elseif (-not (Is-RecentFailure 'inkling')) {
        $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($brain))
        $freeGiB = [math]::Floor($drive.AvailableFreeSpace / 1GB)
        if ($freeGiB -lt 45) {
            Save-State 'inkling' 'failed' ("Insufficient free space: $freeGiB GiB, need >=45 GiB before download.")
            Write-Warning ("INKLING_NO_SPACE: $freeGiB GiB; no download started.")
        } else {
            try {
                Save-State 'inkling' 'running' 'Downloading original checkpoint, with proxy fallback.'
                $env:PIP_DEFAULT_TIMEOUT = '60'
                $env:PIP_RETRIES = '3'
                # User-confirmed HTTP CONNECT route to both PyPI and Hugging Face.
                # Only the child task inherits these values; do not change system/user proxy.
                $proxy = [string]$env:LESHY_HF_PROXY_URL
                if (-not $proxy) { $proxy = 'http://vpn.nidr.cyberhost.biz:7777' }
                # The proxy has been checked by the owner. Override only
                # this scheduled worker's process environment, never User/Machine.
                $env:HTTPS_PROXY = $proxy
                $env:HTTP_PROXY = $proxy
                $env:LESHY_HF_PROXY_URL = $proxy
                $env:PIP_NO_INPUT = '1'
                $prep = Join-Path $PSScriptRoot 'Prepare-InklingOriginal.ps1'
                & $prep -BrainDir $brain -Download
                if (-not $?) { throw 'Inkling download preparation returned failure' }
                if ($LASTEXITCODE -ne 0) { throw ('Inkling download returned '+$LASTEXITCODE) }
                if (-not (Has-OriginalCheckpoint)) { throw 'No verified checkpoint manifest after download' }
                Save-State 'inkling' 'downloaded' 'Original checkpoint downloaded; Qwen remains active.'
                Write-Host 'INKLING_DOWNLOADED: original weights staged in LeshyBrain'
            } catch {
                Save-State 'inkling' 'failed' ([string]$_.Exception.Message)
                Write-Warning ('INKLING_DOWNLOAD_FAILED: '+$_.Exception.Message)
            }
        }
    }
    # Run isolated inference dependencies only after the checkpoint is present.
    # They do not interfere with the main Python interpreter or active Qwen.
    $venvPy = Join-Path $brain 'runtime\inkling-original-venv\Scripts\python.exe'
    $depsMarker = Join-Path $root 'inference-dependencies.ready'
    if ((Has-OriginalCheckpoint) -and -not (Test-Path -LiteralPath $depsMarker -PathType Leaf) -and -not (Is-RecentFailure 'inference-dependencies')) {
        try {
            Save-State 'inference-dependencies' 'running' 'Installing torch/transformers in dedicated venv.'
            $env:PIP_DEFAULT_TIMEOUT = '60'
            $env:PIP_RETRIES = '3'
            $prep = Join-Path $PSScriptRoot 'Prepare-InklingOriginal.ps1'
            & $prep -BrainDir $brain -InstallInference
            if (-not $?) { throw 'Inkling inference dependencies failed' }
            if ($LASTEXITCODE -ne 0) { throw ('Inkling inference dependencies returned '+$LASTEXITCODE) }
            & $venvPy -c 'import torch, transformers, accelerate; print("INKLING_DEPS_OK")'
            if ($LASTEXITCODE -ne 0) { throw 'Inference packages not importable' }
            Set-Content -LiteralPath $depsMarker -Value (Get-Date -Format o) -Encoding ASCII
            Save-State 'inference-dependencies' 'installed' 'Original runtime dependencies installed. GPU compatibility not yet validated.'
        } catch {
            Save-State 'inference-dependencies' 'failed' ([string]$_.Exception.Message)
            Write-Warning ('INKLING_DEPS_FAILED: '+$_.Exception.Message)
        }
    }
    # Stage an officially tested GGUF MoE for actual local NVMe expert streaming.
    # This does not touch Inkling original or activate the experimental backend.
    # Before staging ~22 GiB of GGUF, keep sufficient room for HF Xet cache and
    # for the running Qwen installation.
    if (-not (Is-RecentFailure 'moe')) {
        try {
            $env:PYTHONPATH = Join-Path $repo 'src'
            $env:LESHY_VERSION_FILE = Join-Path $repo 'VERSION'
            # In a fresh environment, pip must reach PyPI through the user's
            # confirmed proxy before the local GGUF installer is prepared.
            if (-not $env:LESHY_HF_PROXY_URL) {
                $env:LESHY_HF_PROXY_URL = 'http://vpn.nidr.cyberhost.biz:7777'
            }
            $env:HTTP_PROXY = $env:LESHY_HF_PROXY_URL
            $env:HTTPS_PROXY = $env:LESHY_HF_PROXY_URL
            $env:PIP_DEFAULT_TIMEOUT = '60'
            if (-not (Test-Path -LiteralPath $venvPy -PathType Leaf)) {
                $prep = Join-Path $PSScriptRoot 'Prepare-InklingOriginal.ps1'
                & $prep -BrainDir $brain
                if (-not $?) { throw 'Isolated Python environment setup failed' }
            }
            $existing = & $venvPy -m leshy.moe_model_download status --brain-dir $brain
            if ($LASTEXITCODE -ne 0) { throw 'MoE GGUF status command failed' }
            $moe = (($existing | Out-String) | ConvertFrom-Json)
            if ([string]$moe.state -eq 'downloaded') {
                Write-Host ('INFERSSD_MOE_PRESENT: ' + $moe.file)
            } else {
                $moeDirectory = Join-Path $brain 'models\InferSSD-Qwen3.6-35B-A3B'
                if ($env:LESHY_INFERSSD_MODEL_DIR) {
                    $moeDirectory = $env:LESHY_INFERSSD_MODEL_DIR
                }
                New-Item -ItemType Directory -Force -Path $moeDirectory | Out-Null
                $moeDrive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot((Resolve-Path -LiteralPath $moeDirectory).Path))
                $freeMoEGiB = [math]::Floor($moeDrive.AvailableFreeSpace / 1GB)
                if ($freeMoEGiB -lt 45) {
                    Save-State 'moe' 'failed' ("Only $freeMoEGiB GiB free: need at least 45 GiB for GGUF and cache.")
                    Write-Warning ('INFERSSD_MOE_NO_SPACE: '+$freeMoEGiB+' GiB')
                } else {
                    Save-State 'moe' 'running' 'Downloading tested Qwen3.6-35B-A3B UD-Q4_K_XL GGUF locally.'
                    $env:PIP_DEFAULT_TIMEOUT = '60'
                    $env:PIP_RETRIES = '3'
                    # Store Xet staging cache on the SAME NVMe as the GGUF,
                    # including when this node uses a separate local model disk.
                    $env:HF_HOME = Join-Path $moeDirectory 'cache\huggingface'
                    New-Item -ItemType Directory -Force -Path $env:HF_HOME | Out-Null
                    & $venvPy -m leshy.moe_model_download download --brain-dir $brain
                    if ($LASTEXITCODE -ne 0) { throw ('InferSSD GGUF download exited with code '+$LASTEXITCODE) }
                    $after = & $venvPy -m leshy.moe_model_download status --brain-dir $brain
                    if ($LASTEXITCODE -ne 0) { throw 'Failed to confirm downloaded GGUF' }
                    $moe = (($after | Out-String) | ConvertFrom-Json)
                    if ([string]$moe.state -ne 'downloaded') { throw 'Downloaded MoE checkpoint failed manifest validation' }
                    Save-State 'moe' 'downloaded' ('Tested GGUF staged on local NVMe: '+$moe.file)
                    Write-Host ('INFERSSD_MOE_DOWNLOADED: '+$moe.file)
                }
            }
        } catch {
            Save-State 'moe' 'failed' ([string]$_.Exception.Message)
            Write-Warning ('INFERSSD_MOE_DOWNLOAD_FAILED: '+$_.Exception.Message)
        }
    }
    Write-Host 'AUTO_MODEL_PREPARE_END'
} catch {
    Write-Warning ('AUTO_MODEL_PREPARE_ERROR: '+$_.Exception.Message)
} finally {
    if ($transcript) { try { Stop-Transcript | Out-Null } catch {} }
    if ($owned) { try { $mutex.ReleaseMutex() | Out-Null } catch {} }
    $mutex.Dispose()
}
