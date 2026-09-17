"""Endpoint contract tests; application permission decorators are tested separately."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('EXCEL_TEST_POSTGRES') == '1', 'Requires the full application runtime')
class ExcelApiTest(unittest.TestCase):
    def setUp(self):
        import sqlbot_xpack  # Match main.py's initialization order for extension imports.
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from apps.datasource.api import datasource as api
        from common.core.deps import get_current_user, get_session
        import openpyxl
        self.api = api
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patcher = patch.object(api, 'path', self.temp.name)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, oid=1)
        app.dependency_overrides[get_session] = lambda: None
        for name, endpoint in [('parseExcel', api.parse_excel), ('convertExcel', api.convert_excel),
                               ('importToDb', api.import_to_db)]:
            app.add_api_route('/' + name, endpoint.__wrapped__, methods=['POST'])
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        source = Path(self.temp.name) / 'source.xlsx'
        book = openpyxl.Workbook()
        book.active.append(['姓名', '政治素养B11'])
        book.active.append(['学生', '分值'])
        book.active.append(['甲', 3])
        book.save(source)
        self.contents = source.read_bytes()

    def test_multi_upload_convert_and_tampered_id(self):
        response = self.client.post('/parseExcel', data={'multiHeader': 'true'},
                                    files={'file': ('test.xlsx', self.contents)})
        self.assertEqual(response.status_code, 200, response.text)
        upload = response.json()
        self.assertTrue(upload['needsConfiguration'])
        payload = {'uploadId': upload['uploadId'], 'sheets': [{'sheetName': 'Sheet', 'headerMode': 'multi',
                   'headerStartRow': 1, 'headerEndRow': 2, 'dataStartRow': 3}]}
        response = self.client.post('/convertExcel', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data'][0]['fields'][1]['fieldName'], 'B11_score')
        response = self.client.post('/importToDb', json={'conversionId': '../unknown', 'sheets': []})
        self.assertEqual(response.status_code, 400)

    def test_ordinary_upload_contract_and_invalid_parameters(self):
        response = self.client.post('/parseExcel', files={'file': ('test.csv', b'id,value\n001,3\n')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('filePath', response.json())
        self.assertIn('conversionId', response.json())
        self.assertEqual(response.json()['data'][0]['data'][0]['id'], '001')
        response = self.client.post('/convertExcel', json={'uploadId': 'bad', 'sheets': [
            {'sheetName': 'Sheet', 'headerMode': 'auto'}]})
        self.assertEqual(response.status_code, 422)


if __name__ == '__main__':
    unittest.main()
