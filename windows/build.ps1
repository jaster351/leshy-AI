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

$repoRoot = (Get-Location).Path
$autoRoot = Join-Path $repoRoot '.autobuild'
$packageDist = Join-Path $autoRoot 'package-dist'
$packageRoot = Join-Path $packageDist 'Leshy'
$packageWork = Join-Path $autoRoot 'pyinstaller-work'
$liveRoot = Join-Path $repoRoot 'dist\Leshy'
$liveExe = Join-Path $liveRoot 'Leshy.exe'
$brainRoot = Join-Path $liveRoot 'LeshyBrain'

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

python -m pip install --upgrade pip
python -m pip install -e "."
python -m pip install "pyinstaller>=6.11,<7"
python -m pip install "llama-cpp-python>=0.3.27,<0.4" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

# .autobuild is disposable compiler workspace only. Finished output is published
# to dist\Leshy either here (manual build) or by AutoBuilder after it creates its
# rollback backup (StageOnly/AutoBuilder mode).
Remove-Item -Recurse -Force $packageDist, $packageWork -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $packageDist, $packageWork | Out-Null

python -m PyInstaller --noconfirm --clean --onedir --console `
  --name Leshy `
  --paths src `
  --distpath $packageDist `
  --workpath $packageWork `
  --collect-all llama_cpp `
  --collect-all ddgs `
  --collect-all tzdata `
  --add-data "web;web" `
  --add-data "VERSION;." `
  windows/launcher.py

if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$packageExe = Join-Path $packageRoot 'Leshy.exe'
if (-not (Test-Path -LiteralPath $packageExe)) { throw "PyInstaller did not create $packageExe" }

function Normalize-PathText {
  param([string]$Value)
  if (-not $Value) { return '' }
  try { return [System.IO.Path]::GetFullPath($Value).TrimEnd('\').ToLowerInvariant() } catch { return '' }
}

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
if (-not $StageOnly) {
  Publish-LiveProgram
} else {
  Write-Host 'STAGE ONLY: AutoBuilder will publish the package to dist\Leshy after creating its rollback backup.'
}

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
Write-Host "DATA SAFE:     $brainRoot is preserved during live publication."
Write-Host 'AUTO TESTS:    disabled; manual verification by user.'
