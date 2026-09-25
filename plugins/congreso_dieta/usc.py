"""USC requests intranet: congress authorization status and download."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlsplit

REQUEST_URL = "https://aplicacions.usc.es/intranet/solicitudes/solicitude/{id}/ver.htm"
REQUESTS_LIST_URL = "https://aplicacions.usc.es/intranet/solicitudes/solicitudes/listaxe.htm"
DOCUMENT_PATH = "/intranet/solicitudes/documento/csv.htm"
SIGNED_STATE = "tramitada"
# Only the signed state is known from real pages; these words mark states that will not become signed.
_NEGATIVE_WORDS = ("deneg", "anulad", "desist", "rexeit", "rechaz", "arquiv", "revogad")


class UscPageError(RuntimeError):
    """The USC requests page could not be read or returned unexpected content."""


@dataclass(frozen=True)
class RequestStatus:
    state: str
    authorization_url: Optional[str]

    @property
    def signed(self) -> bool:
        return self.state.casefold() == SIGNED_STATE and self.authorization_url is not None

    @property
    def rejected(self) -> bool:
        return any(word in self.state.casefold() for word in _NEGATIVE_WORDS)


class _DetailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.state = None
        self.authorization_url = None
        self._paragraph = None
        self._link = None

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self._paragraph = []
        elif tag == "a":
            self._link = (dict(attrs).get("href"), [])

    def handle_endtag(self, tag):
        if tag == "p" and self._paragraph is not None:
            text = " ".join("".join(self._paragraph).split())
            if text.startswith("Estado:") and self.state is None:
                self.state = text[len("Estado:"):].strip()
            self._paragraph = None
        elif tag == "a" and self._link is not None:
            href, parts = self._link
            if " ".join("".join(parts).split()) == "Autorización" and href and self.authorization_url is None:
                self.authorization_url = href
            self._link = None

    def handle_data(self, data):
        if self._paragraph is not None:
            self._paragraph.append(data)
        if self._link is not None:
            self._link[1].append(data)


def _checked_document_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "aplicacions.usc.es" or parts.path != DOCUMENT_PATH:
        raise UscPageError("El enlace de la autorización no corresponde a USC.")
    return url


def parse_request_detail(html: str) -> RequestStatus:
    parser = _DetailParser()
    parser.feed(html)
    if not parser.state:
        raise UscPageError("No se encontró el estado de la solicitude en USC.")
    url = _checked_document_url(parser.authorization_url) if parser.authorization_url else None
    return RequestStatus(parser.state, url)


def fetch_request_status(session, request_id: str) -> RequestStatus:
    if not str(request_id).isdecimal():
        raise UscPageError("El identificador de la solicitude no es válido.")
    session._ensure_access_to(REQUEST_URL.format(id=request_id))
    return parse_request_detail(session.driver.page_source)


def download_authorization(session, url: str) -> bytes:
    _checked_document_url(url)
    # Fetch in the page so the authenticated cookie jar is used.
    response = session.driver.execute_async_script("""
        const url = arguments[0], done = arguments[arguments.length - 1];
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20000);
        fetch(url, {credentials: 'same-origin', signal: controller.signal})
          .then(async response => {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const bytes = new Uint8Array(await response.arrayBuffer());
            let binary = '';
            for (let i = 0; i < bytes.length; i += 8192)
                binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
            done({data: btoa(binary)});
          }).catch(error => done({error: String(error)})).finally(() => clearTimeout(timer));
    """, url)
    try:
        document = base64.b64decode(response["data"], validate=True)
        if not document.startswith(b"%PDF-"):
            raise ValueError("not a PDF")
    except (KeyError, TypeError, ValueError) as exc:
        raise UscPageError("No se pudo descargar la autorización en PDF.") from exc
    return document
