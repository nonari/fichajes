import json
import unittest
from types import SimpleNamespace

from fichaxebot.update_log import describe_update, log_update


def message(text=None, web_app=None, document=None):
    web_app_data = SimpleNamespace(data=web_app) if web_app is not None else None
    return SimpleNamespace(text=text, web_app_data=web_app_data, document=document)


def update(msg=None, callback=None):
    query = SimpleNamespace(data=callback) if callback is not None else None
    return SimpleNamespace(callback_query=query, effective_message=msg, effective_chat=SimpleNamespace(id=123))


class DescribeUpdateTests(unittest.TestCase):
    def test_commands_buttons_and_web_app_actions(self):
        self.assertEqual(describe_update(update(message("/marcar salida 15:00"))), "command /marcar salida 15:00")
        self.assertEqual(describe_update(update(callback="cdieta_absence_yes:" + "a" * 32)), "button cdieta_absence_yes")
        payload = json.dumps({"type": "congreso_dieta_new", "token": "t", "start": "2026-10-20"})
        self.assertEqual(describe_update(update(message(web_app=payload))), "web app data congreso_dieta_new")
        self.assertEqual(describe_update(update(message(web_app="not json"))), "web app data (unreadable)")

    def test_free_text_and_documents_are_not_logged_verbatim(self):
        self.assertEqual(describe_update(update(message("Sí, gracias"))), "text message")
        self.assertEqual(describe_update(update(message(document=object()))), "document")
        self.assertIsNone(describe_update(update()))


class LogUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_update_is_logged_with_its_chat(self):
        with self.assertLogs("fichaxebot.update_log", "INFO") as logs:
            await log_update(update(message("/congreso_dieta")), None)
        self.assertIn("Received command /congreso_dieta (chat 123)", logs.output[0])
