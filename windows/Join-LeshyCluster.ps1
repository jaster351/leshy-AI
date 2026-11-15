#requires -Version 5.1
<#
.SYNOPSIS
    Однократное безопасное присоединение Windows-узла к доверенному кластеру Лешего.
.DESCRIPTION
    Получите cluster-secret.txt с уже доверенного компьютера по защищённому
    каналу (например, USB-накопитель). Этот скрипт НЕ скачивает и НЕ
    отправляет секрет по UDP/HTTP, НЕ заменяет уникальный node-id.txt.
    После сопряжения UDP-обнаружение и восстановление связи автоматические.
.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\Join-LeshyCluster.ps1 -SecretFile E:\cluster-secret.txt
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SecretFile,
    [string]$BrainDir
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $BrainDir) {
    $BrainDir = Join-Path $repo 'dist\Leshy\LeshyBrain'
    if (-not (Test-Path (Join-Path $repo 'dist\Leshy\Leshy.exe'))) {
        $BrainDir = Join-Path $repo 'LeshyBrain'
    }
}
if ($env:LESHY_BRAIN_DIR) {
    Write-Warning "Переменная LESHY_BRAIN_DIR задана: убедитесь, что параметр -BrainDir указывает на действующее хранилище."
}
if ($env:LESHY_CLUSTER_SECRET) {
    throw 'Узел использует LESHY_CLUSTER_SECRET из окружения. Файл не будет иметь эффекта. Удалите переопределение или настройте такой же секрет на обоих узлах.'
}
$source = (Resolve-Path -LiteralPath $SecretFile -ErrorAction Stop).Path
$secret = [System.IO.File]::ReadAllText($source, [System.Text.Encoding]::UTF8).Trim()
if ($secret.Length -lt 24 -or $secret -match '\s') {
    throw 'Неверный формат cluster-secret.txt: ожидается непустая строка не короче 24 символов, без пробелов.'
}
$destDir = Join-Path $BrainDir 'cluster'
$dest = Join-Path $destDir 'cluster-secret.txt'
if ([System.IO.Path]::GetFullPath($source) -eq [System.IO.Path]::GetFullPath($dest)) {
    throw 'Источник совпадает с текущим cluster-secret.txt. Передайте секрет именно с доверенного другого компьютера.'
}
New-Item -ItemType Directory -Force -Path $destDir | Out-Null
if (Test-Path -LiteralPath $dest) {
    $backup = Join-Path $destDir ('cluster-secret.backup-{0}.txt' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Copy-Item -LiteralPath $dest -Destination $backup -ErrorAction Stop
    Write-Host 'Предыдущий секрет сохранён в локальной резервной копии.'
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($dest, $secret, $utf8)
$nodePath = Join-Path $destDir 'node-id.txt'
if (Test-Path -LiteralPath $nodePath) {
    Write-Host 'Уникальный node-id.txt сохранён без изменений.'
} else {
    Write-Host 'node-id.txt ещё отсутствует; Леший создаст его при запуске.'
}
$bytes = [System.Text.Encoding]::UTF8.GetBytes($secret)
$sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant()
} finally {
    $sha.Dispose()
}
Write-Host ('Сопряжение подготовлено. Cluster ID: {0}' -f $hash.Substring(0, 12))
Write-Host 'Перезапустите Лешего на этом компьютере. Узлы с тем же секретом будут автоматически обнаруживаться через UDP 38765.'
Write-Host 'Для подключения задач требуется доступность TCP 8765 между доверенными компьютерами.'
Write-Host 'Не публикуйте файл cluster-secret.txt или его содержимое.'
