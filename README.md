# Мониторинг обратной связи об Анкетке

## Границы deploy-репозитория

`anketka-monitor` содержит только код, миграции, зависимости и конфигурацию, которые нужны для сборки и запуска в Coolify. Управленческие документы, тесты, локальный Docker Compose, схема БД и локальные снимки вынесены в соседние папки внутри `General Project`:

- `anketka-monitor-management/` — PRD, правила работы, прогресс, канбан и схема БД;
- `anketka-monitor-testing/` — тесты и локальная PostgreSQL;
- `anketka-monitor-local-data/` — сохранённые локальные JSON-снимки.

Проект собирает отзывы и ответы пользователей о приложении и панели Анкетка. Первый источник — RuStore. Система сохраняет исходные JSON-снимки, переносит данные в PostgreSQL и отправляет новые события в Telegram.

## Текущий поток данных

```text
RuStore → collector/rustore.py → JSON-снимок → db/persistence.py → PostgreSQL → bot/anketka.py → Telegram
```

Публичный FAQ собирается отдельным потоком и после успешного сбора может записываться в PostgreSQL:

```text
FAQ Анкетки → collector/faq.py → JSON-снимок
```

Сбор начинается с 01.01.2026 по московскому времени. Повторный импорт не создаёт дубли: новый отзыв добавляется, изменение содержания создаёт новую версию и событие.

## Компоненты

- `collector/rustore.py` — сбор отзывов, оценок, ответов разработчика и комментариев.
- `collector/faq.py` — сбор публичной страницы FAQ в JSON и импорт успешного снимка в PostgreSQL.
- `db/migrations/` — версионированная схема PostgreSQL проекта Анкетки.
- `db/preflight.py` — read-only проверка идентичности PostgreSQL перед миграцией.
- `db/migrate.py` — применение миграций с preflight и проверкой контрольных сумм.
- `db/persistence.py` — идемпотентная запись JSON-снимка в PostgreSQL.
- `analytics/` — provider-neutral анализ, FAQ-match, агрегаты и analytics worker.
- `analytics_adapter/` — локальный HTTP adapter текущего контракта к OpenRouter Chat Completions; production endpoint не меняется автоматически.
- `bot/anketka.py` — Telegram-команды, доставка новых событий и кнопка Mini App «Лента».
- `web/main.py` и `web/template.html` — read-only лента последних отзывов RuStore.
- `data/` — runtime-каталог JSON-снимков; каталог исключён из Git. Архив локальных снимков хранится в `../anketka-monitor-local-data/`.
- Локальная PostgreSQL и тесты находятся в `../anketka-monitor-testing/` и не входят в deploy-репозиторий.
- Схема таблиц и административная документация находятся в `../anketka-monitor-management/`.

## Локальный запуск

Для Docker Compose в корне проекта можно создать локальный `.env` на основе `.env.example`; секреты в Git не добавляются. При прямом запуске Python `.env` сам по себе не загружается — задайте те же значения через `$env:...` в текущей PowerShell-сессии.

Запуск тестовой базы:

```powershell
cd ..\anketka-monitor-testing
docker compose up -d
cd ..\anketka-monitor
```

Настройка подключения из каталога проекта:

```powershell
$env:DATABASE_URL = 'postgresql://anketka:anketka_local_dev@127.0.0.1:55432/anketka_monitor'
$env:ANKETKA_EXPECTED_DB_NAME = 'anketka_monitor'
$env:ANKETKA_EXPECTED_DB_USER = 'anketka'
$env:ANKETKA_EXPECTED_DB_HOST = '127.0.0.1'
$env:ANKETKA_EXPECTED_DB_PORT = '55432'
python -m db.preflight
python -m db.migrate
python -m db.migrate
```

`db.preflight` ничего не записывает в базу. Он сверяет host, port, имя базы и пользователя из `DATABASE_URL` с ожидаемыми значениями, проверяет фактические `current_database()`/`current_user` и, если схема уже создана, наличие проекта с кодом `anketka`. При несоответствии `db.migrate` останавливается до применения SQL.

Правило миграций: применённые файлы в `db/migrations/` неизменяемы. Для добавления таблицы, колонки, индекса или ограничения создаётся следующая миграция; checksum в `schema_migrations` вручную не исправляется.

Сбор и сохранение:

```powershell
python main.py collector --database-url $env:DATABASE_URL
```

Сбор FAQ с записью в PostgreSQL:

```powershell
python main.py faq
```

Для проверки только JSON без базы используйте `python main.py faq --no-persist`.

FAQ не отправляется обычным подписчикам. Для административного отчёта задайте `FAQ_ADMIN_CHAT_IDS` через запятую и после сбора выполните:

```powershell
python main.py faq-report
```

Команду можно поставить в тот же еженедельный job после `python main.py faq`. В Telegram администратору доступны `/faq_status`, `/faq_latest` и `/faq_changes`. Эти команды работают только для chat ID из `FAQ_ADMIN_CHAT_IDS`.

Снимок сохраняется в `data/faq/<дата-время>-<идентификатор>/`. В нём находятся `summary.json`, `diagnostics.json`, `raw.html` и `faq.json`. `diagnostics.json` содержит HTTP-код, URL после редиректов, заголовки, размер, время ответа и начало тела ответа. Если сайт возвращает техническую страницу или сетевую ошибку, запуск получает статус `failed` и пустой `faq.json` не считается собранным FAQ.

Запуск Telegram-бота:

```powershell
python main.py bot
```

Бот получает команды через Telegram long polling. После `/start` чат сохраняется в `telegram_subscriptions`. Для локального запуска должен быть задан `TELEGRAM_TOKEN`.

Запуск аналитического worker после применения миграции `003_review_analytics.sql`:

```powershell
$env:ANALYTICS_ENABLED = 'true'
$env:ANALYTICS_ENDPOINT = 'https://provider.example/v1/analyze'
$env:ANALYTICS_API_KEY = 'set-outside-git'
$env:ANALYTICS_MODEL = 'model-name'
python main.py analytics
```

Для безопасной проверки без записи используйте `ANALYTICS_DRY_RUN=true`. Основные настройки: `ANALYTICS_INTERVAL_SECONDS`, `ANALYTICS_MAX_RUNS_PER_TICK`, `ANALYTICS_TIMEOUT`, `ANALYTICS_ATTEMPTS` и `ANALYTICS_PROMPT_VERSION`. Worker по умолчанию выключен.

Для локального OpenRouter dry-run сначала запустите adapter в отдельном окне:

```powershell
python -m analytics_adapter
```

В `.env.local` для бота задайте `ANALYTICS_ENDPOINT=http://127.0.0.1:8090/analyze`. Adapter берёт `ANALYTICS_API_KEY` и `ANALYTICS_MODEL`, отправляет запрос в `https://openrouter.ai/api/v1/chat/completions` и возвращает проектный JSON-контракт. Для ретроспективы оставьте `ANALYTICS_DRY_RUN=true` и выполните в Telegram `/analytics_retro`.

Тесты:

```powershell
# Integration tests intentionally use a separate database from the bot/dry-run DB.
$env:ANALYTICS_TEST_DATABASE_URL = 'postgresql://anketka:anketka_local_dev@127.0.0.1:55433/anketka_monitor_tests'
python -m unittest discover -s ..\anketka-monitor-testing\tests -v
```

`DATABASE_URL` остаётся адресом рабочей базы для бота и dry-run. `ANALYTICS_TEST_DATABASE_URL` нужен только для PostgreSQL integration tests; это защищает импортированные исторические данные от фикстур тестов.

## Конфигурация

```env
DATABASE_URL=postgresql://anketka:anketka_local_dev@127.0.0.1:55432/anketka_monitor
ANKETKA_EXPECTED_DB_NAME=anketka_monitor
ANKETKA_EXPECTED_DB_USER=anketka
ANKETKA_EXPECTED_DB_HOST=127.0.0.1
ANKETKA_EXPECTED_DB_PORT=55432
TELEGRAM_TOKEN=

ANALYTICS_ENABLED=false
ANALYTICS_DRY_RUN=false
ANALYTICS_ENDPOINT=
ANALYTICS_API_KEY=
ANALYTICS_MODEL=
ANALYTICS_PROVIDER=
ANALYTICS_PROMPT_VERSION=v4
```

`ANALYTICS_API_KEY` не записывается в Git, fixtures или отчёты. Конкретный поставщик не зашит в бизнес-логику: endpoint должен возвращать JSON по контракту `review-analytics-v1`.

## Production Compose

`docker-compose.yml` содержит PostgreSQL, Telegram-бота, профильный сервис сборщика и выключенный по умолчанию analytics worker. В Coolify нужно задать `SERVICE_USER_POSTGRES`, `SERVICE_PASSWORD_POSTGRES`, `TELEGRAM_TOKEN` и `FAQ_ADMIN_CHAT_IDS`, применить миграции. Для аналитики дополнительно задаются `ANALYTICS_ENABLED`, `ANALYTICS_ENDPOINT`, `ANALYTICS_API_KEY` и `ANALYTICS_MODEL`. RuStore можно запускать ежедневно в 09:00 по Europe/Moscow. FAQ рекомендуется запускать отдельным еженедельным job, например:

Перед production-миграцией ожидаемые значения `ANKETKA_EXPECTED_DB_*` задаются по той же конфигурации Coolify, что и `DATABASE_URL`; затем выполняются `python -m db.preflight` и `python -m db.migrate`. Миграция не запускается, если host, port, имя базы, пользователь или маркер проекта не совпадают.

```powershell
python main.py faq && python main.py faq-report
```

Команда `faq-report` отправляет результат только администраторам из `FAQ_ADMIN_CHAT_IDS` и не использует таблицу обычных подписок.

Для Mini App задайте `MINI_APP_URL` — публичный HTTPS-адрес сервиса `web`. При старте бот добавляет кнопку «Лента» в меню Telegram. Сервис `web` показывает последние версии отзывов RuStore, фильтры по периоду, оценке и поисковой строке, а также ответы Анкетки. Подробная аналитика загружается отдельным защищённым запросом только для Telegram `initData`, подписанного пользователем из `FAQ_ADMIN_CHAT_IDS`.

Production-база хранится в постоянном Docker-томе с резервным копированием. Архивная локальная база из `_temp/anketka-db-test` к production не подключается.

## Схема данных

Основные таблицы: `projects`, `sources`, `collection_runs`, `reviews`, `review_versions`, `replies`, `reply_versions`, `faq_items`, `faq_item_versions`, `change_events`, `analysis_runs`, `pain_dictionary`, `review_analysis`, `review_analysis_pains`, `telegram_subscriptions`, `reports`, `report_items`, `telegram_deliveries`.

Отзывы и ответы хранятся отдельно от их версий. Для отзыва `review_content_hash` строится только по собственным полям отзыва; ответы, комментарии, реакции и время сбора не создают новую версию отзыва. В PostgreSQL колонка исторически называется `content_hash`, а `raw_payload` сохраняет исходную запись конкретной версии. Для ответов используется отдельный хеш версии ответа.

## Следующие этапы

- G5: ручная проверка исходной выборки из 20 отзывов и дополнительной v4-выборки из 19 `needs_review` кейсов выполнена; feedback сохранён в `anketka-monitor-management/test-artifacts/analytics-g5-feedback-20261111.json` и `anketka-monitor-management/test-artifacts/analytics-v4-manual-review-feedback-20261114.json`;
- регрессионные примеры `#224` и `#265` сохранены в `anketka-monitor-management/test-artifacts/analytics-v4-regression-cases-20261114.json`, для них добавлен тест `test_v4_regressions.py`;
- калибровочный prompt v4 и тесты добавлены; повторный read-only dry-run по тем же 20 отзывам совпал с ручным эталоном по ключевым случаям;
- полный read-only dry-run по 392 отзывам выполнен, автоматическая QA-проверка пройдена; результаты не записывались в production-базу;
- следующий шаг — выполнить rollout web-части analytics v1 и затем production smoke-check; текущая опубликованная web-сборка отвечает на `/health`, но ещё не содержит `/api/analytics`, тогда как admin-команды бота уже зарегистрированы;
- после v1 подготовить фазу 1b для семантического анализа ответов разработчика.

Аналитика работает поверх сохранённых отзывов Anketka Monitor и не возвращает старую схему `materials/findings`.

FAQ хранится отдельно от отзывов: `faq_items` содержит стабильные вопросы, а `faq_item_versions` — неизменяемые версии ответов, наблюдавшиеся при конкретных запусках сбора. Миграция `002_faq.sql` создаёт эти таблицы и добавляет источник `FAQ Анкетки`. Повторный импорт того же снимка не создаёт новую версию.

Миграция `003_review_analytics.sql` создаёт отдельные неизменяемые `analysis_runs`, единый словарь `pain_dictionary` со статусами `approved/candidate/rejected` и результаты анализа по версиям отзывов. Успешный incremental run уникален для одного завершённого RuStore-сбора; ретроспективный запуск всегда получает отдельный `run_key` и FAQ binding.
