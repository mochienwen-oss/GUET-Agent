# routers/chat.py —— 问答（SSE 流式）+ 会话管理
import json
import asyncio
import logging
from contextlib import aclosing
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from agent.runtime import graph as agent_graph
from agent.runtime.retrieval_reply import RETRIEVAL_REPLIES
from conversations import conversation_context_store as context_store
from llm import llm_client
from agent.tools.create_ppt import tool as ppt_tool
from conversations import titles
from security.auth import require_auth
from core.config import settings
from database.db import Conversation, Document, Message, User, get_db
from core.errors import ApiError
from agent.tools.create_ppt.tool import StateCorrupt
from routers.schemas import ChatBody, ConversationCreate, ConversationRename
from agent.core.request_control import ConversationLease, bounded_stream
from agent.core.request_control import check_cancelled
from starlette.background import BackgroundTask
from documents import chat_files
from documents.catalog.document_availability import SourceUnavailable
from conversations.source_references import prepare_references, read_reference

router = APIRouter()

logger = logging.getLogger(__name__)


def _conv_dict(c: Conversation) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _get_conv(db: Session, user: User, conversation_id: int) -> Conversation:
    c = (db.query(Conversation)
         .filter(Conversation.id == conversation_id, Conversation.user_id == user.id)
         .first())
    if not c:
        raise ApiError(404, "会话不存在")
    return c


async def _auto_title(conversation_id: int, question: str, placeholder: str,
                      enabled: bool) -> str:
    """首答定稿后，让模型给会话起一个短标题并落库；没做成则返回空串。"""
    if not enabled or not titles.needs_model(question):
        return ""
    from database.db import SessionLocal
    s = SessionLocal()
    try:
        cur = s.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not cur or cur.title != placeholder:
            return ""
        new_title = await llm_client.generate_title(question)
        if not new_title:
            return ""
        cur.title = new_title
        check_cancelled()
        s.commit()
        return new_title
    except Exception:
        return ""
    finally:
        s.close()


@router.post("/chat")
def chat(body: ChatBody, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    if not (body.message or "").strip():
        raise ApiError(400, "请输入问题")
    if not body.conversation_id:
        conv = Conversation(user_id=user.id,
                            title=titles.placeholder_title(body.message.strip()) or "新对话")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        body = body.model_copy(update={"conversation_id": conv.id})
    _get_conv(db, user, body.conversation_id)
    lease = ConversationLease(body.conversation_id)
    try:
        return _chat(body, user, db, lease)
    except BaseException:
        if lease:
            lease.close()
        raise


def _chat(body, user, db, lease):
    question = (body.message or "").strip()
    if not question:
        raise ApiError(400, "请输入问题")

    placeholder = titles.placeholder_title(question) or "新对话"

    if body.conversation_id:
        conv = _get_conv(db, user, body.conversation_id)
    else:
        conv = Conversation(user_id=user.id, title=placeholder)
        db.add(conv)
        db.commit()
        db.refresh(conv)

    conversation_id = conv.id

    resume_stage = None
    ppt_task_id = None
    if body.ppt_task_id:
        ppt_tool.owned_state(body.ppt_task_id, user.id)
        raise ApiError(409, "请在演示文稿工作区继续任务；旧版补问任务请重新创建")

    # 附件先验归属：只把确实属于本会话、本用户的文件交给模型。
    attachments, attachment_refs = [], []
    for file_id in (body.attachments or []):
        try:
            row = chat_files.get(db, file_id, user_id=user.id, conversation_id=conversation_id)
        except chat_files.FileNotAvailable as error:
            raise ApiError(400, str(error))
        attachments.append(row.id)
        attachment_refs.append(chat_files.public(row))

    from database.db import MemoryControl
    control = db.get(MemoryControl, 1) if settings.database_url else None
    history = context_store.recent_history(db, conv.id, after_id=control.cutoff_message_id if control else 0)
    if settings.database_url:
        from agent.runtime.history_memory import history as prepare_history
        history = prepare_history(history, question, user.id, conv.id)
    else:
        history = [{'role': row['role'], 'content': row['content']} for row in history]
    contexts = []
    n_refs = 0

    # 持久化用户消息并刷新会话时间
    user_message = Message(conversation_id=conv.id, role="user", content=question,
                           files=attachment_refs or None)
    db.add(user_message)
    conv.updated_at = datetime.utcnow()
    db.commit()
    user_message_id = user_message.id

    async def generate():
        full = ""            # 模型已产出的全部文本
        emitted = 0          # 已发给前端的长度
        failed = False
        cancelled = False
        agent_calls: list = []
        agent_done = False
        agent_answer = None
        terminal_reply = False
        result_files: list = []

        async def _delta_source():
            nonlocal n_refs, agent_done, agent_answer, terminal_reply
            ppt_announced = False
            async for ev, payload in agent_graph.run_stream(
                    history, question, conversation_id=conversation_id,
                    ppt_task_id=ppt_task_id, resume_stage=resume_stage,
                    owner_id=user.id, attachments=tuple(attachments),
                    attachment_refs=tuple(attachment_refs)):
                if ev == "delta":
                    yield ("delta", payload)
                elif ev == 'replace':
                    yield ('replace', payload)
                elif ev == 'file':
                    # 表格结果：独立事件，不是答案正文，也不能触发"完成"。
                    result_files.append(payload.get("file") or {})
                    yield ('file', payload)
                elif ev == 'campus_action':
                    # 校园服务任务：只表示"该打开学校页面了"，不代表已查到结果。
                    yield ('campus_action', payload)
                elif ev == "ppt":
                    if not ppt_announced and "ppt_task" in payload:
                        ppt_announced = True
                        yield ("delta", "正在制作 PPT；如需补充信息，我会在这里追问。\n")
                    yield ("ppt", payload)
                else:
                    agent_done = True
                    agent_answer = payload.get("answer")
                    terminal_reply = bool(payload.get('terminal'))
                    contexts.extend(agent_answer["contexts"] if agent_answer else payload["contexts"])
                    n_refs = payload.get("n_refs", len(contexts))
                    agent_calls.extend(payload["calls"])
                    logger.info("[chat] agent calls=%s ctx=%d",
                                payload["calls"], len(payload["contexts"]))


        try:
            async for kind, val in _delta_source():
                if kind == 'replace':
                    full = val
                    emitted = len(full)
                    yield f"data: {json.dumps({'replace': full}, ensure_ascii=False)}\n\n"
                    continue
                if kind == "ppt":
                    yield f"data: {json.dumps(val, ensure_ascii=False)}\n\n"
                    continue
                if kind in ('file', 'campus_action'):
                    yield f"data: {json.dumps(val, ensure_ascii=False)}\n\n"
                    continue
                delta = val
                full += delta
                cut = len(full)
                if cut > emitted:
                    yield f"data: {json.dumps({'content': full[emitted:cut]}, ensure_ascii=False)}\n\n"
                    emitted = cut
        except (asyncio.CancelledError, GeneratorExit):
            failed = cancelled = True
            raise
        except StateCorrupt:
            failed = True
            yield f"data: {json.dumps({'ppt_error': {'stage': 'resume', 'error': 'state_corrupt', 'recoverable': True}}, ensure_ascii=False)}\n\n"
        except ApiError as exc:
            failed = True
            yield f"data: {json.dumps({'error': exc.message}, ensure_ascii=False)}\n\n"
        except (TimeoutError, asyncio.TimeoutError):
            failed = True
            yield f"data: {json.dumps({'error': RETRIEVAL_REPLIES['error']}, ensure_ascii=False)}\n\n"
        except SourceUnavailable:
            failed = True
            yield f"data: {json.dumps({'error': RETRIEVAL_REPLIES['error']}, ensure_ascii=False)}\n\n"
        except Exception:
            failed = True
            yield f"data: {json.dumps({'error': '模型服务不可用，请稍后重试'}, ensure_ascii=False)}\n\n"
        finally:
            if agent_answer is not None and agent_done and not failed:
                finalized = agent_answer["text"]
            else:
                finalized = full
            if agent_calls and not cancelled:
                yield f"data: {json.dumps({'calls': agent_calls}, ensure_ascii=False)}\n\n"
            if finalized and not failed:
                delivery = {"replace": finalized}
                if agent_answer is not None:
                    delivery["answer"] = agent_answer
                yield f"data: {json.dumps(delivery, ensure_ascii=False)}\n\n"
            if finalized and not failed and agent_done:
                check_cancelled()
                from database.db import SessionLocal
                s = SessionLocal()
                try:
                    saved = Message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=finalized,
                        answer_json=agent_answer,
                        retrieved_docs=prepare_references(contexts, finalized, s),
                        # 结果文件跟着消息走，刷新后仍能找到下载入口。
                        files=result_files or None,
                    )
                    s.add(saved)
                    s.flush()
                    c = s.query(Conversation).filter(Conversation.id == conversation_id).first()
                    if c:
                        c.updated_at = datetime.utcnow()
                    check_cancelled()
                    if settings.database_url and not any(
                            call.get('name') == 'forget_memories' and call.get('status') == 'success'
                            for call in agent_calls):
                        from agent.runtime.user_memory import enqueue
                        enqueue(s, user_id=user.id, conversation_id=conversation_id,
                                user_message_id=user_message_id, assistant_message_id=saved.id)
                    s.commit()
                    yield f"data: {json.dumps({'message_id': saved.id, 'retrieved_docs': saved.retrieved_docs}, ensure_ascii=False)}\n\n"
                finally:
                    s.close()
            new_title = await _auto_title(conversation_id, question, placeholder,
                                          enabled=bool(finalized) and not failed and agent_done and not terminal_reply)
            if new_title:
                yield f"data: {json.dumps({'title': new_title}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    async def controlled_generate():
        try:
            async with aclosing(bounded_stream(generate(), settings.agent_request_timeout_seconds)) as stream:
                async for event in stream:
                    yield event
        except (TimeoutError, asyncio.TimeoutError):
            yield f"data: {json.dumps({'error': RETRIEVAL_REPLIES['error']}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            lease.close()

    return StreamingResponse(
        controlled_generate(),
        media_type="text/event-stream",
        background=BackgroundTask(lease.close),
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations")
def list_conversations(user: User = Depends(require_auth), db: Session = Depends(get_db)):
    convs = (db.query(Conversation)
             .filter(Conversation.user_id == user.id)
             .order_by(Conversation.updated_at.desc())
             .all())
    return [_conv_dict(c) for c in convs]


@router.post("/conversations")
def create_conversation(body: ConversationCreate, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    conv = Conversation(user_id=user.id,
                        title=titles.placeholder_title(body.title or "") or "新对话")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return _conv_dict(conv)


@router.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: int, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    _get_conv(db, user, conversation_id)
    msgs = (db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.id.asc())
            .all())
    return [
        {"id": m.id, "role": m.role, "content": m.content,
         "retrieved_docs": m.retrieved_docs,
         "answer": m.answer_json,
         "files": m.files or [],
         "created_at": m.created_at.isoformat() if m.created_at else None}
        for m in msgs
    ]


@router.get("/conversations/{conversation_id}/messages/{message_id}/sources/{citation_id}")
def message_source(conversation_id: int, message_id: int, citation_id: str,
                   full: bool = False, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    _get_conv(db, user, conversation_id)
    message = db.query(Message).filter(Message.id == message_id,
                                      Message.conversation_id == conversation_id,
                                      Message.role == 'assistant').first()
    if not message:
        raise ApiError(404, '回答不存在')
    reference = next((row for row in message.retrieved_docs or []
                      if row.get('citation_id') == citation_id), None)
    if reference is None:
        raise ApiError(404, '该回答没有保存这条引用')
    doc_id = str(reference.get('doc_id') or '')
    document = db.query(Document).filter(Document.id == int(doc_id)).first() if doc_id.isdigit() else None
    try:
        return read_reference(reference, document, full=full)
    except SourceUnavailable:
        raise ApiError(409, '读取期间资料发生更新，请稍后重新打开') from None


@router.patch("/conversations/{conversation_id}")
def rename_conversation(conversation_id: int, body: ConversationRename, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    conv = _get_conv(db, user, conversation_id)
    conv.title = (body.title or "").strip() or "新对话"
    db.commit()
    return _conv_dict(conv)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    conv = _get_conv(db, user, conversation_id)
    lease = ConversationLease(conversation_id)
    try:
        if settings.database_url:
            from agent.runtime.history_memory import delete as delete_summary
            delete_summary(user.id, conversation_id)
        db.delete(conv)  # messages / chat_files 由外键 CASCADE 连带删除
        db.commit()
        agent_graph.forget_conversation(user.id, conversation_id)
        chat_files.drop_conversation_folder(conversation_id)  # 会话自己的文件目录，失败不影响删除结果
    finally:
        lease.close()
    return {"ok": True}
