import unittest
from datetime import date, datetime, time

from fichaxebot.utils import MADRID_TZ
from plugins.congreso_dieta import dates
from plugins.congreso_dieta.config import PromptConfig

PROMPT = PromptConfig(at=time(9, 0), reminder_minutes=30, window_start=time(8, 0), window_end=time(20, 0))


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=MADRID_TZ)


class DateRuleTests(unittest.TestCase):
    def test_absence_days_skip_weekends_holidays_and_usc_non_working_days(self):
        non_working = dates.parse_non_working(["N2026-10-13", "V2026-10-14", "N2026-12-24:2026-12-26"])
        self.assertIn(date(2026, 12, 25), non_working)
        self.assertNotIn(date(2026, 10, 14), non_working)
        # 10-11 weekend, 12 national holiday, 13 USC non-working.
        self.assertEqual(dates.absence_days(date(2026, 10, 9), date(2026, 10, 14), non_working),
                         [date(2026, 10, 9), date(2026, 10, 14)])

    def test_earliest_start_and_generation_day(self):
        self.assertEqual(dates.earliest_start(date(2026, 10, 1)), date(2026, 10, 6))
        self.assertEqual(dates.generation_day(date(2026, 10, 14), date(2026, 10, 2)), date(2026, 10, 15))
        self.assertEqual(dates.generation_day(date(2026, 10, 14), date(2026, 10, 20)), date(2026, 10, 20))

    def test_next_slot_stays_inside_the_window(self):
        self.assertEqual(dates.next_slot(at(1, 7), PROMPT), at(1, 8))
        self.assertEqual(dates.next_slot(at(1, 12, 5), PROMPT), at(1, 12, 5))
        self.assertEqual(dates.next_slot(at(1, 20), PROMPT), at(2, 8))

    def test_first_prompt_reminders_and_deadline(self):
        self.assertEqual(dates.first_prompt(date(2026, 10, 20), 3, PROMPT, at(1, 10)), at(17, 9))
        self.assertEqual(dates.first_prompt(date(2026, 10, 6), 7, PROMPT, at(1, 21)), at(2, 8))
        self.assertEqual(dates.next_reminder(at(1, 10), PROMPT), at(1, 10, 30))
        self.assertEqual(dates.next_reminder(at(1, 19, 45), PROMPT), at(2, 8))
        self.assertEqual(dates.prompt_deadline(date(2026, 10, 6)), at(6, 0))
