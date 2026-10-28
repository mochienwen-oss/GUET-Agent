# schemas.py —— Pydantic 请求/响应模型
from typing import Optional

from pydantic import BaseModel, Field


# ---- auth ----
class RegisterBody(BaseModel):
    username: str
    password: str
    name: str


class LoginBody(BaseModel):
    username: str
    password: str


# ---- chat ----
class ChatBody(BaseModel):
    message: str
    conversation_id: Optional[int] = None
    ppt_task_id: Optional[str] = None  # 仅显式选择「继续制作 PPT」时发送


class ConversationCreate(BaseModel):
    title: Optional[str] = None


class ConversationRename(BaseModel):
    title: str


# ---- admin ----
class DocumentCreate(BaseModel):
    title: str
    source_filename: str = Field(default='', max_length=500)
    source_type: Optional[str] = "txt"
    category: Optional[str] = ""
    content: str


class DocumentUpdate(BaseModel):
    category: str


class SourceMetadataBody(BaseModel):
    published_year: Optional[int] = Field(default=None, ge=1000, le=9999)
    audience: str = Field(default='', max_length=300)
    coverage: str = Field(default='', max_length=400)
    basis: str = Field(default='', max_length=500)


class CategoryRename(BaseModel):
    """分类文件夹重命名：把 old_name 分类下的所有文档改到 new_name。"""
    old_name: str
    new_name: str


class RetrievalTest(BaseModel):
    # ★ 2026-10-01：检索参数已**冻结为标定常量**（后台不再可调），这里的可选字段
    #   仅为向后兼容保留，后端**不再读取**它们（见 admin.test_retrieval）。
    query: str
    # ★ 2026-10-02 新增：`ungated` —— **唯一**一个后端会读的调试开关。
    #   门控判空时 `contexts` 为空，**排序信息就丢了**，复标阈值时拿不到分数
    #  （分不清「库里没有」与「有但被阈值判空」）。它把 crag_low / rerank_low 压到 0，
    #   低档判空分支永不触发 → 拿到的是**门控前的 top-k 与 best 分**。
    #   只用于标定与排查，**不改变聊天链路**（chat.py 仍走 retrieve_frozen）。
    ungated: bool = False
    top_k: Optional[int] = None
    crag_high_score: Optional[float] = None
    crag_low_score: Optional[float] = None
    use_llm_grader: Optional[bool] = None
    use_reranker: Optional[bool] = None
    rerank_high_score: Optional[float] = None
    rerank_low_score: Optional[float] = None


class AdminSettings(BaseModel):
    retrieval_top_k: Optional[int] = None
    crag_high_score: Optional[float] = None
    crag_low_score: Optional[float] = None
    use_llm_grader: Optional[bool] = None
    use_reranker: Optional[bool] = None
    rerank_high_score: Optional[float] = None
    rerank_low_score: Optional[float] = None
