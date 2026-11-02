"""Telegram delivery for the Anketka PostgreSQL schema."""

from __future__ import annotations

import html
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

import psycopg

API = "https://api.telegram.org"
MAX_MESSAGE = 3900


def setting(name):
    return os.environ.get(name, "").strip()


def telegram(method, payload=None):
    token = setting("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN is not set")
    request = urllib.request.Request(
        f"{setting('TELEGRAM_API') or API}/bot{token}/{method}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))
    return result.get("result")


def connect():
    return psycopg.connect(setting("DATABASE_URL"), autocommit=True)


def send_message(chat_id, text):
    return telegram("sendMessage", {
        "chat_id": chat_id,
        "text": text[:MAX_MESSAGE],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def subscribe(conn, chat_id, display_name):
    conn.execute(
        "INSERT INTO telegram_subscriptions(project_id,chat_id,display_name) "
        "SELECT id,%s,%s FROM projects WHERE code='anketka' "
        "ON CONFLICT (project_id,chat_id) DO UPDATE SET enabled=TRUE,display_name=EXCLUDED.display_name",
        (chat_id, display_name),
    )


def status_text(conn):
    row = conn.execute(
        "SELECT status,finished_at,counters->>'reviews_seen',counters->>'reviews_new' "
        "FROM collection_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return "Сборов пока не было."
    return (f"Последний сбор: {html.escape(row[0])}\n"
            f"Завершён: {html.escape(str(row[1] or '—'))}\n"
            f"Получено отзывов: {row[2] or 0}\n"
            f"Новых отзывов: {row[3] or 0}")


def handle_update(conn, update):
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if chat_id is None:
        return
    if text.startswith("/start"):
        subscribe(conn, chat_id, chat.get("title") or chat.get("username") or chat.get("first_name"))
        send_message(chat_id, "Бот мониторинга Анкетки подключён. Новые отзывы RuStore будут приходить сюда.")
    elif text.startswith("/status"):
        send_message(chat_id, status_text(conn))


def _event_text(rows):
    lines = ["<b>Новые изменения в RuStore</b>"]
    for row in rows:
        event_type, url, author, rating, published, text, response = row
        label = "Новый отзыв" if event_type == "review_new" else "Изменённый отзыв"
        lines.append(
            f"\n<b>{label}</b> · оценка {rating} · {html.escape(author or 'без имени')}\n"
            f"{html.escape(str(published or ''))}\n"
            f"{html.escape(text)}\n<a href=\"{html.escape(url)}\">Открыть в RuStore</a>"
        )
        if response:
            lines.append(f"Ответ разработчика: {html.escape(response)}")
    return "\n".join(lines)


def dispatch(conn):
    """Send each completed collection's new review events once per chat."""
    subscriptions = conn.execute(
        "SELECT id,chat_id,subscribed_at FROM telegram_subscriptions "
        "WHERE project_id=(SELECT id FROM projects WHERE code='anketka') AND enabled"
    ).fetchall()
    for subscription_id, chat_id, subscribed_at in subscriptions:
        runs = conn.execute(
            "SELECT DISTINCT e.run_id FROM change_events e JOIN collection_runs r ON r.id=e.run_id "
            "WHERE r.status IN ('complete','partial') AND e.detected_at >= %s ORDER BY e.run_id", (subscribed_at,)
        ).fetchall()
        for (run_id,) in runs:
            report_key = f"telegram-run-{run_id}"
            report = conn.execute("SELECT id FROM reports WHERE project_id=(SELECT id FROM projects WHERE code='anketka') AND report_key=%s", (report_key,)).fetchone()
            event_rows = conn.execute("SELECT id FROM change_events WHERE run_id=%s AND detected_at >= %s ORDER BY id", (run_id, subscribed_at)).fetchall()
            if not event_rows:
                continue
            if not report:
                report_id = conn.execute(
                    "INSERT INTO reports(project_id,report_key,kind,status,period_from,period_to,scheduled_at,prepared_at) "
                    "SELECT p.id,%s,'daily','ready',min(e.detected_at),max(e.detected_at)+interval '1 second',now(),now() "
                    "FROM projects p JOIN change_events e ON e.run_id=%s WHERE p.code='anketka' AND e.detected_at >= %s GROUP BY p.id RETURNING reports.id",
                    (report_key, run_id, subscribed_at),
                ).fetchone()[0]
            else:
                report_id = report[0]
            for position, row in enumerate(event_rows, 1):
                conn.execute(
                    "INSERT INTO report_items(report_id,event_id,position) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (report_id, row[0], position),
                )
            if conn.execute("SELECT 1 FROM telegram_deliveries WHERE report_id=%s AND subscription_id=%s", (report_id, subscription_id)).fetchone():
                continue
            rows = conn.execute(
                "SELECT e.event_type,r.url,rv.author_name,rv.rating,rv.published_at,rv.text, "
                "(SELECT text FROM reply_versions q JOIN replies rp ON rp.id=q.reply_id "
                "WHERE rp.review_id=rv.review_id AND rp.kind='developer' ORDER BY q.version_no DESC LIMIT 1) "
                "FROM report_items i JOIN change_events e ON e.id=i.event_id "
                "JOIN review_versions rv ON rv.id=e.review_version_id JOIN reviews r ON r.id=rv.review_id "
                "WHERE i.report_id=%s ORDER BY i.position", (report_id,)
            ).fetchall()
            if not rows:
                continue
            payload = _event_text(rows)
            conn.execute(
                "INSERT INTO telegram_deliveries(report_id,subscription_id,part_no,payload,status) VALUES (%s,%s,1,%s,'pending')",
                (report_id, subscription_id, payload),
            )
            try:
                message = send_message(chat_id, payload)
                conn.execute(
                    "UPDATE telegram_deliveries SET status='sent',attempts=attempts+1,telegram_message_id=%s,sent_at=now() "
                    "WHERE report_id=%s AND subscription_id=%s", (message.get("message_id"), report_id, subscription_id)
                )
            except Exception as error:
                conn.execute("UPDATE telegram_deliveries SET status='failed',attempts=attempts+1,last_error=%s WHERE report_id=%s AND subscription_id=%s",
                             (str(error)[:500], report_id, subscription_id))
                print(f"[bot] delivery error for chat {chat_id}: {error}", flush=True)


def poll():
    offset = 0
    while True:
        try:
            updates = telegram("getUpdates", {"timeout": 25, "offset": offset})
            with connect() as conn:
                for update in updates:
                    offset = max(offset, update["update_id"] + 1)
                    handle_update(conn, update)
                dispatch(conn)
        except Exception as error:
            print(f"[bot] polling error: {error}", flush=True)
            time.sleep(5)


def main():
    if not setting("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is not set")
    telegram("setMyCommands", {"commands": [
        {"command": "start", "description": "подключить уведомления"},
        {"command": "status", "description": "статус последнего сбора"},
    ]})
    me = telegram("getMe")
    print(f"[bot] @{me.get('username')} is ready", flush=True)
    poll()


if __name__ == "__main__":
    main()
