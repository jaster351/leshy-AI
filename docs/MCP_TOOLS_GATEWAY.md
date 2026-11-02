# MCP Tools Gateway и расширения Лешего

## Назначение

MCP Tools Gateway подключает внешние инструменты к одному Лешему, не создавая второго AI-агента. Планирование и решение о вызове инструмента остаются в LeshyBrain.

## Реестр

`ToolRegistry` хранит локальные manifest в `LeshyBrain/tools/registry.json` и поддерживает два транспорта MCP v2:

- `stdio` — локальный процесс;
- `http` — удалённый сервер; удалённый адрес обязан использовать HTTPS, HTTP разрешён только для localhost.

Разрешения: `read`, `network`, `filesystem`, `mutate`, `desktop`, `secrets`, `financial`.

`mutate`, `desktop`, `secrets` и `financial` требуют явного approval перед вызовом инструмента.

## Extension Marketplace

`ExtensionMarketplace` предназначен для MCP-расширений Python и работает как безопасный исполнительный контур рядом с существующим пользовательским Marketplace Лешего.

Перед установкой обязательно показываются название, pinned `package_spec` вида `package==version`, исходный HTTPS repository, лицензия и список permissions.

Установка выполняется только после явного подтверждения. Для каждого расширения создаётся отдельный venv в `LeshyBrain/extensions/<id>/venv`. Произвольная shell-строка из каталога не выполняется: запускается только `python -m <module>` из собственного venv.

После установки MCP server обязан успешно вернуть список tools. Пустой/нерабочий сервер считается ошибкой, регистрация откатывается. Удаление также требует подтверждения пользователя.

## Web API

- `GET /v1/tools/extensions`;
- `POST /v1/tools/extensions/inspect`;
- `POST /v1/tools/extensions/install`;
- `POST /v1/tools/extensions/uninstall`;
- `POST /v1/tools/call`.

Все endpoint используют существующую авторизацию Web API Лешего.

## Безопасность

Marketplace не должен автоматически устанавливать найденный в интернете код без показа происхождения, версии, лицензии и permissions. Новые permissions не наследуются молча при обновлении: будущий updater обязан повторно запросить approval при расширении прав.

Секреты MCP окружения не возвращаются через `GET /v1/tools/extensions`.
