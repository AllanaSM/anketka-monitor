import io
import json
import tempfile
import unittest
import urllib.error
from datetime import date

from collector.rustore import Client, collect, normalize


def review(rid=1, **changes):
    return {"id": rid, "rating": 3, "firstName": "Анна", "comment": "Отзыв: всё работает",
            "commentDate": "2026-01-01T10:00:00Z", "replies": [], "totalRepliesCount": 0, **changes}


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = 0

    def get(self, path, query):
        self.requests += 1
        return next(self.responses)


class CollectorTests(unittest.TestCase):
    def run_collection(self, responses, maximum=100):
        with tempfile.TemporaryDirectory() as folder:
            result, path = collect(FakeClient(responses), folder, date(2026, 1, 1), maximum)
            records = json.loads((path / "reviews.json").read_text(encoding="utf-8"))
            return result, records

    def test_moscow_boundary_and_full_pagination(self):
        result, records = self.run_collection([
            {"elements": [review(1, commentDate="2025-12-31T21:01:00Z")], "elementsCount": 2, "continuation": "0"},
            {"elements": [review(2, commentDate="2025-12-31T20:59:00Z")], "elementsCount": 2},
        ])
        self.assertEqual(result["status"], "complete")
        self.assertEqual([r["source_id"] for r in records], ["1"])
        self.assertEqual(records[0]["text"], "Отзыв: всё работает")

    def test_page_limit_not_reported_as_success(self):
        result, records = self.run_collection([
            {"elements": [review()], "elementsCount": 2, "continuation": "0"}], maximum=1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(records), 1)

    def test_missing_reply_is_fetched(self):
        reply = {"id": 10, "commentId": 1, "text": "Спасибо", "commentDate": "2026-01-02T10:00:00Z"}
        result, records = self.run_collection([
            {"elements": [review(totalRepliesCount=1)], "elementsCount": 1},
            {"elements": [reply]},
        ])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(records[0]["replies"], [reply])

    def test_duplicate_and_invalid_records_mark_partial(self):
        result, records = self.run_collection([
            {"elements": [review(), review(), review(2, rating="5")], "elementsCount": 2},
        ])
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["warnings"])
        self.assertTrue(result["errors"])
        self.assertEqual(len(records), 1)

    def test_repeated_cursor_stops(self):
        result, _ = self.run_collection([
            {"elements": [review()], "elementsCount": 2, "continuation": "0"},
            {"elements": [review(2)], "elementsCount": 2, "continuation": "0"},
        ])
        self.assertEqual(result["status"], "failed")

    def test_hash_ignores_reactions_but_detects_text_and_response(self):
        original = review(likesCount=0)
        baseline = normalize(original, "one")["content_hash"]
        self.assertEqual(baseline, normalize({**original, "likesCount": 8}, "two")["content_hash"])
        self.assertNotEqual(baseline, normalize({**original, "comment": "Исправлено"}, "two")["content_hash"])
        self.assertNotEqual(baseline, normalize({**original, "devResponse": "Ответ"}, "two")["content_hash"])

    def test_retry_429_then_success(self):
        calls, waits = [], []
        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "busy", {"Retry-After": "2"}, None)
            return io.BytesIO(b'{"elements": []}')
        client = Client(opener=opener, sleep=waits.append)
        self.assertEqual(client.get("ru.anketka", {}), {"elements": []})
        self.assertEqual(len(calls), 2)
        self.assertIn(2, waits)


if __name__ == "__main__":
    unittest.main()
