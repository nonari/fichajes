import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from plugins.congreso_dieta import pdf
from plugins.congreso_dieta.config import SigningConfig

SIGNING = SigningConfig("mozilla", "Alias", None)


def completed(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def minimal_pdf() -> bytes:
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


class PdfTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)


class JoinTests(PdfTestCase):
    def test_join_runs_pdfunite_spreadsheet_first(self):
        calls = []

        def runner(command, timeout):
            calls.append(command)
            Path(command[-1]).write_bytes(b"%PDF-joined")
            return completed()

        out = self.root / "unido.pdf"
        self.assertEqual(pdf.join_pdfs(self.root / "hoja.pdf", self.root / "auth.pdf", out, runner=runner), out)
        self.assertEqual(calls, [["pdfunite", self.root / "hoja.pdf", self.root / "auth.pdf", out]])

    def test_join_failure_reports_the_tool_message(self):
        with self.assertRaisesRegex(pdf.PdfError, "Syntax Error"):
            pdf.join_pdfs(self.root / "a.pdf", self.root / "b.pdf", self.root / "unido.pdf",
                          runner=lambda command, timeout: completed(1, err="Syntax Error: bad xref"))


class SignTests(PdfTestCase):
    def test_sign_command_uses_store_alias_and_optional_password(self):
        self.assertEqual(pdf.sign_command(Path("in.pdf"), Path("out.pdf"), SIGNING),
                         ["autofirma", "sign", "-i", Path("in.pdf"), "-o", Path("out.pdf"), "-format", "pades",
                          "-store", "mozilla", "-alias", "Alias"])
        self.assertEqual(pdf.sign_command(Path("in.pdf"), Path("out.pdf"),
                                          SigningConfig("pkcs12:/c.p12", "A", "pw"))[-2:], ["-password", "pw"])

    def test_sign_success_and_failures(self):
        out = self.root / "firmado.pdf"

        def ok(command, timeout):
            Path(command[5]).write_bytes(b"%PDF-signed")
            return completed()

        self.assertEqual(pdf.sign_pdf(self.root / "unido.pdf", out, SIGNING, runner=ok), out)
        java = "Exception in thread main\n    at es.gob.Foo(Foo.java:1)\nNo se encontro el alias"
        with self.assertRaisesRegex(pdf.PdfError, "No se encontro el alias"):
            pdf.sign_pdf(self.root / "unido.pdf", out, SIGNING, runner=lambda command, timeout: completed(0, out=java))
        self.assertFalse(out.exists())

        def hang(command, timeout):
            raise subprocess.TimeoutExpired(command, timeout)

        with self.assertRaisesRegex(pdf.PdfError, "no respondió"):
            pdf.sign_pdf(self.root / "unido.pdf", out, SIGNING, runner=hang)


@unittest.skipUnless(os.environ.get("CONGRESO_SIGN_ALIAS"), "Set CONGRESO_SIGN_ALIAS to sign a dummy PDF with AutoFirma")
class RealSigningTests(PdfTestCase):
    def test_autofirma_signs_a_dummy_pdf(self):
        source = self.root / "dummy.pdf"
        source.write_bytes(minimal_pdf())
        signing = SigningConfig(os.environ.get("CONGRESO_SIGN_STORE", "mozilla"), os.environ["CONGRESO_SIGN_ALIAS"],
                                os.environ.get("CONGRESO_SIGN_PASSWORD"))
        signed = pdf.sign_pdf(source, self.root / "signed.pdf", signing)
        self.assertIn(b"/ByteRange", signed.read_bytes())
