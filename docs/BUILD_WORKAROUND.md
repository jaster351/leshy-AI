# Временный обход Windows runner

Если GitHub Hosted Runner `windows-latest` не назначается и job завершается до первого шага с `runner_id=0`, разрешается временно проверить ту же сборку на `windows-2022` без изменения продуктового кода. Такой обход не отменяет unit/regression-тесты, frozen EXE smoke-test, Inno Setup и контрольные суммы.
