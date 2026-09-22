from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .models import Run, now


class PersistenceError(RuntimeError):
    pass


def component(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', value):
        raise ValueError(f'非法路径标识：{value!r}')
    return value


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path: Path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def artifact(batch: Path, kind: str, run: Run, suffix='json') -> Path:
    return batch / component(kind) / component(run.question_id) / (component(run.run_id) + '.' + suffix)


def load_runs(batch: Path) -> list[Run]:
    runs = [Run(**v) for v in read_json(batch / 'mapping.json')['runs']]
    keys = [(component(r.question_id), component(r.run_id)) for r in runs]
    if len(set(keys)) != len(keys) or len({r.request_id for r in runs}) != len(runs):
        raise ValueError('映射中的运行标识重复')
    for r in runs:
        if r.execution_status not in {'pending', 'running', 'success', 'failed', 'unknown', 'not_started'}:
            raise ValueError('未知执行状态')
    return runs


def save_runs(batch: Path, runs: list[Run]):
    try:
        write_json(batch / 'mapping.json', {'updated_at': now(), 'runs': [asdict(r) for r in runs]})
    except OSError as exc:
        raise PersistenceError(f'映射保存失败；停止新提交：{exc}') from exc


@contextmanager
def batch_lock(batch: Path):
    """OS lock: released after crash; the persistent file is not the lock state."""
    with (batch / '.operation.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('此批次已有操作正在运行') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
