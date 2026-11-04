import os
import unittest

from bot.anketka import admin_chat_ids, faq_changes_text, faq_status_text


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.index = 0

    def fetchone(self):
        value = self.rows[self.index]
        self.index += 1
        return value

    def fetchall(self):
        value = self.rows[self.index]
        self.index += 1
        return value


class FakeConnection:
    def __init__(self, rows):
        self.cursor = FakeCursor(rows)

    def execute(self, *_args):
        return self.cursor


class BotAdminTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("FAQ_ADMIN_CHAT_IDS", None)

    def test_admin_chat_ids_are_allowlisted_and_invalid_values_ignored(self):
        os.environ["FAQ_ADMIN_CHAT_IDS"] = "123, -456, wrong"
        self.assertEqual(admin_chat_ids(), {-456, 123})

    def test_faq_status_uses_latest_run_data(self):
        conn = FakeConnection([("complete", "started", "finished", {"faq_items_seen": 11, "faq_item_versions": 1}, {"status": 200})])
        text = faq_status_text(conn)
        self.assertIn("HTTP: 200", text)
        self.assertIn("Новых версий: 1", text)

    def test_faq_changes_reports_new_and_changed_versions(self):
        conn = FakeConnection([(7,), [("Вопрос 1?", 1, None, "Ответ 1"), ("Вопрос 2?", 2, "Старый", "Новый")]])
        text = faq_changes_text(conn)
        self.assertIn("Новый вопрос", text)
        self.assertIn("Изменён ответ", text)
        self.assertIn("Старый", text)


if __name__ == "__main__":
    unittest.main()
