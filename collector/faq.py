"""Collect the public Anketka FAQ page into JSON and optionally PostgreSQL."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


SOURCE_URL = "https://www.anketka.ru/faq"
QUESTION_TAGS = {"h2", "h3", "h4", "summary", "button"}
IGNORED_TAGS = {"script", "style", "noscript", "svg"}


class CollectionError(Exception):
    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", value).strip()


class FAQParser:
    """Small dependency-free parser for the FAQ's question/answer blocks."""

    def __init__(self):
        from html.parser import HTMLParser

        class Parser(HTMLParser):
            def __init__(self, owner):
                super().__init__(convert_charrefs=True)
                self.owner = owner

            def handle_starttag(self, tag, attrs):
                self.owner.start(tag.lower())

            def handle_startendtag(self, tag, attrs):
                self.owner.end(tag.lower())

            def handle_endtag(self, tag):
                self.owner.end(tag.lower())

            def handle_data(self, data):
                self.owner.data(data)

        self._parser = Parser(self)
        self.items = []
        self._heading_tag = None
        self._heading_text = []
        self._question = None
        self._answer = []
        self._ignored = 0

    def start(self, tag):
        if tag in IGNORED_TAGS:
            self._ignored += 1
        if self._ignored:
            return
        if tag in QUESTION_TAGS:
            self._heading_tag = tag
            self._heading_text = []

    def end(self, tag):
        if tag in IGNORED_TAGS:
            self._ignored = max(0, self._ignored - 1)
            return
        if self._ignored:
            return
        if tag == self._heading_tag:
            heading = clean_text("".join(self._heading_text))
            self._heading_tag = None
            self._heading_text = []
            if "?" in heading:
                self._flush()
                self._question = heading
                self._answer = []

    def data(self, value):
        if self._ignored:
            return
        if self._heading_tag:
            self._heading_text.append(value)
        elif self._question:
            self._answer.append(value)

    def _flush(self):
        if self._question:
            answer = clean_text(" ".join(self._answer))
            if answer:
                self.items.append({"question": self._question, "answer": answer})

    def parse(self, document):
        self._parser.feed(document)
        self._parser.close()
        self._flush()
        return self.items


def normalize_items(items, collected_at):
    result = []
    seen = set()
    for item in items:
        question = clean_text(item.get("question", ""))
        answer = clean_text(item.get("answer", ""))
        if not question or not answer or question in seen:
            continue
        seen.add(question)
        content = {"question": question, "answer": answer}
        content_hash = hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        result.append({
            "source": "anketka_faq",
            "source_url": SOURCE_URL,
            "external_key": hashlib.sha256(question.casefold().encode("utf-8")).hexdigest(),
            "question": question,
            "answer": answer,
            "collected_at": collected_at,
            "content_hash": content_hash,
        })
    if not result:
        raise CollectionError("No FAQ question/answer blocks found")
    return result


def fetch(url=SOURCE_URL, timeout=30, attempts=3, opener=urllib.request.urlopen, sleep=time.sleep):
    last = None
    diagnostics = {"url": url, "attempts": []}
    for attempt in range(attempts):
        started = time.perf_counter()
        request = urllib.request.Request(url, headers={
            "User-Agent": "Anketka-monitor/0.1 (+FAQ collector)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        })
        try:
            with opener(request, timeout=timeout) as response:
                body = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                status = getattr(response, "status", None) or getattr(response, "getcode", lambda: None)()
                final_url = getattr(response, "geturl", lambda: url)()
                headers = dict(response.headers.items())
            decoded = body.decode(charset, errors="replace")
            diagnostics["attempts"].append({
                "attempt": attempt + 1,
                "status": status,
                "final_url": final_url,
                "content_type": headers.get("Content-Type"),
                "content_length": len(body),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "body_preview": clean_text(decoded[:500]),
            })
            diagnostics.update(status=status, final_url=final_url, headers=headers)
            return decoded, diagnostics
        except urllib.error.HTTPError as error:
            last = f"HTTP {error.code}"
            body = error.read(500) if hasattr(error, "read") else b""
            headers = dict(error.headers.items()) if error.headers else {}
            diagnostics["attempts"].append({
                "attempt": attempt + 1,
                "status": error.code,
                "final_url": getattr(error, "url", url),
                "content_type": headers.get("Content-Type"),
                "content_length": len(body),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "body_preview": clean_text(body.decode("utf-8", errors="replace")),
            })
            diagnostics.update(status=error.code, final_url=getattr(error, "url", url), headers=headers)
            error.close()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last = type(error).__name__
            diagnostics["attempts"].append({
                "attempt": attempt + 1,
                "error": last,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            })
        if attempt + 1 < attempts:
            sleep(2 ** attempt)
    raise CollectionError(f"FAQ request failed after {attempts} attempts: {last}", diagnostics)


def write_json(path, value):
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    pending = path.with_suffix(path.suffix + ".part")
    pending.write_text(encoded, encoding="utf-8")
    json.loads(pending.read_text(encoding="utf-8"))
    os.replace(pending, path)


def collect(output, url=SOURCE_URL, timeout=30, attempts=3,
            opener=urllib.request.urlopen, sleep=time.sleep):
    started = datetime.now(timezone.utc)
    run_dir = Path(output) / (started.strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    run_dir.mkdir(parents=True)
    summary = {
        "schema_version": 1,
        "source": "anketka_faq",
        "source_url": url,
        "started_at": started.isoformat(),
        "status": "running",
    }
    write_json(run_dir / "summary.json", summary)
    diagnostics = {"url": url, "attempts": []}
    try:
        document, diagnostics = fetch(url, timeout=timeout, attempts=attempts, opener=opener, sleep=sleep)
        (run_dir / "raw.html").write_text(document, encoding="utf-8")
        items = normalize_items(FAQParser().parse(document), datetime.now(timezone.utc).isoformat())
        summary.update(status="complete", items=len(items), http_status=diagnostics.get("status"),
                       response_url=diagnostics.get("final_url"),
                       finished_at=datetime.now(timezone.utc).isoformat())
        write_json(run_dir / "faq.json", items)
    except (CollectionError, OSError, UnicodeError) as error:
        if isinstance(error, CollectionError) and error.diagnostics:
            diagnostics = error.diagnostics
        diagnostics["error"] = str(error)
        summary.update(status="failed", error=str(error), finished_at=datetime.now(timezone.utc).isoformat())
        write_json(run_dir / "faq.json", [])
    write_json(run_dir / "diagnostics.json", diagnostics)
    write_json(run_dir / "summary.json", summary)
    print(f"[faq] {summary['status']}: {run_dir}", flush=True)
    return summary, run_dir


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=SOURCE_URL)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "faq")
    parser.add_argument("--timeout", type=positive_int, default=30)
    parser.add_argument("--attempts", type=positive_int, default=3)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"),
                        help="Persist the completed FAQ JSON snapshot in PostgreSQL")
    parser.add_argument("--no-persist", action="store_true",
                        help="Do not persist the snapshot even when DATABASE_URL is set")
    args = parser.parse_args(argv)
    summary, run_dir = collect(args.output, args.url, args.timeout, args.attempts)
    if args.database_url and not args.no_persist and summary["status"] != "failed":
        from db.persistence import persist_faq_records
        snapshot = run_dir / "faq.json"
        records = json.loads(snapshot.read_text(encoding="utf-8"))
        result = persist_faq_records(records, summary, snapshot, args.database_url)
        print(f"[faq] persisted run={result['run_id']} items={result['faq_items_new']} new, "
              f"{result['faq_item_versions']} versions", flush=True)
    return 0 if summary["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
