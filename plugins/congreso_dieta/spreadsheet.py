"""Fill the per-diem workbook and run its CreaPDF macro in a private headless LibreOffice."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

SHEET = "FormularioCL"
MACRO = "vnd.sun.star.script:VBAProject.Módulo1.CreaPDF?language=Basic&location=document"
TIMEOUT_SECONDS = 180
CONNECT_SECONDS = 60
ALWAYS_EXECUTE_NO_WARN = 4  # com.sun.star.document.MacroExecMode


class SpreadsheetError(RuntimeError):
    """The workbook PDF could not be generated."""


@dataclass(frozen=True)
class SheetDates:
    departure: date
    return_: date
    declaration: date

    def cells(self) -> dict[str, date]:
        return {"I17": self.departure, "N17": self.return_, "Q78": self.declaration}


def date_serial(day: date) -> int:
    """Spreadsheet date value (days since 1899-12-30)."""
    return (day - date(1899, 12, 30)).days


def _soffice_command(profile: Path, pipe: str) -> list[str]:
    return ["soffice", "--headless", "--invisible", "--nologo", "--norestore", "--nodefault",
            f"-env:UserInstallation={profile.as_uri()}", f"--accept=pipe,name={pipe};urp;"]


class LibreOffice:
    """One private soffice process with its own profile; generate() blocks (use a worker thread)."""

    def __init__(self, *, timeout: float = TIMEOUT_SECONDS, command=_soffice_command) -> None:
        self._timeout = timeout
        self._command = command
        self._pipe = f"fichaxe_{uuid4().hex}"
        self._profile = None
        self._watchdog = None
        self.process = None

    def __enter__(self) -> "LibreOffice":
        self._profile = tempfile.TemporaryDirectory(prefix="fichaxe-lo-")
        self.process = subprocess.Popen(self._command(Path(self._profile.name), self._pipe),
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # A hung LibreOffice is killed; blocked UNO calls then fail instead of waiting forever.
        self._watchdog = threading.Timer(self._timeout, self.process.kill)
        self._watchdog.daemon = True
        self._watchdog.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._watchdog.cancel()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._profile.cleanup()
        return False

    def _connect(self):
        import uno  # LibreOffice's python3-uno, visible through the venv's system site-packages

        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        deadline = time.monotonic() + CONNECT_SECONDS
        while True:
            try:
                return resolver.resolve(f"uno:pipe,name={self._pipe};urp;StarOffice.ComponentContext")
            except Exception as exc:  # noqa: BLE001 - NoConnectException until soffice listens
                if time.monotonic() > deadline or self.process.poll() is not None:
                    raise SpreadsheetError("No se pudo iniciar LibreOffice.") from exc
                time.sleep(0.5)

    def generate(self, workbook: Path, dates: SheetDates) -> Path:
        try:
            import uno  # noqa: F401 - installs the import hook that makes com.sun.star importable
            from com.sun.star.beans import PropertyValue

            def prop(name, value):
                item = PropertyValue()
                item.Name, item.Value = name, value
                return item

            context = self._connect()
            desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
            document = desktop.loadComponentFromURL(
                workbook.as_uri(), "_blank", 0,
                (prop("Hidden", True), prop("MacroExecutionMode", ALWAYS_EXECUTE_NO_WARN)))
            try:
                sheet = document.Sheets.getByName(SHEET)
                document.CurrentController.setActiveSheet(sheet)
                for cell, day in dates.cells().items():
                    sheet.getCellRangeByName(cell).setValue(date_serial(day))
                document.getScriptProvider().getScript(MACRO).invoke((), (), ())
                code = sheet.getCellRangeByName("P1").getString().strip()
            finally:
                document.close(True)
        except SpreadsheetError:
            raise
        except Exception as exc:  # noqa: BLE001 - UNO raises its own exception types
            raise SpreadsheetError(f"LibreOffice no pudo generar la hoja: {exc}") from exc
        pdf = workbook.parent / f"{code}.pdf"
        if not code or not pdf.is_file():
            raise SpreadsheetError("La macro CreaPDF no generó el PDF.")
        return pdf


def generate_pdf(template: Path, workdir: Path, dates: SheetDates, *, office_factory=LibreOffice) -> Path:
    """Copy the template into workdir, fill the dates and run CreaPDF; the template is never modified."""
    workdir.mkdir(parents=True, exist_ok=True)
    workbook = workdir / "hoja.xlsm"
    shutil.copyfile(template, workbook)
    with office_factory() as office:
        return office.generate(workbook, dates)
