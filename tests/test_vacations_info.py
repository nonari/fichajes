import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from selenium.common.exceptions import TimeoutException

from fichaxebot.scrap_functions.vacations_info import (
    VacationsInfoError,
    fetch_vacations_info,
)


class VacationsInfoTests(unittest.TestCase):
    def make_session(self):
        headers = ["Tipo de vacacións", "Ano", "Núm. de días xerais",
                   "Núm. de días autorizados", "Núm. de días solicitados"]
        row = Mock()
        row.find_elements.return_value = [
            SimpleNamespace(text=value)
            for value in (" Vacacións ", "2026", "22", "10", "2")
        ]
        table = Mock()
        table.find_elements.side_effect = lambda by, selector: {
            "thead th": [SimpleNamespace(text=value) for value in headers],
            "tbody tr": [row],
        }[selector]
        session = SimpleNamespace(_ensure_access_to=Mock(), wait=Mock())
        session.wait.until.return_value = table
        return session, headers

    def test_uses_authenticated_summary_route_independent_of_applicant_id(self):
        for applicant_id in (None, "27478"):
            with self.subTest(applicant_id=applicant_id):
                session, headers = self.make_session()
                if applicant_id is not None:
                    session.internal_user_id = applicant_id

                self.assertEqual(fetch_vacations_info(session), (
                    headers, ["Vacacións"], [[2026, 22, 10, 2]],
                ))
                session._ensure_access_to.assert_called_once_with(
                    "https://fichaxe.usc.gal/pas/vacacionsPermisosLicenzas"
                )

    def test_missing_table_reports_vacation_info_error(self):
        session, _ = self.make_session()
        session.wait.until.side_effect = TimeoutException()
        with self.assertRaisesRegex(VacationsInfoError, "encontrar la tabla"):
            fetch_vacations_info(session)


if __name__ == "__main__":
    unittest.main()
