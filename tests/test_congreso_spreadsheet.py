import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from plugins.congreso_dieta import spreadsheet
from plugins.congreso_dieta.spreadsheet import SheetDates

DATES = SheetDates(date(2026, 10, 12), date(2026, 10, 14), date(2026, 10, 15))


class FakeOffice:
    calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def generate(self, workbook, dates):
        FakeOffice.calls.append((workbook, dates))
        pdf = workbook.parent / "CL1.pdf"
        pdf.write_bytes(b"%PDF-sheet")
        return pdf


class GeneratePdfTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_works_on_a_copy_and_returns_the_macro_pdf(self):
        template = self.root / "plantilla.xlsm"
        template.write_bytes(b"original")
        workdir = self.root / "case" / "hoja"
        pdf = spreadsheet.generate_pdf(template, workdir, DATES, office_factory=FakeOffice)
        self.assertEqual(pdf, workdir / "CL1.pdf")
        workbook, received = FakeOffice.calls[-1]
        self.assertEqual((workbook.parent, workbook.read_bytes(), received), (workdir, b"original", DATES))
        self.assertEqual(template.read_bytes(), b"original")

    def test_cells_and_date_serials(self):
        self.assertEqual(spreadsheet.date_serial(date(2026, 10, 12)), 46307)
        self.assertEqual(DATES.cells(), {"I17": date(2026, 10, 12), "N17": date(2026, 10, 14), "Q78": date(2026, 10, 15)})


class WatchdogTests(unittest.TestCase):
    def test_hung_office_process_is_killed_after_the_timeout(self):
        office = spreadsheet.LibreOffice(
            timeout=0.3, command=lambda profile, pipe: [sys.executable, "-c", "import time; time.sleep(30)"])
        with office:
            time.sleep(1.5)
            self.assertIsNotNone(office.process.poll())


@unittest.skipUnless(os.environ.get("CONGRESO_TEMPLATE"), "Set CONGRESO_TEMPLATE to a real workbook to run LibreOffice")
class LibreOfficeIntegrationTests(unittest.TestCase):
    def test_macro_fills_the_dates_and_exports_the_form(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = spreadsheet.generate_pdf(Path(os.environ["CONGRESO_TEMPLATE"]), Path(directory) / "hoja", DATES)
            text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                                  capture_output=True, text=True, check=True).stdout
        for value in ("12/10/2026", "14/10/2026", "15/10/2026"):
            self.assertIn(value, text)
