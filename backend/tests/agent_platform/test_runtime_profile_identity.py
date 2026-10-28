"""API startup checks knowledge identity without local model paths."""
import json
from types import SimpleNamespace
import pytest
from application import runtime_identity
from core.config import settings

@pytest.mark.parametrize('collection', ['current', 'wrong'])
def test_startup_keeps_knowledge_identity_without_model_probe(tmp_path, monkeypatch, api_config, collection):
    from database import db
    from documents import document_availability
    from agent.tools.search_regulations.scripts import bm25
    import qdrant_client
    import httpx
    api_config()
    profile = tmp_path / 'runtime.json'
    profile.write_text(json.dumps({'collection': 'current'}))
    active = tmp_path / 'active.json'
    active.write_text(json.dumps({'documents': [{'document_id': 1, 'title': '材料', 'chunks': 1}]}), encoding='utf-8')
    vocab = tmp_path / 'vocab.json'; vocab.write_text('{}')
    prompt = tmp_path / 'prompt.txt'; prompt.write_text('prompt')
    monkeypatch.setenv('SAI_RUNTIME_PROFILE', str(profile))
    for key, value in {'qdrant_collection': collection, 'bm25_vocab_path': str(vocab),
                       'active_documents_path': str(active), 'system_prompt_path': str(prompt)}.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(bm25, 'load_vocab', lambda: {'terms': ['材料']})
    monkeypatch.setattr(document_availability, 'snapshot', lambda: {'status': 'ready'})
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def query(self, *args): return SimpleNamespace(all=lambda: [SimpleNamespace(id=1, title='材料', chunk_count=1)])
    monkeypatch.setattr(db, 'SessionLocal', Session)
    monkeypatch.setattr(qdrant_client, 'QdrantClient', lambda **kwargs: SimpleNamespace(get_collection=lambda _: SimpleNamespace(points_count=1)))
    def forbidden(*args, **kwargs): raise AssertionError('Startup must not contact a generation model')
    monkeypatch.setattr(httpx.Client, 'post', forbidden)
    monkeypatch.setattr(httpx.AsyncClient, 'post', forbidden)
    if collection == 'current': runtime_identity.verify_runtime()
    else:
        with pytest.raises(RuntimeError, match='正式运行配置不一致'):
            runtime_identity.verify_runtime()
