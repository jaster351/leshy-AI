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

python -m pip install --upgrade pip
python -m pip install -e "."
python -m pip install "pyinstaller>=6.11,<7"
python -m pip install "llama-cpp-python>=0.3.27,<0.4" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

# .autobuild is disposable compiler workspace only. The finished program is
# published below to dist\Leshy, which is the canonical local runtime location.
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

function Stop-LiveLeshy {
  $target = Normalize-PathText $liveExe
  if (-not $target) { return }
  try {
    foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='Leshy.exe'" -ErrorAction SilentlyContinue)) {
      if ((Normalize-PathText ([string]$proc.ExecutablePath)) -eq $target) {
        Write-Host "Stopping live Leshy before publishing: $($proc.ProcessId)"
        Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction Stop
      }
    }
  } catch {
    throw "Could not stop the current dist\Leshy\Leshy.exe before update: $($_.Exception.Message)"
  }
}

function Clear-LiveProgramFiles {
  New-Item -ItemType Directory -Force -Path $liveRoot | Out-Null
  foreach ($entry in @(Get-ChildItem -LiteralPath $liveRoot -Force -ErrorAction SilentlyContinue)) {
    if ($entry.Name -ieq 'LeshyBrain') { continue }
    Remove-Item -LiteralPath $entry.FullName -Recurse -Force -ErrorAction Stop
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
Publish-LiveProgram

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

# The installer is created from the clean temporary package tree, while the
# runnable copy has already been published to dist\Leshy above.
& $isccPath "/DMyAppVersion=$version" ("/DMyAppSourceDir=" + $packageRoot) 'windows/installer.iss'
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

$installer = Join-Path $repoRoot ("dist\LeshySetup-$version.exe")
if (-not (Test-Path -LiteralPath $installer)) { throw "Installer was not created: $installer" }

Write-Host "READY RUNTIME: $liveRoot"
Write-Host "READY EXE:     $liveExe"
Write-Host "READY SETUP:   $installer"
Write-Host "DATA SAFE:     $brainRoot was preserved."
Write-Host 'AUTO TESTS:    disabled; manual verification by user.'
