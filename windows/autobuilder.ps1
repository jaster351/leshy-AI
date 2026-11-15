param(
    [ValidateSet('watch','once','install','uninstall','status')]
    [string]$Mode = 'watch',
    [ValidateRange(15, 3600)]
    [int]$IntervalSeconds = 60,
    [ValidateRange(1, 1440)]
    [int]$FailureRetryMinutes = 5,
    [ValidateRange(0, 300)]
    [int]$SettleSeconds = 20
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$UiScript = Join-Path $PSScriptRoot 'autobuilder-ui-v3.ps1'
$CoreScript = Join-Path $PSScriptRoot 'autobuilder-core.ps1'
$SettingsPath = Join-Path (Join-Path $RepoRoot '.autobuild') 'ui-settings.json'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$StartupRoot = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$StartupCmd = Join-Path $StartupRoot 'Leshy-AutoBuilder.cmd'

function Get-UiArguments {
    param([bool]$Minimized = $false)
    $args = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $UiScript + '"'
    if ($Minimized) { $args += ' -Minimized' }
    return $args
}

function Quote-Argument {
    param([string]$Value)
    return '"' + (($Value -replace '"','').Trim()) + '"'
}

function Get-CoreTargetArguments {
    $path = $RepoRoot
    $remote = 'origin'
    $branch = 'main'
    if (Test-Path -LiteralPath $SettingsPath) {
        try {
            $settings = Get-Content -LiteralPath $SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($settings.repository_path) { $path = [string]$settings.repository_path }
            if ($settings.remote) { $remote = [string]$settings.remote }
            if ($settings.branch) { $branch = [string]$settings.branch }
        } catch { }
    }
    return ' -RepositoryPath ' + (Quote-Argument $path) + ' -Remote ' + (Quote-Argument $remote) + ' -Branch ' + (Quote-Argument $branch)
}

function Is-AutoBuilderUiCommand {
    param([string]$CommandLine)
    if (-not $CommandLine) { return $false }
    return (
        $CommandLine.IndexOf('autobuilder-ui-v3.ps1',[System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
        $CommandLine.IndexOf('autobuilder-ui.ps1',[System.StringComparison]::OrdinalIgnoreCase) -ge 0
    )
}

function Find-UiProcess {
    try {
        foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue)) {
            if ([int]$proc.ProcessId -eq $PID) { continue }
            if (Is-AutoBuilderUiCommand ([string]$proc.CommandLine)) { return $proc }
        }
    } catch { }
    return $null
}

function Start-Ui {
    param([bool]$Minimized = $false)
    if (-not (Test-Path -LiteralPath $UiScript)) { throw ('AutoBuilder UI was not found: ' + $UiScript) }
    if (Find-UiProcess) {
        Write-Host 'AutoBuilder UI is already running.'
        return
    }
    Start-Process -FilePath $PowerShellExe -ArgumentList (Get-UiArguments -Minimized $Minimized) -WorkingDirectory $RepoRoot -WindowStyle Hidden | Out-Null
}

function Install-UiStartup {
    New-Item -ItemType Directory -Force -Path $StartupRoot | Out-Null
    $line = '@echo off' + [Environment]::NewLine + 'start "" powershell.exe ' + (Get-UiArguments -Minimized $true) + [Environment]::NewLine
    Set-Content -LiteralPath $StartupCmd -Value $line -Encoding ASCII
    Stop-UiProcesses
    Start-Ui -Minimized $false
    Write-Host ('AutoBuilder UI installed in user startup: ' + $StartupCmd)
}

function Stop-UiProcesses {
    try {
        foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue)) {
            if ([int]$proc.ProcessId -eq $PID) { continue }
            if (Is-AutoBuilderUiCommand ([string]$proc.CommandLine)) {
                Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction SilentlyContinue
            }
        }
    } catch { }
}

switch ($Mode) {
    'install' {
        Install-UiStartup
        break
    }
    'uninstall' {
        Stop-UiProcesses
        if (Test-Path -LiteralPath $StartupCmd) { Remove-Item -LiteralPath $StartupCmd -Force }
        Write-Host 'AutoBuilder UI startup registration removed.'
        break
    }
    'status' {
        Write-Host ('Startup: ' + $(if (Test-Path -LiteralPath $StartupCmd) { 'installed' } else { 'not installed' }))
        $args = '-NoProfile -ExecutionPolicy Bypass -File ' + (Quote-Argument $CoreScript) + ' -Mode status' + (Get-CoreTargetArguments)
        $proc = Start-Process -FilePath $PowerShellExe -ArgumentList $args -WorkingDirectory $RepoRoot -NoNewWindow -Wait -PassThru
        exit $proc.ExitCode
    }
    'once' {
        $args = '-NoProfile -ExecutionPolicy Bypass -File ' + (Quote-Argument $CoreScript) + ' -Mode full-update' + (Get-CoreTargetArguments)
        $proc = Start-Process -FilePath $PowerShellExe -ArgumentList $args -WorkingDirectory $RepoRoot -NoNewWindow -Wait -PassThru
        exit $proc.ExitCode
    }
    'watch' {
        Start-Ui -Minimized $true
        break
    }
}
