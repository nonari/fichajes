import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from plugins.congreso_dieta import flow, pdf
from plugins.congreso_dieta.cases import Absence, CaseStore, Stage
from tests.test_congreso_flow import FlowTestCase


class ActionTestCase(FlowTestCase):
    async def ask(self, case, action):
        self.plugin.launch_token = 't' * 32
        message = self.message()
        await self.plugin.handle_action(self.update(message), None,
                                       {'token': 't' * 32, 'case': case.id, 'action': action})
        return message

    async def press(self, data):
        query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
        update = self.update()
        update.callback_query = query
        await self.plugin.handle_callback(update, None)
        await asyncio.gather(*self.tasks)
        return query

    def decision(self, message, yes=True):
        return message.reply_text.await_args.kwargs['reply_markup'].inline_keyboard[0 if yes else 1][0].callback_data


class ProcedureActionTests(ActionTestCase):
    async def test_absence_requires_confirmation_and_decline_does_nothing(self):
        case = self.add_case(absence=Absence.SCHEDULED)
        message = await self.ask(case, 'absence')
        self.assertEqual(self.session.absence_calls, [])
        await self.press(self.decision(message, False))
        self.assertEqual(self.session.absence_calls, [])
        self.assertEqual(case.absence, Absence.SCHEDULED)

    async def test_confirmed_retry_runs_once_and_removes_pending_reminders(self):
        for state in (Absence.SCHEDULED, Absence.SKIPPED, Absence.UNCERTAIN):
            with self.subTest(state=state):
                case = self.add_case(absence=state)
                self.plugin.schedule_prompt(case, self.clock())
                message = await self.ask(case, 'absence')
                if state == Absence.UNCERTAIN:
                    self.assertIn('duplicada', message.reply_text.await_args.args[0])
                decision = self.decision(message)
                before = len(self.session.absence_calls)
                await self.press(decision)
                await self.press(decision)
                self.assertEqual(len(self.session.absence_calls), before + 1)
                self.assertEqual(case.absence, Absence.REQUESTED)
                self.assertFalse([t for t in self.scheduler.pending(flow.PROMPT_KIND) if t.payload['case'] == case.id])

    async def test_changed_absence_state_invalidates_confirmation(self):
        case = self.add_case(absence=Absence.UNCERTAIN)
        message = await self.ask(case, 'absence')
        case.absence = Absence.REQUESTED
        await self.press(self.decision(message))
        self.assertEqual(self.session.absence_calls, [])

    async def test_manual_sign_after_end_generates_signs_and_delivers(self):
        case = self.add_case(date(2026, 9, 28), date(2026, 9, 30), no_auth=True,
                             stage=Stage.NO_AUTH, absence=Absence.SKIPPED)
        message = await self.ask(case, 'sign')
        self.assertEqual(self.generated, [])
        await self.press(self.decision(message))
        self.assertTrue((self.config.output_dir / 'dieta_20260928_20260930.pdf').exists())
        self.app.bot.send_document.assert_awaited_once()

    async def test_signing_is_not_available_on_end_day_or_without_required_authorization(self):
        for end, stage, no_auth in ((date(2026, 10, 1), Stage.NO_AUTH, True),
                                     (date(2026, 9, 30), Stage.AWAITING_AUTH, False),
                                     (date(2026, 9, 30), Stage.SIGNED, True)):
            case = self.add_case(date(2026, 9, 28), end, stage=stage, no_auth=no_auth)
            message = await self.ask(case, 'sign')
            self.assertNotIn('reply_markup', message.reply_text.await_args.kwargs)
        self.assertEqual(self.generated, [])

    async def test_signature_error_can_be_retried_without_regenerating(self):
        case = self.add_case(date(2026, 9, 28), date(2026, 9, 30), no_auth=True,
                             stage=Stage.GENERATED, absence=Absence.SKIPPED, last_problem='Error al firmar')
        (self.store.directory(case) / 'unido.pdf').write_bytes(b'%PDF-existing')
        message = await self.ask(case, 'sign')
        await self.press(self.decision(message))
        self.assertEqual(self.generated, [])
        self.app.bot.send_document.assert_awaited_once()

    async def test_snapshot_marks_the_failed_stage_and_keeps_error_details(self):
        case = self.add_case(date(2026, 9, 28), date(2026, 9, 30), no_auth=True,
                             stage=Stage.NO_AUTH, absence=Absence.REQUESTED, last_problem="No module named 'uno'")
        view = self.plugin.case_view(case)
        self.assertEqual([s['key'] for s in view['steps']], ['absence', 'sign'])
        absence, signature = view['steps']
        self.assertEqual(absence['state'], 'completed')
        self.assertEqual((signature['state'], signature['action']), ('error', 'sign'))
        self.assertIn('uno', view['problem'])

    async def test_authorization_line_is_completed_when_document_received(self):
        case = self.add_case(stage=Stage.AUTH_RECEIVED, auth_date='2026-10-01', absence=Absence.SCHEDULED)
        view = self.plugin.case_view(case)
        self.assertEqual([(s['key'], s['state']) for s in view['steps']],
                         [('authorization', 'completed'), ('absence', 'scheduled'), ('sign', 'scheduled')])

    async def test_signed_document_stays_while_absence_pending_without_redelivery(self):
        case = self.add_case(date(2026, 9, 28), date(2026, 9, 30), no_auth=True,
                             stage=Stage.NO_AUTH, absence=Absence.ASKING)
        message = await self.ask(case, 'sign')
        await self.press(self.decision(message))
        self.assertTrue(case.document_delivered)
        self.assertIs(self.store.get(case.id), case)
        signature = self.plugin.case_view(case)['steps'][-1]
        self.assertEqual((signature['state'], signature['action']), ('completed', None))
        reloaded = CaseStore(self.root / 'cases.json', self.root / 'files')
        reloaded.load()
        self.assertTrue(reloaded.get(case.id).document_delivered)
        await self.plugin.run_daily(None)
        self.app.bot.send_document.assert_awaited_once()
        case.absence = Absence.SKIPPED
        await self.plugin.run_daily(None)
        self.assertIsNotNone(self.store.get(case.id).completed_at)
        self.app.bot.send_document.assert_awaited_once()

    async def test_absence_error_is_shown_only_on_absence_and_cleared_after_success(self):
        case = self.add_case(stage=Stage.AUTH_RECEIVED, auth_date='2026-10-01', absence=Absence.ASKING)
        await self.plugin._ask_again(case, 'USC rechazó las fechas')
        view = self.plugin.case_view(case)
        self.assertEqual([step['state'] for step in view['steps']], ['completed', 'error', 'scheduled'])
        self.assertEqual(view['problem'], 'USC rechazó las fechas')
        reloaded = CaseStore(self.root / 'cases.json', self.root / 'files')
        reloaded.load()
        self.assertEqual(reloaded.get(case.id).absence_problem, 'USC rechazó las fechas')
        message = await self.ask(case, 'absence')
        await self.press(self.decision(message))
        self.assertIsNone(self.plugin.case_view(case)['problem'])

    async def test_unknown_case_invalid_token_and_unknown_action_do_nothing(self):
        case = self.add_case()
        for data in ({'token': 'bad', 'case': case.id, 'action': 'absence'},
                     {'token': 't' * 32, 'case': 'missing', 'action': 'absence'},
                     {'token': 't' * 32, 'case': case.id, 'action': 'invalid'}):
            self.plugin.launch_token = 't' * 32
            message = self.message()
            await self.plugin.handle_action(self.update(message), None, data)
            self.assertNotIn('reply_markup', message.reply_text.await_args.kwargs)
        self.assertFalse(self.plugin.action_requests)
        self.assertEqual(self.session.absence_calls, [])

    async def test_generation_and_daily_run_cannot_overlap(self):
        case = self.add_case(date(2026, 9, 28), date(2026, 9, 30), no_auth=True,
                             stage=Stage.NO_AUTH, absence=Absence.SKIPPED)
        started, release = asyncio.Event(), asyncio.Event()
        original = self.plugin._generate
        async def delayed(*args):
            started.set()
            await release.wait()
            await original(*args)
        self.plugin._generate = delayed
        message = await self.ask(case, 'sign')
        query = SimpleNamespace(data=self.decision(message), answer=AsyncMock(), edit_message_text=AsyncMock())
        update = self.update()
        update.callback_query = query
        await self.plugin.handle_callback(update, None)
        await started.wait()
        try:
            await self.plugin.run_daily(None)
        finally:
            release.set()
        await asyncio.gather(*self.tasks)
        self.assertEqual(len(self.generated), 1)


class CompletedProcedureTests(ActionTestCase):
    """A finished procedure stays listed for a day so its signature can be repeated."""

    async def completed_case(self):
        case = self.add_case(date(2026, 9, 28), date(2026, 9, 30), no_auth=True,
                             stage=Stage.NO_AUTH, absence=Absence.SKIPPED)
        await self.plugin.run_daily(None)
        self.app.bot.send_document.reset_mock()
        return case

    async def open_app(self):
        await self.plugin.open_app(SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock())), None)

    async def test_finished_procedure_is_listed_for_24_hours(self):
        case = await self.completed_case()
        self.assertEqual(case.completed_at, self.clock.now.isoformat())
        view = self.plugin.case_view(case)
        self.assertTrue(view['completed'])
        self.assertIn('Finalizado', view['status'])
        self.assertEqual((view['steps'][-1]['state'], view['steps'][-1]['action']), ('completed', 'resign'))
        reloaded = CaseStore(self.root / 'cases.json', self.root / 'files')
        reloaded.load()
        self.assertEqual(reloaded.get(case.id).completed_at, case.completed_at)
        self.clock.now += timedelta(hours=23, minutes=59)
        await self.open_app()
        await self.plugin.run_daily(None)
        self.assertIs(self.store.get(case.id), case)
        self.app.bot.send_document.assert_not_awaited()
        self.clock.now += timedelta(minutes=1)
        await self.open_app()
        self.assertIsNone(self.store.get(case.id))
        self.assertFalse((self.root / 'files' / case.id).exists())

    async def test_daily_run_removes_expired_procedures(self):
        case = await self.completed_case()
        self.clock.now += timedelta(days=1)
        await self.plugin.run_daily(None)
        self.assertIsNone(self.store.get(case.id))

    async def test_signature_can_be_repeated_without_regenerating(self):
        case = await self.completed_case()
        signed_from = []

        def sign(src, out, signing):
            signed_from.append(src.read_bytes())
            out.write_bytes(b'%PDF-signed-again')

        self.plugin._sign_pdf = sign
        self.clock.now += timedelta(hours=5)
        message = await self.ask(case, 'resign')
        self.assertIn('Repetir la firma', message.reply_text.await_args.args[0])
        await self.press(self.decision(message))
        self.assertEqual(len(self.generated), 1)
        self.assertEqual(signed_from, [b'%PDF-sheet'])
        target = self.config.output_dir / 'dieta_20260928_20260930.pdf'
        self.assertEqual(target.read_bytes(), b'%PDF-signed-again')
        self.app.bot.send_document.assert_awaited_once()
        self.assertEqual((case.stage, case.document_delivered), (Stage.SIGNED, True))
        self.assertEqual(case.completed_at, self.clock.now.isoformat())  # the day starts again

    async def test_failed_repeat_keeps_the_procedure_for_retry(self):
        case = await self.completed_case()

        def fail(src, out, signing):
            raise pdf.PdfError('certificado caducado')

        self.plugin._sign_pdf = fail
        message = await self.ask(case, 'resign')
        await self.press(self.decision(message))
        self.assertEqual((case.stage, case.completed_at), (Stage.GENERATED, None))
        self.assertIn('certificado caducado', case.last_problem)
        self.clock.now += timedelta(days=2)
        await self.open_app()
        self.assertIs(self.store.get(case.id), case)

    async def test_repeat_runs_once_per_confirmation_and_not_after_expiry(self):
        case = await self.completed_case()
        first, second = await self.ask(case, 'resign'), await self.ask(case, 'resign')
        await self.press(self.decision(first))
        await self.press(self.decision(second))
        self.app.bot.send_document.assert_awaited_once()
        self.clock.now += timedelta(days=1)
        message = await self.ask(case, 'resign')
        self.assertNotIn('reply_markup', message.reply_text.await_args.kwargs)

    async def test_unfinished_procedures_cannot_repeat_the_signature(self):
        case = self.add_case(date(2026, 9, 28), date(2026, 9, 30), no_auth=True,
                             stage=Stage.SIGNED, absence=Absence.ASKING, document_delivered=True)
        message = await self.ask(case, 'resign')
        self.assertNotIn('reply_markup', message.reply_text.await_args.kwargs)
        self.assertIsNone(self.plugin.case_view(case)['steps'][-1]['action'])

    async def test_removing_a_finished_procedure_does_not_mention_usc(self):
        case = await self.completed_case()
        self.plugin.launch_token = 't' * 32
        message = SimpleNamespace(reply_text=AsyncMock())
        await self.plugin.handle_cancel(self.update(message), None, {'token': 't' * 32, 'case': case.id})
        self.assertIn('Quitar de la lista', message.reply_text.await_args.args[0])
        query = await self.press(self.decision(message))
        self.assertIsNone(self.store.get(case.id))
        self.assertNotIn('USC', query.edit_message_text.await_args.args[0])
