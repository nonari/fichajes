"""Persisted congress cases and their working files."""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fichaxebot.config import CONFIG_FILE
from fichaxebot.storage import write_json_atomic

DATA_DIR = CONFIG_FILE.parent / ".plugin_data"
CASES_FILE = DATA_DIR / "congreso_dieta.json"
FILES_DIR = DATA_DIR / "congreso_dieta"


@dataclass
class Case:
    id: str
    start: str
    end: str
    config: dict
    request_id: Optional[str] = None
    simulated: bool = False
    absence: str = "scheduled"
    stage: str = "awaiting_auth"
    prompt_token: Optional[str] = None
    auth_date: Optional[str] = None
    last_problem: Optional[str] = None
    check_failures: int = 0
    notified_state: Optional[str] = None
    unsigned_saved: bool = False

    @property
    def start_date(self) -> date:
        return date.fromisoformat(self.start)

    @property
    def end_date(self) -> date:
        return date.fromisoformat(self.end)

    @classmethod
    def new(cls, start: date, end: date, config: dict, *, simulated: bool = False) -> "Case":
        return cls(id=uuid4().hex, start=start.isoformat(), end=end.isoformat(), config=config, simulated=simulated)


class CaseStore:
    def __init__(self, path: Path = CASES_FILE, files_dir: Path = FILES_DIR) -> None:
        self._path = Path(path)
        self._files = Path(files_dir)
        self._cases: dict[str, Case] = {}

    def load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self._cases = {item["id"]: Case(**item) for item in data["cases"]}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self._path, {"version": 1, "cases": [asdict(case) for case in self.open_cases()]})

    def add(self, case: Case) -> None:
        self._cases[case.id] = case
        self.save()

    def get(self, case_id) -> Optional[Case]:
        return self._cases.get(case_id)

    def by_prompt_token(self, token: str) -> Optional[Case]:
        return next((case for case in self._cases.values() if case.prompt_token == token), None)

    def open_cases(self) -> list[Case]:
        return sorted(self._cases.values(), key=lambda case: (case.start, case.id))

    def overlaps(self, start: date, end: date) -> bool:
        return any(case.start_date <= end and start <= case.end_date for case in self._cases.values())

    def directory(self, case: Case) -> Path:
        path = self._files / case.id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def remove(self, case_id: str) -> None:
        self._cases.pop(case_id, None)
        shutil.rmtree(self._files / case_id, ignore_errors=True)
        self.save()
