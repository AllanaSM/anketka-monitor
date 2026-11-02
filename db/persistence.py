"""Persist a RuStore JSON snapshot in the Anketka PostgreSQL schema."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb


def dt(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _reply_author(reply):
    user = reply.get("user") if isinstance(reply.get("user"), dict) else {}
    return user.get("name") or user.get("firstName") or user.get("login"), user.get("id")


def _reply_kind(reply):
    value = str(reply.get("userType") or reply.get("type") or "").lower()
    return "developer" if "dev" in value or "moder" in value else "user"


def persist_records(records, summary, snapshot_path, database_url=None, requested_from=None,
                    mode="backfill", collector_version="rustore-0.1"):
    """Import records in one transaction and return inserted/updated counters."""
    database_url = database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL persistence")
    run_key = uuid4()
    requested_from = requested_from or summary.get("since")
    if isinstance(requested_from, str):
        requested_from = requested_from[:10]

    counters = {"reviews_seen": len(records), "reviews_new": 0, "reviews_updated": 0,
                "review_versions": 0, "replies_new": 0, "replies_updated": 0,
                "reply_versions": 0, "events": 0}
    diagnostics = {"collector_summary": summary, "snapshot_path": str(snapshot_path)}
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            source_id = conn.execute(
                "SELECT s.id FROM sources s JOIN projects p ON p.id=s.project_id "
                "WHERE p.code='anketka' AND s.platform='rustore' AND s.external_key='ru.anketka'"
            ).fetchone()
            if not source_id:
                raise RuntimeError("RuStore source is missing; run the database migration first")
            source_id = source_id[0]
            run_id = conn.execute(
                "INSERT INTO collection_runs(source_id,run_key,mode,status,requested_from,collector_version,snapshot_path) "
                "VALUES (%s,%s,%s,'running',%s,%s,%s) RETURNING id",
                (source_id, run_key, mode, requested_from, collector_version, str(snapshot_path)),
            ).fetchone()[0]
            try:
                for record in records:
                    external_id = str(record["source_id"])
                    existing = conn.execute(
                        "SELECT id FROM reviews WHERE source_id=%s AND external_id=%s", (source_id, external_id)
                    ).fetchone()
                    observed = dt(record["collected_at"])
                    if existing:
                        review_id = existing[0]
                        conn.execute(
                            "UPDATE reviews SET last_seen_at=%s,last_seen_run_id=%s,likes_count=%s,dislikes_count=%s,replies_count=%s "
                            "WHERE id=%s",
                            (observed, run_id, record.get("likes"), record.get("dislikes"), record.get("replies_count"), review_id),
                        )
                    else:
                        review_id = conn.execute(
                            "INSERT INTO reviews(source_id,external_id,url,url_kind,first_seen_at,last_seen_at,first_seen_run_id,last_seen_run_id,likes_count,dislikes_count,replies_count) "
                            "VALUES (%s,%s,%s,'list',%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                            (source_id, external_id, record["source_url"], observed, observed, run_id, run_id,
                             record.get("likes"), record.get("dislikes"), record.get("replies_count")),
                        ).fetchone()[0]
                        counters["reviews_new"] += 1

                    previous = conn.execute(
                        "SELECT id,content_hash FROM review_versions WHERE review_id=%s ORDER BY version_no DESC LIMIT 1",
                        (review_id,),
                    ).fetchone()
                    if not previous or previous[1] != record["content_hash"]:
                        version_no = conn.execute(
                            "SELECT COALESCE(MAX(version_no),0)+1 FROM review_versions WHERE review_id=%s", (review_id,)
                        ).fetchone()[0]
                        version_id = conn.execute(
                            "INSERT INTO review_versions(review_id,version_no,run_id,observed_at,author_name,text,rating,published_at,source_edited_at,content_hash,raw_payload) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                            (review_id, version_no, run_id, observed, record.get("author"), record["text"], record["rating"],
                             dt(record.get("published_at")), dt(record.get("edited_at")), record["content_hash"], Jsonb(record)),
                        ).fetchone()[0]
                        event_type = "review_new" if not previous else "review_updated"
                        conn.execute(
                            "INSERT INTO change_events(run_id,review_version_id,event_type,origin) VALUES (%s,%s,%s,%s)",
                            (run_id, version_id, event_type, "backfill" if mode == "backfill" else "monitoring"),
                        )
                        counters["review_versions"] += 1
                        counters["events"] += 1
                        if previous:
                            counters["reviews_updated"] += 1

                    replies = list(record.get("replies") or [])
                    if record.get("developer_response") is not None:
                        replies.append({"id": "developer-response", "text": record["developer_response"],
                                        "commentDate": record.get("developer_response_at"), "userType": "DEVELOPER"})
                    for reply in replies:
                        reply_key = str(reply.get("id"))
                        author_name, author_id = _reply_author(reply)
                        if reply_key == "developer-response":
                            reply_key = "developer-response"
                        reply_text = reply.get("text") or ""
                        reply_hash = __import__("hashlib").sha256(
                            json.dumps({"text": reply_text, "author": author_name, "published_at": reply.get("commentDate")},
                                       ensure_ascii=False, sort_keys=True).encode("utf-8")
                        ).hexdigest()
                        row = conn.execute(
                            "SELECT id FROM replies WHERE review_id=%s AND external_key=%s", (review_id, reply_key)
                        ).fetchone()
                        kind = "developer" if reply_key == "developer-response" else _reply_kind(reply)
                        if row:
                            reply_id = row[0]
                            conn.execute("UPDATE replies SET last_seen_at=%s WHERE id=%s", (observed, reply_id))
                        else:
                            reply_id = conn.execute(
                                "INSERT INTO replies(review_id,external_key,kind,first_seen_at,last_seen_at) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                                (review_id, reply_key, kind, observed, observed),
                            ).fetchone()[0]
                            counters["replies_new"] += 1
                        previous_reply = conn.execute(
                            "SELECT id,content_hash FROM reply_versions WHERE reply_id=%s ORDER BY version_no DESC LIMIT 1", (reply_id,)
                        ).fetchone()
                        if not previous_reply or previous_reply[1] != reply_hash:
                            reply_version = conn.execute(
                                "SELECT COALESCE(MAX(version_no),0)+1 FROM reply_versions WHERE reply_id=%s", (reply_id,)
                            ).fetchone()[0]
                            reply_version_id = conn.execute(
                                "INSERT INTO reply_versions(reply_id,version_no,run_id,observed_at,author_name,author_external_id,text,published_at,content_hash,raw_payload) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                                (reply_id, reply_version, run_id, observed, author_name, str(author_id) if author_id is not None else None,
                                 reply_text, dt(reply.get("commentDate")), reply_hash, Jsonb(reply)),
                            ).fetchone()[0]
                            event_type = "reply_new" if not previous_reply else "reply_updated"
                            conn.execute(
                                "INSERT INTO change_events(run_id,reply_version_id,event_type,origin) VALUES (%s,%s,%s,%s)",
                                (run_id, reply_version_id, event_type, "backfill" if mode == "backfill" else "monitoring"),
                            )
                            counters["reply_versions"] += 1
                            counters["events"] += 1
                            if previous_reply:
                                counters["replies_updated"] += 1
                finished = datetime.now(timezone.utc)
                conn.execute(
                    "UPDATE collection_runs SET status=%s,finished_at=%s,counters=%s,diagnostics=%s WHERE id=%s",
                    (summary.get("status", "complete"), finished, Jsonb(counters), Jsonb(diagnostics), run_id),
                )
            except Exception:
                conn.execute("UPDATE collection_runs SET status='failed',finished_at=%s,counters=%s,diagnostics=%s WHERE id=%s",
                             (datetime.now(timezone.utc), Jsonb(counters), Jsonb(diagnostics), run_id))
                raise
    return {"run_id": run_id, **counters}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Path to reviews.json")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--since", default="2026-01-01")
    args = parser.parse_args(argv)
    records = json.loads(args.input.read_text(encoding="utf-8-sig"))
    result = persist_records(records, {"status": "complete", "since": args.since}, args.input, args.database_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
