# IP blocking diagnosis: evidence-first contract

This is a safety-critical support workflow. The agent MUST NOT infer a government/TSPU block from a failed connection alone.

## Required structured outcome

- target IP, protocol, port, timestamp and vantage points;
- each executed probe's actual tool receipt and sanitized result;
- DNS check only if a hostname was supplied;
- TCP reachability, optional traceroute and independent network vantage points;
- explicit separation of observation, hypothesis, and confirmed cause;
- status: reachable / unreachable-from-tested-vantage / inconclusive;
- blocking attribution: **unconfirmed** unless supported by independent, reproducible, mechanism-specific evidence;
- operator-facing response with limitations and next verification step.

No probe performed => no claim of a probe. No result receipt => no claim of tool execution. A receipt proves execution only, not correctness of the remote data. Avoid numerical confidence percentages without calibrated evidence. Never label TSPU/RKN blocking as 100% based solely on port failures or traceroute. Distinguish firewall, service-down, routing, ISP filtering and endpoint policy possibilities.

Any active probes must respect rate limits and target authorization; do not scan unrelated ports or broad address ranges. The diagnostic should fail closed when evidence is missing, and should not automatically send an unverified conclusion to a customer.


## Приоритет контрольных точек и выдача результата

Главные фактические измерения доступности — **локальный экземпляр Лешего** и
**принадлежащий владельцу EU-агент**, отдельно для TCP/22 и TCP/3389.
Check-Host предоставляет независимую вспомогательную выборку: ответы других
российских узлов не отменяют реальный тайм-аут у владельца. Cheburcheck
(реестры) не является пробой TCP-доступности.

При локальных 0/3 timeout и собственных EU 3/3 connected следует выводить
расхождение доступности и **не приписывать причину ТСПУ** без дополнительных
доказательств механизма. Когда собственные измерения отсутствуют, один только
Check-Host не подтверждает блокировку. По каждому порту вывод независимый;
неполученный результат EU/3389 нельзя подменять данными EU/22.

Web: кнопка **«Копировать результат»** копирует весь краткий ответ вместе с
IP и выводами по обоим портам, но без диагностических квитанций. В раскрытом
блоке доступно копирование протокола и скачивание UTF-8 файла .txt с кратким
ответом и всеми деталями, содержащимися в ответе. Само скачивание через
браузер не означает публикацию серверного лога, выполнения нового сканирования
или передачи файла в Telegram; для отправки документа Telegram требуется
отдельная реализация.
