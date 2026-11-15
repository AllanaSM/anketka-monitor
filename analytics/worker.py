"""Incremental analytics worker for completed RuStore collection runs."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import psycopg

from .client import StructuredAnalyticsClient
from .persistence import persist_completed_run
from .selection import (
    select_faq_items,
    select_incremental_reviews,
    select_latest_faq_run,
    select_retrospective_reviews,
)


class AnalyticsWorkerError(RuntimeError):
    """Raised when a collection run cannot be analyzed completely."""


def _setting(name, default=""):
    return os.environ.get(name, default).strip()


def _bool_setting(name, default=False):
    value = _setting(name, "true" if default else "false").lower()
    return value in {"1", "true", "yes", "on"}


def _positive_setting(name, default):
    value = int(_setting(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def notify_admins(message):
    """Send worker errors only to the configured FAQ admin chats."""
    from bot.anketka import admin_chat_ids, send_message

    for chat_id in sorted(admin_chat_ids()):
        try:
            send_message(chat_id, message[:3500])
        except Exception as error:
            print(f"[analytics] admin notification error: {error}", flush=True)


def pending_collection_runs(conn, limit=1):
    rows = conn.execute(
        "SELECT cr.id,s.project_id,cr.finished_at FROM collection_runs cr "
        "JOIN sources s ON s.id=cr.source_id "
        "WHERE s.platform='rustore' AND s.external_key='ru.anketka' "
        "AND cr.status='complete' "
        "AND NOT EXISTS (SELECT 1 FROM analysis_runs ar "
        "WHERE ar.mode='incremental' AND ar.source_collection_run_id=cr.id) "
        "ORDER BY cr.finished_at,cr.id LIMIT %s",
        (limit,),
    ).fetchall()
    return [{"id": row[0], "project_id": row[1], "finished_at": row[2]} for row in rows]


def _pain_dictionary(conn, project_id):
    rows = conn.execute(
        "SELECT pain_key,area_key,area_label,label,description,status "
        "FROM pain_dictionary WHERE project_id=%s AND status='approved' "
        "ORDER BY id",
        (project_id,),
    ).fetchall()
    return [
        {
            "pain_key": row[0], "area_key": row[1], "area_label": row[2],
            "label": row[3], "description": row[4], "status": row[5],
        }
        for row in rows
    ]


def process_collection_run(conn, client, collection_run, *, dry_run=False,
                           model_provider="unknown"):
    """Analyze one collection run and persist it only after every input succeeds."""
    started_at = datetime.now(timezone.utc)
    faq_run_id = select_latest_faq_run(conn, collection_run["project_id"], started_at)
    if faq_run_id is None:
        raise AnalyticsWorkerError("No completed FAQ run is available for analytics")
    reviews = select_incremental_reviews(conn, collection_run["id"])
    faq_items = select_faq_items(conn, faq_run_id)
    pain_dictionary = _pain_dictionary(conn, collection_run["project_id"])
    results = []
    for review in reviews:
        results.append({
            "review_id": review["review_id"],
            "review_version_id": review["review_version_id"],
            "version_no": review["version_no"],
            "text": review["text"],
            "result": client.analyze(review, faq_items, pain_dictionary),
        })
    if dry_run:
        return {
            "collection_run_id": collection_run["id"],
            "faq_collection_run_id": faq_run_id,
            "input_count": len(reviews),
            "persisted": False,
        }
    run_id = persist_completed_run(
        conn,
        project_id=collection_run["project_id"],
        mode="incremental",
        source_collection_run_id=collection_run["id"],
        faq_collection_run_id=faq_run_id,
        requested_by_chat_id=None,
        model_provider=model_provider,
        model_name=client.model_name,
        prompt_version=client.prompt_version,
        results=results,
        started_at=started_at,
        diagnostics={"source": "analytics_worker", "dry_run": False},
    )
    return {
        "collection_run_id": collection_run["id"],
        "analysis_run_id": run_id,
        "faq_collection_run_id": faq_run_id,
        "input_count": len(reviews),
        "persisted": True,
    }


def run_retrospective(conn, client, *, project_id, requested_by_chat_id,
                      dry_run=False, model_provider="unknown"):
    """Analyze all stored review versions for one explicit admin request."""
    started_at = datetime.now(timezone.utc)
    faq_run_id = select_latest_faq_run(conn, project_id, started_at)
    if faq_run_id is None:
        raise AnalyticsWorkerError("No completed FAQ run is available for analytics")
    reviews = select_retrospective_reviews(conn, project_id)
    faq_items = select_faq_items(conn, faq_run_id)
    pain_dictionary = _pain_dictionary(conn, project_id)
    results = []
    for review in reviews:
        results.append({
            "review_id": review["review_id"],
            "review_version_id": review["review_version_id"],
            "version_no": review["version_no"],
            "text": review["text"],
            "result": client.analyze(review, faq_items, pain_dictionary),
        })
    if dry_run:
        return {
            "mode": "retrospective",
            "faq_collection_run_id": faq_run_id,
            "input_count": len(reviews),
            "persisted": False,
        }
    run_id = persist_completed_run(
        conn,
        project_id=project_id,
        mode="retrospective",
        source_collection_run_id=None,
        faq_collection_run_id=faq_run_id,
        requested_by_chat_id=requested_by_chat_id,
        model_provider=model_provider,
        model_name=client.model_name,
        prompt_version=client.prompt_version,
        results=results,
        started_at=started_at,
        diagnostics={"source": "analytics_retro", "dry_run": False},
    )
    return {
        "mode": "retrospective",
        "analysis_run_id": run_id,
        "faq_collection_run_id": faq_run_id,
        "input_count": len(reviews),
        "persisted": True,
    }


def run_once(database_url, client=None, *, dry_run=False, max_runs=1, model_provider="unknown"):
    if not database_url:
        raise ValueError("DATABASE_URL is required")
    client = client or StructuredAnalyticsClient.from_env()
    results = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        for collection_run in pending_collection_runs(conn, max_runs):
            results.append(process_collection_run(
                conn, client, collection_run, dry_run=dry_run, model_provider=model_provider,
            ))
    return results


def main(_argv=None):
    if not _bool_setting("ANALYTICS_ENABLED"):
        print("[analytics] disabled by ANALYTICS_ENABLED", flush=True)
        return 0
    database_url = _setting("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    interval = _positive_setting("ANALYTICS_INTERVAL_SECONDS", 300)
    max_runs = _positive_setting("ANALYTICS_MAX_RUNS_PER_TICK", 1)
    dry_run = _bool_setting("ANALYTICS_DRY_RUN")
    provider = _setting("ANALYTICS_PROVIDER", "unknown")
    client = StructuredAnalyticsClient.from_env()
    while True:
        try:
            results = run_once(
                database_url, client, dry_run=dry_run,
                max_runs=max_runs, model_provider=provider,
            )
            if results:
                print(f"[analytics] processed {len(results)} collection run(s)", flush=True)
        except Exception as error:
            message = f"Ошибка аналитики: {str(error)[:500]}"
            print(f"[analytics] worker error: {message}", flush=True)
            notify_admins(message)
        time.sleep(interval)
