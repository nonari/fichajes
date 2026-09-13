"""Calendar categories passed from USC to the vacation selection screen."""
import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from fichaxebot.scrap_functions.view_calendar import fetch_calendar_summary


def calendar_row(kind, first, last=None):
    # USC's renderer subtracts a day from startDate before exposing the array.
    start = datetime.fromisoformat(first) - timedelta(days=1)
    return {"tipo": kind, "startDate": start.isoformat(), "endDate": last or first}


class CalendarSummaryTests(unittest.TestCase):
    def session(self, rows):
        return SimpleNamespace(
            _ensure_access_to=Mock(), wait=Mock(),
            driver=SimpleNamespace(execute_script=Mock(return_value=json.dumps(rows))),
        )

    def test_selection_includes_three_categories_and_preserves_ranges(self):
        session = self.session([
            calendar_row("QUENDA_PRIMARIA", "2026-09-10"),
            calendar_row("QUENDA_ALTERNATIVA", "2026-09-11"),
            calendar_row("DIA_NON_LABORABLE", "2026-09-12", "2026-09-13"),
            calendar_row("DIA_VACACIONS_COMPLETA_APROBADA", "2026-09-14", "2026-09-16"),
            calendar_row("DIA_VACACIONS_SOLICITADA", "2026-09-17"),
        ])
        self.assertEqual(fetch_calendar_summary(session, for_vacation_selection=True), [
            "P2026-09-11", "N2026-09-12:2026-09-13",
            "V2026-09-14:2026-09-16", "V2026-09-17",
        ])

    def test_readonly_calendar_retains_existing_filtering(self):
        session = self.session([
            calendar_row("QUENDA_ALTERNATIVA", "2026-09-11"),
            calendar_row("DIA_NON_LABORABLE", "2026-09-12", "2026-09-13"),
            calendar_row("DIA_NON_LABORABLE", "2026-09-14"),
            calendar_row("DIA_VACACIONS_APROBADA", "2026-09-15"),
        ])
        self.assertEqual(fetch_calendar_summary(session), ["N2026-09-14", "V2026-09-15"])

    def test_empty_calendar(self):
        self.assertEqual(fetch_calendar_summary(self.session([]), for_vacation_selection=True), [])


if __name__ == "__main__":
    unittest.main()
