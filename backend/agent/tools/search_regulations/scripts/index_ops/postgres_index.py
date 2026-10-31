"""PostgreSQL storage for existing dense vectors, BM25 weights and source payloads."""
import json
from types import SimpleNamespace

from sqlalchemy import text


class PostgresIndex:
    """Implement only the index operations used by the existing retrieval pipeline."""

    def __init__(self, engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('pgvector 需要 PostgreSQL 业务数据库')
        self.engine = engine

    @staticmethod
    def _conditions(filter_, alias='p'):
        if filter_ is None:
            return '', {}
        if filter_.should or filter_.must_not or filter_.min_should:
            raise ValueError('当前索引不支持该过滤条件')
        constraints = {}
        for condition in filter_.must or []:
            if not getattr(condition, 'key', None) or getattr(condition, 'match', None) is None:
                raise ValueError('当前索引仅支持字段等值过滤')
            match = condition.match
            if not hasattr(match, 'value'):
                raise ValueError('当前索引仅支持字段等值过滤')
            constraints[condition.key] = match.value
        if not constraints:
            return '', {}
        return f' AND {alias}.payload @> CAST(:payload_filter AS jsonb)', {
            'payload_filter': json.dumps(constraints, ensure_ascii=False)}

    def collection_exists(self, collection_name):
        with self.engine.connect() as conn:
            exists = conn.execute(text("SELECT to_regclass('public.knowledge_collections')")).scalar()
            return bool(exists and conn.execute(text(
                'SELECT 1 FROM knowledge_collections WHERE name=:name'), {'name': collection_name}).scalar())

    def create_collection(self, collection_name, vectors_config, sparse_vectors_config):
        dimension = vectors_config['dense'].size
        if not isinstance(dimension, int) or not 1 <= dimension <= 2000:
            raise ValueError('向量维度不受支持')
        # Current embedding uses one shared dimension. Refuse a second incompatible collection.
        with self.engine.begin() as conn:
            conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS vector')
            conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS knowledge_collections '
                                 '(name TEXT PRIMARY KEY, dimensions INTEGER NOT NULL)')
            existing = conn.execute(text('SELECT DISTINCT dimensions FROM knowledge_collections')).scalars().all()
            if existing and existing != [dimension]:
                raise ValueError('已有知识集合的向量维度不同')
            conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS knowledge_chunks ('
                'collection TEXT NOT NULL REFERENCES knowledge_collections(name) ON DELETE CASCADE, '
                'point_id BIGINT NOT NULL, payload JSONB NOT NULL, '
                f'embedding vector({dimension}) NOT NULL, PRIMARY KEY (collection, point_id))')
            conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS knowledge_bm25 ('
                'collection TEXT NOT NULL, point_id BIGINT NOT NULL, term_id INTEGER NOT NULL, '
                'weight DOUBLE PRECISION NOT NULL, PRIMARY KEY (collection, point_id, term_id), '
                'FOREIGN KEY (collection, point_id) REFERENCES knowledge_chunks(collection, point_id) ON DELETE CASCADE)')
            conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS knowledge_bm25_term_idx '
                                 'ON knowledge_bm25(collection, term_id)')
            conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS knowledge_chunks_payload_idx '
                                 'ON knowledge_chunks USING GIN(payload jsonb_path_ops)')
            conn.execute(text('INSERT INTO knowledge_collections(name, dimensions) VALUES (:name, :dim) '
                              'ON CONFLICT (name) DO NOTHING'), {'name': collection_name, 'dim': dimension})

    @staticmethod
    def _sparse(conn, collection, point_id, vector):
        conn.execute(text('DELETE FROM knowledge_bm25 WHERE collection=:collection AND point_id=:id'),
                     {'collection': collection, 'id': point_id})
        if vector is not None:
            rows = [{'collection': collection, 'id': point_id, 'term': int(term), 'weight': float(weight)}
                    for term, weight in zip(vector.indices, vector.values)]
            if rows:
                conn.execute(text('INSERT INTO knowledge_bm25(collection, point_id, term_id, weight) '
                                  'VALUES (:collection, :id, :term, :weight)'), rows)

    def upsert(self, collection_name, points, wait=True):
        with self.engine.begin() as conn:
            self.write_points(conn, collection_name, points)

    def write_points(self, conn, collection_name, points):
        for point in points:
            vectors = point.vector
            dense = vectors['dense']
            conn.execute(text('INSERT INTO knowledge_chunks(collection, point_id, payload, embedding) '
                'VALUES (:collection, :id, CAST(:payload AS jsonb), CAST(:embedding AS vector)) '
                'ON CONFLICT (collection, point_id) DO UPDATE '
                'SET payload=EXCLUDED.payload, embedding=EXCLUDED.embedding'),
                {'collection': collection_name, 'id': point.id,
                 'payload': json.dumps(point.payload or {}, ensure_ascii=False),
                 'embedding': '[' + ','.join(str(float(x)) for x in dense) + ']'})
            self._sparse(conn, collection_name, point.id, vectors.get('sparse'))

    def update_vectors(self, collection_name, points, wait=True):
        with self.engine.begin() as conn:
            for point in points:
                if set(point.vector) != {'sparse'}:
                    raise ValueError('仅支持更新 BM25 稀疏向量')
                self._sparse(conn, collection_name, point.id, point.vector['sparse'])

    def delete(self, collection_name, points_selector, wait=True):
        where, params = self._conditions(points_selector.filter)
        if not where:
            raise ValueError('删除文档必须指定过滤条件')
        params['collection'] = collection_name
        with self.engine.begin() as conn:
            conn.execute(text('DELETE FROM knowledge_chunks AS p WHERE p.collection=:collection' + where), params)

    def count(self, collection_name, exact=True, count_filter=None):
        where, params = self._conditions(count_filter)
        params['collection'] = collection_name
        with self.engine.connect() as conn:
            count = conn.execute(text('SELECT COUNT(*) FROM knowledge_chunks AS p '
                                      'WHERE p.collection=:collection' + where), params).scalar_one()
        return SimpleNamespace(count=count)

    def get_collection(self, collection_name):
        return SimpleNamespace(points_count=self.count(collection_name).count)

    def scroll(self, collection_name, scroll_filter=None, limit=10, offset=None,
               with_payload=True, with_vectors=False):
        if with_vectors:
            raise ValueError('此接口不导出向量')
        where, params = self._conditions(scroll_filter)
        params.update(collection=collection_name, offset=offset or 0, limit=limit + 1)
        with self.engine.connect() as conn:
            rows = conn.execute(text('SELECT p.point_id, p.payload FROM knowledge_chunks AS p '
                'WHERE p.collection=:collection AND p.point_id>=:offset' + where +
                ' ORDER BY p.point_id LIMIT :limit'), params).mappings().all()
        next_offset = rows[limit]['point_id'] if len(rows) > limit else None
        return [SimpleNamespace(id=row['point_id'], payload=row['payload']) for row in rows[:limit]], next_offset

    def query_points(self, collection_name, query, using, limit, with_payload=True, query_filter=None):
        where, params = self._conditions(query_filter)
        params.update(collection=collection_name, limit=limit)
        if using == 'dense':
            params['embedding'] = '[' + ','.join(str(float(x)) for x in query) + ']'
            statement = ('SELECT p.point_id, p.payload, 1 - (p.embedding <=> CAST(:embedding AS vector)) AS score '
                         'FROM knowledge_chunks AS p WHERE p.collection=:collection' + where +
                         ' ORDER BY p.embedding <=> CAST(:embedding AS vector), p.point_id LIMIT :limit')
        elif using == 'sparse':
            params.update(term_ids=list(query.indices), weights=list(query.values))
            statement = ('SELECT p.point_id, p.payload, SUM(b.weight*q.weight) AS score '
                         'FROM unnest(CAST(:term_ids AS integer[]), CAST(:weights AS double precision[])) '
                         'AS q(term_id, weight) JOIN knowledge_bm25 b ON b.term_id=q.term_id '
                         'JOIN knowledge_chunks p ON p.collection=b.collection AND p.point_id=b.point_id '
                         'WHERE p.collection=:collection' + where +
                         ' GROUP BY p.point_id, p.payload ORDER BY score DESC, p.point_id LIMIT :limit')
        else:
            raise ValueError('未知的向量类型')
        with self.engine.connect() as conn:
            rows = conn.execute(text(statement), params).mappings().all()
        return SimpleNamespace(points=[SimpleNamespace(id=row['point_id'], payload=row['payload'],
                                                       score=float(row['score'])) for row in rows])
