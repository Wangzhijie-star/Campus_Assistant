from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .models import Run, Snapshot, TERMINAL, now
from .sqlbot_client import HTTPFailure
from .storage import read_json, load_runs, save_runs, PersistenceError


async def confirm(run: Run, collector, config: dict) -> Snapshot:
    deadline = time.monotonic() + config['confirm_timeout_s']
    snapshot = Snapshot('unknown')
    while True:
        # Database queries use server statement_timeout; no model or SQL re-execution.
        try:
            snapshot = collector.lookup(run)
        except Exception as exc:
            run.events.append({'type': 'confirmation_error', 'time': now(), 'message': str(exc)})
            return Snapshot('unknown')
        if snapshot.status in TERMINAL:
            if snapshot.record:
                run.record_id = snapshot.record['id']
            return snapshot
        if time.monotonic() >= deadline:
            return snapshot
        await asyncio.sleep(min(config['poll_s'], max(0, deadline - time.monotonic())))


async def run_batch(batch: Path, client, collector) -> dict:
    config = read_json(batch / 'manifest.json')['config']
    cases = {c['question_id']: c for c in read_json(batch / 'cases.json')}
    runs = load_runs(batch)
    await client.preflight()
    paused = None
    for run in runs:
        if run.execution_status in TERMINAL or run.execution_status == 'not_started':
            continue
        if run.last_stage in {'submitting', 'submitted', 'response_ended'}:
            snapshot = await confirm(run, collector, config)
            run.execution_status = snapshot.status
            save_runs(batch, runs)
            if snapshot.status not in TERMINAL:
                paused = '恢复时无法确认上次请求结束，未重新提交'
                break
            continue
        if run.last_stage == 'creating' and run.chat_id is None:
            run.execution_status = 'not_started'
            run.error = '上次会话创建结果未知，未提交问题；可人工补跑'
            save_runs(batch, runs)
            continue
        question = cases[run.question_id]['question']
        if run.chat_id is None:
            run.last_stage = 'creating'
            save_runs(batch, runs)
            try:
                run.chat_id = await asyncio.wait_for(client.create_chat(question), config['timeout_s'])
            except Exception as exc:
                run.execution_status = 'not_started'
                run.error = f'创建会话异常：{type(exc).__name__}: {exc}'
                save_runs(batch, runs)
                paused = run.error  # creation is an environment/interface issue, not SQL accuracy
                break
            run.last_stage = 'created'
            save_runs(batch, runs)
        run.last_stage, run.started_at = 'submitting', now()
        save_runs(batch, runs)
        started = time.monotonic()
        async def consume():
            async for event in client.ask(run.chat_id, run.request_id, question):
                if event.kind == 'id':
                    identifier = event.payload.get('id')
                    if type(identifier) is not int or identifier <= 0:
                        raise ValueError('无效 record_id')
                    if run.record_id is not None and run.record_id != identifier:
                        raise ValueError('同一请求出现不同 record_id')
                    run.record_id = identifier
                    run.execution_status, run.last_stage = 'running', 'submitted'
                if event.kind in {'id', 'sql', 'sql-data', 'chart', 'finish', 'error'}:
                    entry = {'type': event.kind, 'time': now(), 'elapsed_s': round(time.monotonic()-started, 3)}
                    if event.kind == 'error':
                        run.error = str(event.payload.get('content', '后端错误'))
                        entry['message'] = run.error
                    run.events.append(entry)
                if event.kind in {'id', 'finish', 'error'}:
                    save_runs(batch, runs)
        systemic = False
        try:
            await asyncio.wait_for(consume(), config['timeout_s'])
            run.last_stage = 'response_ended'
        except PersistenceError:
            # Persistence failures must not be downgraded to ordinary request failures.
            raise
        except Exception as exc:
            run.error = f'{type(exc).__name__}: {exc}'
            run.events.append({'type': 'client_error', 'time': now(), 'message': run.error})
            run.execution_status = 'unknown'
            systemic = isinstance(exc, HTTPFailure) and exc.status in {401, 403, 429, 503}
        run.elapsed_s = round(time.monotonic() - started, 3)
        save_runs(batch, runs)
        snapshot = await confirm(run, collector, config)
        run.execution_status = snapshot.status
        save_runs(batch, runs)
        if snapshot.status not in TERMINAL or systemic:
            paused = '请求未确认结束或公共依赖异常，停止提交后续题目'
            break
    return {'paused': paused, 'runs': len(runs)}


def rerun(batch: Path, question_id: str) -> Run:
    runs = load_runs(batch)
    existing = [r for r in runs if r.question_id == question_id]
    if not existing:
        raise ValueError('题号不在此批次')
    if any(r.execution_status not in TERMINAL | {'not_started'} for r in existing):
        raise ValueError('本题仍有未确认或未执行的运行，不能补跑')
    run = Run(question_id, f'run_{len(existing)+1:03d}')
    runs.append(run)
    save_runs(batch, runs)
    return run
