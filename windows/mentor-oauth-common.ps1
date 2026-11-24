# Общий локальный контур авторизации GitHub App для Windows PowerShell 5.1.
# Токены не печатаются и не передаются в параметры внешних процессов.
Add-Type -AssemblyName System.Security
function Invoke-MentorOAuth([string]$Path, [hashtable]$Fields) {
    if ($Path -notin @('/login/device/code','/login/oauth/access_token')) { throw 'Недопустимый маршрут OAuth' }
    $pairs = @($Fields.GetEnumerator() | ForEach-Object {
        [Uri]::EscapeDataString([string]$_.Key) + '=' + [Uri]::EscapeDataString([string]$_.Value)
    })
    $req = [Net.HttpWebRequest]::Create('https://github.com' + $Path)
    $req.AllowAutoRedirect = $false
    $req.Method = 'POST'
    $req.Timeout = 20000
    $req.ReadWriteTimeout = 20000
    $req.UserAgent = 'Leshy-Mentor'
    $req.Accept = 'application/json'
    $req.ContentType = 'application/x-www-form-urlencoded'
    $bytes = [Text.Encoding]::UTF8.GetBytes(($pairs -join '&'))
    $req.ContentLength = $bytes.Length
    $body = $req.GetRequestStream()
    try { $body.Write($bytes,0,$bytes.Length) } finally { $body.Dispose(); [Array]::Clear($bytes,0,$bytes.Length) }
    $response = $req.GetResponse()
    try {
        if ([int]$response.StatusCode -ne 200) { throw 'Ошибка ответа OAuth' }
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        try {
            $buffer = New-Object char[] 8192
            $count = $reader.ReadBlock($buffer,0,$buffer.Length)
            if ($count -ge $buffer.Length) { throw 'Слишком большой ответ OAuth' }
            $raw = -join $buffer[0..($count-1)]
            return ($raw | ConvertFrom-Json)
        } finally { $reader.Dispose() }
    } finally { $response.Dispose() }
}
function Read-MentorSecret([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 65536) { throw 'Недопустимый файл секрета' }
    $data = [IO.File]::ReadAllText($Path).Trim()
    if (-not $data.StartsWith('dpapi:')) { throw 'Требуется секрет DPAPI' }
    $clear = [Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($data.Substring(6)), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    try { return [Text.Encoding]::UTF8.GetString($clear).Trim() } finally { [Array]::Clear($clear,0,$clear.Length) }
}
function Write-MentorSecret([string]$Path,[string]$Value) {
    $parent = Split-Path -Parent $Path
    if ((Get-Item -LiteralPath $parent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Недопустимый каталог секрета' }
    if (Test-Path -LiteralPath $Path) {
        if ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Недопустимый файл секрета' }
    }
    $clear = [Text.Encoding]::UTF8.GetBytes($Value)
    try { $cipher = [Security.Cryptography.ProtectedData]::Protect($clear,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser) }
    finally { [Array]::Clear($clear,0,$clear.Length) }
    $tmp = Join-Path $parent ('.oauth-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllText($tmp,('dpapi:' + [Convert]::ToBase64String($cipher)),[Text.Encoding]::ASCII)
        if ([IO.File]::Exists($Path)) { [IO.File]::Replace($tmp,$Path,$null) } else { [IO.File]::Move($tmp,$Path) }
    } finally { if ([IO.File]::Exists($tmp)) { [IO.File]::Delete($tmp) } }
}
function Enter-MentorCredentialLock([string]$Root) {
    $path = Join-Path $Root 'oauth.lock'
    if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Недопустимый файл блокировки' }
    return [IO.File]::Open($path,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
}
function Get-MentorAccessJson([string]$Path,[string]$Token) {
    if ($Path -notmatch '^/user/installations(?:/[0-9]+/repositories)?\?per_page=100$') { throw 'Недопустимый маршрут проверки установки' }
    $req = [Net.HttpWebRequest]::Create('https://api.github.com' + $Path)
    $req.AllowAutoRedirect = $false
    $req.Timeout = 20000
    $req.ReadWriteTimeout = 20000
    $req.UserAgent = 'Leshy-Mentor'
    $req.Accept = 'application/vnd.github+json'
    $req.Headers.Add('Authorization','Bearer ' + $Token)
    $response = $req.GetResponse()
    try {
        if ([int]$response.StatusCode -ne 200) { throw 'Ошибка проверки установки' }
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        try {
            $buffer = New-Object char[] 262145
            $count = $reader.ReadBlock($buffer,0,$buffer.Length)
            if ($count -lt 1 -or $count -ge $buffer.Length) { throw 'Недопустимый размер ответа установки' }
            return ((-join $buffer[0..($count-1)]) | ConvertFrom-Json)
        } finally { $reader.Dispose() }
    } finally { $response.Dispose() }
}
function Assert-MentorAppAccess([string]$Token) {
    if ($Token -cnotmatch '^ghu_[A-Za-z0-9_]+$') { throw 'Требуется пользовательский токен GitHub App' }
    $list = Get-MentorAccessJson '/user/installations?per_page=100' $Token
    if ($list.total_count -ne 1 -or @($list.installations).Count -ne 1) { throw 'Разрешена ровно одна установка приложения' }
    $installation = $list.installations[0]
    if ($installation.repository_selection -cne 'selected' -or $installation.permissions.contents -cne 'write' -or [string]$installation.id -notmatch '^[0-9]+$') { throw 'Требуется установка только на выбранный репозиторий с Contents read/write' }
    foreach ($property in $installation.permissions.PSObject.Properties) {
        if ($property.Name -notin @('contents','metadata') -and $property.Value -notin @('none','')) { throw 'Обнаружены лишние разрешения GitHub App' }
    }
    $repos = Get-MentorAccessJson ('/user/installations/' + $installation.id + '/repositories?per_page=100') $Token
    if ($repos.total_count -ne 1 -or @($repos.repositories).Count -ne 1 -or $repos.repositories[0].full_name -ine 'jaster351/AI-mentor' -or $repos.repositories[0].private -ne $true) { throw 'Разрешён только закрытый AI-mentor' }
}
function Save-MentorOAuthGrant([string]$Root,[string]$ClientId,$Grant) {
    if ($Grant.token_type -ine 'bearer' -or $Grant.access_token -cnotmatch '^ghu_[A-Za-z0-9_]+$' -or $Grant.refresh_token -cnotmatch '^ghr_[A-Za-z0-9_]+$' -or $Grant.expires_in -lt 60 -or $Grant.expires_in -gt 28800 -or $Grant.refresh_token_expires_in -le 0) { throw 'Недопустимый ответ GitHub App' }
    $now = [DateTimeOffset]::UtcNow
    $record = @{client_id=$ClientId;access_token=[string]$Grant.access_token;refresh_token=[string]$Grant.refresh_token;expires_at=$now.AddSeconds([int]$Grant.expires_in).ToUnixTimeSeconds();refresh_expires_at=$now.AddSeconds([int]$Grant.refresh_token_expires_in).ToUnixTimeSeconds()}
    # Один зашифрованный источник истины; копию токена можно восстановить после сбоя.
    Write-MentorSecret (Join-Path $Root 'github_oauth.dat') ($record | ConvertTo-Json -Compress)
    Write-MentorSecret (Join-Path $Root 'github_token.dat') $record.access_token
}
function Update-MentorOAuthGrant([string]$Root) {
    $secret = Read-MentorSecret (Join-Path $Root 'github_oauth.dat')
    if (-not $secret) { return }
    $record = $secret | ConvertFrom-Json
    if ($record.client_id -cnotmatch '^[A-Za-z0-9_.-]{8,100}$' -or $record.access_token -cnotmatch '^ghu_[A-Za-z0-9_]+$' -or $record.refresh_token -cnotmatch '^ghr_[A-Za-z0-9_]+$') { throw 'Недопустимая локальная авторизация' }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ($record.expires_at -le ($now + 1200)) {
        if ($record.refresh_expires_at -le $now) { throw 'Требуется повторная авторизация в браузере' }
        $grant = Invoke-MentorOAuth '/login/oauth/access_token' @{client_id=[string]$record.client_id;grant_type='refresh_token';refresh_token=[string]$record.refresh_token}
        if ($grant.error) { throw 'GitHub отклонил обновление авторизации' }
        Assert-MentorAppAccess ([string]$grant.access_token)
        Save-MentorOAuthGrant $Root ([string]$record.client_id) $grant
    } else {
        if ((Read-MentorSecret (Join-Path $Root 'github_token.dat')) -cne $record.access_token) {
            Write-MentorSecret (Join-Path $Root 'github_token.dat') ([string]$record.access_token)
        }
    }
}
