"""Database selection rules for incremental and retrospective analysis."""

from __future__ import annotations


def _review_row(row):
    (
        review_id, review_version_id, version_no, event_type, author, text, rating,
        published_at, observed_at, url,
    ) = row
    return {
        "review_id": review_id,
        "review_version_id": review_version_id,
        "version_no": version_no,
        "status": "new" if event_type == "review_new" else "updated",
        "author": author,
        "text": text,
        "rating": rating,
        "published_at": published_at,
        "observed_at": observed_at,
        "url": url,
    }


def select_incremental_reviews(conn, collection_run_id):
    """Select review versions from one complete, not-yet-analyzed RuStore run."""
    rows = conn.execute(
        "SELECT r.id,rv.id,rv.version_no,e.event_type,rv.author_name,rv.text,rv.rating,"
        "rv.published_at,rv.observed_at,r.url "
        "FROM change_events e "
        "JOIN collection_runs cr ON cr.id=e.run_id "
        "JOIN sources s ON s.id=cr.source_id "
        "JOIN review_versions rv ON rv.id=e.review_version_id "
        "JOIN reviews r ON r.id=rv.review_id "
        "WHERE e.run_id=%s AND cr.status='complete' AND s.platform='rustore' "
        "AND e.review_version_id IS NOT NULL "
        "AND e.event_type IN ('review_new','review_updated') "
        "AND NOT EXISTS (SELECT 1 FROM analysis_runs ar "
        "WHERE ar.mode='incremental' AND ar.source_collection_run_id=e.run_id) "
        "ORDER BY r.id,rv.version_no",
        (collection_run_id,),
    ).fetchall()
    return [_review_row(row) for row in rows]


def select_retrospective_reviews(conn, project_id):
    """Select every stored review version for an explicit retrospective run."""
    rows = conn.execute(
        "SELECT r.id,rv.id,rv.version_no,"
        "CASE WHEN rv.version_no=1 THEN 'review_new' ELSE 'review_updated' END,"
        "rv.author_name,rv.text,rv.rating,rv.published_at,rv.observed_at,r.url "
        "FROM reviews r "
        "JOIN sources s ON s.id=r.source_id "
        "JOIN review_versions rv ON rv.review_id=r.id "
        "WHERE s.project_id=%s AND s.platform='rustore' "
        "ORDER BY r.id,rv.version_no",
        (project_id,),
    ).fetchall()
    return [_review_row(row) for row in rows]


def select_latest_faq_run(conn, project_id, as_of):
    """Return the latest completed FAQ run finished no later than ``as_of``."""
    row = conn.execute(
        "SELECT cr.id FROM collection_runs cr "
        "JOIN sources s ON s.id=cr.source_id "
        "WHERE s.project_id=%s AND s.platform='faq' AND cr.status='complete' "
        "AND cr.finished_at IS NOT NULL AND cr.finished_at <= %s "
        "ORDER BY cr.finished_at DESC,cr.id DESC LIMIT 1",
        (project_id, as_of),
    ).fetchone()
    return row[0] if row else None


def select_faq_items(conn, faq_collection_run_id):
    """Select the latest FAQ version per item as of the bound FAQ run."""
    rows = conn.execute(
        "SELECT v.id,i.external_key,i.question,v.answer "
        "FROM faq_item_versions v "
        "JOIN faq_items i ON i.id=v.faq_item_id "
        "JOIN collection_runs fr ON fr.id=%s "
        "WHERE v.observed_at <= fr.finished_at "
        "AND v.version_no=(SELECT MAX(v2.version_no) FROM faq_item_versions v2 "
        "WHERE v2.faq_item_id=v.faq_item_id AND v2.observed_at <= fr.finished_at) "
        "ORDER BY i.id",
        (faq_collection_run_id,),
    ).fetchall()
    return [
        {
            "faq_item_version_id": row[0],
            "external_key": row[1],
            "question": row[2],
            "answer": row[3],
        }
        for row in rows
    ]
