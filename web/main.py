"""Read-only RuStore review feed for Telegram Mini App and a regular browser."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
from urllib.parse import parse_qsl
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


def admin_chat_ids():
    result = set()
    for value in os.environ.get("FAQ_ADMIN_CHAT_IDS", "").split(","):
        try:
            if value.strip():
                result.add(int(value.strip()))
        except ValueError:
            continue
    return result


def telegram_webapp_chat_id(init_data, bot_token=None, now=None, max_age=86400):
    """Verify Telegram Web App initData and return the embedded user ID."""
    bot_token = bot_token or os.environ.get("TELEGRAM_TOKEN", "")
    if not init_data or not bot_token:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        values = dict(pairs)
        received_hash = values.pop("hash", "")
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        user_id = int(user["id"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        return None
    current = int((now or datetime.now(timezone.utc)).timestamp())
    if auth_date > current + 60 or current - auth_date > max_age:
        return None
    return user_id


def authorized_admin(init_data, now=None):
    chat_id = telegram_webapp_chat_id(init_data, now=now)
    return chat_id if chat_id in admin_chat_ids() else None


def analytics_page_data(conn, run_id=None):
    if run_id is None:
        row = conn.execute(
            "SELECT id,mode,faq_collection_run_id,finished_at,input_count,result_count "
            "FROM analysis_runs ORDER BY finished_at DESC,id DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id,mode,faq_collection_run_id,finished_at,input_count,result_count "
            "FROM analysis_runs WHERE id=%s", (run_id,)
        ).fetchone()
    if not row:
        return {"available": False, "runs": [], "pains": [], "reviews": [], "candidates": []}
    selected_run_id, mode, faq_run_id, finished_at, input_count, result_count = row
    runs = conn.execute(
        "SELECT id,mode,faq_collection_run_id,finished_at,input_count,result_count "
        "FROM analysis_runs ORDER BY finished_at DESC,id DESC LIMIT 20"
    ).fetchall()
    analyzed_count = conn.execute(
        "SELECT count(DISTINCT rv.review_id) FROM review_analysis ra "
        "JOIN review_versions rv ON rv.id=ra.review_version_id WHERE ra.analysis_run_id=%s",
        (selected_run_id,),
    ).fetchone()[0]
    pain_rows = conn.execute(
        "SELECT pd.pain_key,pd.label,pd.area_label,pd.status,count(DISTINCT rv.review_id) "
        "FROM review_analysis_pains rap "
        "JOIN review_analysis ra ON ra.id=rap.review_analysis_id "
        "JOIN review_versions rv ON rv.id=ra.review_version_id "
        "JOIN pain_dictionary pd ON pd.id=rap.pain_id "
        "WHERE ra.analysis_run_id=%s GROUP BY pd.id "
        "ORDER BY count(DISTINCT rv.review_id) DESC,pd.label",
        (selected_run_id,),
    ).fetchall()
    reviews = conn.execute(
        "SELECT ra.review_version_id,rv.review_id,rv.version_no,rv.author_name,rv.rating,rv.text,"
        "ra.tone,ra.summary,ra.needs_review "
        "FROM review_analysis ra JOIN review_versions rv ON rv.id=ra.review_version_id "
        "WHERE ra.analysis_run_id=%s ORDER BY rv.observed_at DESC,ra.id DESC LIMIT 100",
        (selected_run_id,),
    ).fetchall()
    candidates = conn.execute(
        "SELECT id,pain_key,area_label,label,description,metadata "
        "FROM pain_dictionary WHERE status='candidate' ORDER BY updated_at DESC LIMIT 100"
    ).fetchall()
    return {
        "available": True,
        "selected_run": {
            "id": selected_run_id, "mode": mode, "faq_collection_run_id": faq_run_id,
            "finished_at": iso(finished_at), "input_count": input_count, "result_count": result_count,
            "analyzed_review_count": analyzed_count,
        },
        "runs": [
            {"id": item[0], "mode": item[1], "faq_collection_run_id": item[2],
             "finished_at": iso(item[3]), "input_count": item[4], "result_count": item[5]}
            for item in runs
        ],
        "pains": [
            {"pain_key": item[0], "label": item[1], "area_label": item[2], "status": item[3],
             "pain_review_count": item[4], "analyzed_review_count": analyzed_count,
             "share_percent": round(item[4] / analyzed_count * 100, 2) if analyzed_count else 0}
            for item in pain_rows
        ],
        "reviews": [
            {"review_version_id": item[0], "review_id": item[1], "version_no": item[2],
             "author": item[3] or "Без имени", "rating": item[4], "text": item[5],
             "tone": item[6], "summary": item[7], "needs_review": item[8]}
            for item in reviews
        ],
        "candidates": [
            {"id": item[0], "pain_key": item[1], "area_label": item[2], "label": item[3],
             "description": item[4], "metadata": item[5] or {}}
            for item in candidates
        ],
    }


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
                elif path == "/api/analytics":
                    body, status, kind = b"method not allowed", 405, "text/plain; charset=utf-8"
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

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/analytics":
            self.send_response(404)
            self.end_headers()
            return
        init_data = self.headers.get("X-Telegram-Init-Data", "")
        if not authorized_admin(init_data):
            self.send_response(403)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"forbidden")
            return
        try:
            with connect() as conn:
                body = json.dumps(analytics_page_data(conn), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as error:
            print(f"[web] analytics request failed: {error}", flush=True)
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"analytics unavailable")

    def log_message(self, *_args):
        pass


def main(_argv=None):
    with connect() as conn:
        conn.execute("SELECT 1")
    print(f"[web] feed listening on 0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
