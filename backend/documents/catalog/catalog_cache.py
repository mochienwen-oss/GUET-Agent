"""Optional cache for the public active-document catalog."""
import json
import random
from threading import Lock

from core.config import settings

_lock = Lock()
_client = None


def _redis():
    global _client
    if not settings.redis_url:
        return None
    if _client is None:
        import redis
        _client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.3,
                                       socket_timeout=0.3, decode_responses=True)
    return _client


def read_catalog(path):
    # The catalog is small and local; Redis failure falls back to this file, not PostgreSQL.
    stat = path.stat()
    key = f'guet:catalog:{path}:{stat.st_mtime_ns}:{stat.st_size}'
    try:
        client = _redis()
        if client is None:
            return json.loads(path.read_text(encoding='utf-8-sig'))
        cached = client.get(key)
        if cached is not None:
            return json.loads(cached)
    except Exception:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    # One backend process is the supported configuration for InMemorySaver.
    with _lock:
        try:
            cached = client.get(key)
            if cached is not None:
                return json.loads(cached)
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            client.set(key, json.dumps(data, ensure_ascii=False), ex=300 + random.randint(0, 60))
            return data
        except Exception:
            return json.loads(path.read_text(encoding='utf-8-sig'))
