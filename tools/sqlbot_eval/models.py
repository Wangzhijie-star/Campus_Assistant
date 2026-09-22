from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timezone
import uuid

DIMENSIONS = ['correctness', 'completion', 'verifiability', 'sql_execution',
              'information_recall', 'error_handling']
TERMINAL = {'success', 'failed'}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Case:
    question_id: str
    question: str
    expected: str
    criteria: dict[str, Any]
    source_row: int


@dataclass
class Run:
    question_id: str
    run_id: str = 'run_001'
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    chat_id: int | None = None
    record_id: int | None = None
    execution_status: str = 'pending'
    collection_status: str = 'pending'
    last_stage: str = 'prepared'
    events: list[dict] = field(default_factory=list)
    error: str | None = None
    started_at: str | None = None
    elapsed_s: float | None = None


@dataclass
class Event:
    kind: str
    payload: dict


@dataclass
class Snapshot:
    status: str
    record: dict | None = None
    logs: list[dict] = field(default_factory=list)
