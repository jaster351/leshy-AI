param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Version,
    [string]$Branch = 'logs/windows-local'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RuntimeRoot = Join-Path $RepoRoot '.autobuild'
$SyncRepo = Join-Path $RuntimeRoot 'log-sync-repo'
$SyncDest = Join-Path $SyncRepo 'log\windows-local\current'

function Get-GitExe {
    $cmd = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($dir in @('C:\Program Files\Git\cmd', (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd'))) {
        if ($dir -and (Test-Path (Join-Path $dir 'git.exe'))) { return (Join-Path $dir 'git.exe') }
    }
    throw 'Git was not found.'
}

function Sanitize-Text {
    param([string]$Text)
    $value = $Text
    if ($env:USERPROFILE) {
        $value = [regex]::Replace($value, [regex]::Escape($env:USERPROFILE), '<home>', 'IgnoreCase')
    }
    $value = [regex]::Replace($value, '(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+', '$1<redacted>')
    $value = [regex]::Replace($value, '(?i)(/bot)(\d+:[A-Za-z0-9_-]+)', '$1<redacted>')
    $value = [regex]::Replace($value, '(?i)((?:token|secret|password|api[_-]?key|authorization|cookie|signature)=)([^&\s"'']+)', '$1<redacted>')
    $value = [regex]::Replace($value, '(?i)("(?:token|secret|password|api[_-]?key|authorization|cookie|signature)"\s*:\s*")[^"]*(")', '$1<redacted>$2')
    return $value
}

if (-not (Test-Path -LiteralPath $Source)) {
    Write-Host 'No Leshy build logs to publish.'
    exit 0
}

$files = @(Get-ChildItem -LiteralPath $Source -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in @('.log','.json','.txt') })
if ($files.Count -eq 0) {
    Write-Host 'No Leshy build logs to publish.'
    exit 0
}

$git = Get-GitExe
$origin = (& $git -C $RepoRoot remote get-url origin 2>$null | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or -not $origin) { throw 'Could not resolve origin URL.' }

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
if (-not (Test-Path (Join-Path $SyncRepo '.git'))) {
    Remove-Item -Recurse -Force $SyncRepo -ErrorAction SilentlyContinue
    & $git clone --quiet --single-branch --branch $Branch $origin $SyncRepo
    if ($LASTEXITCODE -ne 0) { throw "Could not clone diagnostics branch: $Branch" }
} else {
    & $git -C $SyncRepo fetch origin $Branch --quiet
    if ($LASTEXITCODE -ne 0) { throw "Could not fetch diagnostics branch: $Branch" }
    & $git -C $SyncRepo checkout -B $Branch "origin/$Branch" --quiet
    if ($LASTEXITCODE -ne 0) { throw "Could not checkout diagnostics branch: $Branch" }
    & $git -C $SyncRepo reset --hard "origin/$Branch" --quiet
    if ($LASTEXITCODE -ne 0) { throw "Could not reset diagnostics branch: $Branch" }
}

Remove-Item -Recurse -Force $SyncDest -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $SyncDest | Out-Null

foreach ($file in $files) {
    try {
        $text = [System.IO.File]::ReadAllText($file.FullName)
        if ($text.Length -gt 2000000) {
            $text = '[truncated to last 2000000 characters]' + [Environment]::NewLine + $text.Substring($text.Length - 2000000)
        }
        $safe = Sanitize-Text $text
        [System.IO.File]::WriteAllText((Join-Path $SyncDest $file.Name), $safe, [System.Text.UTF8Encoding]::new($false))
    } catch {
        Write-Warning "Could not sanitize $($file.Name): $($_.Exception.Message)"
    }
}

$sha = (& $git -C $RepoRoot rev-parse HEAD 2>$null | Select-Object -First 1).Trim()
$metadata = [ordered]@{
    version = $Version
    commit = $sha
    uploaded_at = (Get-Date).ToString('o')
    source = 'dist/Leshy/LeshyBrain/logs'
    sanitized = $true
}
$metadata | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $SyncDest 'upload-metadata.json') -Encoding UTF8

& $git -C $SyncRepo config user.name 'Leshy AutoBuilder'
& $git -C $SyncRepo config user.email 'leshy-autobuilder@localhost'
& $git -C $SyncRepo add -A -- 'log/windows-local'
& $git -C $SyncRepo diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host 'Leshy build logs are unchanged.'
    exit 0
}

$shortSha = $sha.Substring(0, [Math]::Min(8, $sha.Length))
& $git -C $SyncRepo commit -m "logs: Windows build diagnostics $shortSha" --quiet
if ($LASTEXITCODE -ne 0) { throw 'Could not commit build logs.' }
& $git -C $SyncRepo push origin "HEAD:$Branch" --quiet
if ($LASTEXITCODE -ne 0) { throw 'Could not push build logs.' }
Write-Host "Published sanitized Leshy build logs to $Branch/log/windows-local/current"
