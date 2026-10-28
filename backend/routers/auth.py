# routers/auth.py —— 注册 / 登录 / 登出 / 当前用户
from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from security.auth import create_token, require_auth
from database.db import Session as SessionModel, User, get_db
from core.errors import ApiError
from routers.schemas import LoginBody, RegisterBody
from security.passwords import hash_password, verify_password

router = APIRouter()


def _user_dict(user: User) -> dict:
    return {"id": user.id, "username": user.username, "name": user.name, "role": user.role}


@router.post("/register")
def register(body: RegisterBody, db: Session = Depends(get_db)):
    username = (body.username or "").strip()
    password = body.password or ""
    name = (body.name or "").strip()
    if not username or not password or not name:
        raise ApiError(400, "请填写用户名、密码和昵称")
    if db.query(User).filter(User.username == username).first():
        raise ApiError(400, "用户名已存在")
    user = User(username=username, password_hash=hash_password(password), name=name, role="user")
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_token(db, user.id)
    return {"token": token, "user": _user_dict(user)}


@router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    if not body.username or not body.password:
        raise ApiError(400, "请输入用户名和密码")
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise ApiError(401, "用户名或密码错误")
    token = create_token(db, user.id)
    return {"token": token, "user": _user_dict(user)}


@router.post("/logout")
def logout(authorization: str = Header(default=""), user: User = Depends(require_auth), db: Session = Depends(get_db)):
    token = (authorization or "").replace("Bearer ", "").strip()
    s = db.query(SessionModel).filter(SessionModel.token == token).first()
    if s:
        db.delete(s)
        db.commit()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(require_auth)):
    return _user_dict(user)