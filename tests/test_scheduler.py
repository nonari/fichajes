import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fichaxebot import config, scheduler, storage


class ScheduleFileLocationTests(unittest.TestCase):
    def test_schedule_file_lives_next_to_config_not_in_the_working_directory(self):
        self.assertTrue(scheduler.SCHEDULE_FILE.is_absolute())
        self.assertEqual(scheduler.SCHEDULE_FILE, config.CONFIG_FILE.parent / ".schedule.data")


class AtomicJsonTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "data.json"

    def test_writes_json(self):
        storage.write_json_atomic(self.path, [{"a": 1}])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [{"a": 1}])

    def test_failed_write_keeps_previous_content_and_leaves_no_temp_file(self):
        self.path.write_text('["previous"]', encoding="utf-8")
        with patch("fichaxebot.storage.os.replace", side_effect=OSError("disk full")), \
             self.assertRaises(OSError):
            storage.write_json_atomic(self.path, ["new"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), '["previous"]')
        self.assertEqual(sorted(p.name for p in Path(self.dir.name).iterdir()), ["data.json"])
