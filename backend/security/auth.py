# auth.py —— 鉴权：登录态 token + FastAPI 依赖注入（require_auth / require_admin）
import secrets

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from database.db import Session as SessionModel, User, get_db
from core.errors import ApiError


def create_token(db: Session, user_id: int) -> str:
    """生成随机 64 位 token 并存入 sessions 表。"""
    token = secrets.token_hex(32)
    db.add(SessionModel(token=token, user_id=user_id))
    db.commit()
    return token


def _get_user(db: Session, token: str) -> User:
    if not token:
        raise ApiError(401, "未登录")
    s = db.query(SessionModel).filter(SessionModel.token == token).first()
    if not s:
        raise ApiError(401, "登录已失效，请重新登录")
    user = db.query(User).filter(User.id == s.user_id).first()
    if not user:
        raise ApiError(401, "用户不存在")
    return user


def require_auth(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    """要求已登录：解析 Bearer token，返回用户对象。"""
    token = (authorization or "").replace("Bearer ", "").strip()
    return _get_user(db, token)


def require_admin(user: User = Depends(require_auth)) -> User:
    """要求管理员角色。"""
    if user.role != "admin":
        raise ApiError(403, "无权限执行此操作")
    return user