"""Generate the standalone database proposal; does not modify a database."""
import html
import json
from pathlib import Path

OUT = Path(__file__).with_name('database-schema.html')
tables = []

def table(name, title, group, purpose, fields, rules):
    tables.append(dict(name=name, title=title, group=group, purpose=purpose, fields=fields, rules=rules))

# Field: name, PostgreSQL type, constraint/required marker, explanation.
table('projects', 'Проект мониторинга', 'base', 'Один проект сейчас — Анкетка. Отдельная запись позволит позже подключать другие панели, не смешивая данные.', [
('id','bigint','PK · identity','Внутренний идентификатор проекта.'),
('code','text','UNIQUE · NOT NULL','Постоянный код: anketka.'),
('name','text','NOT NULL','Название: Анкетка.'),
('history_from','date','NOT NULL','Начало архива: 2026-01-01.'),
('timezone','text','NOT NULL','Europe/Moscow: календарные границы и расписание.'),
('report_time','time','NOT NULL','09:00; меняется настройкой, без изменения кода.'),
('enabled','boolean','NOT NULL · true','Включён ли мониторинг проекта.'),
('created_at','timestamptz','NOT NULL · now()','Когда проект добавлен.')],
['code уникален; timezone проверяется по списку часовых поясов PostgreSQL при сохранении настройки.', 'Расписание общее для проекта. Индивидуальное время для каждого получателя пока не предусматривается.'])

table('sources', 'Источник', 'base', 'Одна запись — одна карточка приложения на площадке. Для пилота: RuStore, пакет ru.anketka.', [
('id','bigint','PK · identity','Идентификатор источника.'),
('project_id','bigint','FK → projects.id · NOT NULL','К какому проекту относится источник.'),
('platform','text','NOT NULL','rustore; позже возможны другие площадки.'),
('external_key','text','NOT NULL','Пакет приложения: ru.anketka.'),
('name','text','NOT NULL','Название для отображения.'),
('url','text','NOT NULL','Адрес списка отзывов.'),
('enabled','boolean','NOT NULL · true','Участвует ли источник в сборе.'),
('settings','jsonb','NOT NULL · {}','Только параметры сборщика: паузы, лимиты и таймауты. Без секретов.')],
['UNIQUE(project_id, platform, external_key).', 'Индекс project_id. Для каждого источника одновременно допускается один активный сбор.'])

table('collection_runs', 'Запуск сбора', 'base', 'Фиксирует, что и насколько полно удалось получить. Ошибка сбора не равна отсутствию новых отзывов.', [
('id','bigint','PK · identity','Идентификатор запуска.'),
('source_id','bigint','FK → sources.id · NOT NULL','Какой источник проверяли.'),
('run_key','uuid','UNIQUE · NOT NULL','Ключ повторного импорта одного JSON-снимка; не меняется при повторной попытке.'),
('mode','text','NOT NULL · CHECK','backfill — архив; incremental — очередная проверка.'),
('status','text','NOT NULL · CHECK','running / complete / partial / failed.'),
('started_at','timestamptz','NOT NULL','Начало сбора.'),
('finished_at','timestamptz','NULL','Окончание; NULL только у running.'),
('requested_from','date','NOT NULL','Начальная дата для данного запуска.'),
('counters','jsonb','NOT NULL · {}','Страницы, счётчик площадки, уникальные и отобранные отзывы, ошибки, новые версии.'),
('diagnostics','jsonb','NOT NULL · []','Предупреждения и ошибки без токенов.'),
('snapshot_path','text','NULL','Путь к исходному JSON-снимку, если он сохранён; не публичная ссылка.'),
('collector_version','text','NOT NULL','Версия кода / формата сборщика.')],
['CHECK(mode IN (backfill, incremental)); CHECK(status IN (running, complete, partial, failed)). Значения в SQL заключаются в кавычки.', 'Индекс (source_id, started_at DESC). Частичный UNIQUE(source_id) WHERE status = running. Зависший запуск явно завершается перед новым.', 'finished_at ≥ started_at. Обработка каждого отзыва и создание его событий выполняются в одной транзакции.'])

table('reviews', 'Отзыв: постоянная запись', 'base', 'Хранит идентичность отзыва и текущее состояние наблюдения. Сам текст находится в версиях.', [
('id','bigint','PK · identity','Внутренний ID.'),
('source_id','bigint','FK → sources.id · NOT NULL','Площадка и приложение.'),
('external_id','text','NOT NULL','ID RuStore, например 2420260434. Текстовый тип подходит для будущих площадок.'),
('url','text','NOT NULL','Пока ссылка на общий список отзывов.'),
('url_kind','text','NOT NULL · CHECK','list / direct. Не выдавать общую ссылку за прямую.'),
('first_seen_at','timestamptz','NOT NULL','Когда система впервые увидела отзыв.'),
('last_seen_at','timestamptz','NOT NULL','Последнее успешное обнаружение.'),
('first_seen_run_id','bigint','FK → collection_runs.id · NOT NULL','Первый запуск обнаружения.'),
('last_seen_run_id','bigint','FK → collection_runs.id · NOT NULL','Последний запуск, где отзыв присутствовал.'),
('likes_count','integer','NULL · CHECK ≥ 0','Последнее наблюдаемое число лайков.'),
('dislikes_count','integer','NULL · CHECK ≥ 0','Последнее наблюдаемое число дизлайков.'),
('replies_count','integer','NULL · CHECK ≥ 0','Последнее заявленное число комментариев пользователей; не смешивать с devResponse.')],
['UNIQUE(source_id, external_id) защищает от повторного создания отзыва.', 'Последняя версия определяется по максимальному version_no: current_version_id не нужен, циклических связей нет.', 'Нельзя удалять отзыв лишь потому, что он отсутствует в одном сборе. Изменение реакций не создаёт новую текстовую версию.', 'Запуски first_seen_run_id / last_seen_run_id должны принадлежать тому же source_id: составные внешние ключи к collection_runs(id, source_id).'])

table('review_versions', 'Версия текста и оценки', 'history', 'Новая строка появляется только при содержательном изменении. Предыдущие версии остаются доступны.', [
('id','bigint','PK · identity','ID версии.'),
('review_id','bigint','FK → reviews.id · NOT NULL','Какому отзыву принадлежит версия.'),
('version_no','integer','NOT NULL · CHECK > 0','1, 2, 3… внутри отзыва.'),
('run_id','bigint','FK → collection_runs.id · NOT NULL','В каком запуске обнаружена версия.'),
('observed_at','timestamptz','NOT NULL','Время обнаружения этой версии.'),
('author_name','text','NULL','Отображаемое имя. Не является идентификатором человека.'),
('text','text','NOT NULL','Исходный текст без пересказа.'),
('rating','smallint','NOT NULL · CHECK 1…5','Оценка пользователя.'),
('published_at','timestamptz','NULL','Дата публикации, сообщённая источником.'),
('source_edited_at','timestamptz','NULL','Дата изменения на стороне RuStore.'),
('content_hash','text','NOT NULL · SHA-256','Хеш содержательных полей версии, без реакций и времени сбора.'),
('raw_payload','jsonb','NOT NULL','Исходный объект RuStore на момент появления версии.')],
['UNIQUE(review_id, version_no); индекс (review_id, version_no DESC) и индекс published_at.', 'Хеш сравнивается только с последней версией. Глобальная уникальность хеша не нужна: возврат текста A → B → A должен создать третью версию.', 'Блокировка строки reviews на время сравнения и вставки версии предотвращает гонки.', 'run_id должен относиться к источнику родительского отзыва; проверка триггером. Поля версии после записи неизменяемы.', 'published_at допускает NULL: неизвестная дата не подменяется датой сбора и не участвует в годовом сравнении.'])

table('replies', 'Комментарий или ответ компании', 'history', 'Единая таблица связанных сообщений. Ответ разработчика и комментарий участника различаются полем kind.', [
('id','bigint','PK · identity','Внутренний ID сообщения.'),
('review_id','bigint','FK → reviews.id · NOT NULL','Родительский отзыв.'),
('external_key','text','NOT NULL','Для комментария: reply:<ID>; для отдельного поля devResponse: developer_response.'),
('kind','text','NOT NULL · CHECK','user / developer / unknown. DEV в ответе RuStore означает developer.'),
('first_seen_at','timestamptz','NOT NULL','Первое обнаружение.'),
('last_seen_at','timestamptz','NOT NULL','Последнее обнаружение.'),
('likes_count','integer','NULL · CHECK ≥ 0','Реакции на комментарий, если доступны.'),
('dislikes_count','integer','NULL · CHECK ≥ 0','Реакции на комментарий, если доступны.')],
['UNIQUE(review_id, external_key). Индекс review_id.', 'Для devResponse нет отдельного ID: предлагаем один стабильный ключ в пределах отзыва, а изменения сохранять версиями.', 'Не объединять разные сообщения только по одинаковому тексту. Если один официальный ответ придёт одновременно в devResponse и replies, нужен подтверждённый способ сопоставления; автоматическое слияние по тексту запрещено.'])

table('reply_versions', 'Версия связанного сообщения', 'history', 'Сохраняет текст ответа компании или комментария и его изменения с начала нашего наблюдения.', [
('id','bigint','PK · identity','ID версии сообщения.'),
('reply_id','bigint','FK → replies.id · NOT NULL','Родительское сообщение.'),
('version_no','integer','NOT NULL · CHECK > 0','Номер версии внутри сообщения.'),
('run_id','bigint','FK → collection_runs.id · NOT NULL','Где обнаружено изменение.'),
('observed_at','timestamptz','NOT NULL','Когда версия обнаружена.'),
('author_name','text','NULL','Имя автора; для devResponse можно показывать «Разработчик».'),
('author_external_id','text','NULL','ID автора комментария, когда предоставлен. Не связываем с авторами отзывов по имени.'),
('text','text','NOT NULL','Текст сообщения.'),
('published_at','timestamptz','NULL','Дата сообщения / devResponseDate.'),
('source_edited_at','timestamptz','NULL','Если источник предоставит дату редактирования.'),
('content_hash','text','NOT NULL · SHA-256','Хеш содержательных полей сообщения.'),
('raw_payload','jsonb','NOT NULL','Исходные поля сообщения.')],
['UNIQUE(reply_id, version_no); индекс (reply_id, version_no DESC).', 'Та же транзакционная схема версионирования, что у отзыва. run_id принадлежит источнику родительского отзыва; проверка триггером.', 'Время первой публикации не выводится из даты обнаружения. Старые редакции до начала мониторинга неизвестны.'])

table('change_events', 'Событие для будущей сводки', 'history', 'Отделяет обнаруженное изменение от его доставки. Повторная обработка версии не должна создавать второе событие.', [
('id','bigint','PK · identity','ID события.'),
('run_id','bigint','FK → collection_runs.id · NOT NULL','Запуск, создавший событие.'),
('review_version_id','bigint','FK → review_versions.id · NULL','Версия отзыва, если изменился отзыв.'),
('reply_version_id','bigint','FK → reply_versions.id · NULL','Версия сообщения, если изменился ответ или комментарий.'),
('event_type','text','NOT NULL · CHECK','review_new / review_updated / reply_new / reply_updated.'),
('origin','text','NOT NULL · CHECK','backfill / monitoring / late_discovery.'),
('detected_at','timestamptz','NOT NULL','Когда событие обнаружено, а не когда опубликован отзыв.')],
['CHECK: заполнена ровно одна ссылка на версию. Тип события обязан соответствовать типу ссылки.', 'UNIQUE(review_version_id) и UNIQUE(reply_version_id); NULL не конфликтуют. Одной версии соответствует одно событие.', 'run_id должен совпадать с run_id версии; триггер или составной FK. Индекс (detected_at, id).', 'Архивные события не отправляются как свежие отзывы за сутки. Поздно найденная старая публикация помечается late_discovery.'])

table('telegram_subscriptions', 'Получатель проекта', 'telegram', 'Один пользователь или чат может получать отчёты по нескольким проектам. Токен бота в этой таблице не хранится.', [
('id','bigint','PK · identity','ID подписки.'),
('project_id','bigint','FK → projects.id · NOT NULL','Какой проект получает чат.'),
('chat_id','bigint','NOT NULL','Telegram chat_id; может быть отрицательным для групп.'),
('display_name','text','NULL','Понятное имя получателя.'),
('enabled','boolean','NOT NULL · true','Получает ли чат отчёты.'),
('subscribed_at','timestamptz','NOT NULL','Начало подписки.')],
['UNIQUE(project_id, chat_id). Индекс (project_id, enabled).', 'При блокировке бота подписку выключаем, не удаляем историю доставок.', 'Получатели подключаются явно; новые подписчики не получают весь архив автоматически.'])

table('reports', 'Сформированный отчёт', 'telegram', 'Фиксирует состав сводки до отправки. Перезапуск бота не должен менять уже подготовленный отчёт.', [
('id','bigint','PK · identity','ID отчёта.'),
('project_id','bigint','FK → projects.id · NOT NULL','Проект.'),
('report_key','text','NOT NULL','Детерминированный ключ: daily:2026-11-01 или initial:<batch-id>.'),
('kind','text','NOT NULL · CHECK','initial / daily / recovery.'),
('status','text','NOT NULL · CHECK','building / ready / failed.'),
('period_from','timestamptz','NOT NULL','Начало окна обнаружения событий, включительно.'),
('period_to','timestamptz','NOT NULL','Конец окна, не включительно.'),
('scheduled_at','timestamptz','NOT NULL','Плановая доставка, вычисленная из 09:00 Europe/Moscow.'),
('created_at','timestamptz','NOT NULL','Создание.'),
('prepared_at','timestamptz','NULL','Когда завершена подготовка.'),
('coverage','jsonb','NOT NULL · {}','Снимок охвата источников, ID запусков, ошибок и параметров отчёта.')],
['UNIQUE(project_id, report_key); CHECK(period_to > period_from). Индекс (project_id, scheduled_at).', 'Ежедневный отчёт создаётся даже без новых событий: показывает, выполнена ли проверка.', 'Готовый отчёт и его состав неизменяемы. Запоздавшие события включаются в следующий recovery/daily с пометкой, не теряются из-за временной границы.'])

table('report_items', 'Состав отчёта', 'telegram', 'Связывает отчёт с конкретными событиями и фиксирует порядок карточек.', [
('report_id','bigint','PK + FK → reports.id','Какой отчёт.'),
('event_id','bigint','PK + FK → change_events.id','Какое событие включено.'),
('position','integer','NOT NULL · CHECK > 0','Порядок в отчёте.')],
['Составной PRIMARY KEY(report_id, event_id). UNIQUE(report_id, position).', 'Предлагаем UNIQUE(event_id): событие включается в один отчёт, который доставляется всем его получателям. Повтор доставки не создаёт новый отчёт.', 'Проект события через источник должен совпадать с проектом отчёта; проверка триггером.', 'Состав архивного отчёта фиксирует архивные события, даже если Telegram показывает только итоги и несколько примеров.'])

table('telegram_deliveries', 'Доставка частей отчёта', 'telegram', 'Отдельная запись на получателя и часть сообщения: длинный отчёт делится на несколько сообщений.', [
('id','bigint','PK · identity','ID доставки.'),
('report_id','bigint','FK → reports.id · NOT NULL','Отправляемый отчёт.'),
('subscription_id','bigint','FK → telegram_subscriptions.id · NOT NULL','Получатель.'),
('part_no','integer','NOT NULL · CHECK > 0','Номер части сообщения.'),
('payload','text','NOT NULL','Зафиксированный текст части для отправки без повторного рендеринга.'),
('status','text','NOT NULL · CHECK','pending / sending / sent / retry / uncertain / failed.'),
('attempts','integer','NOT NULL · 0 · CHECK ≥ 0','Число попыток.'),
('next_attempt_at','timestamptz','NULL','Когда повторять временную ошибку.'),
('locked_until','timestamptz','NULL','Срок аренды задания отправляющим процессом.'),
('telegram_message_id','bigint','NULL','ID сообщения из успешного ответа Telegram.'),
('sent_at','timestamptz','NULL','Подтверждённое время отправки.'),
('last_error','text','NULL','Причина ошибки, без токенов.')],
['UNIQUE(report_id, subscription_id, part_no). Проекты отчёта и подписки должны совпадать; триггер.', 'Частичный индекс (next_attempt_at) для pending/retry; индекс locked_until для sending. Задания забираются с блокировкой SKIP LOCKED.', 'Для sent обязательны telegram_message_id и sent_at. Для sending обязателен locked_until.', 'Если сообщение могло уйти, но подтверждение потеряно, статус uncertain. Абсолютная гарантия «ровно один раз» без поддержки Telegram невозможна; не повторять такие сообщения автоматически.'])

relations = [
('projects','sources','1 → N','sources.project_id'),
('projects','telegram_subscriptions','1 → N','telegram_subscriptions.project_id'),
('projects','reports','1 → N','reports.project_id'),
('sources','collection_runs','1 → N','collection_runs.source_id'),
('sources','reviews','1 → N','reviews.source_id'),
('collection_runs','reviews','1 → N','first_seen_run_id / last_seen_run_id'),
('collection_runs','review_versions','1 → N','review_versions.run_id'),
('collection_runs','reply_versions','1 → N','reply_versions.run_id'),
('collection_runs','change_events','1 → N','change_events.run_id'),
('reviews','review_versions','1 → N','review_versions.review_id'),
('reviews','replies','1 → N','replies.review_id'),
('replies','reply_versions','1 → N','reply_versions.reply_id'),
('review_versions','change_events','1 → 0..1','change_events.review_version_id'),
('reply_versions','change_events','1 → 0..1','change_events.reply_version_id'),
('reports','report_items','1 → N','report_items.report_id'),
('change_events','report_items','1 → 0..1','report_items.event_id'),
('reports','telegram_deliveries','1 → N','telegram_deliveries.report_id'),
('telegram_subscriptions','telegram_deliveries','1 → N','telegram_deliveries.subscription_id')]

esc = html.escape
group_labels = {'base':'01 · Сбор', 'history':'02 · История', 'telegram':'03 · Telegram'}
cards = []
for t in tables:
    rows = ''.join(f'<tr><td><code>{esc(n)}</code></td><td><code>{esc(typ)}</code></td><td>{esc(key)}</td><td>{esc(desc)}</td></tr>' for n,typ,key,desc in t['fields'])
    rules = ''.join(f'<li>{esc(rule)}</li>' for rule in t['rules'])
    cards.append(f'''<article class="table-card" id="{t['name']}" data-group="{t['group']}">
    <header><div><span class="eyebrow">{group_labels[t['group']]}</span><h3>{esc(t['title'])}</h3><code class="table-name">{t['name']}</code></div><span class="field-count">{len(t['fields'])} полей</span></header>
    <p class="purpose">{esc(t['purpose'])}</p><div class="table-scroll"><table><caption>Поля таблицы {t['name']}</caption><thead><tr><th>Поле</th><th>Тип PostgreSQL</th><th>Ключ / обязательность</th><th>Что означает</th></tr></thead><tbody>{rows}</tbody></table></div>
    <details open><summary>Ключи, индексы и правила</summary><ul>{rules}</ul></details>
    <details class="comment"><summary>Мои предложения по этой таблице</summary><label for="note-{t['name']}">Что добавить, убрать или изменить?</label><textarea id="note-{t['name']}" data-note="{t['name']}" rows="3" placeholder="Например: нужно ли хранить…"></textarea></details></article>''')

links = ''.join(f'<a href="#{t["name"]}"><span>{i:02}</span>{esc(t["title"])}</a>' for i,t in enumerate(tables,1))
edges = ''.join(f'<tr><td><a href="#{a}"><code>{a}</code></a></td><td>{c}</td><td><a href="#{b}"><code>{b}</code></a></td><td><code>{esc(k)}</code></td></tr>' for a,b,c,k in relations)

nodes = [('projects',40,40),('sources',40,150),('collection_runs',40,260),('reviews',330,150),('review_versions',620,100),('replies',330,300),('reply_versions',620,300),('change_events',910,200),('reports',620,470),('report_items',910,470),('telegram_subscriptions',40,470),('telegram_deliveries',330,470)]
node_svg = ''.join(f'<a href="#{name}"><rect x="{x}" y="{y}" width="240" height="66" rx="9"/><text x="{x+14}" y="{y+27}">{name}</text><text class="node-sub" x="{x+14}" y="{y+49}">{esc(next(t["title"] for t in tables if t["name"]==name))}</text></a>' for name,x,y in nodes)
paths = ['M160 106V150','M160 216V260','M280 183H330','M570 174H595V133H620','M450 216V300','M570 333H620','M860 133H890V218H910','M860 333H890V248H910','M1030 266V470','M860 503H910','M620 503H570','M280 503H330']
path_svg = ''.join(f'<path d="{d}" marker-end="url(#arrow)"/>' for d in paths)

template = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Анкетка · схема базы данных</title>
<style>
:root{--ink:#21372f;--muted:#65766e;--paper:#f3f2eb;--white:#fffefa;--line:#d6ddd5;--green:#145c47;--lime:#d5eb8d;--orange:#a2522c}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 "Segoe UI",Tahoma,sans-serif}a{color:var(--green)}button,input,textarea,select{font:inherit}button{cursor:pointer;border:1px solid var(--green);border-radius:6px;padding:9px 15px;background:var(--green);color:white}button.secondary{background:transparent;color:var(--green)}button:hover{filter:brightness(.94)}:focus-visible{outline:3px solid var(--orange);outline-offset:3px}code{font:13px/1.5 Consolas,monospace;overflow-wrap:anywhere}h1,h2,h3,p{margin-top:0}h1{font:clamp(34px,4.6vw,62px)/1.08 Georgia,serif;letter-spacing:-1.5px;max-width:800px;margin-bottom:23px}h2{font:32px/1.2 Georgia,serif;margin-bottom:15px}h3{font-size:22px;line-height:1.3;margin:5px 0}h4{margin:0 0 6px;font-size:17px}.masthead{background:var(--ink);color:var(--white);padding:48px max(5vw,24px) 38px}.masthead p{max-width:800px;color:#d9e2d7;font-size:18px}.topline{display:flex;justify-content:space-between;gap:20px;margin-bottom:40px;font-size:12px;letter-spacing:1.8px;text-transform:uppercase}.badge{color:var(--lime)}.metrics{display:flex;gap:40px;border-top:1px solid #587164;padding-top:23px;margin-top:30px;flex-wrap:wrap}.metrics b{font-size:29px;display:block;color:var(--lime)}.metrics span{font-size:13px;color:#d9e2d7}.layout{display:grid;grid-template-columns:236px minmax(0,1fr);gap:40px;max-width:1510px;margin:0 auto;padding:35px 35px 70px}aside{align-self:start;position:sticky;top:24px;max-height:calc(100vh - 48px);overflow:auto;font-size:13px;padding-right:12px}aside h4{font-size:11px;letter-spacing:1.6px;color:var(--muted);text-transform:uppercase}aside a{display:block;text-decoration:none;padding:8px 0;border-bottom:1px solid var(--line)}aside a span{display:inline-block;width:28px;color:var(--muted);font:11px Consolas,monospace}.side-actions{display:grid;gap:8px;margin:22px 0}main{min-width:0}section{margin-bottom:42px}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:1.8px;color:var(--muted)}.intro-note{padding:20px 24px;background:#e4eadb;border-left:4px solid var(--green);margin-bottom:28px}.intro-note p{margin:0}.overview{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.overview>div{background:var(--white);border:1px solid var(--line);padding:20px}.overview p{font-size:14px;margin:0}.small{font-size:13px;color:var(--muted)}.diagram{overflow:auto;background:var(--white);border:1px solid var(--line);border-radius:10px}.diagram svg{display:block;width:100%;min-width:850px;height:auto}.diagram rect{fill:#f6f7ef;stroke:#bccfc0;stroke-width:1.3}.diagram a:hover rect{fill:var(--lime)}.diagram text{fill:var(--ink);font:14px Consolas,monospace}.diagram text.node-sub{font:12px "Segoe UI",sans-serif;fill:var(--muted)}.diagram path{fill:none;stroke:#7f9c88;stroke-width:1.5}.legend{display:flex;gap:20px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin:15px 0}.legend b{color:var(--green)}.toolbar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:20px 0}.toolbar input{flex:1;min-width:180px}.toolbar input,.toolbar select,textarea{background:var(--white);color:var(--ink);border:1px solid #aabbb0;padding:10px 12px;border-radius:6px}.table-card{background:var(--white);border:1px solid var(--line);border-top:3px solid var(--green);border-radius:9px;margin-bottom:24px;overflow:hidden;scroll-margin-top:20px}.table-card header{display:flex;align-items:flex-start;justify-content:space-between;gap:15px;padding:22px 24px 12px}.table-card[data-group=history]{border-top-color:#9a7934}.table-card[data-group=telegram]{border-top-color:#537d99}.table-name{color:var(--green)}.field-count{font-size:12px;color:var(--muted);white-space:nowrap;background:var(--paper);padding:4px 9px;border-radius:20px}.purpose{padding:0 24px;font-size:14px;color:var(--muted);margin-bottom:18px}.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px;text-align:left}caption{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}th{background:#eaf0e7;font-size:11px;text-transform:uppercase;letter-spacing:.5px;padding:12px 15px;border-bottom:1px solid var(--line)}td{padding:11px 15px;border-bottom:1px solid #e7ebe3;vertical-align:top}td:nth-child(1){min-width:160px}td:nth-child(2){min-width:115px}td:nth-child(3){min-width:190px;font-size:12px}tr:last-child td{border-bottom:0}details{padding:15px 24px;border-top:1px solid var(--line);font-size:13px}summary{cursor:pointer;font-weight:600}details ul{padding-left:18px;margin-bottom:4px}details li{margin:7px 0}.comment{background:#faf8ef}.comment label{display:block;margin:12px 0 6px;color:var(--muted)}textarea{width:100%;resize:vertical;font-size:14px}.decisions{counter-reset:decision;display:grid;grid-template-columns:1fr 1fr;gap:14px}.decision{background:var(--white);border:1px solid var(--line);padding:20px}.decision::before{counter-increment:decision;content:"0" counter(decision);display:block;color:var(--orange);font:14px Consolas,monospace;margin-bottom:10px}.decision p{font-size:14px;margin-bottom:0}.flow{border-left:2px solid #aec2ad;padding-left:25px}.flow li{margin:12px 0;padding-left:5px}.foot{border-top:1px solid var(--line);padding-top:20px;color:var(--muted);font-size:13px}.export-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}[hidden]{display:none!important}#save-status{font-size:12px;color:var(--muted);margin-top:10px}.noscript{padding:20px;background:#fff0da} @media(max-width:1050px){.layout{grid-template-columns:1fr;padding:25px 20px}.layout aside{position:static;max-height:none;display:none}.overview{grid-template-columns:1fr}.decisions{grid-template-columns:1fr}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{body{background:white;font-size:11px}.masthead{background:white;color:#21372f;padding:15px 0}.masthead p,.badge,.metrics span{color:#21372f}.metrics{border-color:#aaa}.metrics b{color:#21372f}.layout{display:block;padding:0}aside,.toolbar,.comment,.export-actions,#save-status{display:none!important}.table-card{break-inside:auto}tr{break-inside:avoid}header{break-after:avoid}.table-scroll,.diagram{overflow:visible}.diagram svg{min-width:0}.decisions{display:block}.decision{margin-bottom:10px}a{color:inherit;text-decoration:none}.table-card[hidden]{display:block!important}}
</style></head><body>
<header class="masthead"><div class="topline"><span>Анкетка / мониторинг обратной связи</span><span class="badge">Проект схемы · v1 · 31.10.2026</span></div><h1>Что храним.<br>Как связываем.<br>Что отправляем.</h1><p>Схема PostgreSQL для отзывов RuStore, истории изменений и доставки в Telegram. Документ для обсуждения — база ещё не создана.</p><div class="metrics"><div><b>12</b><span>таблиц в предложении</span></div><div><b>01.01.2026</b><span>начало истории</span></div><div><b>09:00 МСК</b><span>ежедневная сводка</span></div><div><b>Без AI</b><span>исходные тексты и оценки</span></div></div></header>
<div class="layout"><aside><h4>Навигация</h4><a href="#overview">Общая схема</a><a href="#decisions">Решения для обсуждения</a><a href="#dictionary">Поля и ограничения</a>__NAV__<a href="#relations">Все связи</a><a href="#scenarios">Как меняются данные</a><a href="#notes">Ваши замечания</a><div class="side-actions"><button type="button" onclick="window.print()">Печать / PDF</button><button class="secondary" type="button" data-export>Скачать замечания</button></div><p class="small">PK — первичный ключ<br>FK — внешний ключ<br>UNIQUE — без повторов</p></aside><main>
<noscript><p class="noscript">Таблицы доступны без JavaScript. Для поиска и скачивания замечаний включите JavaScript в браузере.</p></noscript>
<section id="overview"><div class="intro-note"><p><strong>Предложение, а не миграция.</strong> Схема предназначена для проекта мониторинга Анкетки и разворачивается версионированными миграциями PostgreSQL. В обозначениях ниже префикс опущен для краткости.</p></div><div class="overview"><div><span class="eyebrow">01 / сбор</span><h3>Откуда пришло</h3><p>Проект, источник, запуск и постоянный ID отзыва. Основа для первого импорта 392 уже собранных отзывов за 2026 год.</p></div><div><span class="eyebrow">02 / история</span><h3>Что изменилось</h3><p>Версии текстов, оценки, ответы и комментарии. Отдельные события для новых записей и изменений.</p></div><div><span class="eyebrow">03 / Telegram</span><h3>Что доставлено</h3><p>Получатели, состав отчёта и доставка каждой части. Эти таблицы подключаются на следующем этапе.</p></div></div><h2 style="margin-top:30px">Карта основных связей</h2><p class="small">Нажмите на таблицу, чтобы перейти к полям. Стрелка направлена от родителя к дочерним записям; точные ключи и кратности перечислены в разделе «Все связи». Служебные связи запусков на карте опущены.</p><div class="diagram"><svg viewBox="0 0 1190 580" role="img" aria-label="Основные связи двенадцати таблиц базы данных"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8Z" style="fill:#7f9c88;stroke:none"/></marker></defs>__PATHS____NODES__</svg></div></section>
<section id="decisions"><span class="eyebrow">Сначала — правила</span><h2>Решения для обсуждения</h2><div class="decisions">
<div class="decision"><h4>Текст храним полностью</h4><p>Не только цитату: исходный текст и версии. Для Telegram используем тот же текст без анализа. Историю до первого наблюдения восстановить нельзя.</p></div>
<div class="decision"><h4>Ответы компании — отдельно</h4><p>Ответ разработчика и комментарии пользователей храним как связанные сообщения с собственными версиями. Изменение ответа не становится изменением текста отзыва.</p></div>
<div class="decision"><h4>Реакции — только актуальные</h4><p>Предлагается хранить последние значения лайков и дизлайков. Их динамику не версионируем и не отправляем как событие. Если она нужна, добавим таблицу наблюдений.</p></div>
<div class="decision"><h4>Архив не равен новым отзывам</h4><p>Первоначальная загрузка помечается backfill. События мониторинга идут в ежедневный отчёт, поздно найденные старые публикации получают отдельную пометку.</p></div>
<div class="decision"><h4>Нет удаления «по исчезновению»</h4><p>Отсутствие записи в очередном сборе не доказывает удаление на площадке. Все внешние ключи — ON DELETE RESTRICT; автоматическое каскадное удаление не предусматривается.</p></div>
<div class="decision"><h4>Расписание общее</h4><p>09:00 Europe/Moscow для проекта. Передача времени в БД — timestamptz; календарные периоды вычисляются по часовому поясу проекта.</p></div></div></section>
<section id="dictionary"><span class="eyebrow">Словарь данных</span><h2>Таблицы и поля</h2><p class="small">Все названия и ограничения — предложение. NOT NULL — обязательное поле; NULL — отсутствие данных; identity — ID, выдаваемый PostgreSQL. JSONB используется для исходных данных и диагностики, а ключевые поля доступны обычными колонками.</p><div class="toolbar"><label for="search">Поиск</label><input id="search" type="search" placeholder="Например: rating, дата, ответ"><label for="group">Блок</label><select id="group"><option value="all">Все блоки</option><option value="base">Сбор</option><option value="history">История</option><option value="telegram">Telegram</option></select><button class="secondary" type="button" id="reset">Сбросить</button><span id="results" class="small" aria-live="polite">12 таблиц</span></div><div id="cards">__CARDS__</div><p id="empty" hidden>Совпадений нет. Измените запрос или сбросьте фильтр.</p></section>
<section id="relations"><span class="eyebrow">Целостность</span><h2>Все связи</h2><p>1 → N: одной записи может соответствовать много дочерних. 1 → 0..1: дочерняя запись необязательна, но может быть только одна.</p><div class="table-scroll"><table><thead><tr><th>Родитель</th><th>Связь</th><th>Дочерняя таблица</th><th>Внешний ключ</th></tr></thead><tbody>__RELATIONS__</tbody></table></div><div class="intro-note" style="margin-top:20px"><p><strong>Важная проверка при реализации:</strong> простых FK недостаточно для всех межтабличных правил. Совпадение источника запуска, проекта отчёта и подписки необходимо закрепить составными FK или триггерами. Добавляются вспомогательные UNIQUE(id, source_id) для составных ссылок. Проверки нельзя оставлять только в интерфейсе.</p></div></section>
<section id="scenarios"><span class="eyebrow">Поведение</span><h2>Как меняются данные</h2><ol class="flow"><li><strong>Первый импорт.</strong> Создаём запуск backfill, отзывы и первые версии. Связанные ответы и комментарии получают свои записи. События архива идут в initial-отчёт, а не в ежедневную рассылку.</li><li><strong>Повтор без изменений.</strong> По UNIQUE(source_id, external_id) находим отзыв. Обновляем last_seen и реакции. Новую версию и событие не создаём.</li><li><strong>Изменился текст или оценка.</strong> Сравниваем хеш с последней версией под блокировкой строки. Сохраняем следующую версию и одно событие в той же транзакции.</li><li><strong>Появился ответ компании.</strong> Создаём reply и reply_version, затем reply_new. Сам отзыв не получает новую версию только из-за этого ответа.</li><li><strong>09:00 — отправка.</strong> Фиксируем состав отчёта и части текста. Создаём отдельные задания каждому получателю; подтверждённо отправленные части повторно не берём.</li><li><strong>Сбой или неполный сбор.</strong> Сохраняем корректно полученные записи, отмечаем неполноту. Поздние события включаем в следующий отчёт. При потере подтверждения Telegram используем uncertain — абсолютное отсутствие дублей в этом случае не обещаем.</li></ol><p class="small">Поле content_hash текущего JSON-сборщика включает ответы и комментарии. Для этой схемы при импорте потребуются раздельные хеши отзыва и каждого сообщения; копировать общий хеш в review_versions без изменения нельзя.</p></section>
<section><h2>Что пока не включено</h2><p>FAQ, тональность, боли, результаты AI-анализа, отдельная база пользователей-панелистов, история реакций и автоматическое обнаружение удаления. Их можно добавить позже, не меняя идентичность отзывов и версий.</p><p>В текущем коде ещё нет миграций этой схемы. Перед внедрением проверим уникальность, параллельные запуски, откат транзакций, повторный импорт, составные связи и доставку после сбоев. Резервные копии и секреты настраиваются отдельно от таблиц.</p></section>
<section id="notes"><span class="eyebrow">Обсуждение</span><h2>Ваши замечания</h2><p>Можно написать предложения под нужной таблицей или здесь. Кнопка скачает JSON с текстом замечаний — этот файл можно прислать для следующей редакции схемы.</p><label for="note-general">Общие предложения</label><textarea id="note-general" data-note="general" rows="5" placeholder="Например: хочу получать только новые отзывы; нужна история оценок…"></textarea><div class="export-actions"><button type="button" data-export>Скачать замечания</button><button class="secondary" type="button" onclick="window.print()">Печать / PDF</button></div><p id="save-status" role="status"></p><p class="small">Замечания остаются только в вашем браузере и не отправляются в интернет. Автосохранение локального HTML зависит от браузера — перед закрытием скачайте файл. Восстановление замечаний между разными копиями HTML не гарантируется.</p></section>
<footer class="foot">Анкетка · PostgreSQL / схема v1 · Подготовлено для обсуждения. Все 12 таблиц описывают целевое устройство; создание БД и отправка сообщений этим HTML не выполняются.</footer></main></div>
<script>
const cards=[...document.querySelectorAll('.table-card')];
const search=document.querySelector('#search'), group=document.querySelector('#group');
function filter(){const q=search.value.trim().toLocaleLowerCase('ru');let count=0;for(const card of cards){card.hidden=!((group.value==='all'||card.dataset.group===group.value)&&card.textContent.toLocaleLowerCase('ru').includes(q));if(!card.hidden)count++;}document.querySelector('#results').textContent=count+' / 12 таблиц';document.querySelector('#empty').hidden=count!==0;}
search.addEventListener('input',filter);group.addEventListener('change',filter);
document.querySelector('#reset').addEventListener('click',()=>{search.value='';group.value='all';filter();});
document.querySelectorAll('a[href^="#"]').forEach(link=>link.addEventListener('click',()=>{const target=document.getElementById(link.getAttribute('href').slice(1));if(target&&target.classList.contains('table-card')&&target.hidden){search.value='';group.value='all';filter();}}));
const notes=[...document.querySelectorAll('[data-note]')], key='anketka-db-schema-v1-notes';
const status=document.querySelector('#save-status');let storage=true;
try{const saved=JSON.parse(localStorage.getItem(key)||'{}');for(const n of notes){if(typeof saved[n.dataset.note]==='string')n.value=saved[n.dataset.note];}status.textContent='Замечания автоматически сохраняются в этом браузере.';}catch(e){storage=false;status.textContent='Автосохранение недоступно. Перед закрытием скачайте замечания.';}
function content(){return Object.fromEntries(notes.map(n=>[n.dataset.note,n.value]));}
notes.forEach(n=>n.addEventListener('input',()=>{if(storage){try{localStorage.setItem(key,JSON.stringify(content()));status.textContent='Замечания сохранены локально. Для передачи скачайте файл.';}catch(e){storage=false;status.textContent='Не удалось сохранить автоматически. Скачайте замечания перед закрытием.';}}}));
document.querySelectorAll('[data-export]').forEach(b=>b.addEventListener('click',()=>{const payload={schema_version:'1',exported_at:new Date().toISOString(),comments:content()};const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json;charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='anketka-schema-comments.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
</script></body></html>'''

for token,value in {'__NAV__':links,'__CARDS__':''.join(cards),'__RELATIONS__':edges,'__PATHS__':path_svg,'__NODES__':node_svg}.items():
    template=template.replace(token,value)
OUT.write_text(template,encoding='utf-8')
assert OUT.read_text(encoding='utf-8')==template
assert '\ufffd' not in template and '???' not in template
print(f'Created {OUT.name}: {len(tables)} tables, {sum(len(t["fields"]) for t in tables)} fields, {len(relations)} relationships')

