"""Join the spreadsheet and authorization PDFs and sign the result with AutoFirma's command line."""
from __future__ import annotations

import subprocess
import unicodedata
from pathlib import Path

from plugins.congreso_dieta.config import SigningConfig

JOIN_TIMEOUT = 60
LIST_TIMEOUT = 60
SIGN_TIMEOUT = 180


class PdfError(RuntimeError):
    """Joining or signing failed."""


def run(command, timeout) -> subprocess.CompletedProcess:
    return subprocess.run([str(part) for part in command], capture_output=True, text=True, timeout=timeout)


def _message(result) -> str:
    # AutoFirma prints Java stack traces; the last line that is not a frame is the useful one.
    lines = [line.strip() for line in f"{result.stderr or ''}\n{result.stdout or ''}".splitlines()]
    lines = [line for line in lines if line and not line.startswith("at ")]
    return lines[-1] if lines else f"código de salida {result.returncode}"


def _is_pdf(path: Path) -> bool:
    return path.is_file() and path.read_bytes()[:5] == b"%PDF-"


def join_pdfs(first: Path, second: Path, out: Path, *, runner=run) -> Path:
    try:
        result = runner(["pdfunite", first, second, out], JOIN_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PdfError(f"No se pudieron unir los PDF: {exc}") from exc
    if result.returncode != 0 or not _is_pdf(out):
        raise PdfError(f"No se pudieron unir los PDF: {_message(result)}")
    return out


def _store_arguments(signing: SigningConfig) -> list:
    return ["-store", signing.store] + (["-password", signing.password] if signing.password else [])


def _repaired(alias: str) -> str:
    """AutoFirma decodes UTF-8 certificate nicknames as Latin-1 ("Ñ" -> "Ã" + U+0091); undo that."""
    try:
        alias = alias.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return unicodedata.normalize("NFC", alias.strip())


def resolve_alias(signing: SigningConfig, *, runner=run) -> str:
    """Return AutoFirma's own spelling of the configured certificate alias."""
    try:
        result = runner(["autofirma", "listaliases"] + _store_arguments(signing), LIST_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PdfError(f"AutoFirma no respondió al consultar el almacén: {exc}") from exc
    if result.returncode != 0:
        raise PdfError(f"AutoFirma no pudo abrir el almacén {signing.store}: {_message(result)}")
    # AutoFirma prints the aliases on stderr; read both streams in case that changes.
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    aliases = [line.rstrip("\r") for line in output.splitlines() if line.strip() and not line[0].isspace()]
    wanted = unicodedata.normalize("NFC", signing.alias.strip())
    for alias in aliases:
        if alias == signing.alias or _repaired(alias) == wanted:
            return alias
    available = ", ".join(_repaired(alias) for alias in aliases) or "ninguno"
    raise PdfError(f"No se encontró el certificado «{signing.alias}» en el almacén {signing.store}. "
                   f"Disponibles: {available}")


def sign_command(src: Path, out: Path, signing: SigningConfig, alias: str = None) -> list:
    command = ["autofirma", "sign", "-i", src, "-o", out, "-format", "pades",
               "-store", signing.store, "-alias", alias or signing.alias]
    if signing.password:
        command += ["-password", signing.password]
    return command


def sign_pdf(src: Path, out: Path, signing: SigningConfig, *, runner=run) -> Path:
    out.unlink(missing_ok=True)
    alias = resolve_alias(signing, runner=runner)
    try:
        result = runner(sign_command(src, out, signing, alias), SIGN_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PdfError(f"AutoFirma no respondió: {exc}") from exc
    if result.returncode != 0 or not _is_pdf(out):
        out.unlink(missing_ok=True)
        raise PdfError(f"AutoFirma no firmó el documento: {_message(result)}")
    return out
