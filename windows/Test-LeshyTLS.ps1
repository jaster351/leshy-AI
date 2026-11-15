#requires -Version 5.1
<#
.SYNOPSIS
    Безопасная диагностика HTTPS на этом ПК: Windows и Python.
.DESCRIPTION
    Только публичные адреса, без токена Telegram. Ничего не устанавливает,
    не выключает проверку сертификатов и не меняет хранилища CA.
#>
[CmdletBinding()]
param()
$ErrorActionPreference='Continue'
Write-Host '=== Леший: диагностика Telegram TLS / сети ==='
Write-Host "Компьютер: $env:COMPUTERNAME"
Write-Host "Python: $((Get-Command python -ErrorAction SilentlyContinue).Source)"
Write-Host "HTTPS_PROXY задан: $([bool]$env:HTTPS_PROXY)"
Write-Host "HTTP_PROXY задан: $([bool]$env:HTTP_PROXY)"
Write-Host "LESHY_TELEGRAM_CA_BUNDLE задан: $([bool]$env:LESHY_TELEGRAM_CA_BUNDLE)"
Write-Host 'Проверка TLS в Windows (без токена Telegram):'
foreach($url in @('https://api.telegram.org','https://huggingface.co')) {
    try {
        $r=Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
        Write-Host "$url : TLS OK (HTTP $($r.StatusCode))"
    } catch {
        $msg=$_.Exception.Message
        if ($_.Exception.Response) {
            Write-Host "$url : TLS-соединение установлено, HTTP ошибка от сервера ($msg)"
        } else {
            Write-Host "$url : ошибка подключения/TLS: $msg"
        }
    }
}
Write-Host 'Проверка TLS в Python (без токена Telegram):'
$script=@'
import ssl, urllib.request
print("Python/OpenSSL:", ssl.OPENSSL_VERSION)
print("Проверка TLS:", ssl.create_default_context().verify_mode == ssl.CERT_REQUIRED)
for host in ("https://api.telegram.org", "https://huggingface.co"):
    try:
        urllib.request.urlopen(host, timeout=10)
        print(host, ": TLS OK")
    except urllib.error.HTTPError as exc:
        print(host, ": TLS OK, HTTP", exc.code)
    except Exception as exc:
        print(host, ":", type(exc).__name__, str(exc)[:180])
'@
$script | python -
Write-Host 'Диагностика завершена. Никаких сертификатов или секретов не изменено.'
