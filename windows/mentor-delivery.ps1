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
if (-not (Test-RegularFile $configPath) -or -not (Test-RegularFile $tokenPath)) { return 'not_configured' }
foreach ($directory in @($BrainRoot, $root)) {
    if ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unsafe mentor directory' }
}
if ((Get-Item -LiteralPath $configPath).Length -gt 65536 -or (Get-Item -LiteralPath $tokenPath).Length -gt 65536) { throw 'Invalid local configuration size' }
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.enabled -isnot [bool] -or -not $config.enabled -or $config.repository -cne 'jaster351/AI-mentor') { return 'disabled' }
$id = [string]$config.batch_id
$expected = [string]$config.batch_sha256
if ($id -cnotmatch '^mentor-batch-[a-z0-9-]{1,55}$' -or $expected -cnotmatch '^[0-9a-f]{64}$') { return 'not_configured' }
$target = Join-Path $root ($id + '.delivery.json')
if (Test-Path -LiteralPath $target) {
    if (-not (Test-RegularFile $target)) { throw 'Unsafe mentor delivery path' }
    if ((Get-Item -LiteralPath $target).Length -le 1048576 -and (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $expected) { return 'up_to_date' }
    throw 'Existing delivery does not match approved package'
}
$secret = [IO.File]::ReadAllText($tokenPath).Trim()
if (-not $secret.StartsWith('dpapi:')) { return 'secure_token_required' }
Add-Type -AssemblyName System.Security
$clear = [Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($secret.Substring(6)), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
try { $token = [Text.Encoding]::UTF8.GetString($clear).Trim() } finally { [Array]::Clear($clear,0,$clear.Length) }
if (-not $token -or $token -match '[\r\n]') { throw 'Invalid mentor credential' }
try {
    $repository = Get-MentorJson '' $token
    if ($repository.private -isnot [bool] -or -not $repository.private -or $repository.full_name -ine 'jaster351/AI-mentor') { throw 'Private repository required' }
    $item = Get-MentorJson ('/contents/batches/' + $id + '.json?ref=main') $token
    if ($item.type -cne 'file' -or $item.encoding -cne 'base64' -or $item.size -gt 1048576) { throw 'Invalid mentor package' }
    $raw = [Convert]::FromBase64String([string]$item.content)
    if ($raw.Length -gt 1048576 -or (Get-BytesSha256 $raw) -cne $expected) { throw 'Approved mentor digest mismatch' }
    $latest = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($latest.enabled -isnot [bool] -or -not $latest.enabled -or $latest.repository -cne $config.repository -or $latest.batch_id -cne $id -or $latest.batch_sha256 -cne $expected) { return 'configuration_changed' }
    $tmp = Join-Path $root ('.delivery-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllBytes($tmp,$raw)
        [IO.File]::Move($tmp,$target)
    } finally { if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force } }
    return 'delivered'
} finally { $token = $null; $secret = $null }
