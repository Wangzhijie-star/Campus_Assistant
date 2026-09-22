import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.sqlbot_eval.models import Run, Event, Snapshot, DIMENSIONS
from tools.sqlbot_eval.storage import write_json, read_json, save_runs, load_runs, component, artifact
from tools.sqlbot_eval.inputs import load_cases, validate_config, prepare_batch
from tools.sqlbot_eval.runner import run_batch, rerun
from tools.sqlbot_eval.review import build_review, prepare_reviews
from tools.sqlbot_eval.scorecard import validate_score, weighted_total, export_plan
from tools.sqlbot_eval.collector import record_status, build_trial, collect_batch


class EvalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        self.config = validate_config({'base_url': 'http://test/api/v1', 'datasource_id': 8,
                                       'datasource_name': '校园', 'confirm_timeout_s': 0.01, 'poll_s': 0.005})
        self.manifest = {'batch_id': 'b1', 'config': self.config}
        self.cases = [{'question_id': q, 'question': q+'问题', 'expected': '预期', 'criteria': {}, 'source_row': i+2}
                      for i, q in enumerate(['S001', 'S002'])]
        write_json(self.batch / 'manifest.json', self.manifest)
        write_json(self.batch / 'cases.json', self.cases)
        (self.batch / 'rubric.txt').write_text('使用既有规则', encoding='utf8')
        save_runs(self.batch, [Run('S001'), Run('S002')])

    def test_real_workbook_explicit_ids(self):
        root = Path(__file__).resolve().parents[3]
        cases = load_cases(root/'项目学习文档/数据集和测试问题集/单任务问题表 (2).xlsx', ['S002', 'S003'])
        self.assertEqual([c.question_id for c in cases], ['S002', 'S003'])
        self.assertIn('加权', cases[0].question)
        with self.assertRaises(ValueError):
            load_cases(root/'项目学习文档/数据集和测试问题集/单任务问题表 (2).xlsx', ['S999'])

    def test_identifiers_and_config(self):
        for value in ['../bad', '/tmp', 'S001/x']:
            with self.assertRaises(ValueError): component(value)
        with self.assertRaises(ValueError): validate_config({**self.config, 'datasource_id': None})

    def test_backend_status_not_finish(self):
        self.assertEqual(record_status({'status': 'PROCESSING', 'finish': True}), 'running')
        self.assertEqual(record_status({'finish': True}), 'unknown')

    def test_prepare_freezes_inputs_and_refuses_overwrite(self):
        root = Path(__file__).resolve().parents[3]
        inputs = root/'项目学习文档/数据集和测试问题集'
        rubric = self.batch/'rubric.txt'
        target = self.batch/'new_batch'
        prepare_batch(self.config, inputs/'单任务问题表 (2).xlsx', ['S002','S003'], target, rubric, inputs/'Text2SQL评分表模板.xlsx')
        self.assertEqual([r.question_id for r in load_runs(target)], ['S002','S003'])
        rubric.write_text('changed', encoding='utf8')
        self.assertEqual((target/'rubric.txt').read_text(encoding='utf8'), '使用既有规则')
        self.assertTrue((target/'prompt_template.txt').exists())
        with self.assertRaises(FileExistsError):
            prepare_batch(self.config, inputs/'单任务问题表 (2).xlsx', ['S002'], target, rubric, inputs/'Text2SQL评分表模板.xlsx')

    def test_persistence_failure_stops_before_request(self):
        from tools.sqlbot_eval.storage import PersistenceError
        calls = []
        class Client:
            async def preflight(self): pass
            async def create_chat(self, question): calls.append(question); return 1
        with patch('tools.sqlbot_eval.runner.save_runs', side_effect=PersistenceError('磁盘满')):
            with self.assertRaises(PersistenceError):
                asyncio.run(run_batch(self.batch, Client(), object()))
        self.assertEqual(calls, [])

    def test_serial_failure_continue_and_resume_no_resend(self):
        calls = []
        class Client:
            async def preflight(self): pass
            async def create_chat(self, question):
                calls.append(('create', question)); return len(calls)
            async def ask(self, chat_id, request_id, question):
                calls.append(('ask', question))
                yield Event('id', {'id': chat_id+10})
                yield Event('finish', {})
        class Collector:
            def lookup(self, run):
                calls.append(('confirm', run.question_id))
                return Snapshot('failed' if run.question_id == 'S001' else 'success', {'id': run.record_id})
        result = asyncio.run(run_batch(self.batch, Client(), Collector()))
        self.assertIsNone(result['paused'])
        self.assertEqual([x[0] for x in calls], ['create','ask','confirm','create','ask','confirm'])
        asyncio.run(run_batch(self.batch, Client(), Collector()))
        self.assertEqual(len(calls), 6)
        self.assertEqual(rerun(self.batch, 'S001').run_id, 'run_002')

    def test_disconnect_unknown_pauses_and_never_resends(self):
        asks = []
        class Client:
            async def preflight(self): pass
            async def create_chat(self, question): return 1
            async def ask(self, *args):
                asks.append(args)
                yield Event('id', {'id': 26})
                raise RuntimeError('连接中断')
        class Collector:
            def lookup(self, run): return Snapshot('running', {'id': 26})
        for _ in range(2):
            self.assertTrue(asyncio.run(run_batch(self.batch, Client(), Collector()))['paused'])
        self.assertEqual(len(asks), 1)
        self.assertEqual(load_runs(self.batch)[1].last_stage, 'prepared')

    def test_total_deadline_and_confirmed_disconnect(self):
        class Client:
            async def preflight(self): pass
            async def create_chat(self, question): return 1
            async def ask(self, *args):
                yield Event('id', {'id': 26})
                await asyncio.sleep(10)
        class Collector:
            def lookup(self, run): return Snapshot('success', {'id': 26})
        self.config['timeout_s'] = 0.01
        write_json(self.batch/'manifest.json', self.manifest)
        self.assertIsNone(asyncio.run(run_batch(self.batch, Client(), Collector()))['paused'])
        self.assertTrue(all(r.execution_status == 'success' for r in load_runs(self.batch)))

    def trial_review(self):
        run = load_runs(self.batch)[0]
        run.chat_id, run.record_id = 3, 26
        trial = build_trial(Snapshot('success', {'id':26,'data': [],'sql':'select 1'}, []), run, 'b1')
        review = build_review(self.cases[0], trial, self.manifest, '规则')
        return run, trial, review

    def test_score_identity_ranges_evidence_and_totals(self):
        run, trial, review = self.trial_review()
        score = copy.deepcopy(review['score_contract'])
        for item in score['dimensions'].values():
            item.update(status='scored', score=100, reason='完成', evidence=['execution.result'])
        self.assertEqual(validate_score(score, review), score)
        for key, value in [('record_id', 27), ('input_sha256', 'stale')]:
            bad = copy.deepcopy(score); bad[key] = value
            with self.assertRaises(ValueError): validate_score(bad, review)
        for value in [True, float('nan'), 101]:
            bad = copy.deepcopy(score); bad['dimensions']['correctness']['score'] = value
            with self.assertRaises(ValueError): validate_score(bad, review)
        bad = copy.deepcopy(score); bad['dimensions']['correctness']['evidence'] = ['missing.field']
        with self.assertRaises(ValueError): validate_score(bad, review)
        weights = {d:1 for d in DIMENSIONS}
        score['dimensions']['error_handling'].update(status='not_applicable', score=None)
        self.assertIsNone(weighted_total(score['dimensions'], weights, 'pending'))
        self.assertEqual(weighted_total(score['dimensions'], weights, 'exclude'), 100)

    def test_offline_review_and_missing_score_export(self):
        run, trial, review = self.trial_review()
        write_json(artifact(self.batch, 'trials', run), trial)
        result = prepare_reviews(self.batch)
        self.assertEqual(result['generated'], ['S001/run_001'])
        self.assertIn('S002/run_001', result['errors'])
        self.assertEqual(len(export_plan(self.batch)['rows']), 2)

    def test_collection_failure_does_not_overwrite(self):
        runs = load_runs(self.batch)
        runs[0].chat_id, runs[0].record_id, runs[0].last_stage = 3, 26, 'submitted'
        save_runs(self.batch, runs)
        target = artifact(self.batch, 'trials', runs[0]); write_json(target, {'old': True})
        class Collector:
            def lookup(self, *args, **kwargs): raise RuntimeError('数据库不可访问')
        collect_batch(self.batch, Collector())
        self.assertEqual(read_json(target), {'old':True})


class HTTPTest(unittest.IsolatedAsyncioTestCase):
    async def test_wrapped_api_responses(self):
        import httpx
        from tools.sqlbot_eval.sqlbot_client import SQLBotClient, ProtocolError, unwrap_response
        config = {'base_url':'http://test/api/v1', 'timeout_s':1, 'connect_timeout_s':1,
                  'datasource_id':8, 'datasource_name':'校园'}
        def handler(request):
            if request.url.path.endswith('/datasource/list'):
                data = [{'id':8, 'name':'校园'}]
            elif request.url.path.endswith('/chat/start'):
                data = {'id':3}
            else:
                data = {'replay':True,'chat_id':3,'request_id':'r','record_id':26,'status':'SUCCESS'}
            return httpx.Response(200, json={'code':0,'data':data,'msg':None})
        client = SQLBotClient(config, 'secret', httpx.MockTransport(handler))
        try:
            await client.preflight()
            self.assertEqual(await client.create_chat('问题'), 3)
            self.assertEqual([e.kind async for e in client.ask(3,'r','问题')], ['id','finish'])
            with self.assertRaises(ProtocolError):
                unwrap_response({'code':1,'data':None,'msg':'error'})
        finally:
            await client.close()

    async def test_sse_fragments_and_request(self):
        import httpx
        from tools.sqlbot_eval.sqlbot_client import SQLBotClient, ProtocolError
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for fragment in [b'data: {"type":"id",', b'"id":26}\n\n', b'data: {"type":"finish"}\n\n']:
                    yield fragment
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=Stream())
        config = {'base_url':'http://test/api/v1','timeout_s':1,'connect_timeout_s':1}
        client = SQLBotClient(config, 'secret', httpx.MockTransport(handler))
        try:
            events = [event async for event in client.ask(3, 'req1', '问题')]
            self.assertEqual([e.kind for e in events], ['id','finish'])
            self.assertEqual(json.loads(requests[0].content)['request_id'], 'req1')
            self.assertEqual(requests[0].headers['X-SQLBOT-TOKEN'], 'Bearer secret')
        finally: await client.close()

    async def test_json_replay_and_incomplete(self):
        import httpx
        from tools.sqlbot_eval.sqlbot_client import SQLBotClient, ProtocolError, HTTPFailure
        config = {'base_url':'http://test/api/v1','timeout_s':1,'connect_timeout_s':1}
        for response, error in [(httpx.Response(200, json={'replay':True,'chat_id':3,'request_id':'r','record_id':26,'status':'SUCCESS'}), None),
                                (httpx.Response(200, headers={'content-type':'text/event-stream'}, text='data: {"type":"id","id":26}\n\n'), ProtocolError),
                                (httpx.Response(401), HTTPFailure)]:
            client = SQLBotClient(config, 'secret', httpx.MockTransport(lambda request: response))
            try:
                if error:
                    with self.assertRaises(error):
                        [e async for e in client.ask(3,'r','问题')]
                else:
                    self.assertEqual(len([e async for e in client.ask(3,'r','问题')]), 2)
            finally: await client.close()


if __name__ == '__main__':
    unittest.main()
