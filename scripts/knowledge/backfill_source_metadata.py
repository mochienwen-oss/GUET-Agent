"""Fill local document display metadata only; no indexing or application startup."""
import json
import os
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import pymysql
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from documents.catalog.source_metadata import document_catalog, extract_metadata


def main():
    config = dotenv_values(ROOT / '.env')
    url = urlsplit(os.environ.get('MYSQL_URL') or config.get('MYSQL_URL') or '')
    if url.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise SystemExit('只允许更新本地知识库的文档元数据。')
    catalog = document_catalog(os.environ.get('ACTIVE_DOCUMENTS_PATH') or
                               config.get('ACTIVE_DOCUMENTS_PATH') or
                               ROOT / 'backend/data/documents.active.json')
    connection = pymysql.connect(
        host=url.hostname, port=url.port or 3306, user=unquote(url.username or ''),
        password=unquote(url.password or ''), database=unquote(url.path.lstrip('/')),
        charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
    )
    updated = skipped = 0
    counts = Counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id, title, content FROM documents WHERE status=%s', ('ready',))
            documents = cursor.fetchall()
        connection.commit()
        for document in documents:
            content = document['content'] or ''
            if not content.strip():
                skipped += 1
                continue
            entry = catalog.get(str(document['id']), {})
            filename = PurePosixPath(entry.get('source_path', '').replace('\\', '/')).name
            extraction = extract_metadata(document['title'], content,
                                          source_filename=filename, scope=entry.get('scope'))
            with connection.cursor() as cursor:
                cursor.execute('SELECT title, content, status, source_metadata FROM documents WHERE id=%s FOR UPDATE',
                               (document['id'],))
                current = cursor.fetchone()
                if (not current or current['status'] != 'ready' or
                        current['title'] != document['title'] or current['content'] != document['content']):
                    connection.rollback()
                    skipped += 1
                    continue
                stored = current['source_metadata']
                stored = json.loads(stored) if isinstance(stored, str) else stored or {}
                if not filename:
                    # Retain the upload filename when this document has no catalog path.
                    filename = (stored.get('extracted') or {}).get('source_filename', '')
                    if filename:
                        extraction = extract_metadata(document['title'], content,
                                                      source_filename=filename, scope=entry.get('scope'))
                stored = dict(stored, extracted=extraction)
                cursor.execute('UPDATE documents SET source_metadata=%s WHERE id=%s',
                               (json.dumps(stored, ensure_ascii=False), document['id']))
            connection.commit()
            updated += 1
            counts.update(item['key'] for item in extraction['items'])
        print(json.dumps({'updated': updated, 'skipped': skipped, 'fields': dict(counts)}, ensure_ascii=False))
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == '__main__':
    main()
