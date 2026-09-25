import asyncio
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote
from uuid import uuid4

from fichaxebot.scheduler import Interrupted, Misfire, TaskScheduler
from fichaxebot.scrap_functions.absence_request import AbsenceRequestError, AbsenceRequestUncertain
from fichaxebot.scrap_functions.commit import ReadOnlyStop
from fichaxebot.scrap_functions.congress_request import CongressRequestError
from fichaxebot.utils import MADRID_TZ
from fichaxebot.webapp_controller.vacation_confirmation import ACTIVE_KEY
from plugins.congreso_dieta import flow, pdf, usc
from plugins.congreso_dieta.cases import Case, CaseStore
from plugins.congreso_dieta.config import parse_config
from plugins.congreso_dieta.spreadsheet import SheetDates
from tests.scheduler_fakes import Clock, fake_app, fire
from tests.test_congreso_config import raw_config

NOW = datetime(2026, 10, 1, 10, 0, tzinfo=MADRID_TZ)  # Thursday
AUTH_URL = "https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm?solicitudeId=100001&csv=E"


class FakeSession:
    def __init__(self):
        self.congress_calls, self.absence_calls = [], []
        self.congress_result = {"status": "unverified", "submission_attempted": True}
        self.absence_result = {"id": "9", "state": "Solicitada"}
        self.catalog = {"years": [2026], "types": [{"id": "7", "name": "Asistencia  a congresos", "requiresHours": True}]}
        self.calendar = ["N2026-10-13"]

    def submit_congress_request(self, data, confirm=None):
        self.congress_calls.append(data)
        if isinstance(self.congress_result, Exception):
            raise self.congress_result
        return self.congress_result

    def submit_absence_request(self, data, confirm=None):
        self.absence_calls.append(data)
        if isinstance(self.absence_result, Exception):
            raise self.absence_result
        return self.absence_result

    def fetch_absence_selection_data(self):
        return self.catalog

    def fetch_calendar_summary(self, *, for_vacation_selection=False):
        return self.calendar

    def run(self, operation, *args):
        return operation(self, *args)


class FlowTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.raw = raw_config(directory.name)
        self.config = parse_config(self.raw, today=NOW.date())
        self.clock = Clock(NOW)
        self.app = fake_app()
        self.app.bot.send_document = AsyncMock()
        self.tasks = []

        def create_task(coro, **kwargs):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task

        self.app.create_task = create_task
        self.app.web_session = self.session = FakeSession()
        self.app.scheduler = self.scheduler = TaskScheduler("123", path=self.root / "schedule.json", clock=self.clock)
        self.store = CaseStore(self.root / "cases.json", self.root / "files")
        self.global_config = SimpleNamespace(
            telegram_chat_id="123", absence_confirmation_enabled=False, absence_confirmation_timeout_seconds=60,
            vacation_confirmation_timeout_seconds=60)
        patcher = patch.object(flow, "get_config", return_value=self.global_config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.generated = []
        self.plugin = flow.CongresoDieta(self.app, self.config, self.raw, self.store, clock=self.clock,
                                         generate=self.fake_generate, join=self.fake_join, sign=self.fake_sign)
        self.scheduler.register_kind(flow.PROMPT_KIND, self.plugin.run_absence_prompt, misfire=Misfire.RUN_LATE,
                                     interrupted=Interrupted.RETRY)
        self.scheduler.start(self.app)

    async def asyncTearDown(self):
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def fake_generate(self, template, workdir, sheet_dates):
        workdir.mkdir(parents=True, exist_ok=True)
        out = workdir / "CL1.pdf"
        out.write_bytes(b"%PDF-sheet")
        self.generated.append(sheet_dates)
        return out

    def fake_join(self, first, second, out):
        out.write_bytes(first.read_bytes() + second.read_bytes())
        return out

    def fake_sign(self, src, out, signing):
        out.write_bytes(b"%PDF-signed")
        return out

    def texts(self):
        return [call.kwargs["text"] for call in self.app.bot.send_message.await_args_list]

    def message(self):
        return SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())))

    def update(self, message=None):
        return SimpleNamespace(effective_message=message, message=message,
                               effective_chat=SimpleNamespace(id=123), effective_user=SimpleNamespace(id=456))

    def add_case(self, start=date(2026, 10, 20), end=date(2026, 10, 22), **fields):
        case = Case.new(start, end, self.raw)
        for key, value in fields.items():
            setattr(case, key, value)
        self.store.add(case)
        return case


class OpenAndRangeTests(FlowTestCase):
    async def test_open_app_sends_snapshot_with_token(self):
        case = self.add_case(last_problem="Error al firmar")
        message = SimpleNamespace(reply_text=AsyncMock())
        await self.plugin.open_app(SimpleNamespace(message=message), None)
        url = message.reply_text.await_args.kwargs["reply_markup"].keyboard[0][0].web_app.url
        self.assertTrue(url.startswith("https://example.test/congreso.html#data="))
        payload = json.loads(unquote(url.split("#data=", 1)[1]))
        self.assertEqual((payload["token"], payload["minStart"]), (self.plugin.launch_token, "2026-10-06"))
        self.assertEqual([(item["id"], item["problem"]) for item in payload["cases"]], [(case.id, "Error al firmar")])
        self.assertIn("Esperando la autorización", payload["cases"][0]["status"])

    def test_range_rules(self):
        self.add_case(date(2026, 10, 20), date(2026, 10, 22))
        today = NOW.date()
        self.assertIsNone(self.plugin.check_range(date(2026, 10, 6), date(2026, 10, 7), today))
        self.assertIn("como pronto", self.plugin.check_range(date(2026, 10, 5), date(2026, 10, 7), today))
        self.assertIn("anterior", self.plugin.check_range(date(2026, 10, 8), date(2026, 10, 7), today))
        self.assertIn("cambio de año", self.plugin.check_range(date(2026, 12, 30), date(2027, 1, 2), today))
        self.assertIn("solapan", self.plugin.check_range(date(2026, 10, 22), date(2026, 10, 23), today))


class NewRequestTests(FlowTestCase):
    async def submit(self, start="2026-10-20", end="2026-10-22"):
        self.plugin.launch_token = "t" * 32
        message = self.message()
        await self.plugin.handle_new(self.update(message), None, {"token": "t" * 32, "start": start, "end": end})
        await asyncio.gather(*self.tasks)
        return message

    def status_text(self, message):
        return message.reply_text.return_value.edit_text.await_args.args[0]

    async def test_stale_token_is_rejected(self):
        self.plugin.launch_token = "a" * 32
        message = self.message()
        await self.plugin.handle_new(self.update(message), None,
                                     {"token": "b" * 32, "start": "2026-10-20", "end": "2026-10-22"})
        self.assertIn("ya no es válida", message.reply_text.await_args.args[0])
        self.assertEqual((self.session.congress_calls, self.plugin.launch_token), ([], "a" * 32))

    async def test_submitted_request_creates_case_and_schedules_the_prompt(self):
        message = await self.submit()
        [call] = self.session.congress_calls
        self.assertEqual((call["start_date"], call["end_date"], call["reason"]),
                         ("2026-10-20", "2026-10-22", "Asistencia a congreso"))
        [case] = self.store.open_cases()
        self.assertFalse(case.simulated)
        self.assertEqual([task.when for task in self.scheduler.pending(flow.PROMPT_KIND)],
                         [datetime(2026, 10, 17, 9, 0, tzinfo=MADRID_TZ)])
        self.assertIn("enviada", self.status_text(message))
        self.assertIsNone(self.plugin.launch_token)
        self.assertNotIn(ACTIVE_KEY, self.app.bot_data)

    async def test_read_only_creates_a_simulated_case(self):
        self.session.congress_result = ReadOnlyStop()
        message = await self.submit()
        [case] = self.store.open_cases()
        self.assertTrue(case.simulated)
        self.assertIn("solo lectura", self.status_text(message))

    async def test_rejected_request_creates_no_case(self):
        self.session.congress_result = CongressRequestError("Supervisor ambiguo")
        message = await self.submit()
        self.assertEqual(self.store.open_cases(), [])
        self.assertIn("Supervisor ambiguo", self.status_text(message))

    async def test_invalid_range_is_reported_before_contacting_usc(self):
        message = await self.submit(start="2026-10-02", end="2026-10-03")
        self.assertIn("como pronto", message.reply_text.await_args.args[0])
        self.assertEqual(self.session.congress_calls, [])


class CancelTests(FlowTestCase):
    async def ask_cancel(self, case):
        self.plugin.launch_token = "t" * 32
        message = SimpleNamespace(reply_text=AsyncMock())
        await self.plugin.handle_cancel(self.update(message), None, {"token": "t" * 32, "case": case.id})
        return message

    async def press(self, callback_data):
        query = SimpleNamespace(data=callback_data, answer=AsyncMock(), edit_message_text=AsyncMock())
        await self.plugin.handle_callback(SimpleNamespace(callback_query=query), None)
        return query

    async def test_confirmed_cancel_stops_following_the_case(self):
        case = self.add_case(absence="requested")
        self.scheduler.schedule(flow.PROMPT_KIND, NOW + timedelta(days=1), {"case": case.id})
        folder = self.store.directory(case)
        message = await self.ask_cancel(case)
        yes = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        query = await self.press(yes)
        self.assertIsNone(self.store.get(case.id))
        self.assertFalse(folder.exists())
        self.assertEqual(self.scheduler.pending(flow.PROMPT_KIND), [])
        text = query.edit_message_text.await_args.args[0]
        self.assertIn(usc.REQUESTS_LIST_URL, text)
        self.assertIn(flow.ABSENCES_URL, text)

    async def test_declining_keeps_the_case(self):
        case = self.add_case()
        message = await self.ask_cancel(case)
        no = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[1][0].callback_data
        await self.press(no)
        self.assertIsNotNone(self.store.get(case.id))

    async def test_unknown_case_or_used_token(self):
        self.plugin.launch_token = "t" * 32
        message = SimpleNamespace(reply_text=AsyncMock())
        await self.plugin.handle_cancel(self.update(message), None, {"token": "t" * 32, "case": "missing"})
        self.assertIn("ya no existe", message.reply_text.await_args.args[0])
        query = await self.press("cdieta_cancel_yes:" + "f" * 32)
        query.answer.assert_awaited_once_with("Esta cancelación ya no está disponible.")


class AbsencePromptTests(FlowTestCase):
    async def run_prompt(self, case):
        task = self.scheduler.schedule(flow.PROMPT_KIND, self.clock.now + timedelta(minutes=1), {"case": case.id})
        job = next(job for job in self.app.job_queue.of("once") if job.data["id"] == task.id)
        await fire(self.app, job)

    async def answer(self, case, action):
        query = SimpleNamespace(data=f"cdieta_absence_{action}:{case.prompt_token}", answer=AsyncMock())
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=123),
                                 effective_user=SimpleNamespace(id=456))
        await self.plugin.handle_callback(update, None)
        await asyncio.gather(*self.tasks)
        return query

    def reminders_for(self, case):
        return [task for task in self.scheduler.pending(flow.PROMPT_KIND) if task.payload["case"] == case.id]

    async def test_prompt_asks_and_schedules_a_reminder(self):
        case = self.add_case()
        await self.run_prompt(case)
        kwargs = self.app.bot.send_message.await_args.kwargs
        self.assertIn("¿Solicito la ausencia", kwargs["text"])
        stored = self.store.get(case.id)
        self.assertEqual(kwargs["reply_markup"].inline_keyboard[0][0].callback_data,
                         f"cdieta_absence_yes:{stored.prompt_token}")
        self.assertEqual(stored.absence, "asking")
        self.assertEqual([task.when for task in self.reminders_for(case)], [NOW + timedelta(minutes=30)])
        await self.run_prompt(case)
        self.assertIn("Recordatorio", self.texts()[-1])

    async def test_outside_the_window_the_prompt_waits(self):
        self.clock.now = NOW.replace(hour=21)
        case = self.add_case()
        await self.run_prompt(case)
        self.app.bot.send_message.assert_not_awaited()
        self.assertEqual([task.when for task in self.reminders_for(case)],
                         [datetime(2026, 10, 2, 8, 0, tzinfo=MADRID_TZ)])

    async def test_after_the_start_the_absence_is_not_requested(self):
        self.clock.now = datetime(2026, 10, 20, 0, 0, tzinfo=MADRID_TZ)
        case = self.add_case()
        await self.run_prompt(case)
        self.assertEqual(self.store.get(case.id).absence, "not_requested")
        self.assertIn("No se solicitó la ausencia", self.texts()[-1])

    async def test_not_now_skips_the_absence_and_stops_reminders(self):
        case = self.add_case(absence="asking", prompt_token=uuid4().hex)
        self.scheduler.schedule(flow.PROMPT_KIND, NOW + timedelta(minutes=30), {"case": case.id})
        await self.answer(case, "no")
        self.assertEqual(self.store.get(case.id).absence, "skipped")
        self.assertEqual(self.reminders_for(case), [])

    async def test_yes_requests_the_working_days_with_hours(self):
        case = self.add_case(date(2026, 10, 9), date(2026, 10, 14), absence="asking", prompt_token=uuid4().hex)
        await self.answer(case, "yes")
        hours = {"startTime": "08:00", "endTime": "15:00"}
        self.assertEqual(self.session.absence_calls, [{
            "year": 2026, "absenceTypeId": "7", "observations": "", "attachments": [],
            "periods": [{"date": "2026-10-09", **hours}, {"date": "2026-10-14", **hours}]}])
        self.assertEqual(self.store.get(case.id).absence, "requested")
        self.assertIn("Ausencia solicitada", self.texts()[-1])

    async def test_read_only_rejection_and_unclear_outcomes(self):
        for result, state, text in ((ReadOnlyStop(), "simulated", "solo lectura"),
                                    (AbsenceRequestError("Día cerrado"), "asking", "Día cerrado"),
                                    (AbsenceRequestUncertain("USC no confirmó"), "uncertain", "USC no confirmó")):
            with self.subTest(result=type(result).__name__):
                case = self.add_case(absence="asking", prompt_token=uuid4().hex)
                self.session.absence_result = result
                await self.answer(case, "yes")
                self.assertEqual(self.store.get(case.id).absence, state)
                self.assertIn(text, self.texts()[-1])
                self.assertEqual(bool(self.reminders_for(case)), state == "asking")

    async def test_unknown_absence_type_keeps_asking(self):
        self.session.catalog = {"years": [2026], "types": [{"id": "1", "name": "Outro", "requiresHours": False}]}
        case = self.add_case(absence="asking", prompt_token=uuid4().hex)
        await self.answer(case, "yes")
        self.assertEqual(self.store.get(case.id).absence, "asking")
        self.assertIn("Asistencia a congresos", self.texts()[-1])
        self.assertEqual(self.session.absence_calls, [])

    async def test_stale_prompt_button(self):
        case = self.add_case(absence="requested", prompt_token=uuid4().hex)
        query = await self.answer(case, "yes")
        query.answer.assert_awaited_once_with("Esta pregunta ya no está disponible.")


class DailyTests(FlowTestCase):
    def status(self, state="Tramitada", url=AUTH_URL):
        return usc.RequestStatus(state, url)

    async def daily(self):
        await self.plugin.run_daily(SimpleNamespace(bot=self.app.bot, application=self.app))

    async def test_cases_without_request_id_or_simulated_wait(self):
        self.add_case()
        self.add_case(date(2026, 11, 2), date(2026, 11, 3), simulated=True, request_id="1")
        with patch.object(usc, "fetch_request_status") as fetch:
            await self.daily()
        fetch.assert_not_called()

    async def test_authorization_before_generation_day_is_stored_and_waits(self):
        case = self.add_case(request_id="100001")
        with patch.object(usc, "fetch_request_status", return_value=self.status()), \
             patch.object(usc, "download_authorization", return_value=b"%PDF-auth"):
            await self.daily()
        stored = self.store.get(case.id)
        self.assertEqual((stored.stage, stored.auth_date), ("auth_received", "2026-10-01"))
        self.assertEqual((self.store.directory(stored) / "autorizacion.pdf").read_bytes(), b"%PDF-auth")
        self.assertEqual(self.generated, [])
        self.assertIn("Autorización firmada recibida", self.texts()[-1])

    async def test_full_document_run_on_generation_day(self):
        case = self.add_case(date(2026, 10, 6), date(2026, 10, 7), request_id="100001")
        self.clock.now = datetime(2026, 10, 8, 10, 0, tzinfo=MADRID_TZ)
        with patch.object(usc, "fetch_request_status", return_value=self.status()), \
             patch.object(usc, "download_authorization", return_value=b"%PDF-auth"):
            await self.daily()
        self.assertEqual(self.generated, [SheetDates(date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8))])
        target = self.config.output_dir / "dieta_20261006_20261007.pdf"
        self.assertEqual(target.read_bytes(), b"%PDF-signed")
        self.app.bot.send_document.assert_awaited_once()
        self.assertIsNone(self.store.get(case.id))

    async def test_check_failures_are_reported_on_the_third_day(self):
        case = self.add_case(request_id="100001")
        with patch.object(usc, "fetch_request_status", side_effect=RuntimeError("timeout")):
            for _ in range(4):
                await self.daily()
        self.assertEqual(sum("No se pudo consultar" in text for text in self.texts()), 1)
        self.assertEqual(self.store.get(case.id).check_failures, 4)

    async def test_rejected_request_is_reported_once(self):
        case = self.add_case(request_id="100001")
        with patch.object(usc, "fetch_request_status", return_value=self.status("Denegada", None)):
            await self.daily()
            await self.daily()
        self.assertEqual(sum("Denegada" in text for text in self.texts()), 1)
        self.assertEqual(self.store.get(case.id).stage, "awaiting_auth")

    async def test_signing_failure_keeps_an_unsigned_copy_and_retries(self):
        case = self.add_case(date(2026, 10, 6), date(2026, 10, 7), stage="auth_received", auth_date="2026-10-02")
        (self.store.directory(case) / "autorizacion.pdf").write_bytes(b"%PDF-auth")
        self.clock.now = datetime(2026, 10, 8, 10, 0, tzinfo=MADRID_TZ)

        def failing(src, out, signing):
            raise pdf.PdfError("Certificado caducado")

        self.plugin._sign_pdf = failing
        await self.daily()
        unsigned = self.config.output_dir / "dieta_20261006_20261007_SIN_FIRMAR.pdf"
        stored = self.store.get(case.id)
        self.assertEqual(stored.stage, "generated")
        self.assertIn("Certificado caducado", stored.last_problem)
        self.assertTrue(unsigned.exists())
        self.plugin._sign_pdf = self.fake_sign
        await self.daily()
        self.assertIsNone(self.store.get(case.id))
        self.assertFalse(unsigned.exists())
