"""Transactional persistence for completed review analysis runs."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from psycopg.types.json import Jsonb

from .domain import candidate_pain_key
from .schema import validate_analysis_result


def _candidate_metadata(candidate, analysis_run_id, review_version_id):
    return {
        "candidate": candidate,
        "analysis_run_id": analysis_run_id,
        "example_review_version_ids": [review_version_id],
    }


def _resolve_pain_id(conn, project_id, pain, analysis_run_id, review_version_id, created_by_chat_id):
    if pain["pain_key"] is not None:
        row = conn.execute(
            "SELECT id FROM pain_dictionary WHERE project_id=%s AND pain_key=%s",
            (project_id, pain["pain_key"]),
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown pain_key: {pain['pain_key']}")
        return row[0]

    candidate = pain["candidate"]
    pain_key = candidate_pain_key(candidate)
    conn.execute(
        "INSERT INTO pain_dictionary(project_id,pain_key,area_key,area_label,label,description,status,"
        "created_by_chat_id,metadata) VALUES (%s,%s,%s,%s,%s,%s,'candidate',%s,%s) "
        "ON CONFLICT (project_id,pain_key) DO NOTHING",
        (project_id, pain_key, candidate["area_key"], candidate["area_label"], candidate["label"],
         candidate["description"], created_by_chat_id,
         Jsonb(_candidate_metadata(candidate, analysis_run_id, review_version_id))),
    )
    row = conn.execute(
        "SELECT id FROM pain_dictionary WHERE project_id=%s AND pain_key=%s",
        (project_id, pain_key),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Candidate pain was not stored: {pain_key}")
    return row[0]


def persist_completed_run(conn, *, project_id, mode, source_collection_run_id,
                          faq_collection_run_id, requested_by_chat_id, model_provider,
                          model_name, prompt_version, results, started_at=None,
                          run_key=None, diagnostics=None):
    """Persist one complete run atomically, returning its database ID.

    The caller supplies one item per analyzed review version. A candidate pain is
    inserted into the shared dictionary with status ``candidate`` and is never
    auto-approved.
    """
    items = list(results)
    started_at = started_at or datetime.now(timezone.utc)
    run_key = run_key or uuid4()
    diagnostics = diagnostics or {}
    with conn.transaction():
        run_id = conn.execute(
            "INSERT INTO analysis_runs(run_key,project_id,mode,source_collection_run_id,"
            "faq_collection_run_id,requested_by_chat_id,status,model_provider,model_name,"
            "prompt_version,started_at,finished_at,input_count,result_count,error_count,diagnostics) "
            "VALUES (%s,%s,%s,%s,%s,%s,'complete',%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (run_key, project_id, mode, source_collection_run_id, faq_collection_run_id,
             requested_by_chat_id, model_provider, model_name, prompt_version, started_at,
             started_at, len(items), len(items), 0, Jsonb(diagnostics)),
        ).fetchone()[0]
        for item in items:
            result = validate_analysis_result(item["result"])
            analysis_id = conn.execute(
                "INSERT INTO review_analysis(analysis_run_id,review_version_id,tone,summary,"
                "confidence,needs_review,raw_result) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (run_id, item["review_version_id"], result["tone"], result["summary"],
                 Jsonb(result["confidence"]), result["needs_review"], Jsonb(result)),
            ).fetchone()[0]
            for pain in result["pains"]:
                pain_id = _resolve_pain_id(
                    conn, project_id, pain, run_id, item["review_version_id"], requested_by_chat_id
                )
                conn.execute(
                    "INSERT INTO review_analysis_pains(review_analysis_id,pain_id,intensity,"
                    "pain_evidence,confidence,faq_status,faq_item_version_id,faq_evidence) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (analysis_id, pain_id, pain["intensity"], pain["pain_evidence"],
                     Jsonb(pain["confidence"]), pain["faq_status"],
                     pain["faq_item_version_id"], pain["faq_evidence"]),
                )
        finished_at = datetime.now(timezone.utc)
        conn.execute(
            "UPDATE analysis_runs SET finished_at=%s WHERE id=%s",
            (finished_at, run_id),
        )
    return run_id
