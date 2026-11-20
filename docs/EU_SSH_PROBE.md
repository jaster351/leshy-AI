# Собственный европейский SSH-пробник для Лешего

Сервис независим от Cheburcheck. Он делает только TCP Connect на порт 22, без
SSH-входа и без проверки SMB/445. Используйте только для серверов, на проверку
которых есть разрешение.

## Логика

Леший проверяет один и тот же публичный IP трижды со своего компьютера и трижды
с европейского сервера, затем сопоставляет результаты.

| РФ | Европа | Вывод |
| --- | --- | --- |
| 3/3 connected | 3/3 connected | SSH/22 доступен в обеих сетях, блокировка порта не выявлена |
| 3/3 timeout | 3/3 connected | Признаки ограничения SSH из РФ, возможна фильтрация, причина не доказана |
| 0/3 connected | 0/3 connected | Недоступно обоим, причина неизвестна |
| 3/3 connected | 0/3 connected | SSH из РФ доступен |
| смешанные статусы | любые | Недостаточно данных |

**TCP-проверка не доказывает, что ограничение установлено именно ТСПУ.**
Сервер может фильтровать соединения по IP-адресам источника, ACL или маршруту.
Вывод относится только к TCP/22, а не ко всему IP и не к другим портам.

## Установка на европейском Linux-сервере

Нужны Linux с systemd и Python 3.11 или новее. Выполните:

    git clone https://github.com/jaster351/leshy-AI.git
    cd leshy-AI
    sudo bash ops/install-eu-ssh-probe.sh DE

Вместо DE укажите фактический код страны (NL, FI и т. п.). Установщик
создаёт systemd-службу leshy-eu-ssh-probe с автозапуском, генерирует случайный
Bearer-токен и сохраняет его в /etc/leshy-eu-ssh-probe.env с правами 0600.

    sudo systemctl status leshy-eu-ssh-probe --no-pager
    sudo cat /etc/leshy-eu-ssh-probe.env

Не публикуйте этот файл и Bearer-токен. Повторная установка сохраняет токен.

Агент слушает только 127.0.0.1:8768. Нужен внешний HTTPS reverse proxy
с действительным сертификатом. Пример конфигурации Caddy:

    probe.example.com {
        reverse_proxy 127.0.0.1:8768
    }

Настройте домен probe.example.com на IP европейского сервера и входящий TCP/443.
Не открывайте HTTP-порт 8768 в интернет без HTTPS.

Агент ограничен 15 запросами в минуту, двумя параллельными проверками и
строго TCP/22. Принимаются только публичные IP, а не DNS-имена.

## Настройки Windows-сборки Лешего

Параметры окружения:

- LESHY_EU_PROBE_URL — HTTPS URL до европейского сервера (без пути).
- LESHY_EU_PROBE_TOKEN — значение LESHY_PROBE_API_TOKEN на сервере.
- LESHY_EU_PROBE_EXPECTED_COUNTRY — ожидаемый код страны, например DE.
- LESHY_RU_PROBE_CONFIRMED=1 — установить только если выход из сети Лешего
  действительно находится в России без VPN/прокси.

Пример PowerShell для текущего пользователя:

    [Environment]::SetEnvironmentVariable('LESHY_EU_PROBE_URL', 'https://probe.example.com', 'User')
    [Environment]::SetEnvironmentVariable('LESHY_EU_PROBE_EXPECTED_COUNTRY', 'DE', 'User')
    [Environment]::SetEnvironmentVariable('LESHY_RU_PROBE_CONFIRMED', '1', 'User')
    $token = Read-Host 'Токен европейского агента'
    [Environment]::SetEnvironmentVariable('LESHY_EU_PROBE_TOKEN', $token, 'User')
    Remove-Variable token

Перезапустите Лешего после настройки. Учтите: переменные окружения не являются
защищённым хранилищем секретов; ограничьте доступ к Windows-профилю.

**Важно:** страна узла предоставляется оператором. Она не проверяется по
геолокации автоматически. Если Вы используете VPN, результаты географического
сравнения не должны считаться доказательством блокировки.

При ошибке внешнего сервиса или отсутствии токена Леший помечает эту проверку
как недоступную и не делает отрицательного вывода из ошибки сети.


## Прямой HTTPS без reverse proxy (любой IPv4-интерфейс)

Для зарубежного Linux-сервера, где порты 80/443 заняты другими службами,
предусмотрен отдельный TLS-сервис на TCP/8768: процесс слушает `0.0.0.0:8768`.
Самого Лешего здесь нет: работает только небольшой Python-агент TCP/22.

Уже установленный экземпляр можно перевести на прямой HTTPS:

    curl -fL https://raw.githubusercontent.com/jaster351/leshy-AI/main/ops/configure-eu-ssh-probe-https.sh -o /root/configure-eu-ssh-probe-https.sh
    bash /root/configure-eu-ssh-probe-https.sh PUBLIC_IPV4
    systemctl status leshy-eu-ssh-probe --no-pager
    ss -lntp '( sport = :8768 )'

Замените `PUBLIC_IPV4` на публичный IP Вашего зарубежного сервера. Этот IP
передаётся скрипту только локально: **он не записывается в GitHub**. Команда
затрагивает только текущий пробник, его systemd drop-in и его TLS-файлы;
Docker, Nginx, Xray и другие службы не перезапускаются. Токен и страна в
`/etc/leshy-eu-ssh-probe.env` сохраняются. TLS-сертификат выписывается на
переданный IP сроком на 365 дней: позже его потребуется обновить. Не
используйте HTTP для удалённой авторизации.

На Windows копируйте **только публичный сертификат**, а не закрытый ключ:

    scp root@PUBLIC_IPV4:/etc/leshy-eu-ssh-probe/tls.crt "$env:USERPROFILE\\leshy-eu-probe.crt"

В PowerShell для того же Windows-пользователя, под которым запускается Леший:

    [Environment]::SetEnvironmentVariable('LESHY_EU_PROBE_URL', 'https://PUBLIC_IPV4:8768', 'User')
    [Environment]::SetEnvironmentVariable('LESHY_EU_PROBE_CA_CERT', "$env:USERPROFILE\\leshy-eu-probe.crt", 'User')
    $token = Read-Host 'Секретный токен европейского сервера'
    [Environment]::SetEnvironmentVariable('LESHY_EU_PROBE_TOKEN', $token, 'User')
    Remove-Variable token

`LESHY_EU_PROBE_EXPECTED_COUNTRY` должен соответствовать фактической стране,
`LESHY_RU_PROBE_CONFIRMED=1` допустим лишь при прямом выходе Windows в интернет
из РФ (без VPN/прокси). После настройки перезапустите Лешего. Порт 8768
должен быть доступен с Windows через внешние сетевые экраны; существующие
80/443 не затрагиваются.

Самоподписанный сертификат проверяется строго по локальному файлу
`LESHY_EU_PROBE_CA_CERT` и по IP в SAN. Убирать проверку TLS не требуется.
