"""Opt-in integration tests. All writes are isolated in a disposable schema."""
from concurrent.futures import ThreadPoolExecutor
import os
import unittest
import uuid

import test_excel_import
from apps.datasource.utils.excel_import import ConversionStore, import_conversion


@unittest.skipUnless(os.environ.get('EXCEL_TEST_POSTGRES') == '1', 'Set EXCEL_TEST_POSTGRES=1 for isolated PostgreSQL tests')
class ExcelPostgresTest(unittest.TestCase):
    def setUp(self):
        test_excel_import.ExcelImportTest.setUp(self)
        from apps.db.engine import get_engine_conn
        from sqlalchemy import create_engine, text
        self.admin = get_engine_conn()
        self.schema = 'test_excel_' + uuid.uuid4().hex
        with self.admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(self.admin.url, connect_args={'options': f'-c search_path={self.schema}'})
        self.addCleanup(self.cleanup_database)
        store = ConversionStore(self.root, 1, 1)
        upload_id, _ = store.upload('input.xlsx', self.source.read_bytes())
        self.conversion_id, self.conversion = store.convert(upload_id, [self.option, {'sheetName': '普通'}])
        self.selections = [{'sheetName': s['sheetName'], 'fields': s['fields']} for s in self.conversion['sheets']]

    def cleanup_database(self):
        from sqlalchemy import text
        self.engine.dispose()
        # This identifier is generated locally above, never taken from user input.
        with self.admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin.dispose()

    def run_import(self):
        return import_conversion(self.engine, self.conversion_id, self.conversion, self.selections)

    def test_real_database_comments_rows_and_concurrent_retry(self):
        from sqlalchemy import text
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(lambda _: self.run_import(), range(3)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        table = results[0]['sheets'][0]['tableName']
        with self.engine.connect() as conn:
            rows = conn.execute(text(f'SELECT "学号", "B11_score", "B11_items" FROM "{table}" ORDER BY "学号"')).all()
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0], ('001', 0, '含\t制表符\n换行'))
            self.assertEqual(rows[2], ('003', None, '=literal'))
            comments = conn.execute(text('''SELECT a.attname, col_description(c.oid, a.attnum)
                FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=:schema AND c.relname=:table AND a.attnum>0 ORDER BY a.attnum'''),
                {'schema': self.schema, 'table': table}).all()
            self.assertEqual(comments[1][1], self.conversion['sheets'][0]['fields'][1]['meaning'])

    def test_multiple_sheet_failure_rolls_back_all_tables(self):
        from sqlalchemy import text
        self.conversion['sheets'][1]['values'][0][0] = 'invalid\x00value'
        with self.assertRaises(Exception):
            self.run_import()
        with self.engine.connect() as conn:
            count = conn.execute(text('SELECT count(*) FROM pg_tables WHERE schemaname=:schema'),
                                 {'schema': self.schema}).scalar_one()
            self.assertEqual(count, 0)

    def test_cleanup_preserves_imported_and_recent_results(self):
        from apps.datasource.utils.excel_import import cleanup_conversions
        store = ConversionStore(self.root, 1, 1)
        old_date = '2000-01-01T00:00:00+00:00'
        for path in store.root.glob('*.json'):
            import json
            data = json.loads(path.read_text())
            data['createdAt'] = old_date
            store.write(path.stem, data)
        self.assertIn(self.conversion_id, cleanup_conversions(self.root, self.engine))
        self.assertTrue(store._path(self.conversion_id).exists())
        self.run_import()
        self.assertEqual(cleanup_conversions(self.root, self.engine, apply=True), [])
        new_upload, _ = store.upload('another.xlsx', self.source.read_bytes())
        new_id, _ = store.convert(new_upload, [self.option])
        self.assertEqual(cleanup_conversions(self.root, self.engine, apply=True), [])
        for identifier, kind in [(new_upload, 'upload'), (new_id, 'conversion')]:
            data = store.load(identifier, kind)
            data['createdAt'] = old_date
            store.write(identifier, data)
        removed = cleanup_conversions(self.root, self.engine, apply=True)
        self.assertEqual(set(removed), {new_id, new_upload})
        self.assertTrue(store._path(self.conversion_id).exists())

    def test_metadata_sync_and_embedding_text(self):
        import sqlbot_xpack
        from sqlalchemy.orm import scoped_session, sessionmaker
        from sqlmodel import Session
        from unittest.mock import patch
        from apps.datasource.models.datasource import CoreDatasource, CoreTable, CoreField, ColumnSchema
        from apps.datasource.crud.datasource import sync_fields
        from apps.datasource.crud import table as table_crud
        from sqlalchemy import text
        for model in (CoreDatasource, CoreTable, CoreField):
            model.__table__.create(self.engine)
        result = self.run_import()
        table_name = result['sheets'][0]['tableName']
        with Session(self.engine) as session:
            ds = CoreDatasource(name='测试综测', description='学生测评', type='excel', oid=1)
            session.add(ds)
            session.commit()
            session.refresh(ds)
            table = CoreTable(ds_id=ds.id, table_name=table_name, custom_comment='综测')
            session.add(table)
            session.commit()
            session.refresh(table)
            fields = session.execute(text('''SELECT a.attname, format_type(a.atttypid,a.atttypmod),
                col_description(c.oid,a.attnum) FROM pg_class c
                JOIN pg_attribute a ON a.attrelid=c.oid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=:schema AND c.relname=:table AND a.attnum>0 ORDER BY a.attnum'''),
                {'schema': self.schema, 'table': table_name}).all()
            sync_fields(session, ds, table, [ColumnSchema(*row) for row in fields])
            ds_id, table_id = ds.id, table.id
        captured = []
        class Model:
            def embed_query(self, text):
                captured.append(text)
                return [0.1, 0.2]
        factory = scoped_session(sessionmaker(bind=self.engine))
        with patch.object(table_crud.settings, 'TABLE_EMBEDDING_ENABLED', True), \
                patch.object(table_crud.EmbeddingModelCache, 'get_model', return_value=Model()):
            table_crud.save_table_embedding(factory, [table_id])
            table_crud.save_ds_embedding(factory, [ds_id])
        self.assertEqual(len(captured), 2)
        for schema in captured:
            self.assertIn('B11_score', schema)
            self.assertIn(self.conversion['sheets'][0]['fields'][1]['meaning'], schema)
        with Session(self.engine) as session:
            field = session.query(CoreField).filter(CoreField.field_name == 'B11_score').one()
            field.custom_comment = '人工补充的说明'
            session.commit()
            ds, table = session.get(CoreDatasource, ds_id), session.get(CoreTable, table_id)
            sync_fields(session, ds, table, [ColumnSchema(*row) for row in fields])
            session.refresh(field)
            self.assertEqual(field.custom_comment, '人工补充的说明')


if __name__ == '__main__':
    unittest.main()
