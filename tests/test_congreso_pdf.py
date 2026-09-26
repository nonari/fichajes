import subprocess
import tempfile
import unittest
from pathlib import Path

from plugins.congreso_dieta import pdf
from plugins.congreso_dieta.config import SigningConfig, VisibleSignature

SIGNING = SigningConfig("mozilla", "Alias", None)


def completed(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


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

    def test_visible_signature_is_passed_as_autofirma_extra_params(self):
        visible = VisibleSignature(page=1, x=90, y=141, width=29, height=27.5,
                                   text="Firmado por $$SUBJECTCN$$ el día $$SIGNDATE=dd/MM/yyyy$$", font_size=9)
        command = pdf.sign_command(Path("in.pdf"), Path("out.pdf"), SigningConfig("mozilla", "Alias", None, visible))
        extra = command[command.index("-config") + 1]
        # AutoFirma's command line splits settings on a literal backslash-n, not a newline.
        self.assertEqual(extra.split("\\n"), [
            "signaturePage=1",
            "signaturePositionOnPageLowerLeftX=90", "signaturePositionOnPageLowerLeftY=141",
            # AutoFirma silently drops the stamp unless coordinates are whole points: 141 + 27.5 rounds up.
            "signaturePositionOnPageUpperRightX=119", "signaturePositionOnPageUpperRightY=169",
            "layer2Text=Firmado por $$SUBJECTCN$$ el día $$SIGNDATE=dd/MM/yyyy$$", "layer2FontSize=9",
        ])
        self.assertNotIn("\n", extra)
        third = pdf.visible_signature_params(VisibleSignature(1, 300, 1, 193.33, 33, "Firmado", 12))
        self.assertIn("signaturePositionOnPageUpperRightX=493\\n", third)
        appended = pdf.visible_signature_params(VisibleSignature("append", 250, 40, 310, 70, "Firmado", 14))
        self.assertTrue(appended.startswith("signaturePage=append\\n"))
        self.assertNotIn("-config", pdf.sign_command(Path("in.pdf"), Path("out.pdf"), SIGNING))

    def fake_autofirma(self, aliases=("as-logins-key", "Alias"), sign=None):
        """Answer listaliases with the given aliases and sign with `sign` (default: write a PDF)."""
        calls = []

        def runner(command, timeout):
            calls.append(command)
            if command[1] == "listaliases":  # AutoFirma prints the aliases on stderr
                return completed(err="".join(f"{alias}\n" for alias in aliases))
            if sign:
                return sign(command, timeout)
            Path(command[5]).write_bytes(b"%PDF-signed")
            return completed()

        return runner, calls

    def test_sign_success_and_failures(self):
        out = self.root / "firmado.pdf"
        runner, _ = self.fake_autofirma()
        self.assertEqual(pdf.sign_pdf(self.root / "unido.pdf", out, SIGNING, runner=runner), out)
        java = "Exception in thread main\n    at es.gob.Foo(Foo.java:1)\nNo se encontro el alias"
        runner, _ = self.fake_autofirma(sign=lambda command, timeout: completed(0, out=java))
        with self.assertRaisesRegex(pdf.PdfError, "No se encontro el alias"):
            pdf.sign_pdf(self.root / "unido.pdf", out, SIGNING, runner=runner)
        self.assertFalse(out.exists())

        def hang(command, timeout):
            raise subprocess.TimeoutExpired(command, timeout)

        runner, _ = self.fake_autofirma(sign=hang)
        with self.assertRaisesRegex(pdf.PdfError, "no respondió"):
            pdf.sign_pdf(self.root / "unido.pdf", out, SIGNING, runner=runner)


class AliasResolutionTests(SignTests):
    CORRECT = "BUDIÑO REGUEIRA ALEJANDRO - 00000000T"
    # AutoFirma decodes the UTF-8 certificate nickname as Latin-1: "Ñ" (C3 91) becomes "Ã" + U+0091.
    GARBLED = "BUDIÃ\u0091O REGUEIRA ALEJANDRO - 00000000T"

    def test_misencoded_alias_is_matched_and_signed_with_autofirmas_raw_name(self):
        runner, calls = self.fake_autofirma(aliases=("as-logins-key", self.GARBLED))
        signing = SigningConfig("mozilla", self.CORRECT, None)
        pdf.sign_pdf(self.root / "unido.pdf", self.root / "firmado.pdf", signing, runner=runner)
        self.assertEqual(calls[0], ["autofirma", "listaliases", "-store", "mozilla"])
        self.assertEqual(calls[1][calls[1].index("-alias") + 1], self.GARBLED)

    def test_correctly_encoded_alias_is_used_as_is(self):
        runner, calls = self.fake_autofirma(aliases=(self.CORRECT,))
        pdf.sign_pdf(self.root / "unido.pdf", self.root / "firmado.pdf",
                     SigningConfig("pkcs12:/c.p12", self.CORRECT, "pw"), runner=runner)
        self.assertEqual(calls[0], ["autofirma", "listaliases", "-store", "pkcs12:/c.p12", "-password", "pw"])
        self.assertEqual(calls[1][calls[1].index("-alias") + 1], self.CORRECT)

    def test_unknown_alias_lists_the_available_certificates(self):
        runner, calls = self.fake_autofirma(aliases=("as-logins-key", self.GARBLED))
        with self.assertRaisesRegex(pdf.PdfError, "Otra Persoa.*BUDIÑO REGUEIRA"):
            pdf.sign_pdf(self.root / "unido.pdf", self.root / "firmado.pdf",
                         SigningConfig("mozilla", "Otra Persoa", None), runner=runner)
        self.assertEqual(len(calls), 1)

    def test_listaliases_failure(self):
        with self.assertRaisesRegex(pdf.PdfError, "almacén"):
            pdf.sign_pdf(self.root / "unido.pdf", self.root / "firmado.pdf", SIGNING,
                         runner=lambda command, timeout: completed(1, err="Error al abrir el almacen"))
