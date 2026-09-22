from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = 'tools.sqlbot_eval'

from .storage import batch_lock, load_runs, read_json


async def execute(batch, config, collector):
    from .runner import run_batch
    from .sqlbot_client import SQLBotClient
    client = SQLBotClient(config, os.environ.get(config.get('token_env', 'SQLBOT_TOKEN'), ''))
    try:
        return await run_batch(batch, client, collector)
    finally:
        await client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='校园 SQLBot 串行评测；只有 run 提交问题')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    for name in ['config', 'questions', 'ids', 'batch-id', 'rubric', 'template']:
        prepare.add_argument('--' + name, required=True)
    prepare.add_argument('--output', default=str(Path(__file__).parent / 'reports'))
    for name in ['run', 'collect', 'review', 'export', 'rerun']:
        command = commands.add_parser(name)
        command.add_argument('--batch', required=True)
        if name == 'rerun':
            command.add_argument('--question-id', required=True)
        if name == 'export':
            command.add_argument('--allow-missing-hash', action='store_true', help='允许缺少哈希的人工评分导入，明确标记版本未验证；不允许哈希冲突')
            command.add_argument('--output-xlsx')
            command.add_argument('--base-xlsx')
            command.add_argument('--node', default='node')
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        from .inputs import prepare_batch
        from .storage import component
        result = prepare_batch(read_json(Path(args.config)), Path(args.questions), args.ids.split(','),
                               Path(args.output) / component(args.batch_id), Path(args.rubric), Path(args.template))
        print(result)
        return 0
    batch = Path(args.batch).resolve()
    with batch_lock(batch):
        manifest = read_json(batch / 'manifest.json')
        if args.command in {'run', 'collect'}:
            from .collector import Collector, collect_batch
            config = manifest['config']
            cases = {c['question_id']: c for c in read_json(batch / 'cases.json')}
            collector = Collector(config, cases)
            result = {}
            if args.command == 'run':
                try:
                    result = asyncio.run(execute(batch, config, collector))
                finally:
                    # Collect existing requests only; never start another question here.
                    print(json.dumps(collect_batch(batch, collector), ensure_ascii=False, indent=2))
            else:
                result = collect_batch(batch, collector)
        elif args.command == 'review':
            from .review import prepare_reviews
            result = prepare_reviews(batch)
        elif args.command == 'rerun':
            from dataclasses import asdict
            from .runner import rerun
            result = asdict(rerun(batch, args.question_id))
        else:
            from .scorecard import export_plan
            result = export_plan(batch, args.allow_missing_hash)
            if args.output_xlsx:
                command = [args.node, str(Path(__file__).with_name('export_scorecard.mjs')),
                           str(batch / 'export_plan.json'), str(Path(args.output_xlsx).resolve())]
                if args.base_xlsx:
                    command.append(str(Path(args.base_xlsx).resolve()))
                subprocess.run(command, check=True)
            result = {'plan': str(batch / 'export_plan.json'), 'errors': result['errors'], 'warnings': result['warnings']}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get('paused') or result.get('errors') or result.get('collection_errors') else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'评测停止：{exc}', file=sys.stderr)
        raise SystemExit(2)
