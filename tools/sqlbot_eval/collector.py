from __future__ import annotations

import os
import sys
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from .models import Run, Snapshot, now
from .storage import artifact, load_runs, read_json, save_runs, write_json


def jsonable(value):
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(v) for v in value]
    return value


def record_status(record: dict | None) -> str:
    if record is None:
        return 'unknown'
    return {'SUCCESS': 'success', 'FAILED': 'failed', 'PROCESSING': 'running'}.get(record.get('status'), 'unknown')


class Collector:
    def __init__(self, config: dict, cases: dict[str, dict]):
        self.config, self.cases = config, cases
        backend = Path(__file__).resolve().parents[2] / 'backend'
        sys.path.insert(0, str(backend))
        # Backend settings resolve local configuration relative to backend.
        previous = Path.cwd()
        try:
            os.chdir(backend)
            from common.core.db import engine
            from apps.chat.models.chat_model import ChatRecord, ChatLog
        finally:
            os.chdir(previous)
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        self.engine = create_engine(engine.url, poolclass=NullPool, connect_args={
            'connect_timeout': 10, 'options': '-c statement_timeout=10000 -c lock_timeout=3000'})
        self.Record, self.Log = ChatRecord, ChatLog

    def lookup(self, run: Run, include_logs: bool = False) -> Snapshot:
        from sqlmodel import Session, select
        from sqlalchemy import text
        with Session(self.engine) as session:
            session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            if run.record_id is not None:
                row = session.get(self.Record, run.record_id)
            elif run.chat_id is not None:
                row = session.exec(select(self.Record).where(self.Record.chat_id == run.chat_id,
                                      self.Record.request_id == run.request_id)).one_or_none()
            else:
                return Snapshot('unknown')
            if row is None:
                return Snapshot('unknown')
            record = jsonable(row.model_dump())
            if (record['chat_id'] != run.chat_id or record.get('request_id') != run.request_id
                    or record.get('datasource') != self.config['datasource_id']
                    or record.get('question') != self.cases[run.question_id]['question']):
                raise ValueError('问数记录身份/问题/数据源不匹配，拒绝采集')
            logs = []
            if include_logs:
                logs = [jsonable(log.model_dump()) for log in session.exec(select(self.Log).where(
                    self.Log.pid == row.id).order_by(self.Log.start_time, self.Log.id)).all()]
            return Snapshot(record_status(record), record, logs)


def build_trial(snapshot: Snapshot, run: Run, batch_id: str) -> dict:
    record = snapshot.record
    if record is None:
        raise ValueError('未找到问数记录')
    warnings = []
    operations = {log.get('operate') for log in snapshot.logs}
    for operation in ['GENERATE_SQL', 'EXECUTE_SQL', 'CHOOSE_TABLE']:
        if operation not in operations:
            warnings.append(f'未记录 {operation}，不能据此推断该阶段未发生')
    if any(not log.get('finish_time') for log in snapshot.logs):
        warnings.append('存在尚未结束的日志')
    if snapshot.status not in {'success', 'failed'}:
        warnings.append('问数尚未确认终态，不应按完整答案评分')
    duration = None
    if record.get('create_time') and record.get('finish_time'):
        duration = (datetime.fromisoformat(record['finish_time']) - datetime.fromisoformat(record['create_time'])).total_seconds()
    return {
        'identity': {'batch_id': batch_id, 'question_id': run.question_id, 'run_id': run.run_id,
                     'request_id': run.request_id, 'chat_id': run.chat_id, 'record_id': record['id']},
        'execution': {'status': snapshot.status, 'sql': record.get('sql'), 'result': record.get('data'),
                      'error': record.get('error'), 'sql_exec_result': record.get('sql_exec_result')},
        'evidence': {'record': record, 'logs': snapshot.logs},
        'metrics': {'server_main_s': duration, 'client_elapsed_s': run.elapsed_s,
                    'token_usage_by_log': [{'log_id': log['id'], 'operation': log.get('operate'),
                                            'usage': log.get('token_usage')} for log in snapshot.logs],
                    'note': 'token按日志保存，可能含主流程结束后的异步推荐；未记录的首次有效响应留空'},
        'collection': {'collected_at': now(), 'warnings': warnings, 'client_events': run.events,
                       'client_error': run.error}}


def collect_batch(batch: Path, collector) -> dict:
    runs = load_runs(batch)
    manifest = read_json(batch / 'manifest.json')
    errors = {}
    for run in runs:
        if run.chat_id is None or run.last_stage in {'prepared', 'creating', 'created'}:
            continue
        try:
            snapshot = collector.lookup(run, include_logs=True)
            trial = build_trial(snapshot, run, manifest['batch_id'])
            target = artifact(batch, 'trials', run)
            # Once review material exists, keep its underlying evidence immutable.
            if artifact(batch, 'review_inputs', run).exists() and target.exists():
                continue
            write_json(target, trial)
            run.record_id = snapshot.record['id']
            run.execution_status = snapshot.status
            run.collection_status = 'success'
        except Exception as exc:
            run.collection_status = 'failed'
            errors[f'{run.question_id}/{run.run_id}'] = str(exc)
        save_runs(batch, runs)
    result = {'runs': len(runs), 'collection_errors': errors,
              'execution_counts': {s: sum(r.execution_status == s for r in runs)
                                   for s in ['pending', 'not_started', 'running', 'unknown', 'success', 'failed']}}
    write_json(batch / 'summary.json', result)
    return result
