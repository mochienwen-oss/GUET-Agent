# -*- coding: utf-8 -*-
"""会话文件上传/下载、回答导出、校园服务任务状态。

权限原则：所有入口都先按 `user.id` 校验会话或文件归属，命中不了就 404，
不透露"存在但不属于你"这种信息。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from core.errors import ApiError
from database.db import Conversation, Message, User, get_db
from documents import campus_tasks, chat_export, chat_files
from security.auth import require_auth

router = APIRouter()


def _owned_conversation(db: Session, user: User, conversation_id: int) -> Conversation:
    row = (db.query(Conversation)
           .filter(Conversation.id == conversation_id, Conversation.user_id == user.id)
           .first())
    if not row:
        raise ApiError(404, "会话不存在")
    return row


def _owned_message(db: Session, conversation: Conversation, message_id: int) -> Message:
    row = (db.query(Message)
           .filter(Message.id == message_id, Message.conversation_id == conversation.id)
           .first())
    if not row:
        raise ApiError(404, "消息不存在")
    return row


@router.post("/conversations/{conversation_id}/files")
async def upload_file(conversation_id: int, file: UploadFile = File(...),
                      user: User = Depends(require_auth), db: Session = Depends(get_db)):
    _owned_conversation(db, user, conversation_id)
    data = await file.read()
    try:
        row = chat_files.save_bytes(db, user_id=user.id, conversation_id=conversation_id,
                                    filename=file.filename or "上传文件", data=data)
    except chat_files.UnsupportedSheet as error:
        raise ApiError(400, str(error))
    return {"file": chat_files.public(row)}


@router.get("/files/{file_id}/download")
def download_file(file_id: str, user: User = Depends(require_auth),
                  db: Session = Depends(get_db)):
    try:
        row = chat_files.get(db, file_id, user_id=user.id)
    except chat_files.FileNotAvailable as error:
        raise ApiError(404, str(error))
    suffix = row.filename.lower().rsplit('.', 1)[-1]
    media = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "csv": "text/csv", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "md": "text/markdown; charset=utf-8"}
    return FileResponse(row.path, filename=row.filename, media_type=media.get(suffix, "application/octet-stream"))


@router.get("/conversations/{conversation_id}/messages/{message_id}/export")
def export_message(conversation_id: int, message_id: int, format: str = "docx",
                   user: User = Depends(require_auth), db: Session = Depends(get_db)):
    if format not in {"docx", "md"}:
        raise ApiError(400, "只支持 docx 或 md")
    conversation = _owned_conversation(db, user, conversation_id)
    message = _owned_message(db, conversation, message_id)
    if message.role != "assistant" or not (message.content or "").strip():
        raise ApiError(400, "这条消息还没有可导出的回答")

    if format == "md":
        payload = chat_export.markdown_bytes(message.content)
        media = "text/markdown; charset=utf-8"
    else:
        try:
            payload = chat_export.docx_bytes(message.content, title=conversation.title,
                                             sources=_cited_sources(message))
        except Exception as error:  # 转换失败只影响本次导出，原消息不动
            raise ApiError(500, "导出失败：%s" % str(error)[:120])
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    filename = chat_export.filename_for(message.id, format, conversation.title)
    return Response(content=payload, media_type=media, headers={
        "Content-Disposition": "attachment; filename*=UTF-8''%s" % _quote(filename)})


def _quote(name: str) -> str:
    from urllib.parse import quote
    return quote(name, safe="")


def _cited_sources(message: Message) -> list[dict]:
    """只取该条消息里真实被引用的来源，不补造。"""
    from conversations.source_references import cited_ids
    answer = message.answer_json if isinstance(message.answer_json, dict) else {}
    contexts = answer.get("contexts") or []
    cited = cited_ids(message.content or "")
    if not cited:
        return []
    picked = []
    for item in contexts:
        if str(item.get("citation_id") or "") not in cited:
            continue
        picked.append({"title": item.get("title") or item.get("source"),
                       "url": item.get("url") or ""})
    return picked


@router.get("/campus/tasks/{task_id}")
def campus_task(task_id: str, user: User = Depends(require_auth)):
    try:
        record = campus_tasks.load(task_id, user_id=user.id)
    except campus_tasks.TaskNotAvailable as error:
        raise ApiError(404, str(error))
    return {"task": campus_tasks.public(record)}


@router.get('/conversations/{conversation_id}/campus-task')
def conversation_campus_task(conversation_id: int, user: User = Depends(require_auth),
                             db: Session = Depends(get_db)):
    _owned_conversation(db, user, conversation_id)
    record = campus_tasks.latest(user_id=user.id, conversation_id=conversation_id)
    return {'task': campus_tasks.public(record) if record else None}


@router.post('/campus/tasks/{task_id}/decision')
def campus_decision(task_id: str, body: dict, user: User = Depends(require_auth)):
    try:
        record = campus_tasks.load(task_id, user_id=user.id)
    except campus_tasks.TaskNotAvailable as error:
        raise ApiError(404, str(error))
    snapshot, history = body.get('snapshot'), body.get('history', [])
    if not isinstance(snapshot, dict) or not isinstance(history, list):
        raise ApiError(400, '页面观察数据无效')
    from agent.tools.campus.decision import decide
    try:
        return {'decision': decide(record, snapshot, history)}
    except Exception:
        raise ApiError(503, '模型暂时无法处理学校页面，请稍后继续')


@router.post("/campus/tasks/{task_id}/status")
def campus_task_status(task_id: str, body: dict, user: User = Depends(require_auth)):
    try:
        record = campus_tasks.update_status(task_id, user_id=user.id,
                                            status=str(body.get("status") or ""),
                                            detail=str(body.get("detail") or ""))
    except campus_tasks.TaskNotAvailable as error:
        raise ApiError(404, str(error))
    except ValueError as error:
        raise ApiError(400, str(error))
    return {"task": campus_tasks.public(record)}


@router.post("/campus/tasks/{task_id}/result")
def campus_task_result(task_id: str, body: dict, user: User = Depends(require_auth),
                       db: Session = Depends(get_db)):
    date = str(body.get("date") or "")
    units = body.get("units")
    summary = body.get("summary")
    query = body.get("query") or {}
    if not isinstance(units, list) or not isinstance(summary, dict) or not isinstance(query, dict):
        raise ApiError(400, "查询结果数据无效")
    try:
        # 只有「同一天 + 相同结果」才视为重复回传（桌面端 did-stop-loading 并发重入
        # 会重复 POST）；接着问新条件（日期变了）要插入新的结果消息。
        old = campus_tasks.load(task_id, user_id=user.id)
        if query and query != old.get('query'):
            raise ApiError(409, '查询条件已更新，请重新查询')
        old_result = old.get("result") or {}
        duplicate = (old_result.get("date") == date
                     and old_result.get("summary") == summary
                     and old_result.get("query", {}) == query)
        record = campus_tasks.save_result(task_id, user_id=user.id,
                                          date=date, units=units, summary=summary, query=query)
    except campus_tasks.TaskNotAvailable as error:
        raise ApiError(404, str(error))
    except ValueError as error:
        raise ApiError(409, str(error))
    if not duplicate:
        # 把查询结果作为一条 assistant 消息写入对话流，前端按时间顺序展示、刷新后仍在。
        conversation_id = int(record["conversation_id"])
        _owned_conversation(db, user, conversation_id)
        total = int((summary or {}).get("total", 0))
        content = "已查到 %d 间空闲教室（%s）" % (total, date)
        db.add(Message(conversation_id=conversation_id, role="assistant",
                       content=content,
                       answer_json={"kind": "campus_result", "date": date,
                                    "units": [str(u) for u in units],
                                    "query": query, "summary": summary}))
        db.commit()
    return {"task": campus_tasks.public(record)}

