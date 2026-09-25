import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fichaxebot.scrap_functions.vacation_request import (
    VacationRequestError, VacationRequestUncertain, build_catalog, validate_selection, _verify_review,
)
from fichaxebot.webapp_controller.calendar_vacations import handle_vacation_request
from fichaxebot.webapp_controller.router import dispatch_webapp_reply
from fichaxebot.commands.vacations import VACATION_SELECTION_KEY, _build_vacations_url
from urllib.parse import urlsplit, parse_qs
import json

TODAY = date(2026, 1, 10)
FORM = {"years": ["2026", "2025", "2024"], "types": [
    {"id": "16", "name": "Vacacións"}, {"id": "13", "name": "Asuntos particulares xerais"},
]}


def balances(previous=3):
    return [
        {"year": str(year), "typeId": kind, "numeroDiasDisponhibles": remaining}
        for year, kind, remaining in [(2026, "16", 20), (2026, "13", 0),
                                      (2025, "16", previous), (2025, "13", 0)]
    ]


class VacationRequestTests(unittest.TestCase):
    def setUp(self):
        self.catalog = build_catalog(FORM, balances(), TODAY)
        self.selection = {"year": 2025, "vacationTypeId": "16", "days": ["2026-01-12"]}

    def test_previous_credit_can_fund_current_january_dates(self):
        self.assertEqual([year["year"] for year in self.catalog["years"]], [2026, 2025])
        result = validate_selection(self.selection, self.catalog, ["P2026-01-12"], TODAY)
        self.assertEqual(result["year"], 2025)
        self.assertEqual(result["days"], ["2026-01-12"])

    def test_balance_valid_through_following_february(self):
        selection = {"year": 2026, "vacationTypeId": "16", "days": ["2027-01-12", "2027-02-28"]}
        self.assertEqual(validate_selection(selection, self.catalog, [], TODAY)["days"], selection["days"])
        with self.assertRaises(VacationRequestError):
            validate_selection({**selection, "days": ["2027-03-01"]}, self.catalog, [], TODAY)

    def test_expired_previous_balance_is_not_offered(self):
        catalog = build_catalog(FORM, balances(), date(2026, 3, 1))
        self.assertEqual([item["year"] for item in catalog["years"]], [2026])

    def test_previous_year_requires_both_usc_permission_and_credit(self):
        for credit in [0, -1]:
            catalog = build_catalog(FORM, balances(credit), TODAY)
            self.assertEqual([year["year"] for year in catalog["years"]], [2026])
        catalog = build_catalog({**FORM, "years": ["2026"]}, balances(), TODAY)
        self.assertEqual([year["year"] for year in catalog["years"]], [2026])

    def test_decimal_days_and_hours_are_not_rounded_into_full_days(self):
        data = balances()
        data[2].update(numeroDiasDisponhibles="0,5", numeroHorasDisponhibles="3,5")
        catalog = build_catalog(FORM, data, TODAY)
        kind = catalog["years"][1]["types"][0]
        self.assertEqual((kind["remainingDays"], kind["remainingHours"]), (0.5, 3.5))
        with self.assertRaises(VacationRequestError):
            validate_selection(self.selection, catalog, [], TODAY)

    def test_unknown_or_nonfinite_balances_fail_instead_of_becoming_zero(self):
        for value in [None, "unknown", "NaN", "Infinity", True]:
            data = balances()
            data[0]["numeroDiasDisponhibles"] = value
            with self.subTest(value=value), self.assertRaises(VacationRequestError):
                build_catalog(FORM, data, TODAY)
        with self.assertRaises(VacationRequestError):
            build_catalog(FORM, balances()[:-1], TODAY)

    def test_rejects_duplicates_past_invalid_dates_unknown_types_and_overdraw(self):
        bad = [
            {"days": []}, {"days": ["2026-01-12", "2026-01-12"]},
            {"days": ["2025-12-31"]}, {"days": ["2027-01-12"]},
            {"days": ["2026-02-30"]}, {"days": [None]}, {"days": "2026-01-12"},
            {"vacationTypeId": "999"}, {"vacationTypeId": "13"}, {"year": True},
            {"year": "2025"}, {"year": 2024},
            {"days": ["2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15"]},
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(VacationRequestError):
                validate_selection({**self.selection, **value}, self.catalog, [], TODAY)

    def test_blocks_nonworking_and_existing_vacation_inclusive_ranges(self):
        for code in ["N", "V"]:
            with self.subTest(code=code), self.assertRaises(VacationRequestError):
                validate_selection(self.selection, self.catalog, [f"{code}2026-01-10:2026-01-12"], TODAY)

    def test_sorts_independent_dates(self):
        result = validate_selection({**self.selection, "days": ["2026-01-15", "2026-01-12"]}, self.catalog, [], TODAY)
        self.assertEqual(result["days"], ["2026-01-12", "2026-01-15"])

    def test_url_roundtrip_keeps_payload_in_fragment(self):
        payload = {**self.catalog, "requestId": "test", "entries": []}
        url = urlsplit(_build_vacations_url("https://example.test/vacaciones.html?theme=light", payload))
        self.assertEqual(url.query, "theme=light")
        self.assertEqual(json.loads(parse_qs(url.fragment)["data"][0]), payload)


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = SimpleNamespace(vacation_confirmation_enabled=False, vacation_confirmation_timeout_seconds=60)
        config_patch = patch('fichaxebot.webapp_controller.calendar_vacations.get_config', return_value=self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.message = SimpleNamespace(reply_text=AsyncMock())
        self.message.reply_text.return_value = SimpleNamespace(edit_text=AsyncMock())
        self.update = SimpleNamespace(effective_message=self.message, effective_chat=SimpleNamespace(id=123),
                                      effective_user=SimpleNamespace(id=456))
        self.snapshot = {**build_catalog(FORM, balances(), TODAY), "entries": [], "requestId": "valid"}
        self.context = SimpleNamespace(user_data={VACATION_SELECTION_KEY: self.snapshot},
                                       application=SimpleNamespace(web_session=object(), bot_data={}))
        self.payload = {"type": "vacation_request_submit", "requestId": "valid", "year": 2025,
                        "vacationTypeId": "16", "days": ["2026-01-12"]}

    async def test_stale_launch_does_not_touch_browser(self):
        await handle_vacation_request(self.update, self.context, {**self.payload, "requestId": "old"})
        self.assertIn("caducado", self.message.reply_text.call_args.args[0])

    async def test_enabled_handler_returns_while_worker_waits_and_cleans_up(self):
        import asyncio
        from fichaxebot.scrap_functions.vacation_request import VacationRequestCancelled
        from fichaxebot.webapp_controller.vacation_confirmation import ACTIVE_KEY, handle_confirmation
        self.config.vacation_confirmation_enabled = True
        delivered = asyncio.Event()
        document = SimpleNamespace(edit_reply_markup=AsyncMock())
        async def send_document(**kwargs):
            delivered.set()
            return document
        self.context.application.bot = SimpleNamespace(send_document=send_document)
        self.context.application.create_task = lambda coro, **kwargs: asyncio.create_task(coro)
        def submit(selection, confirm):
            if not confirm(b'png'):
                raise VacationRequestCancelled()
            return {**selection, 'id': '123', 'state': 'Solicitada'}
        self.context.application.web_session = SimpleNamespace(submit_vacation_request=submit)
        with patch('fichaxebot.webapp_controller.calendar_vacations.get_madrid_now') as now:
            now.return_value.date.return_value = TODAY
            await asyncio.wait_for(handle_vacation_request(self.update, self.context, self.payload), 1)
        pending = self.context.application.bot_data[ACTIVE_KEY]
        self.addAsyncCleanup(self.finish_pending, pending)
        await asyncio.wait_for(delivered.wait(), 1)
        self.assertFalse(pending.task.done())
        self.update.callback_query = SimpleNamespace(data=f'vacation_confirm:{pending.token}', answer=AsyncMock())
        await handle_confirmation(self.update, self.context)
        await asyncio.wait_for(pending.task, 1)
        self.assertNotIn(ACTIVE_KEY, self.context.application.bot_data)
        document.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        self.assertIn('Solicitada', self.message.reply_text.return_value.edit_text.call_args.args[0])

    async def finish_pending(self, pending):
        pending.abort('test cleanup')
        await pending.task

    async def test_success_consumes_token_and_duplicate_is_rejected(self):
        selection = {**validate_selection(self.payload, self.snapshot, [], TODAY),
                     "id": "123", "state": "Solicitada"}
        session = SimpleNamespace(submit_vacation_request=Mock(return_value=selection))
        self.context.application.web_session = session
        with patch('fichaxebot.webapp_controller.calendar_vacations.get_madrid_now') as now:
            now.return_value.date.return_value = TODAY
            await handle_vacation_request(self.update, self.context, self.payload)
        self.assertNotIn(VACATION_SELECTION_KEY, self.context.user_data)
        self.assertEqual(self.context.user_data, {})
        result = self.message.reply_text.return_value.edit_text.call_args
        self.assertIn("Solicitada", result.args[0])
        self.assertIn("123", result.args[0])
        self.assertNotIn("reply_markup", result.kwargs)
        await handle_vacation_request(self.update, self.context, self.payload)
        self.assertIn("caducado", self.message.reply_text.call_args.args[0])
        session.submit_vacation_request.assert_called_once()

    async def test_updated_usc_balance_rejection_is_reported(self):
        def submit(data):
            raise VacationRequestError("Solo quedan 0 días")
        self.context.application.web_session = SimpleNamespace(submit_vacation_request=submit)
        with patch('fichaxebot.webapp_controller.calendar_vacations.get_madrid_now') as now:
            now.return_value.date.return_value = TODAY
            await handle_vacation_request(self.update, self.context, self.payload)
        self.assertEqual(self.context.user_data, {})
        self.assertIn('Solo quedan 0 días', self.message.reply_text.return_value.edit_text.call_args.args[0])

    async def test_router_rejects_nonobject_json_and_other_chats(self):
        self.message.web_app_data = SimpleNamespace(data='[]')
        with patch('fichaxebot.webapp_controller.router.get_config', return_value=SimpleNamespace(telegram_chat_id='123')):
            await dispatch_webapp_reply(self.update, self.context)
        self.assertIn('formato', self.message.reply_text.call_args.args[0])
        self.message.reply_text.reset_mock()
        with patch('fichaxebot.webapp_controller.router.get_config', return_value=SimpleNamespace(telegram_chat_id='456')):
            await dispatch_webapp_reply(self.update, self.context)
        self.message.reply_text.assert_not_awaited()

    async def test_uncertain_submission_is_not_retried(self):
        session = SimpleNamespace(submit_vacation_request=Mock(
            side_effect=VacationRequestUncertain("No se pudo confirmar el envío.")))
        self.context.application.web_session = session
        with patch('fichaxebot.webapp_controller.calendar_vacations.get_madrid_now') as now:
            now.return_value.date.return_value = TODAY
            await handle_vacation_request(self.update, self.context, self.payload)
            await handle_vacation_request(self.update, self.context, self.payload)
        session.submit_vacation_request.assert_called_once()
        self.assertIn('solicitudesPropias', self.message.reply_text.return_value.edit_text.call_args.args[0])

    async def test_read_only_is_reported_without_claiming_submission(self):
        self.context.application.web_session = SimpleNamespace(submit_vacation_request=Mock(
            side_effect=PermissionError("Modo de solo lectura")))
        with patch('fichaxebot.webapp_controller.calendar_vacations.get_madrid_now') as now:
            now.return_value.date.return_value = TODAY
            await handle_vacation_request(self.update, self.context, self.payload)
        self.assertIn('solo lectura', self.message.reply_text.return_value.edit_text.call_args.args[0])

    async def test_unknown_payload_is_rejected_without_using_selection(self):
        self.message.web_app_data = SimpleNamespace(data=json.dumps({**self.payload, 'type': 'unknown'}))
        with patch('fichaxebot.webapp_controller.router.get_config', return_value=SimpleNamespace(telegram_chat_id='123')):
            await dispatch_webapp_reply(self.update, self.context)
        self.assertIn('/vacaciones', self.message.reply_text.call_args.args[0])
        self.assertIn(VACATION_SELECTION_KEY, self.context.user_data)

    async def test_explicit_submit_payload_is_routed_to_submission(self):
        result = {**validate_selection(self.payload, self.snapshot, [], TODAY), 'id': '123', 'state': 'Solicitada'}
        session = SimpleNamespace(submit_vacation_request=Mock(return_value=result))
        self.context.application.web_session = session
        self.message.web_app_data = SimpleNamespace(data=json.dumps(self.payload))
        with patch('fichaxebot.webapp_controller.router.get_config', return_value=SimpleNamespace(telegram_chat_id='123')), \
             patch('fichaxebot.webapp_controller.calendar_vacations.get_madrid_now') as now:
            now.return_value.date.return_value = TODAY
            await dispatch_webapp_reply(self.update, self.context)
        session.submit_vacation_request.assert_called_once()


class ReviewTests(unittest.TestCase):
    def test_transient_summary_must_match_year_type_and_individual_full_days(self):
        selection = {"year": 2025, "vacationTypeName": "Vacacións", "days": ["2026-01-12", "2026-01-14"]}
        review = {"year": "2025", "vacationTypeName": "Vacacións", "requestType": "Vacacións, permisos e licenzas",
                  "periods": [["12/01/2026", "12/01/2026", "1"], ["14/01/2026", "14/01/2026", "1,0"]]}
        _verify_review(review, selection)
        for change in [{"year":"2026"}, {"vacationTypeName":"Different"}, {"periods":[]},
                       {"periods":[["12/01/2026", "14/01/2026", "2"]]},
                       {"periods":[["12/01/2026", "12/01/2026", "0,5"],review['periods'][1]]},
                       {"periods":[["12/01/2026", "12/01/2026", "unknown"],review['periods'][1]]}]:
            with self.subTest(change=change), self.assertRaises(VacationRequestError):
                _verify_review({**review, **change}, selection)
