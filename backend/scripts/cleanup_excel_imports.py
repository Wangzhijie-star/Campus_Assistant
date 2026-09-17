"""Run from backend: python scripts/cleanup_excel_imports.py [--days 7] [--apply]."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.datasource.utils.excel_import import cleanup_conversions
from apps.db.engine import get_engine_conn
from common.core.config import settings


def main():
    parser = argparse.ArgumentParser(description='清理过期且从未入库的 Excel 转换文件；默认只预览')
    parser.add_argument('--days', type=int, default=7)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    engine = get_engine_conn()
    try:
        identifiers = cleanup_conversions(settings.EXCEL_PATH, engine, args.days, args.apply)
        print({'applied': args.apply, 'count': len(identifiers), 'identifiers': identifiers})
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
