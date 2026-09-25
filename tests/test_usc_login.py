import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fichaxebot.usc_api import CAS_LOGIN_URL, UscWebSession

PROTECTED = "https://aplicacions.usc.es/intranet/solicitudes/RRHH_InvAsistenciaCongresos.htm"
GENERIC_LOGIN = f"{CAS_LOGIN_URL}?service=https://fichaxe.usc.gal/"
RENEW_LOGIN = f"{CAS_LOGIN_URL}?renew=true&service=https://aplicacions.usc.es/intranet/solicitudes/redirect.htm"


class FakeDriver:
    """Each get() lands on the next scripted URL, as USC's redirects would."""

    def __init__(self, landings):
        self.landings = list(landings)
        self.current_url = "about:blank"

    def get(self, url):
        self.current_url = self.landings.pop(0)


def session_with(landings):
    session = UscWebSession.__new__(UscWebSession)
    session.driver = FakeDriver(landings)
    session.wait = SimpleNamespace(until=lambda condition, message=None: condition(session.driver))
    session._perform_login = Mock()
    session._submit_credentials = Mock()
    return session


class EnsureAccessTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("fichaxebot.usc_api.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_active_session_needs_no_login(self):
        session = session_with([PROTECTED])
        session._ensure_access_to(PROTECTED)
        session._perform_login.assert_not_called()
        session._submit_credentials.assert_not_called()

    def test_expired_session_logs_in_once(self):
        session = session_with([GENERIC_LOGIN, PROTECTED])
        session._ensure_access_to(PROTECTED)
        session._perform_login.assert_called_once_with()
        session._submit_credentials.assert_not_called()
        self.assertEqual(session.driver.current_url, PROTECTED)

    def test_service_demanding_reauthentication_is_logged_into_on_its_own_page(self):
        session = session_with([GENERIC_LOGIN, RENEW_LOGIN])

        def submit():
            session.driver.current_url = PROTECTED + "?execution=e1s1"

        session._submit_credentials.side_effect = submit
        session._ensure_access_to(PROTECTED)
        session._perform_login.assert_called_once_with()
        session._submit_credentials.assert_called_once_with()
        self.assertEqual(session.driver.current_url, PROTECTED + "?execution=e1s1")
