"""Copy local Qdrant points to an empty PostgreSQL collection and compare values."""
import argparse
import json
import struct
import sys
from pathlib import Path

from qdrant_client import QdrantClient, models
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
if (ROOT / 'run/python-pg-deps').is_dir():
    sys.path.insert(0, str(ROOT / 'run/python-pg-deps'))
sys.path.insert(0, str(ROOT / 'backend'))
from dotenv import load_dotenv
load_dotenv(ROOT / 'run/postgres.local.env', override=False)
from core.config import settings
from agent.tools.search_regulations.scripts.index_ops.postgres_index import PostgresIndex


def floats32(values):
    return [struct.pack('f', value) for value in values]


def main():
    parser = argparse.ArgumentParser(description='迁移本地向量和 BM25 权重；必须先停止所有源库写入')
    parser.add_argument('--source-stopped', action='store_true', help='确认源知识库所有写入已停止')
    args = parser.parse_args()
    if not args.source_stopped:
        parser.error('请先停止所有源知识库写入，再指定 --source-stopped')
    target_url = make_url(settings.database_url)
    if (target_url.get_backend_name() != 'postgresql'
            or target_url.host not in ('127.0.0.1', 'localhost', '::1')
            or not settings.qdrant_path):
        raise SystemExit('仅支持本地 Qdrant 目录和本机 PostgreSQL。')
    source = QdrantClient(path=settings.qdrant_path, force_disable_check_same_thread=True)
    engine = create_engine(target_url)
    index = PostgresIndex(engine)
    collection = settings.qdrant_collection
    try:
        info = source.get_collection(collection)
        dense_config = info.config.params.vectors['dense']
        if dense_config.distance != models.Distance.COSINE:
            raise ValueError('源集合的向量距离不是余弦，不能直接迁移')
        dimension = dense_config.size
        if not index.collection_exists(collection):
            index.create_collection(collection, {'dense': models.VectorParams(
                size=dimension, distance=models.Distance.COSINE)}, {'sparse': models.SparseVectorParams()})
        if index.count(collection).count:
            raise ValueError('目标向量集合非空，未复制任何数据')
        copied = 0
        offset = None
        with engine.begin() as conn:
            while True:
                points, offset = source.scroll(collection_name=collection, offset=offset, limit=128,
                                               with_payload=True, with_vectors=True)
                if not points:
                    break
                if any(not isinstance(point.id, int) or not isinstance(point.vector, dict)
                       or 'dense' not in point.vector for point in points):
                    raise ValueError('源向量身份或稠密向量不完整')
                index.write_points(conn, collection, points)
                ids = [point.id for point in points]
                rows = conn.execute(text('SELECT point_id, payload, CAST(embedding AS text) AS embedding '
                    'FROM knowledge_chunks WHERE collection=:collection AND point_id = ANY(:ids)'),
                    {'collection': collection, 'ids': ids}).mappings().all()
                stored = {row['point_id']: row for row in rows}
                terms = conn.execute(text('SELECT point_id, term_id, weight FROM knowledge_bm25 '
                    'WHERE collection=:collection AND point_id = ANY(:ids)'),
                    {'collection': collection, 'ids': ids}).mappings().all()
                sparse = {}
                for row in terms:
                    sparse.setdefault(row['point_id'], {})[row['term_id']] = row['weight']
                for point in points:
                    row = stored.get(point.id)
                    source_sparse = point.vector.get('sparse')
                    expected = dict(zip(source_sparse.indices, source_sparse.values)) if source_sparse else {}
                    if (not row or row['payload'] != (point.payload or {})
                            or floats32(json.loads(row['embedding'])) != floats32(point.vector['dense'])
                            or sparse.get(point.id, {}) != expected):
                        raise ValueError('向量、BM25 权重或原始字段核对失败，目标事务已回滚')
                copied += len(points)
                if offset is None:
                    break
            if copied != info.points_count:
                raise ValueError('源向量数量变化或未完整复制，目标事务已回滚')
            documents = dict(conn.execute(text('SELECT id, chunk_count FROM documents')).all())
            indexed = dict(conn.execute(text("SELECT CAST(payload->>'document_id' AS integer), COUNT(*) "
                'FROM knowledge_chunks WHERE collection=:collection GROUP BY 1'),
                {'collection': collection}).all())
            if documents != indexed:
                raise ValueError('文档身份或分块数量不匹配，目标事务已回滚')
        print(f'向量、BM25 和来源字段复制核对完成：{copied} 条')
    finally:
        source.close()
        engine.dispose()


if __name__ == '__main__':
    main()
