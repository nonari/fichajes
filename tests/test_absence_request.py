import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from threading import RLock

from fichaxebot.scrap_functions import absence_request as absence
from fichaxebot.usc_api import UscWebSession

CATALOG = {"years": [2026], "types": [
    {"id": "3", "name": "Asistencia a curso de formación", "requiresHours": True},
    {"id": "6", "name": "Traslado de domicilio", "requiresHours": False},
]}


class SelectionTests(unittest.TestCase):
    def selection(self, **changes):
        return {"year": 2026, "absenceTypeId": "3", "periods": [
            {"date": "2026-01-12", "startTime": "09:00", "endTime": "11:00"}], **changes}

    def test_past_hourly_dates_and_optional_fields(self):
        value = absence.validate_selection(self.selection(), CATALOG)
        self.assertEqual(value["periods"], [{"date": "2026-01-12", "startTime": "09:00", "endTime": "11:00"}])
        self.assertEqual(value["absenceTypeName"], "Asistencia a curso de formación")
        self.assertEqual(value["observations"], "")
        self.assertEqual(value["attachments"], [])

    def test_full_days_sorted_without_vacation_restrictions(self):
        value = absence.validate_selection(self.selection(absenceTypeId="6", periods=[
            {"date": "2026-12-27"}, {"date": "2026-01-01"}]), CATALOG)
        self.assertEqual(value["periods"], [{"date": "2026-01-01"}, {"date": "2026-12-27"}])

    def test_invalid_inputs_rejected(self):
        changes = [
            {"year": True}, {"year": 2025}, {"absenceTypeId": "999"},
            {"periods": []}, {"periods": [None]}, {"periods": [{"date": "2025-12-31"}]},
            {"periods": [{"date": "2026-02-30"}]},
            {"periods": [{"date": "2026-01-12"}]},
            {"periods": [{"date": "2026-01-12", "startTime": "11:00", "endTime": "09:00"}]},
            {"periods": [{"date": "2026-01-12", "startTime": "9:00", "endTime": "11:00"}]},
            {"periods": self.selection()["periods"] * 2},
            {"absenceTypeId": "6"}, {"observations": []}, {"attachments": "file.pdf"},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(absence.AbsenceRequestError):
                absence.validate_selection(self.selection(**change), CATALOG)

    def test_pdf_paths_validated_and_caller_files_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proof.pdf"
            path.write_bytes(b"%PDF-1.4\nproof")
            value = absence.validate_selection(self.selection(attachments=[str(path)]), CATALOG)
            self.assertEqual(value["attachments"], [str(path)])
            self.assertTrue(path.exists())
            for content in (b"", b"not a pdf", b"%PDF-" + b"x" * 1048576):
                path.write_bytes(content)
                with self.assertRaises(absence.AbsenceRequestError):
                    absence.validate_selection(self.selection(attachments=[str(path)]), CATALOG)
            with self.assertRaises(absence.AbsenceRequestError):
                absence.validate_selection(self.selection(attachments=[str(path)] * 11), CATALOG)


class ApiTests(unittest.TestCase):
    def test_api_needs_no_telegram_and_forwards_explicit_callback(self):
        session = UscWebSession.__new__(UscWebSession)
        session.config = SimpleNamespace(read_only=False)
        session._lock = RLock()
        confirm = lambda png: True
        data = {"year": 2026, "absenceTypeId": "6", "periods": [{"date": "2026-01-01"}]}
        with patch('fichaxebot.usc_api.fetch_absence_catalog', return_value=CATALOG), \
             patch('fichaxebot.usc_api.fill_absence_request'), \
             patch('fichaxebot.usc_api._submit_absence_request', return_value={"id": "12", "state": "Solicitada"}) as submit:
            result = session.submit_absence_request(data, confirm=confirm)
        self.assertEqual(result["id"], "12")
        self.assertIs(submit.call_args.kwargs["confirm"], confirm)
        self.assertEqual(submit.call_args.args[1]["absenceTypeName"], "Traslado de domicilio")


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.selection = absence.validate_selection({"year": 2026, "absenceTypeId": "3", "periods": [
            {"date": "2026-01-12", "startTime": "09:00", "endTime": "11:00"}],
            "observations": "Curso"}, CATALOG)
        self.review = {"year": "2026", "absenceTypeName": "Asistencia a curso de formación",
            "requestType": "Ausencias autorizadas", "state": "Borrador", "observations": "Curso", "attachments": [],
            "headers": ["Dende", "Ata", "Hora de inicio", "Hora de fin"],
            "periods": [["12/01/2026", "12/01/2026", "09:00", "11:00"]], "canSubmit": True}

    def test_review_matches_dates_times_observations_and_files(self):
        absence.verify_review(self.review, self.selection)
        for change in ({"year": "2025"}, {"absenceTypeName": "Other"}, {"requestType": "Vacacións"},
                       {"periods": [["12/01/2026", "12/01/2026", "09:00", "12:00"]]},
                       {"observations": ""}, {"attachments": ["unexpected.pdf"]}):
            with self.subTest(change=change), self.assertRaises(absence.AbsenceRequestError):
                absence.verify_review({**self.review, **change}, self.selection)


class CatalogTests(unittest.TestCase):
    def test_types_and_hour_requirements_come_from_live_response(self):
        session = SimpleNamespace(driver=Mock())
        session.driver.execute_script.return_value = {'years':['2026'], 'types':[{'id':'99','name':'New type'}]}
        session.driver.execute_async_script.return_value = {'types':[{'id':'99','name':'New type','requiresHours':True}]}
        with patch.object(absence, '_open_form'):
            catalog = absence.fetch_absence_catalog(session)
        self.assertEqual(catalog['years'], [2026])
        self.assertEqual(catalog['types'], [{'id':'99','name':'New type','requiresHours':True}])

    def test_failed_hour_lookup_prevents_using_incomplete_catalog(self):
        session = SimpleNamespace(driver=Mock())
        session.driver.execute_script.return_value = {'years':['2026'], 'types':[{'id':'3','name':'Curso'}]}
        for response in ({'error':True}, {'types':[]}, {'types':[{'id':'3','name':'Curso','requiresHours':'true'}]}):
            session.driver.execute_async_script.return_value = response
            with self.subTest(response=response), patch.object(absence,'_open_form'), self.assertRaises(absence.AbsenceRequestError):
                absence.fetch_absence_catalog(session)
