import base64
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from plugins.congreso_dieta import usc

FIXTURE = (Path(__file__).parent / "fixtures" / "congreso_solicitude.html").read_text(encoding="utf-8")
AUTH_URL = ("https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm"
            "?solicitudeId=100001&csv=EEEE-5555-FFFF-6666")
WITHOUT_AUTHORIZATION = re.sub(r'<li class="mt-2"><a[^>]*>Autorización\s*</a></li>', "", FIXTURE)


class DetailParsingTests(unittest.TestCase):
    def test_signed_request_exposes_the_authorization_link(self):
        status = usc.parse_request_detail(FIXTURE)
        self.assertEqual((status.state, status.authorization_url), ("Tramitada", AUTH_URL))
        self.assertTrue(status.signed)
        self.assertFalse(status.rejected)

    def test_in_progress_and_rejected_states(self):
        pending = usc.parse_request_detail(WITHOUT_AUTHORIZATION.replace("Estado: Tramitada", "Estado: En tramitación"))
        self.assertEqual((pending.state, pending.authorization_url), ("En tramitación", None))
        self.assertFalse(pending.signed or pending.rejected)
        rejected = usc.parse_request_detail(FIXTURE.replace("Estado: Tramitada", "Estado: Denegada"))
        self.assertTrue(rejected.rejected)
        self.assertFalse(rejected.signed)

    def test_page_without_state_or_with_foreign_link_is_an_error(self):
        with self.assertRaises(usc.UscPageError):
            usc.parse_request_detail("<html><body><p>Login</p></body></html>")
        foreign = FIXTURE.replace("https://aplicacions.usc.es/intranet/solicitudes/documento/csv.htm"
                                  "?solicitudeId=100001&amp;csv=EEEE", "https://evil.test/csv.htm?csv=EEEE")
        with self.assertRaises(usc.UscPageError):
            usc.parse_request_detail(foreign)


class FetchTests(unittest.TestCase):
    def test_fetch_opens_the_detail_page(self):
        session = SimpleNamespace(driver=SimpleNamespace(page_source=FIXTURE), _ensure_access_to=Mock())
        self.assertTrue(usc.fetch_request_status(session, "100001").signed)
        session._ensure_access_to.assert_called_once_with(
            "https://aplicacions.usc.es/intranet/solicitudes/solicitude/100001/ver.htm")
        with self.assertRaises(usc.UscPageError):
            usc.fetch_request_status(session, "../100001")

    def test_download_returns_pdf_bytes_only(self):
        driver = Mock()
        session = SimpleNamespace(driver=driver)
        driver.execute_async_script.return_value = {"data": base64.b64encode(b"%PDF-1.7 auth").decode()}
        self.assertEqual(usc.download_authorization(session, AUTH_URL), b"%PDF-1.7 auth")
        driver.execute_async_script.return_value = {"data": base64.b64encode(b"<html>login</html>").decode()}
        with self.assertRaises(usc.UscPageError):
            usc.download_authorization(session, AUTH_URL)
        driver.execute_async_script.return_value = {"error": "HTTP 500"}
        with self.assertRaises(usc.UscPageError):
            usc.download_authorization(session, AUTH_URL)
        with self.assertRaises(usc.UscPageError):
            usc.download_authorization(session, "https://evil.test/autorizacion.pdf")
