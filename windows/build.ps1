param(
  [switch]$StageOnly
)

$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))

$version = (Get-Content VERSION -Raw).Trim()
if (-not ($version -match '^\d+\.\d+\.\d+')) { throw "Invalid VERSION: $version" }

# A packaged Windows copy always keeps its own LeshyBrain next to Leshy.exe.
# Ignore stale overrides inherited from an older shell.
Remove-Item Env:LESHY_BRAIN_DIR -ErrorAction SilentlyContinue
Remove-Item Env:LESHY_INSTALL_ROOT -ErrorAction SilentlyContinue
Remove-Item Env:LESHY_LEGACY_BRAIN_DIR -ErrorAction SilentlyContinue
Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue

$repoRoot = (Get-Location).Path
$autoRoot = Join-Path $repoRoot '.autobuild'
$packageDist = Join-Path $autoRoot 'package-dist'
$packageRoot = Join-Path $packageDist 'Leshy'
$packageWork = Join-Path $autoRoot 'pyinstaller-work'
$smokePackageRoot = Join-Path $autoRoot 'frozen-smoke-package'
$liveRoot = Join-Path $repoRoot 'dist\Leshy'
$liveExe = Join-Path $liveRoot 'Leshy.exe'
$brainRoot = Join-Path $liveRoot 'LeshyBrain'

# Diagnostic output is a single rotating file beside existing build logs.
$script:profileFile = Join-Path $autoRoot 'logs\build-performance-latest.log'
$script:profileMirrorFile = Join-Path $brainRoot 'logs\build-performance-latest.log'
$script:profileStageFile = Join-Path $autoRoot 'profile-stage.tmp'
$script:profileJob = $null
$script:profileActive = $false
$script:profileStage = ''
$script:profileWatch = $null
$script:profileTotal = [Diagnostics.Stopwatch]::StartNew()

function Log-BuildProfile([string]$Entry) {
  if (-not $script:profileActive) { return }
  $line = (Get-Date).ToString('o') + ' ' + $Entry
  for ($i = 0; $i -lt 3; $i++) {
    try { Add-Content -LiteralPath $script:profileFile -Value $line -Encoding UTF8 -ErrorAction Stop; break }
    catch { Start-Sleep -Milliseconds 80 }
  }
}
function Build-Stage([string]$Name) {
  if ($script:profileWatch) {
    $script:profileWatch.Stop()
    Log-BuildProfile "STAGE_END name=$($script:profileStage) seconds=$([math]::Round($script:profileWatch.Elapsed.TotalSeconds,2))"
  }
  $script:profileStage = $Name
  $script:profileWatch = [Diagnostics.Stopwatch]::StartNew()
  if ($script:profileActive) {
    try { Set-Content -LiteralPath $script:profileStageFile -Value $Name -Encoding ASCII -ErrorAction Stop } catch { }
    Log-BuildProfile "STAGE_START name=$Name"
  }
}
try {
  New-Item -Path (Join-Path $autoRoot 'logs') -ItemType Directory -Force -ErrorAction Stop | Out-Null
  New-Item -Path (Join-Path $brainRoot 'logs') -ItemType Directory -Force -ErrorAction Stop | Out-Null
  # Always truncate at the start of the next run, including failed previous builds.
  Set-Content -LiteralPath $script:profileFile -Value "LESHY BUILD PROFILE started=$((Get-Date).ToString('o')) version=$version machine=$env:COMPUTERNAME" -Encoding UTF8 -ErrorAction Stop
  # Reset user-facing copy at the start; populate it from the complete local log at the end.
  Copy-Item -LiteralPath $script:profileFile -Destination $script:profileMirrorFile -Force -ErrorAction Stop
  $script:profileActive = $true
  try {
    $hw = Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1
    Log-BuildProfile "HARDWARE cpu=$($hw.Name.Trim()) cores=$($hw.NumberOfCores) threads=$($hw.NumberOfLogicalProcessors)"
    foreach ($dev in @(Get-CimInstance Win32_DiskDrive -ErrorAction Stop)) {
      Log-BuildProfile "HARDWARE disk=$($dev.Model) interface=$($dev.InterfaceType) sizeGB=$([math]::Round([double]$dev.Size / 1GB,0))"
    }
  } catch { Log-BuildProfile "HARDWARE_UNAVAILABLE reason=$($_.Exception.Message)" }
  $script:profileJob = Start-Job -ArgumentList $script:profileFile,$script:profileStageFile,([Environment]::ProcessorCount) -ScriptBlock {
    param($Log,$StagePath,$CpuCount)
    while ($true) {
      try {
        $cpu = @(Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -ErrorAction Stop)
        $all = $cpu | Where-Object Name -eq '_Total' | Select-Object -First 1
        $cores = @($cpu | Where-Object { $_.Name -match '^\d+(,\d+)?$' } | Sort-Object PercentProcessorTime -Descending | Select-Object -First 4 | ForEach-Object { "$($_.Name):$($_.PercentProcessorTime)%" }) -join ','
        $disk = Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -Filter "Name='_Total'" -ErrorAction Stop
        $mem = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory -ErrorAction Stop
        $tasks = @(Get-CimInstance Win32_PerfFormattedData_PerfProc_Process -ErrorAction Stop | Where-Object { $_.Name -match '^(python|pyinstaller|iscc|msmpeng|powershell|pwsh|cmd|git|cl|link|ninja)(#\d+)?$' } | Sort-Object PercentProcessorTime -Descending | Select-Object -First 7)
        $summary = @($tasks | ForEach-Object { "$($_.Name):cpu=$([math]::Round([double]$_.PercentProcessorTime / [math]::Max(1,$CpuCount),1))%:ram=$([math]::Round([double]$_.WorkingSet / 1MB,0))MB" }) -join ','
        $stage = if (Test-Path -LiteralPath $StagePath) { [string](Get-Content -LiteralPath $StagePath -TotalCount 1 -ErrorAction SilentlyContinue) } else { 'startup' }
        $mbps = [math]::Round([double]$disk.DiskBytesPersec / 1MB,2)
        $latency = [math]::Round([double]$disk.AvgDisksecPerTransfer * 1000,2)
        $row = "{0} SAMPLE stage={1} cpuPercent={2} hotLogical=[{3}] diskBusyPercent={4} diskMBps={5} diskLatencyMs={6} freeRAM_MB={7} pageReadsSec={8} process=[{9}]" -f (Get-Date).ToString('o'),$stage,$all.PercentProcessorTime,$cores,$disk.PercentDiskTime,$mbps,$latency,$mem.AvailableMBytes,$mem.PageReadsPersec,$summary
        Add-Content -LiteralPath $Log -Value $row -Encoding UTF8 -ErrorAction Stop
      } catch {
        try { Add-Content -LiteralPath $Log -Value "$((Get-Date).ToString('o')) SAMPLE_UNAVAILABLE $($_.Exception.Message)" -Encoding UTF8 -ErrorAction SilentlyContinue } catch { }
      }
      Start-Sleep -Seconds 5
    }
  } -ErrorAction Stop
  Log-BuildProfile 'PROFILER_STARTED interval=5s'
} catch { Write-Warning "Build profiler failed to start (build unaffected): $($_.Exception.Message)" }
$script:buildOK = $false
try {
Build-Stage 'preflight'

# When AutoBuilder launches this script it owns live deployment/rollback itself.
# Direct/manual build.ps1 runs publish immediately to dist\Leshy.
if (-not $StageOnly) {
  try {
    $coreRunning = $false
    foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" -ErrorAction SilentlyContinue)) {
      $cmdline = [string]$proc.CommandLine
      if ($cmdline -and $cmdline.IndexOf('autobuilder-core.ps1',[System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        $coreRunning = $true
        break
      }
    }
    if ($coreRunning) {
      $StageOnly = $true
      Write-Host 'AutoBuilder core detected: staging package only; AutoBuilder will publish it safely to dist\Leshy.'
    }
  } catch { }
}

Build-Stage 'pip-dependencies'
python -m pip install --upgrade pip
python -m pip install -e "."
python -m pip install "pyinstaller>=6.11,<7"
python -m pip install "llama-cpp-python>=0.3.27,<0.4" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

# Browser engines are intentionally NOT downloaded or packaged here. Leshy uses
# an existing system Edge/Chrome/Chromium. If none exists on Windows, runtime
# browser code may install one through winget when a browser module is first used.

# .autobuild is disposable compiler workspace only. Finished output is published
# to dist\Leshy either here (manual build) or by AutoBuilder after it creates its
# rollback backup (StageOnly/AutoBuilder mode).
Build-Stage 'workspace-preparation'
Remove-Item -Recurse -Force $packageDist, $packageWork, $smokePackageRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $packageDist, $packageWork | Out-Null

Build-Stage 'pyinstaller'
python -m PyInstaller --noconfirm --clean --onedir --console `
  --name Leshy `
  --paths src `
  --distpath $packageDist `
  --workpath $packageWork `
  --collect-all llama_cpp `
  --collect-all ddgs `
  --collect-all tzdata `
  --collect-all faster_whisper `
  --collect-all ctranslate2 `
  --collect-all av `
  --collect-all tokenizers `
  --collect-all huggingface_hub `
  --collect-all hf_xet `
  --collect-all playwright `
  --collect-all browser_use `
  --exclude-module browser_use.llm.oci_raw `
  --exclude-module oci `
  --collect-all markitdown `
  --collect-all mcp `
  --collect-all mss `
  --collect-all pyautogui `
  --collect-all pypdf `
  --add-data "web;web" `
  --add-data "resources;resources" `
  --add-data "VERSION;." `
  windows/launcher.py

if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

# Public knowledge is data beside the executable; private LeshyBrain is not copied.
Build-Stage 'knowledge-copy'
Copy-Item -LiteralPath (Join-Path $repoRoot 'knowledge') -Destination $packageRoot -Recurse -Force

# Machine-readable self-knowledge and a separate human-readable history backup.
foreach ($name in @('capabilities.json', 'CHANGELOG.md')) {
  Copy-Item -LiteralPath (Join-Path $repoRoot $name) -Destination (Join-Path $packageRoot $name) -Force -ErrorAction Stop
}

$packageExe = Join-Path $packageRoot 'Leshy.exe'
if (-not (Test-Path -LiteralPath $packageExe)) { throw "PyInstaller did not create $packageExe" }

function Normalize-PathText {
  param([string]$Value)
  if (-not $Value) { return '' }
  try { return [System.IO.Path]::GetFullPath($Value).TrimEnd('\').ToLowerInvariant() } catch { return '' }
}

function Get-FreeTcpPort {
  $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  try {
    $listener.Start()
    return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
  } finally {
    $listener.Stop()
  }
}

function Invoke-FrozenSmokeTest {
  param(
    [Parameter(Mandatory=$true)][string]$SourcePackageRoot,
    [Parameter(Mandatory=$true)][string]$SmokePackageRoot
  )

  if (Test-Path -LiteralPath $SmokePackageRoot) {
    Remove-Item -LiteralPath $SmokePackageRoot -Recurse -Force
  }
  New-Item -ItemType Directory -Force -Path $SmokePackageRoot | Out-Null
  Copy-Item -Path (Join-Path $SourcePackageRoot '*') -Destination $SmokePackageRoot -Recurse -Force

  $executable = Join-Path $SmokePackageRoot 'Leshy.exe'
  if (-not (Test-Path -LiteralPath $executable)) {
    throw "Frozen smoke package does not contain Leshy.exe: $executable"
  }

  # Launcher intentionally keeps LeshyBrain next to Leshy.exe. Running smoke from
  # a disposable full package copy verifies that exact production behaviour while
  # preventing test data from contaminating the package used by the installer.
  $port = Get-FreeTcpPort
  $baseUrl = "http://127.0.0.1:$port"
  $oldBackend = $env:LESHY_LLM_BACKEND
  $oldEmbeddings = $env:LESHY_EMBEDDINGS_ENABLED
  $oldHeadless = $env:LESHY_BROWSER_HEADLESS
  $oldToken = $env:LESHY_API_TOKEN
  $oldSupportBootstrap = $env:LESHY_SUPPORT_BOOTSTRAP
  $process = $null
  $smokeStdout = Join-Path $autoRoot 'logs\frozen-smoke.stdout.log'
  $smokeStderr = Join-Path $autoRoot 'logs\frozen-smoke.stderr.log'
  Remove-Item -LiteralPath $smokeStdout, $smokeStderr -Force -ErrorAction SilentlyContinue

  try {
    $env:LESHY_LLM_BACKEND = 'echo'
    $env:LESHY_EMBEDDINGS_ENABLED = '0'
    $env:LESHY_BROWSER_HEADLESS = '1'
    $env:LESHY_API_TOKEN = ''
    $env:LESHY_SUPPORT_BOOTSTRAP = '0'

    Write-Host "FROZEN SMOKE: starting $executable on $baseUrl"
    $process = Start-Process -FilePath $executable -ArgumentList @(
      '--host', '127.0.0.1',
      '--port', [string]$port,
      '--no-browser'
    ) -PassThru -WindowStyle Hidden -RedirectStandardOutput $smokeStdout -RedirectStandardError $smokeStderr

    $deadline = (Get-Date).AddSeconds(60)
    $status = $null
    do {
      if ($process.HasExited) {
        # Preserve the actual exception instead of reporting only exit code 1.
        $stderrTail = @()
        if (Test-Path -LiteralPath $smokeStderr) {
          $stderrTail = @(Get-Content -LiteralPath $smokeStderr -Tail 60 -ErrorAction SilentlyContinue)
        }
        $stdoutTail = @()
        if (Test-Path -LiteralPath $smokeStdout) {
          $stdoutTail = @(Get-Content -LiteralPath $smokeStdout -Tail 15 -ErrorAction SilentlyContinue)
        }
        $details = (($stderrTail + $stdoutTail) -join [Environment]::NewLine).Trim()
        if ($details.Length -gt 6000) { $details = $details.Substring($details.Length - 6000) }
        throw "Frozen Leshy exited before /v1/status became ready (exit code $($process.ExitCode)). Logs: $smokeStderr ; $smokeStdout $details"
      }
      try {
        $status = Invoke-RestMethod -Uri "$baseUrl/v1/status" -Method Get -TimeoutSec 5
        if ($status) { break }
      } catch {
        Start-Sleep -Milliseconds 500
      }
    } while ((Get-Date) -lt $deadline)

    if (-not $status) { throw 'Frozen /v1/status did not become ready within 60 seconds' }
    if ([string]$status.version -ne $version) {
      throw "Frozen /v1/status version mismatch: expected $version, got $($status.version)"
    }

    $chatBody = @{ message = 'frozen smoke'; owner_id = 'windows-build-smoke' } | ConvertTo-Json -Compress
    $chat = Invoke-RestMethod -Uri "$baseUrl/v1/chat" -Method Post -ContentType 'application/json; charset=utf-8' -Body $chatBody -TimeoutSec 30
    if (-not $chat) { throw 'Frozen /v1/chat returned an empty response' }
    if (-not ([string]$chat.session_id).Trim()) { throw 'Frozen /v1/chat did not return session_id' }
    if (-not ([string]$chat.answer).Trim()) { throw 'Frozen /v1/chat did not return answer' }

    Write-Host "FROZEN SMOKE OK: version=$($status.version); session=$($chat.session_id)"
  } finally {
    if ($process -and -not $process.HasExited) {
      try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch { }
      try { $process.WaitForExit(10000) | Out-Null } catch { }
    }
    if ($null -eq $oldBackend) { Remove-Item Env:LESHY_LLM_BACKEND -ErrorAction SilentlyContinue } else { $env:LESHY_LLM_BACKEND = $oldBackend }
    if ($null -eq $oldEmbeddings) { Remove-Item Env:LESHY_EMBEDDINGS_ENABLED -ErrorAction SilentlyContinue } else { $env:LESHY_EMBEDDINGS_ENABLED = $oldEmbeddings }
    if ($null -eq $oldHeadless) { Remove-Item Env:LESHY_BROWSER_HEADLESS -ErrorAction SilentlyContinue } else { $env:LESHY_BROWSER_HEADLESS = $oldHeadless }
    if ($null -eq $oldToken) { Remove-Item Env:LESHY_API_TOKEN -ErrorAction SilentlyContinue } else { $env:LESHY_API_TOKEN = $oldToken }
    if ($null -eq $oldSupportBootstrap) { Remove-Item Env:LESHY_SUPPORT_BOOTSTRAP -ErrorAction SilentlyContinue } else { $env:LESHY_SUPPORT_BOOTSTRAP = $oldSupportBootstrap }
    Remove-Item -LiteralPath $SmokePackageRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}

# Quality gate is run against a disposable copy of the actual PyInstaller package.
# Existence of Leshy.exe alone is not considered a successful build.
Build-Stage 'frozen-smoke-test'
Invoke-FrozenSmokeTest -SourcePackageRoot $packageRoot -SmokePackageRoot $smokePackageRoot

function Get-LiveProcesses {
  $root = Normalize-PathText $liveRoot
  if (-not $root) { return @() }
  $prefix = $root + '\'
  $result = @()
  try {
    foreach ($proc in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
      $path = Normalize-PathText ([string]$proc.ExecutablePath)
      if ($path -and ($path -eq (Normalize-PathText $liveExe) -or $path.StartsWith($prefix,[System.StringComparison]::OrdinalIgnoreCase))) {
        $result += $proc
      }
    }
  } catch { }
  return @($result)
}

function Stop-LiveLeshy {
  $processes = @(Get-LiveProcesses)
  foreach ($proc in $processes) {
    try {
      Write-Host "Stopping process from dist\Leshy: PID $($proc.ProcessId) $($proc.Name)"
      Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction Stop
    } catch {
      Write-Warning "Could not stop PID $($proc.ProcessId): $($_.Exception.Message)"
    }
  }

  $deadline = (Get-Date).AddSeconds(15)
  do {
    $remaining = @(Get-LiveProcesses)
    if ($remaining.Count -eq 0) { break }
    Start-Sleep -Milliseconds 300
  } while ((Get-Date) -lt $deadline)

  $remaining = @(Get-LiveProcesses)
  if ($remaining.Count -gt 0) {
    $ids = ($remaining | ForEach-Object { [string]$_.ProcessId }) -join ', '
    throw "Could not stop all processes using dist\Leshy. Remaining PID(s): $ids"
  }

  # Windows can keep DLL handles briefly after process termination.
  Start-Sleep -Milliseconds 800
}

function Remove-WithRetry {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [int]$Attempts = 12,
    [int]$DelayMs = 500
  )
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $lastError = $null
  for ($i = 1; $i -le $Attempts; $i++) {
    try {
      Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
      return
    } catch {
      $lastError = $_.Exception.Message
      if ($i -lt $Attempts) {
        Write-Host "Waiting for file unlock ($i/$Attempts): $Path"
        Start-Sleep -Milliseconds $DelayMs
      }
    }
  }
  throw "Could not remove locked runtime path after $Attempts attempts: $Path ; $lastError"
}

function Clear-LiveProgramFiles {
  New-Item -ItemType Directory -Force -Path $liveRoot | Out-Null
  foreach ($entry in @(Get-ChildItem -LiteralPath $liveRoot -Force -ErrorAction SilentlyContinue)) {
    if ($entry.Name -ieq 'LeshyBrain') { continue }
    Remove-WithRetry -Path $entry.FullName
  }
}

function Publish-LiveProgram {
  Stop-LiveLeshy
  Clear-LiveProgramFiles
  foreach ($entry in @(Get-ChildItem -LiteralPath $packageRoot -Force)) {
    if ($entry.Name -ieq 'LeshyBrain') { continue }
    Copy-Item -LiteralPath $entry.FullName -Destination $liveRoot -Recurse -Force
  }
  if (-not (Test-Path -LiteralPath $liveExe)) { throw "Publishing failed: $liveExe was not created" }
}

# Canonical local output agreed for Leshy:
#   dist\Leshy\...      compiled runnable program
#   dist\LeshySetup-*   installer
# LeshyBrain is persistent user data and is never removed during publication.
Build-Stage 'publish-or-stage'
if (-not $StageOnly) {
  Publish-LiveProgram
} else {
  Write-Host 'STAGE ONLY: AutoBuilder will publish the package to dist\Leshy after creating its rollback backup.'
}

Build-Stage 'inno-setup'
$isccPath = $null
$cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($cmd) { $isccPath = $cmd.Source }
if (-not $isccPath) {
  $candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
  )
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) { $isccPath = $candidate; break }
  }
}
if (-not $isccPath) { throw 'Inno Setup 6 (ISCC.exe) was not found' }

# Installer always comes from the clean staged package and is written to dist.
& $isccPath "/DMyAppVersion=$version" ("/DMyAppSourceDir=" + $packageRoot) 'windows/installer.iss'
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

$installer = Join-Path $repoRoot ("dist\LeshySetup-$version.exe")
if (-not (Test-Path -LiteralPath $installer)) { throw "Installer was not created: $installer" }

if (-not $StageOnly) {
  Write-Host "READY RUNTIME: $liveRoot"
  Write-Host "READY EXE:     $liveExe"
}
Write-Host "READY SETUP:   $installer"
Write-Host "BROWSER:       system Edge/Chrome/Chromium; no browser bundled"
Write-Host "DATA SAFE:     $brainRoot is preserved during live publication."
Write-Host 'AUTO TESTS:    frozen /v1/status + /v1/chat passed before installer creation.'
$script:buildOK = $true
} finally {
  try {
    if ($script:profileWatch) {
      $script:profileWatch.Stop()
      $stageEnd = if ($script:buildOK) { 'END' } else { 'FAILED' }
      Log-BuildProfile "STAGE_$stageEnd name=$($script:profileStage) seconds=$([math]::Round($script:profileWatch.Elapsed.TotalSeconds,2))"
    }
    $script:profileTotal.Stop()
    $status = if ($script:buildOK) { 'SUCCESS' } else { 'FAILED_OR_INTERRUPTED' }
    Log-BuildProfile "BUILD_RESULT outcome=$status totalSeconds=$([math]::Round($script:profileTotal.Elapsed.TotalSeconds,2))"
    if ($script:profileJob) {
      Stop-Job -Job $script:profileJob -ErrorAction SilentlyContinue
      Remove-Job -Job $script:profileJob -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $script:profileStageFile -Force -ErrorAction SilentlyContinue
    # Publish the full profile after background sampling stops. Live data can be
    # rotated/cleared by the application while a build is running.
    if ($script:profileActive) {
      try {
        Copy-Item -LiteralPath $script:profileFile -Destination $script:profileMirrorFile -Force -ErrorAction Stop
      } catch { Write-Warning "Could not publish full build profile into LeshyBrain logs: $($_.Exception.Message)" }
    }
  } catch { }
}
