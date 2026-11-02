"""Read-only RuStore collector. JSON snapshots, no database or AI dependencies."""
import argparse
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

BASE = "https://web-api.rustore.ru/v1/showcase/web/feedbacks/comments"
PACKAGE = "ru.anketka"
SOURCE_URL = "https://www.rustore.ru/catalog/app/ru.anketka/reviews"
MSK = timezone(timedelta(hours=3))


class CollectionError(Exception):
    pass


def timestamp(value):
    if not isinstance(value, str):
        raise CollectionError("Missing or invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CollectionError("Invalid timestamp") from error
    if parsed.tzinfo is None:
        raise CollectionError("Timestamp lacks timezone")
    return parsed


def identifier(value):
    if type(value) is not int or value <= 0:
        raise CollectionError("Invalid record ID")
    return value


def write_json(path, value):
    """Publish a file only after complete UTF-8 serialization."""
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    pending = path.with_suffix(path.suffix + ".part")
    pending.write_text(encoded, encoding="utf-8")
    if json.loads(pending.read_text(encoding="utf-8")) != value:
        raise CollectionError("JSON verification failed")
    os.replace(pending, path)


class Client:
    def __init__(self, delay=1.0, timeout=30, attempts=3, opener=urllib.request.urlopen,
                 sleep=time.sleep):
        self.delay, self.timeout, self.attempts = delay, timeout, attempts
        self.opener, self.sleep = opener, sleep
        self.requests = 0

    def get(self, path, query):
        url = BASE + "/" + path + "?" + urllib.parse.urlencode(query)
        for attempt in range(self.attempts):
            if self.requests:
                self.sleep(self.delay)
            self.requests += 1
            request = urllib.request.Request(url, headers={
                "User-Agent": "Anketka-monitor/0.1", "Accept": "application/json"})
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    data = json.load(response)
                if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
                    raise CollectionError("Unexpected RuStore response schema")
                return data
            except urllib.error.HTTPError as error:
                if error.code not in {408, 429, 500, 502, 503, 504}:
                    error.close()
                    raise CollectionError(f"HTTP {error.code}") from error
                retry_after = error.headers.get("Retry-After", "") if error.headers else ""
                wait = max(2 ** attempt, min(float(retry_after), 300)) if retry_after.isdigit() else 2 ** attempt
                last = f"HTTP {error.code}"
                error.close()
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                wait, last = 2 ** attempt, type(error).__name__
            except (ValueError, UnicodeError) as error:
                raise CollectionError("Response is not valid JSON") from error
            if attempt + 1 < self.attempts:
                self.sleep(wait)
        raise CollectionError(f"Request failed after {self.attempts} attempts: {last}")


def pages(client, path, sort, maximum, raw_dir, prefix):
    cursor = None
    seen = set()
    for index in range(maximum):
        query = {"limit": 30 if sort == "NEW_FIRST" else 10, "sortBy": sort}
        if cursor is not None:
            query["continuation"] = cursor
        data = client.get(path, query)
        write_json(raw_dir / f"{prefix}-{index:04d}.json", data)
        yield data
        cursor = data.get("continuation")
        if cursor is None:
            return
        if not isinstance(cursor, str) or not data["elements"] or cursor in seen:
            raise CollectionError("Broken or repeated pagination cursor")
        seen.add(cursor)
    raise CollectionError(f"Page limit reached ({maximum}); collection incomplete")


def normalize(item, collected_at):
    if not isinstance(item, dict):
        raise CollectionError("Review must be an object")
    rid = identifier(item.get("id"))
    timestamp(item.get("commentDate"))
    for key in ("comment", "firstName"):
        if not isinstance(item.get(key), str):
            raise CollectionError(f"Review {rid}: invalid {key}")
    if type(item.get("rating")) is not int or not 1 <= item["rating"] <= 5:
        raise CollectionError(f"Review {rid}: invalid rating")
    for key in ("editedAt", "devResponseDate"):
        if item.get(key) is not None:
            timestamp(item[key])
    if item.get("devResponse") is not None and not isinstance(item["devResponse"], str):
        raise CollectionError(f"Review {rid}: invalid developer response")
    replies = item.get("replies")
    if not isinstance(replies, list) or type(item.get("totalRepliesCount")) is not int or item['totalRepliesCount'] < 0:
        raise CollectionError(f"Review {rid}: invalid replies")
    reply_ids = set()
    for reply in replies:
        reply_id = identifier(reply.get("id"))
        if reply_id in reply_ids or reply.get("commentId") != rid:
            raise CollectionError(f"Review {rid}: inconsistent reply IDs")
        reply_ids.add(reply_id)
        timestamp(reply.get("commentDate"))
        if not isinstance(reply.get("text"), str):
            raise CollectionError(f"Review {rid}: invalid reply text")
    result = {"source": "rustore", "package_name": PACKAGE, "source_id": str(rid),
              "source_url": SOURCE_URL, "url_kind": "review_list",
              "author": item["firstName"], "text": item["comment"], "rating": item["rating"],
              "published_at": item["commentDate"], "edited_at": item.get("editedAt"),
              "collected_at": collected_at, "developer_response": item.get("devResponse"),
              "developer_response_at": item.get("devResponseDate"),
              "likes": item.get("likesCount"), "dislikes": item.get("dislikesCount"),
              "replies": replies, "replies_count": item["totalRepliesCount"]}
    # Reactions and collection time must not turn into a text-change event.
    content = {k: result[k] for k in ("text", "rating", "author", "developer_response", "developer_response_at")}
    content["replies"] = [{k: r.get(k) for k in ("id", "text", "user", "userType", "commentDate")}
                          for r in sorted(replies, key=lambda r: r["id"])]
    result["content_hash"] = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return result


def collect(client, output, since, max_pages=100):
    started = datetime.now(timezone.utc)
    run_dir = Path(output) / (started.strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    records, seen, warnings, errors, counts = [], {}, [], [], []
    page_count = 0
    summary = {"schema_version": 1, "source": "rustore", "package_name": PACKAGE,
               "started_at": started.isoformat(), "since": since.isoformat(),
               "filter_timezone": "Europe/Moscow", "status": "running"}
    write_json(run_dir / "summary.json", summary)
    try:
        for data in pages(client, PACKAGE, "NEW_FIRST", max_pages, raw_dir, "reviews"):
            page_count += 1
            if type(data.get("elementsCount")) is not int or data['elementsCount'] < 0:
                raise CollectionError("Missing or invalid source review count")
            counts.append(data["elementsCount"])
            for item in data["elements"]:
                rid = identifier(item.get("id"))
                if rid in seen:
                    warnings.append(f"Repeated review ID: {rid}")
                    continue
                seen[rid] = True
                try:
                    record = normalize(item, datetime.now(timezone.utc).isoformat())
                    if timestamp(record["published_at"]).astimezone(MSK).date() < since:
                        continue
                    if len(record["replies"]) != record["replies_count"]:
                        replies = []
                        for reply_page in pages(client, f"{rid}/replies", "OLD_FIRST", max_pages, raw_dir, f"replies-{rid}"):
                            replies.extend(reply_page["elements"])
                        item = {**item, "replies": replies}
                        record = normalize(item, record['collected_at'])
                        if len(replies) != record["replies_count"]:
                            raise CollectionError(f"Review {rid}: reply count mismatch")
                    records.append(record)
                except (CollectionError, AttributeError, TypeError) as error:
                    errors.append(f"Review {rid}: {error}")
            print(f"[rustore] page={page_count} seen={len(seen)} selected={len(records)}", flush=True)
        if counts and (len(set(counts)) != 1 or len(seen) != counts[-1]):
            warnings.append("Source count changed or differs from unique collected reviews")
        summary["status"] = "complete" if not errors and not warnings else "partial"
    except (CollectionError, urllib.error.URLError, OSError, ValueError, TypeError, AttributeError) as error:
        errors.append(str(error))
        summary["status"] = "failed"
    records.sort(key=lambda r: (r["published_at"], r["source_id"]), reverse=True)
    summary.update(finished_at=datetime.now(timezone.utc).isoformat(), pages=page_count,
                   unique_reviews_seen=len(seen), source_counts=sorted(set(counts)),
                   selected_reviews=len(records), with_developer_response=sum(bool(r['developer_response']) for r in records),
                   replies=sum(len(r['replies']) for r in records), requests=client.requests,
                   warnings=warnings, errors=errors)
    write_json(run_dir / "reviews.json", records)
    write_json(run_dir / "summary.json", summary)
    print(f"[rustore] {summary['status']}: {run_dir}", flush=True)
    return summary, run_dir


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def delay_value(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be finite and nonnegative")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "rustore")
    parser.add_argument("--max-pages", type=positive_int, default=100)
    parser.add_argument("--delay", type=delay_value, default=1.0)
    parser.add_argument("--timeout", type=positive_int, default=30)
    parser.add_argument("--attempts", type=positive_int, default=3)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"),
                        help="Persist the completed JSON snapshot in PostgreSQL")
    parser.add_argument("--no-persist", action="store_true",
                        help="Do not persist the snapshot even when DATABASE_URL is set")
    args = parser.parse_args(argv)
    summary, run_dir = collect(Client(args.delay, args.timeout, args.attempts), args.output, args.since, args.max_pages)
    if args.database_url and not args.no_persist and summary["status"] != "failed":
        from db.persistence import persist_records
        snapshot = run_dir / "reviews.json"
        records = json.loads(snapshot.read_text(encoding="utf-8"))
        result = persist_records(records, summary, snapshot, args.database_url,
                                 requested_from=args.since.isoformat(), mode="backfill")
        print(f"[rustore] persisted run={result['run_id']} reviews={result['reviews_new']} new, "
              f"{result['review_versions']} versions, {result['events']} events", flush=True)
    return 0 if summary['status'] == 'complete' else 1


if __name__ == "__main__":
    raise SystemExit(main())
