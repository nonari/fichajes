"""Join the spreadsheet and authorization PDFs and sign the result with AutoFirma's command line."""
from __future__ import annotations

import subprocess
from pathlib import Path

from plugins.congreso_dieta.config import SigningConfig

JOIN_TIMEOUT = 60
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


def sign_command(src: Path, out: Path, signing: SigningConfig) -> list:
    command = ["autofirma", "sign", "-i", src, "-o", out, "-format", "pades",
               "-store", signing.store, "-alias", signing.alias]
    if signing.password:
        command += ["-password", signing.password]
    return command


def sign_pdf(src: Path, out: Path, signing: SigningConfig, *, runner=run) -> Path:
    out.unlink(missing_ok=True)
    try:
        result = runner(sign_command(src, out, signing), SIGN_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PdfError(f"AutoFirma no respondió: {exc}") from exc
    if result.returncode != 0 or not _is_pdf(out):
        out.unlink(missing_ok=True)
        raise PdfError(f"AutoFirma no firmó el documento: {_message(result)}")
    return out
