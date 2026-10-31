"""按用户要求将模型整理的内容保存为会话文件。"""
from typing import Literal
from pydantic import BaseModel, Field


class DocumentArgs(BaseModel):
    format: Literal['docx', 'md'] = Field(description='用户要求的文件格式')
    title: str = Field(description='文档标题')
    content: str = Field(description='文档完整 Markdown 正文；依据对话和真实来源，不编造')


def create_document(runtime_context, format: str, title: str, content: str):
    from database.db import SessionLocal
    from documents import chat_export, chat_files
    if not content.strip():
        raise ValueError('文档内容为空')
    payload = (chat_export.docx_bytes(content, title=title) if format == 'docx'
               else chat_export.markdown_bytes(content))
    db = SessionLocal()
    try:
        saved = chat_files.save_result(db, user_id=runtime_context.user_id,
                                       conversation_id=runtime_context.conversation_id,
                                       filename=f'{title}.{format}', data=payload)
        file = chat_files.public(saved)
    finally:
        db.close()
    return {'status': 'success', 'data': {'filename': file['filename']}, 'file': file}
