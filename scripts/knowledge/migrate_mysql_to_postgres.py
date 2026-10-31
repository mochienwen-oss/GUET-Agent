"""Copy local business rows to an empty PostgreSQL database and compare values.

Run only after all application writers have stopped. Source data is never modified.
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
if (ROOT / 'run/python-pg-deps').is_dir():
    sys.path.insert(0, str(ROOT / 'run/python-pg-deps'))
sys.path.insert(0, str(ROOT / 'backend'))
from dotenv import load_dotenv
load_dotenv(ROOT / 'run/postgres.local.env', override=False)
from core.config import settings
from database.db import Base, User, Session, Conversation, Message, Document, SystemSetting


TABLES = (User.__table__, Session.__table__, Conversation.__table__,
          Message.__table__, Document.__table__, SystemSetting.__table__)


def main():
    parser = argparse.ArgumentParser(description='复制并核对本地业务数据；必须先停止所有写入')
    parser.add_argument('--source-stopped', action='store_true', help='确认所有源数据库应用写入已停止')
    args = parser.parse_args()
    if not args.source_stopped:
        parser.error('请先停止所有源数据库应用写入，再指定 --source-stopped')
    source_url = make_url(settings.mysql_url)
    target_url = make_url(settings.database_url)
    if (source_url.get_backend_name() != 'mysql' or target_url.get_backend_name() != 'postgresql'
            or source_url.host not in ('127.0.0.1', 'localhost', '::1')
            or target_url.host not in ('127.0.0.1', 'localhost', '::1')):
        raise SystemExit('仅支持从本机 MySQL 导入本机 PostgreSQL。')
    source = create_engine(source_url, isolation_level='REPEATABLE READ')
    target = create_engine(target_url)
    try:
        with target.connect() as check:
            if check.exec_driver_sql('SHOW server_encoding').scalar().upper() != 'UTF8':
                raise ValueError('目标数据库必须使用 UTF8 编码')
        Base.metadata.create_all(target)
        with source.connect() as old, target.begin() as new:
            old.exec_driver_sql('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            for table in TABLES:
                if new.execute(select(func.count()).select_from(table)).scalar_one():
                    raise ValueError('目标业务表非空，未复制任何数据')
            counts = {}
            for table in TABLES:
                key = list(table.primary_key.columns)
                statement = select(table).order_by(*key)
                cursor = old.execution_options(stream_results=True).execute(statement).mappings()
                copied = 0
                while rows := cursor.fetchmany(250):
                    new.execute(table.insert(), [dict(row) for row in rows])
                    copied += len(rows)
                counts[table.name] = copied
                # Exact values include Unicode text, JSON, identity and timestamps.
                expected = old.execution_options(stream_results=True).execute(statement).mappings()
                for row in expected:
                    condition = [column == row[column.name] for column in key]
                    actual = new.execute(select(table).where(*condition)).mappings().one_or_none()
                    if actual is None or dict(row) != dict(actual):
                        raise ValueError(f'{table.name} 原值核对失败，目标事务已回滚')
                if new.execute(select(func.count()).select_from(table)).scalar_one() != copied:
                    raise ValueError(f'{table.name} 条数核对失败，目标事务已回滚')
                if 'id' in table.c and table.c.id.primary_key:
                    new.execute(text('SELECT setval(pg_get_serial_sequence(:table_name, :column_name), '
                                     'COALESCE((SELECT MAX(id) FROM ' + table.name + '), 1), '
                                     '(SELECT MAX(id) IS NOT NULL FROM ' + table.name + '))'),
                                {'table_name': table.name, 'column_name': 'id'})
            old.rollback()
        print('业务数据复制并逐字段核对完成：' + ', '.join(f'{key}={value}' for key, value in counts.items()))
    finally:
        source.dispose()
        target.dispose()


if __name__ == '__main__':
    main()
