"""Read-only RuStore review feed for Telegram Mini App and a regular browser."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg


HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "8080"))


def connect():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(database_url, autocommit=True)


def iso(value):
    if not value:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def page_data(conn):
    latest_run = conn.execute(
        "SELECT r.id,r.status,r.finished_at,r.counters "
        "FROM collection_runs r JOIN sources s ON s.id=r.source_id "
        "WHERE s.platform='rustore' ORDER BY r.id DESC LIMIT 1"
    ).fetchone()
    latest_run_id = latest_run[0] if latest_run else None
    rows = conn.execute(
        "WITH latest_versions AS ("
        " SELECT DISTINCT ON (rv.review_id) rv.id,rv.review_id,rv.run_id,rv.version_no,"
        " rv.observed_at,rv.author_name,rv.text,rv.rating,rv.published_at,rv.source_edited_at"
        " FROM review_versions rv ORDER BY rv.review_id,rv.version_no DESC)"
        " SELECT r.id,s.name,s.url,r.url,lv.version_no,lv.run_id,lv.observed_at,"
        " lv.author_name,lv.text,lv.rating,lv.published_at,lv.source_edited_at,"
        " dr.text"
        " FROM latest_versions lv JOIN reviews r ON r.id=lv.review_id"
        " JOIN sources s ON s.id=r.source_id"
        " LEFT JOIN LATERAL ("
        "   SELECT q.text FROM reply_versions q JOIN replies rp ON rp.id=q.reply_id"
        "   WHERE rp.review_id=r.id AND rp.kind='developer'"
        "   ORDER BY q.version_no DESC LIMIT 1"
        " ) dr ON TRUE"
        " WHERE s.platform='rustore'"
        " ORDER BY COALESCE(lv.published_at,lv.observed_at) DESC,lv.id DESC LIMIT 300"
    ).fetchall()
    reviews = []
    for row in rows:
        (review_id, source, source_url, url, version_no, run_id, observed_at,
         author, text, rating, published_at, edited_at, developer_response) = row
        reviews.append({
            "id": review_id,
            "source": source,
            "source_url": source_url,
            "url": url,
            "version": version_no,
            "run_id": run_id,
            "collected_at": iso(observed_at),
            "author": author or "Без имени",
            "text": text,
            "rating": rating,
            "published_at": iso(published_at),
            "edited_at": iso(edited_at),
            "developer_response": developer_response,
            "is_new": run_id == latest_run_id,
        })
    totals = conn.execute(
        "SELECT count(*) FROM reviews r JOIN sources s ON s.id=r.source_id WHERE s.platform='rustore'"
    ).fetchone()[0]
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totals": {"reviews": totals, "shown": len(reviews)},
        "last_run": {
            "id": latest_run[0],
            "status": latest_run[1],
            "finished_at": iso(latest_run[2]),
            "counters": latest_run[3] or {},
        } if latest_run else None,
        "reviews": reviews,
    }


def render(conn):
    template = (HERE / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(page_data(conn), ensure_ascii=False).replace("</", "<\\/")
    return template.replace("/*DATA*/null/*END*/", payload)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            with connect() as conn:
                if path == "/health":
                    conn.execute("SELECT 1")
                    body, status, kind = b"ok", 200, "text/plain; charset=utf-8"
                elif path in {"/", "/index.html"}:
                    body, status, kind = render(conn).encode("utf-8"), 200, "text/html; charset=utf-8"
                else:
                    body, status, kind = b"not found", 404, "text/plain; charset=utf-8"
        except Exception as error:
            print(f"[web] request failed: {error}", flush=True)
            body, status, kind = b"database unavailable", 503, "text/plain; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main(_argv=None):
    with connect() as conn:
        conn.execute("SELECT 1")
    print(f"[web] feed listening on 0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
