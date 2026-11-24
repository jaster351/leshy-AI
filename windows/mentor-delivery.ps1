# Доставка учебного пакета из закрытого репозитория. PowerShell 5.1.
# Секрет расшифровывается только в текущем Windows-профиле.
param([Parameter(Mandatory=$true)][string]$BrainRoot)
$ErrorActionPreference = 'Stop'
$root = Join-Path $BrainRoot 'ai-mentor'
$configPath = Join-Path $root 'config.json'
$tokenPath = Join-Path $root 'github_token.dat'
function Test-RegularFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    return -not ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)
}
function Get-MentorJson([string]$Path, [string]$Token) {
    $req = [Net.HttpWebRequest]::Create('https://api.github.com/repos/jaster351/AI-mentor' + $Path)
    $req.AllowAutoRedirect = $false
    $req.Timeout = 20000
    $req.ReadWriteTimeout = 20000
    $req.UserAgent = 'Leshy-Mentor-Updater'
    $req.Accept = 'application/vnd.github+json'
    $req.Headers.Add('Authorization', 'Bearer ' + $Token)
    $resp = $req.GetResponse()
    try {
        if ([int]$resp.StatusCode -ne 200) { throw 'Unexpected HTTP status' }
        $stream = $resp.GetResponseStream()
        $buffer = New-Object byte[] 8192
        $memory = New-Object IO.MemoryStream
        try {
            while (($count = $stream.Read($buffer,0,$buffer.Length)) -gt 0) {
                if (($memory.Length + $count) -gt 2097152) { throw 'Response limit exceeded' }
                $memory.Write($buffer,0,$count)
            }
            return ([Text.Encoding]::UTF8.GetString($memory.ToArray()) | ConvertFrom-Json)
        } finally { $memory.Dispose(); $stream.Dispose() }
    } finally { $resp.Dispose() }
}
function Get-BytesSha256([byte[]]$Bytes) {
    $h = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($h.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant() }
    finally { $h.Dispose() }
}
function Read-MentorConfig {
    if (-not (Test-RegularFile $configPath) -or (Get-Item -LiteralPath $configPath).Length -gt 65536) { throw 'Invalid mentor configuration' }
    return (Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json)
}
function Test-MentorComplete($Config) {
    $oldId = [string]$Config.batch_id
    $oldSha = [string]$Config.batch_sha256
    if ($oldId -cnotmatch '^mentor-batch-[a-z0-9-]{1,55}$' -or $oldSha -cnotmatch '^[0-9a-f]{64}$') { return $false }
    if (Test-Path -LiteralPath (Join-Path $root 'export.json')) { return $false }
    $manifest = Join-Path $root ($oldId + '.manifest.json')
    if (-not (Test-RegularFile $manifest) -or (Get-Item -LiteralPath $manifest).Length -gt 65536) { return $false }
    $m = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($m.batch_id -cne $oldId -or $m.batch_sha256 -cne $oldSha -or $m.repository -cne 'jaster351/AI-mentor' -or @($m.question_ids).Count -lt 1 -or @($m.question_ids).Count -gt 256) { return $false }
    foreach ($qid in @($m.question_ids)) {
        if ($qid -cnotmatch '^q[0-9]{1,6}$') { return $false }
        $rid = $oldId + '-' + $qid
        $answer = Join-Path $root ($rid + '.json')
        $marker = Join-Path $root ($rid + '.published.json')
        if (-not (Test-RegularFile $answer) -or -not (Test-RegularFile $marker)) { return $false }
        if ((Get-Item -LiteralPath $answer).Length -gt 65536 -or (Get-Item -LiteralPath $marker).Length -gt 65536) { return $false }
        $a = Get-Content -LiteralPath $answer -Raw -Encoding UTF8 | ConvertFrom-Json
        $p = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($a.request_id -cne $rid -or $a.batch_sha256 -cne $oldSha -or $a.status -cne 'answered' -or $a.source -cne 'leshy_model' -or $a.answer -isnot [string] -or $p.request_id -cne $rid -or $p.answer_sha256 -cne (Get-BytesSha256 ([Text.Encoding]::UTF8.GetBytes($a.answer)))) { return $false }
    }
    return $true
}
if (-not (Test-RegularFile $configPath)) { return 'not_configured' }
foreach ($directory in @($BrainRoot, $root)) {
    if ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unsafe mentor directory' }
}
$config = Read-MentorConfig
if ($config.enabled -isnot [bool] -or -not $config.enabled -or $config.repository -cne 'jaster351/AI-mentor') { return 'disabled' }
. (Join-Path $PSScriptRoot 'mentor-oauth-common.ps1')
$lock = Enter-MentorCredentialLock $root
try { Update-MentorOAuthGrant $root } finally { $lock.Dispose() }
if (-not (Test-RegularFile $tokenPath)) { return 'not_configured' }
$config = Read-MentorConfig
if ($config.enabled -isnot [bool] -or -not $config.enabled -or $config.repository -cne 'jaster351/AI-mentor') { return 'disabled' }
$configHash = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash
$id = [string]$config.batch_id
$expected = [string]$config.batch_sha256
$autoQueue = $config.auto_queue -is [bool] -and $config.auto_queue
$queueChange = $false
$token = Read-MentorSecret $tokenPath
if (-not $token -or $token -match '[\r\n]') { return 'not_configured' }
try {
    $repository = Get-MentorJson '' $token
    if ($repository.private -isnot [bool] -or -not $repository.private -or $repository.full_name -ine 'jaster351/AI-mentor') { throw 'Private repository required' }
    if ($autoQueue) {
        $entry = Get-MentorJson '/contents/queue/current.json?ref=main' $token
        if ($entry.type -cne 'file' -or $entry.encoding -cne 'base64' -or $entry.size -gt 8192) { throw 'Invalid mentor queue' }
        $queueRaw = [Convert]::FromBase64String([string]$entry.content)
        if ($queueRaw.Length -gt 8192) { throw 'Invalid mentor queue size' }
        $queue = [Text.Encoding]::UTF8.GetString($queueRaw) | ConvertFrom-Json
        if ($queue.schema -cne 'ai-mentor.queue.v1' -or $queue.batch_id -cnotmatch '^mentor-batch-[a-z0-9-]{1,55}$' -or $queue.batch_sha256 -cnotmatch '^[0-9a-f]{64}$' -or $queue.question_count -lt 1 -or $queue.question_count -gt 256) { throw 'Invalid mentor queue schema' }
        if ($queue.batch_id -ceq $id -and $queue.batch_sha256 -cne $expected) { throw 'Batch ID reused with a different digest' }
        if ($queue.batch_id -cne $id) {
            if (-not $id -or (Test-MentorComplete $config)) {
                $id = [string]$queue.batch_id
                $expected = [string]$queue.batch_sha256
                $queueChange = $true
            }
        }
    }
    if ($id -cnotmatch '^mentor-batch-[a-z0-9-]{1,55}$' -or $expected -cnotmatch '^[0-9a-f]{64}$') { return 'not_configured' }
    $target = Join-Path $root ($id + '.delivery.json')
    $existing = Test-Path -LiteralPath $target
    if ($existing) {
        if (-not (Test-RegularFile $target) -or (Get-Item -LiteralPath $target).Length -gt 1048576 -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expected) { throw 'Existing delivery does not match approved package' }
    } else {
        $item = Get-MentorJson ('/contents/batches/' + $id + '.json?ref=main') $token
        if ($item.type -cne 'file' -or $item.encoding -cne 'base64' -or $item.size -gt 1048576) { throw 'Invalid mentor package' }
        $raw = [Convert]::FromBase64String([string]$item.content)
        if ($raw.Length -gt 1048576 -or (Get-BytesSha256 $raw) -cne $expected) { throw 'Approved mentor digest mismatch' }
        if ((Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash -cne $configHash) { return 'configuration_changed' }
        $tmp = Join-Path $root ('.delivery-' + [Guid]::NewGuid().ToString('N') + '.tmp')
        try { [IO.File]::WriteAllBytes($tmp,$raw); [IO.File]::Move($tmp,$target) }
        finally { if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force } }
    }
    if ($queueChange) {
        $lock = Enter-MentorCredentialLock $root
        try {
            if ((Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash -cne $configHash -or (Read-MentorSecret $tokenPath) -cne $token) { return 'configuration_changed' }
            $config.batch_id = $id
            $config.batch_sha256 = $expected
            $config.approved_export_sha256 = ''
            $config.approved_review_sha256 = ''
            $tmp = Join-Path $root ('.config-' + [Guid]::NewGuid().ToString('N') + '.tmp')
            try {
                [IO.File]::WriteAllText($tmp,($config | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
                [IO.File]::Replace($tmp,$configPath,$null)
            } finally { if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force } }
        } finally { $lock.Dispose() }
    }
    if ($existing -and -not $queueChange) { return 'up_to_date' }
    return 'delivered'
} finally { $token = $null }
