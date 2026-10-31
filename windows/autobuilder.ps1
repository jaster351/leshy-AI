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
$UiScript = Join-Path $PSScriptRoot 'autobuilder-ui.ps1'
$CoreScript = Join-Path $PSScriptRoot 'autobuilder-core.ps1'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$StartupRoot = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$StartupCmd = Join-Path $StartupRoot 'Leshy-AutoBuilder.cmd'

function Get-UiArguments {
    param([bool]$Minimized = $false)
    $args = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $UiScript + '"'
    if ($Minimized) { $args += ' -Minimized' }
    return $args
}

function Start-Ui {
    param([bool]$Minimized = $false)
    if (-not (Test-Path -LiteralPath $UiScript)) { throw ('AutoBuilder UI was not found: ' + $UiScript) }
    Start-Process -FilePath $PowerShellExe -ArgumentList (Get-UiArguments -Minimized $Minimized) -WorkingDirectory $RepoRoot -WindowStyle Hidden | Out-Null
}

function Install-UiStartup {
    New-Item -ItemType Directory -Force -Path $StartupRoot | Out-Null
    $line = '@echo off' + [Environment]::NewLine + 'start "" powershell.exe ' + (Get-UiArguments -Minimized $true) + [Environment]::NewLine
    Set-Content -LiteralPath $StartupCmd -Value $line -Encoding ASCII
    Start-Ui -Minimized $false
    Write-Host ('AutoBuilder UI installed in user startup: ' + $StartupCmd)
}

function Stop-UiProcesses {
    try {
        foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue)) {
            if ([int]$proc.ProcessId -eq $PID) { continue }
            $cmd = [string]$proc.CommandLine
            if ($cmd -and $cmd.IndexOf('autobuilder-ui.ps1',[System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
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
        & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $CoreScript -Mode status
        exit $LASTEXITCODE
    }
    'once' {
        & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $CoreScript -Mode full-update
        exit $LASTEXITCODE
    }
    'watch' {
        Start-Ui -Minimized $true
        break
    }
}
