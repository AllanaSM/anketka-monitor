# Мониторинг обратной связи об Анкетке

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
- `db/migrate.py` — применение миграций с проверкой контрольных сумм.
- `db/persistence.py` — идемпотентная запись JSON-снимка в PostgreSQL.
- `bot/anketka.py` — Telegram-команды `/start`, `/status` и доставка новых событий.
- `data/rustore/` — локальные JSON-снимки; каталог исключён из Git.
- `docker-compose.local.yml` — отдельная временная PostgreSQL для тестов.
- `docs/database-schema.html` — описание таблиц и связей.

## Локальный запуск

В корне проекта создайте `.env` на основе `.env.example`. Секреты в Git не добавляются.

Запуск тестовой базы:

```powershell
cd anketka-db-test
docker compose up -d
```

Настройка подключения из каталога проекта:

```powershell
$env:DATABASE_URL = 'postgresql://anketka_test:anketka_test_local@127.0.0.1:55432/anketka_monitor_test'
python -m db.migrate
```

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

Тесты:

```powershell
python -m unittest discover -s tests -v
```

## Конфигурация

```env
DATABASE_URL=postgresql://user:password@localhost:5432/anketka_monitor
TELEGRAM_TOKEN=

# Резерв на будущий аналитический модуль; сейчас не используется.
OPENROUTER_API_KEY=
MODEL=anthropic/claude-haiku-5.5
```

`OPENROUTER_API_KEY` и `MODEL` сохранены только как задел для будущего анализа тональности, болей и сопоставления с FAQ. Текущий сборщик и Telegram-бот их не используют.

## Production Compose

`docker-compose.yml` содержит PostgreSQL, Telegram-бота и профильный сервис сборщика. В Coolify нужно задать `SERVICE_USER_POSTGRES`, `SERVICE_PASSWORD_POSTGRES`, `TELEGRAM_TOKEN` и `FAQ_ADMIN_CHAT_IDS`, применить миграции. RuStore можно запускать ежедневно в 09:00 по Europe/Moscow. FAQ рекомендуется запускать отдельным еженедельным job, например:

```powershell
python main.py faq && python main.py faq-report
```

Команда `faq-report` отправляет результат только администраторам из `FAQ_ADMIN_CHAT_IDS` и не использует таблицу обычных подписок.

Production-база хранится в постоянном Docker-томе с резервным копированием. Локальная база из `anketka-db-test` к production не подключается.

## Схема данных

Основные таблицы: `projects`, `sources`, `collection_runs`, `reviews`, `review_versions`, `replies`, `reply_versions`, `faq_items`, `faq_item_versions`, `change_events`, `telegram_subscriptions`, `reports`, `report_items`, `telegram_deliveries`.

Отзывы и ответы хранятся отдельно от их версий. `content_hash` используется для определения содержательных изменений, а `raw_payload` сохраняет исходную запись конкретной версии.

## Следующие этапы

- FAQ Анкетки и сопоставление вопросов пользователей с политиками компании.
- Анализ тональности, болей и частоты проблем.
- Дополнительные площадки и поиск новых постоянных источников.
- Настройка ежедневного расписания в Coolify.
- Веб-интерфейс поверх той же PostgreSQL-схемы.

Аналитический модуль будет подключён позже к сохранённым отзывам. Он не является отдельным проектом и не должен возвращать старую схему `materials/findings`.

FAQ хранится отдельно от отзывов: `faq_items` содержит стабильные вопросы, а `faq_item_versions` — неизменяемые версии ответов, наблюдавшиеся при конкретных запусках сбора. Миграция `002_faq.sql` создаёт эти таблицы и добавляет источник `FAQ Анкетки`. Повторный импорт того же снимка не создаёт новую версию.
