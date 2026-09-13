import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from telegram import CallbackQuery, Chat, Message, Update, User, WebAppData
from telegram.ext import ApplicationBuilder, TypeHandler

from fichaxebot.access_control import restrict_to_chat


class AccessControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = ApplicationBuilder().token('123456:TEST_TOKEN').build()
        # Exercise real dispatch without initializing Telegram/network access.
        self.app._initialized = True
        restrict_to_chat(self.app, '123')
        self.handler = AsyncMock()
        self.later_handler = AsyncMock()
        self.app.add_handler(TypeHandler(Update, self.handler))
        self.app.add_handler(TypeHandler(Update, self.later_handler), group=1)

    def update(self, chat_id, kind):
        user = User(456, 'Test', False)
        message = Message(1, datetime.now(timezone.utc), Chat(chat_id, 'private'),
                          from_user=user, text='/marcar entrada' if kind == 'command' else 'Sí',
                          web_app_data=WebAppData('{}', 'Test') if kind == 'webapp' else None)
        if kind == 'callback':
            return Update(1, callback_query=CallbackQuery('test', user, 'instance',
                          message=message, data='vacation_submit:1'))
        return Update(1, message=message)

    async def test_other_chats_never_reach_any_handler_group(self):
        for kind in ('command', 'text', 'webapp', 'callback'):
            with self.subTest(kind=kind):
                await self.app.process_update(self.update(999, kind))
        self.handler.assert_not_awaited()
        self.later_handler.assert_not_awaited()

    async def test_configured_chat_reaches_handlers(self):
        for kind in ('command', 'text', 'webapp', 'callback'):
            await self.app.process_update(self.update(123, kind))
        self.assertEqual(self.handler.await_count, 4)
        self.assertEqual(self.later_handler.await_count, 4)

    async def test_updates_without_chat_are_ignored(self):
        await self.app.process_update(Update(1))
        self.handler.assert_not_awaited()

    async def test_inline_callback_without_chat_is_ignored(self):
        callback = CallbackQuery('test', User(123, 'Test', False), 'instance',
                                 inline_message_id='inline', data='vacation_submit:1')
        await self.app.process_update(Update(1, callback_query=callback))
        self.handler.assert_not_awaited()
