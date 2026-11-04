import json
import tempfile
import unittest
from pathlib import Path

from collector.faq import FAQParser, collect, normalize_items


HTML = """
<html><body><h1>Часто задаваемые вопросы</h1>
<h3>Что такое Анкетка?</h3><p>Сервис онлайн-опросов.</p>
<h3>Что делать, если возникла ошибка?</h3><p>Напишите в поддержку.</p>
<h3>Пустой вопрос?</h3>
</body></html>
"""


class FAQTests(unittest.TestCase):
    def test_parser_extracts_question_answer_blocks(self):
        items = FAQParser().parse(HTML)
        self.assertEqual(items[0]["question"], "Что такое Анкетка?")
        self.assertEqual(items[1]["answer"], "Напишите в поддержку.")

    def test_normalize_adds_stable_key_and_hash(self):
        items = normalize_items([{"question": "Что такое?", "answer": "Ответ."}], "now")
        self.assertEqual(len(items[0]["external_key"]), 64)
        self.assertEqual(len(items[0]["content_hash"]), 64)

    def test_collect_writes_verified_snapshot(self):
        class Response:
            headers = type("Headers", (), {"get_content_charset": lambda self: "utf-8"})()
            def read(self): return HTML.encode("utf-8")
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with tempfile.TemporaryDirectory() as folder:
            summary, path = collect(Path(folder), opener=lambda request, timeout: Response())
            self.assertEqual(summary["status"], "complete")
            self.assertEqual(len(json.loads((path / "faq.json").read_text(encoding="utf-8"))), 2)
            self.assertTrue((path / "raw.html").exists())


if __name__ == "__main__":
    unittest.main()
