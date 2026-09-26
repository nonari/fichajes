"""Persisted congress cases and their working files."""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fichaxebot.config import CONFIG_FILE
from fichaxebot.storage import write_json_atomic

DATA_DIR = CONFIG_FILE.parent / ".plugin_data"
CASES_FILE = DATA_DIR / "congreso_dieta.json"
FILES_DIR = DATA_DIR / "congreso_dieta"


class Stage(StrEnum):
    """Where the per-diem document is; the values are what the cases file stores."""
    NO_AUTH = "no_auth"                # special procedure: waits for the congress to end
    AWAITING_AUTH = "awaiting_auth"
    AUTH_RECEIVED = "auth_received"
    GENERATED = "generated"
    SIGNED = "signed"


class Absence(StrEnum):
    """Where the absence request is; the values are what the cases file stores."""
    SCHEDULED = "scheduled"
    ASKING = "asking"
    REQUESTING = "requesting"
    REQUESTED = "requested"
    UNCERTAIN = "uncertain"
    SKIPPED = "skipped"
    SIMULATED = "simulated"
    NOT_REQUESTED = "not_requested"

    @property
    def settled(self) -> bool:
        """The question was answered or can no longer be asked."""
        return self not in (Absence.SCHEDULED, Absence.ASKING, Absence.REQUESTING)


@dataclass
class Case:
    id: str
    start: str
    end: str
    request_id: Optional[str] = None
    simulated: bool = False
    absence: Absence = Absence.SCHEDULED
    stage: Stage = Stage.AWAITING_AUTH
    prompt_token: Optional[str] = None
    auth_date: Optional[str] = None
    last_problem: Optional[str] = None
    check_failures: int = 0
    notified_state: Optional[str] = None
    unsigned_saved: bool = False
    no_auth: bool = False  # special procedure: no congress authorization

    def __post_init__(self) -> None:
        # The cases file stores the plain values; an unknown one raises ValueError.
        self.stage, self.absence = Stage(self.stage), Absence(self.absence)

    @property
    def start_date(self) -> date:
        return date.fromisoformat(self.start)

    @property
    def end_date(self) -> date:
        return date.fromisoformat(self.end)

    @classmethod
    def new(cls, start: date, end: date, *, simulated: bool = False, no_auth: bool = False) -> "Case":
        return cls(id=uuid4().hex, start=start.isoformat(), end=end.isoformat(), simulated=simulated,
                   no_auth=no_auth, stage=Stage.NO_AUTH if no_auth else Stage.AWAITING_AUTH)


class CaseStore:
    def __init__(self, path: Path = CASES_FILE, files_dir: Path = FILES_DIR) -> None:
        self._path = Path(path)
        self._files = Path(files_dir)
        self._cases: dict[str, Case] = {}

    def load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        for item in data["cases"]:
            item.pop("config", None)  # older cases kept a copy of the plugin config; the current one is used
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
