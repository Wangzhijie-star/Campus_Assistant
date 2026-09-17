import importlib.util
from pathlib import Path
import unittest

# Load this pure helper without importing the optional pandas Excel runtime.
spec = importlib.util.spec_from_file_location(
    'identifiers', Path(__file__).resolve().parents[1] / 'apps/datasource/utils/identifiers.py'
)
identifiers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identifiers)
postgres_column_names = identifiers.postgres_column_names


class PostgresColumnNamesTest(unittest.TestCase):
    def test_reported_chinese_headers(self):
        prefix = '能力素质评价A_社会工作A1 任职1*考核系数+任职2*考核系数*0.2 满分10分'
        columns = [prefix + '_A1', prefix + '_加减分项目']
        names = postgres_column_names(columns)
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(all(len(name.encode('utf-8')) <= 63 for name in names))
        self.assertEqual(names, postgres_column_names(columns))

    def test_short_names_and_byte_boundary(self):
        columns = ['学号', '学生姓名', 'a' * 63, '中' * 21]
        self.assertEqual(postgres_column_names(columns), columns)
        self.assertLessEqual(len(postgres_column_names(['中' * 22])[0].encode('utf-8')), 63)

    def test_generated_name_does_not_replace_existing_name(self):
        long_name = '测试' * 40
        generated = postgres_column_names([long_name])[0]
        names = postgres_column_names([long_name, generated, long_name])
        self.assertEqual(names[1], generated)
        self.assertEqual(len(set(names)), 3)
        self.assertTrue(all(len(name.encode('utf-8')) <= 63 for name in names))

    def test_duplicate_short_names(self):
        names = postgres_column_names(['name', 'name', 'name'])
        self.assertEqual(names[0], 'name')
        self.assertEqual(len(set(names)), 3)


if __name__ == '__main__':
    unittest.main()
