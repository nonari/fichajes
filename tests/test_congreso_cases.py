import tempfile
import unittest
from datetime import date
from pathlib import Path

from fichaxebot import config
from plugins.congreso_dieta import cases
from plugins.congreso_dieta.cases import Case, CaseStore


class CaseStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.path, self.files = root / "cases.json", root / "files"
        self.store = CaseStore(self.path, self.files)

    def reloaded(self):
        store = CaseStore(self.path, self.files)
        store.load()
        return store

    def test_cases_round_trip_and_sort_by_start(self):
        late = Case.new(date(2026, 11, 2), date(2026, 11, 3), {"k": 1})
        early = Case.new(date(2026, 10, 20), date(2026, 10, 21), {"k": 2}, simulated=True)
        self.store.add(late)
        self.store.add(early)
        store = self.reloaded()
        self.assertEqual([case.id for case in store.open_cases()], [early.id, late.id])
        self.assertTrue(store.get(early.id).simulated)
        self.assertEqual(store.get(late.id).config, {"k": 1})
        self.assertEqual(store.get(late.id).start_date, date(2026, 11, 2))

    def test_overlaps_include_touching_days(self):
        self.store.add(Case.new(date(2026, 10, 20), date(2026, 10, 22), {}))
        self.assertTrue(self.store.overlaps(date(2026, 10, 22), date(2026, 10, 23)))
        self.assertTrue(self.store.overlaps(date(2026, 10, 18), date(2026, 10, 25)))
        self.assertFalse(self.store.overlaps(date(2026, 10, 23), date(2026, 10, 24)))

    def test_remove_deletes_the_case_and_its_directory(self):
        case = Case.new(date(2026, 10, 20), date(2026, 10, 22), {})
        self.store.add(case)
        folder = self.store.directory(case)
        (folder / "autorizacion.pdf").write_bytes(b"%PDF-")
        self.store.remove(case.id)
        self.assertIsNone(self.store.get(case.id))
        self.assertFalse(folder.exists())
        self.assertEqual(self.reloaded().open_cases(), [])

    def test_prompt_token_lookup(self):
        case = Case.new(date(2026, 10, 20), date(2026, 10, 22), {})
        case.prompt_token = "a" * 32
        self.store.add(case)
        self.assertIs(self.store.by_prompt_token("a" * 32), case)
        self.assertIsNone(self.store.by_prompt_token("b" * 32))

    def test_default_locations_live_next_to_config(self):
        self.assertEqual(cases.CASES_FILE, config.CONFIG_FILE.parent / ".plugin_data" / "congreso_dieta.json")
        self.assertEqual(cases.FILES_DIR, config.CONFIG_FILE.parent / ".plugin_data" / "congreso_dieta")
