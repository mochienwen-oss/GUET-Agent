# db.py —— SQLAlchemy 引擎、ORM 模型、建表与种子管理员
from datetime import datetime

from sqlalchemy import (
    JSON, Column, DateTime, ForeignKey, Integer, String, Text, create_engine,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from core.config import settings
from security.passwords import hash_password

engine = create_engine(settings.mysql_url, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    name = Column(String(64), nullable=False)
    role = Column(String(20), nullable=False, default="user")  # user / admin
    created_at = Column(DateTime, default=datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"

    token = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), default="新对话")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # ConversationContext v1（versioned JSON）与乐观锁 revision。
    context_json = Column(JSON, nullable=True)
    context_revision = Column(Integer, nullable=True)

    messages = relationship("Message", back_populates="conversation",
                            cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"),
                             nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user / assistant
    content = Column(Text, nullable=False)
    retrieved_docs = Column(JSON, nullable=True)  # 本次检索引用的文档片段
    answer_json = Column(JSON, nullable=True)  # final-answer/1，旧消息保留null
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    source_type = Column(String(50), default="txt")
    chunk_count = Column(Integer, default=0)
    status = Column(String(20), default="ready")  # ready / processing / failed
    category = Column(String(100), default="")  # 分类 / 文件夹
    content = Column(LONGTEXT, nullable=True)  # 全文原文（用于重建索引/重新分块）
    source_metadata = Column(JSON, nullable=True)  # 引用展示信息，不参与检索或向量计算
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class SystemSetting(Base):
    """键值配置表：存放检索 top_k / 相似度阈值等后台可调参数。"""
    __tablename__ = "system_settings"

    key = Column(String(64), primary_key=True)
    value = Column(String(200), nullable=False)


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    """读取一个字符串配置项，不存在时返回 default。"""
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    """写入一个字符串配置项（存在则覆盖）。"""
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))
    db.commit()


def get_setting_int(db: Session, key: str, default: int = 0) -> int:
    v = get_setting(db, key)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def get_setting_float(db: Session, key: str, default: float = 0.0) -> float:
    v = get_setting(db, key)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def get_setting_bool(db: Session, key: str, default: bool = False) -> bool:
    v = get_setting(db, key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def init_db():
    """建表（已存在则跳过），并做轻量补列迁移。"""
    Base.metadata.create_all(bind=engine)
    _migrate_documents()
    _migrate_conversations()
    _migrate_messages()


def _migrate_messages() -> None:
    from sqlalchemy import inspect
    with engine.begin() as conn:
        if "answer_json" not in {row["name"] for row in inspect(conn).get_columns("messages")}:
            conn.exec_driver_sql("ALTER TABLE messages ADD COLUMN answer_json JSON NULL")


def _migrate_documents() -> None:
    """为已有 documents 表补列 / 改列型（create_all 不会改动已存在表的结构）。"""
    with engine.begin() as conn:
        def has_column(name: str) -> bool:
            return bool(conn.exec_driver_sql(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'documents' "
                f"AND COLUMN_NAME = '{name}'"
            ).scalar())

        def column_type(name: str) -> str:
            return (conn.exec_driver_sql(
                "SELECT DATA_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'documents' "
                f"AND COLUMN_NAME = '{name}'"
            ).scalar() or "").lower()

        if not has_column("content"):
            conn.exec_driver_sql("ALTER TABLE documents ADD COLUMN content LONGTEXT")
        # ★ content 必须能装下**最长的那一份文档**。TEXT 的上限是 65,535 **字节**
        #   （不是字符）—— 中文一个字 UTF-8 占 3 字节，实际只有约 21,845 字。
        #   而目录里最长的《…专业培养方案》正文约 39,000 字 ≈ 110 KB：
        #   灌库对账（`vector.sync_documents`）会整批 INSERT 被
        #   `Data too long for column 'content'` 打回，**一条写不进去、90 条全回滚**。
        #   ⚠️ 这个上限对**上传**同样成立（大 md 一样写不进去），所以列型是硬约束，
        #      不是对账专属 —— 修它顺手也修掉了上传大文件的隐性失败。
        elif column_type("content") not in ("longtext", "mediumtext"):
            conn.exec_driver_sql("ALTER TABLE documents MODIFY COLUMN content LONGTEXT")
        if not has_column("source_metadata"):
            conn.exec_driver_sql("ALTER TABLE documents ADD COLUMN source_metadata JSON NULL")
        if not has_column("category"):
            conn.exec_driver_sql(
                "ALTER TABLE documents ADD COLUMN category VARCHAR(100) NOT NULL DEFAULT ''"
            )


def _migrate_conversations() -> None:
    """为已有 conversations 表补 ConversationContext 两列。"""
    with engine.begin() as conn:
        def has_column(name: str) -> bool:
            return bool(conn.exec_driver_sql(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'conversations' "
                f"AND COLUMN_NAME = '{name}'"
            ).scalar())

        if not has_column("context_json"):
            conn.exec_driver_sql("ALTER TABLE conversations ADD COLUMN context_json JSON")
        if not has_column("context_revision"):
            conn.exec_driver_sql(
                "ALTER TABLE conversations ADD COLUMN context_revision INTEGER")


def seed_admin():
    """首次启动创建管理员种子账号。"""
    db = SessionLocal()
    try:
        exists = db.query(User).filter(User.username == settings.admin_username).first()
        if not exists:
            db.add(User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                name="系统管理员",
                role="admin",
            ))
            db.commit()
    finally:
        db.close()


def get_db():
    """FastAPI 依赖：每请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
