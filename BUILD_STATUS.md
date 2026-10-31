# Build Status

Version: 1.10.3

Первый рабочий контур Web + Telegram + локальный чат + управляемое самообучение восстановлен в `main`.

Windows build workflow должен собрать `LeshySetup-1.10.3.exe` и обязательно пройти frozen smoke-test `/v1/status` + `/v1/chat` перед публикацией artifact.

Статус считается готовым только после успешного GitHub Actions Windows Build и наличия установщика artifact.
