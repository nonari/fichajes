import copy
import re
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from plugins.congreso_dieta.config import VisibleSignature, parse_config

TODAY = date(2026, 10, 1)
CONGRESS = {
    "data_processing_authorized": True,
    "address": {"country": "España", "province": "Coruña, A", "municipality": "Santiago de Compostela",
                "postal_code": "15782", "line1": "Rúa Exemplo 1"},
    "reason": "Asistencia a congreso", "organization": "Universidade de Exemplo",
    "employment_category": "PREDOUTORAIS", "teaching_assigned": False, "supervisor_query": "Persoa Supervisora",
}


def raw_config(directory):
    root = Path(directory)
    (root / "dietas").mkdir()
    template = root / "plantilla.xlsm"
    template.write_bytes(b"xlsm")
    return {
        "webapp_url": "https://example.test/congreso.html",
        "output_dir": str(root / "dietas"),
        "auth_check_time": "10:00",
        "prompt": {"at": "09:00", "reminder_minutes": 30, "window": ["08:00", "20:00"]},
        "signing": {"store": "mozilla", "alias": "Alias de proba", "password": None},
        "days_before": 3,
        "spreadsheet_template": str(template),
        "absence": {"type": "Asistencia a congresos", "start_time": "8:00", "end_time": "15:00"},
        "congress": copy.deepcopy(CONGRESS),
    }


class ConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.raw = raw_config(directory.name)

    def test_valid_config_is_parsed_and_normalised(self):
        config = parse_config(self.raw, today=TODAY)
        self.assertEqual((config.prompt.at, config.prompt.window_start, config.prompt.window_end),
                         (time(9), time(8), time(20)))
        self.assertEqual((config.absence.start_time, config.absence.end_time), ("08:00", "15:00"))
        self.assertEqual((config.days_before, config.signing.alias), (3, "Alias de proba"))
        self.assertEqual(config.auth_check_time, time(10))
        self.assertEqual(config.congress, CONGRESS)

    def test_invalid_values_name_the_setting(self):
        cases = [
            (("webapp_url",), "http://example.test", "webapp_url"),
            (("prompt", "at"), "25:00", "prompt.at"),
            (("prompt", "window"), ["20:00", "08:00"], "prompt.window"),
            (("prompt", "at"), "21:00", "prompt.at"),
            (("prompt", "reminder_minutes"), 0, "prompt.reminder_minutes"),
            (("days_before",), 0, "days_before"),
            (("signing", "store"), "windows", "signing.store"),
            (("signing", "alias"), " ", "signing.alias"),
            (("absence", "end_time"), "07:00", "absence.start_time"),
            (("output_dir",), "/nonexistent/dietas", "output_dir"),
            (("spreadsheet_template",), "/nonexistent/plantilla.xlsm", "spreadsheet_template"),
            (("congress", "start_date"), "2026-11-01", "congress"),
            (("congress", "data_processing_authorized"), False, "congress"),
        ]
        for path, value, expected in cases:
            with self.subTest(path=path, value=value):
                raw = copy.deepcopy(self.raw)
                target = raw
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaisesRegex(ValueError, re.escape(expected)):
                    parse_config(raw, today=TODAY)

    def test_missing_section_and_skipping_file_checks(self):
        with self.assertRaisesRegex(ValueError, "congreso_dieta"):
            parse_config(None, today=TODAY)
        raw = copy.deepcopy(self.raw)
        raw["output_dir"] = "/nonexistent/dietas"
        self.assertEqual(parse_config(raw, today=TODAY, check_files=False).output_dir, Path("/nonexistent/dietas"))


class VisibleSignatureConfigTests(unittest.TestCase):
    VISIBLE = {"page": 1, "x": 90, "y": 141, "width": 29, "height": 27,
               "text": "Firmado por $$SUBJECTCN$$ el día $$SIGNDATE=dd/MM/yyyy$$", "font_size": 9}

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.raw = raw_config(directory.name)

    def parse(self, visible):
        raw = copy.deepcopy(self.raw)
        raw["signing"]["visible"] = visible
        return parse_config(raw, today=TODAY)

    def test_invisible_by_default(self):
        self.assertIsNone(parse_config(self.raw, today=TODAY).signing.visible)

    def test_visible_signature_is_parsed(self):
        self.assertEqual(self.parse(dict(self.VISIBLE)).signing.visible,
                         VisibleSignature(page=1, x=90, y=141, width=29, height=27,
                                          text=self.VISIBLE["text"], font_size=9))
        without_font = {key: value for key, value in self.VISIBLE.items() if key != "font_size"}
        self.assertEqual(self.parse(without_font).signing.visible.font_size, 9)

    def test_append_places_the_stamp_on_a_new_last_page(self):
        self.assertEqual(self.parse({**self.VISIBLE, "page": "append"}).signing.visible.page, "append")

    def test_invalid_visible_values_name_the_setting(self):
        for key, value in (("page", 0), ("page", "1"), ("x", -1), ("y", "141"), ("width", 0), ("height", True),
                           ("text", ""), ("text", "dos\nlíneas"), ("text", "con \\ barra"), ("font_size", 0)):
            with self.subTest(key=key, value=value), \
                    self.assertRaisesRegex(ValueError, re.escape(f"signing.visible.{key}")):
                self.parse({**self.VISIBLE, key: value})
        with self.assertRaisesRegex(ValueError, "signing.visible"):
            self.parse([])

