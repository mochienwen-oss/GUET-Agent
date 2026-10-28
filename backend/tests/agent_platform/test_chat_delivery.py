"""Rendered citations must survive HTTP delivery and message persistence."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import db as dbmod
from routers import chat
from agent.runtime import grounded_answer as G
from agent.runtime.answer_service import deliver
from test_grounded_answers import draft_for, ledger
from test_paragraph_answer import paragraph


@pytest.mark.parametrize("protocol", ["claims", "paragraphs"])
@pytest.mark.parametrize("score", [None, 0.82])
@pytest.mark.parametrize("structured", [False, True])
def test_rendered_answer_stream_and_storage_agree(monkeypatch, protocol, score, structured):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    for model in (dbmod.User, dbmod.Conversation, dbmod.Message):
        model.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        user = dbmod.User(username="delivery", password_hash="unused", name="测试")
        session.add(user)
        session.flush()
        user_id = user.id
        conv = dbmod.Conversation(user_id=user_id, title="交付测试")
        session.add(conv)
        session.commit()
        conversation_id = conv.id

    packet = G.evidence_packet({"question": "修业年限？",
                                "evidence": ledger("修业年限为3至6年。")})
    draft = (paragraph(packet) if protocol == "paragraphs" else
             draft_for(packet, "修业年限为3至6年。", [packet["evidence"][0]["evidence_id"]]))
    text, contexts = G.render_draft(draft, packet)
    assert contexts and "score" not in contexts[0]
    if score is not None:
        contexts[0]["score"] = score
    answer = deliver(text, contexts).to_dict() if structured else None

    async def run_stream(*args, **kwargs):
        yield "delta", text
        yield "done", {"contexts": contexts, "calls": [], "call_validations": [], "answer": answer}

    async def no_title(*args, **kwargs):
        return ""

    async def forbidden_post(*args, **kwargs):
        raise AssertionError("Local delivery test must not call model services")

    def get_db():
        with sessions() as session:
            yield session

    monkeypatch.setattr(dbmod, "SessionLocal", sessions)
    monkeypatch.setattr(dbmod, "engine", engine)
    # MySQL advisory-lock behavior is outside this SQLite delivery regression.
    monkeypatch.setattr(chat, "ConversationLease",
                        lambda conversation_id: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(chat.agent_graph, "run_stream", run_stream)
    monkeypatch.setattr(chat, "_auto_title", no_title)
    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden_post)
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    app.dependency_overrides[chat.get_db] = get_db
    app.dependency_overrides[chat.require_auth] = lambda: dbmod.User(id=user_id)
    try:
        with TestClient(app) as client:
            response = client.post("/api/chat", json={"message": "修业年限？",
                                                      "conversation_id": conversation_id})
            saved = client.get(f"/api/conversations/{conversation_id}/messages")
        assert response.status_code == 200
        events = [line.removeprefix("data: ") for line in response.text.splitlines()
                  if line.startswith("data: ")]
        assert events[-1] == "[DONE]"
        payloads = [json.loads(event) for event in events[:-1]]
        assert not any("error" in event for event in payloads)
        final = next(event["replace"] for event in payloads if "replace" in event)
        assert saved.json()[-1]["content"] == final
        assert saved.json()[-1]["answer"] == answer
        assert "修业年限为3至6年。" in final and "资料来源" in final
        with sessions() as session:
            messages = session.query(dbmod.Message).order_by(dbmod.Message.id).all()
            assert [message.role for message in messages] == ["user", "assistant"]
            assert messages[1].content == final
            assert messages[1].answer_json == answer
            if structured:
                event = next(event for event in payloads if "replace" in event)
                assert event["answer"] == answer
                assert final == answer["text"]
            assert messages[1].retrieved_docs[0]["score"] == score
            assert messages[1].retrieved_docs[0]["source"] == contexts[0]["source"]
            assert session.get(dbmod.Conversation, conversation_id).context_revision == 2
    finally:
        engine.dispose()


def test_answer_column_migration_is_additive_and_idempotent(monkeypatch):
    from sqlalchemy import inspect
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)")
        connection.exec_driver_sql("INSERT INTO messages (id, content) VALUES (1, 'existing')")
    monkeypatch.setattr(dbmod, "engine", engine)
    try:
        dbmod._migrate_messages()
        dbmod._migrate_messages()
        assert "answer_json" in {row["name"] for row in inspect(engine).get_columns("messages")}
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT content, answer_json FROM messages").one() == ("existing", None)
    finally:
        engine.dispose()


@pytest.mark.parametrize("mixed,protocol", [(False, "grounded-draft/5.1"),
                                           (True, "grounded-draft/5.1"), (True, "grounded-body/1")])
def test_real_graph_policy_gap_survives_http_and_storage(monkeypatch, mixed, protocol):
    from agent.core import Source, ToolResult
    graph = chat.agent_graph
    question = ("我是2024级学生，请参考2025级人工智能培养方案，介绍该方案的修业年限，并说明是否能直接适用于我。"
                if mixed else "我是2024级人工智能专业学生，能按2025级培养方案判断修业年限吗？")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (dbmod.User, dbmod.Conversation, dbmod.Message):
        model.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        session.add(dbmod.User(id=1, username="gap", password_hash="unused", name="测试"))
        session.add(dbmod.Conversation(id=1, user_id=1, title="范围查询"))
        session.commit()
    planning_calls, composition_calls = [], []
    async def plan(*args, **kwargs):
        planning_calls.append(1)
        return {"type": "tool_calls", "calls": [{"name": "search_regulations", "arguments": {"query": question}}]}
    async def forbidden(*args, **kwargs):
        pytest.fail("confirmed policy gap must not request more planning or composition")
    async def compose(messages, packet, **kwargs):
        assert mixed
        composition_calls.append(1)
        assert packet["delivery_constraints"]["status"] == "partial"
        text = "所引2025级方案的修业年限为3至6年。"
        if protocol == "grounded-body/1":
            return {"paragraphs": [{"text": text, "unit_ids": [packet["evidence_units"][0]["unit_id"]],
                                    "calculation_ids": []}]}
        return draft_for(packet, text, [packet["evidence"][0]["evidence_id"]])
    original_run_stream = graph.run_stream
    async def configured_run_stream(*args, **kwargs):
        async for event in original_run_stream(*args, **kwargs, answer_protocol=protocol):
            yield event
    def search(name, args, **kwargs):
        assert name == "search_regulations"
        assert kwargs["context"]["task"].subject_scope.cohort == "2024"
        return ToolResult(tool=name, ok=True, status="success", meta={"args": args}, sources=[
            Source(title="2025级人工智能专业培养方案(主修)", text="修业年限3至6年。")])
    async def no_title(*args, **kwargs):
        return ""
    def get_db():
        with sessions() as session:
            yield session
    monkeypatch.setattr(dbmod, "SessionLocal", sessions)
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(chat, "ConversationLease", lambda value: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(chat, "_auto_title", no_title)
    monkeypatch.setattr(graph.model, "decide", plan)
    monkeypatch.setattr(graph.model, "compose", compose if mixed else forbidden)
    monkeypatch.setattr(graph, "run_stream", configured_run_stream)
    monkeypatch.setattr(graph, "_stream_round", forbidden)
    monkeypatch.setattr(graph.registry, "call", search)
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    app.dependency_overrides[chat.get_db] = get_db
    app.dependency_overrides[chat.require_auth] = lambda: dbmod.User(id=1)
    try:
        with TestClient(app) as client:
            response = client.post("/api/chat", json={"message": question, "conversation_id": 1})
            saved = client.get("/api/conversations/1/messages").json()
        assert "[DONE]" in response.text
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: ") and line != "data: [DONE]"]
        final = next(event for event in events if "replace" in event)
        assert final["answer"]["status"] == ("partial" if mixed else "missing_policy")
        assert final["answer"]["questions"] == [] and final["answer"]["limitations"]
        assert "2024" in final["replace"] and "2025" in final["replace"] and "尚缺少" in final["replace"]
        assert len(planning_calls) == 1 and len(composition_calls) == int(mixed)
        if mixed:
            assert "3至6年" in final["replace"] and final["answer"]["contexts"]
            assert final["answer"]["gaps"][0]["affects"] == "personal_applicability"
            assert final["replace"] == final["answer"]["text"]
        assert saved[-1]["answer"] == final["answer"] and saved[-1]["content"] == final["replace"]
        with sessions() as session:
            assert session.get(dbmod.Conversation, 1).context_revision == 2
    finally:
        engine.dispose()
