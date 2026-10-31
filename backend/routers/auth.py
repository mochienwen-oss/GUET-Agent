# routers/auth.py —— 注册 / 登录 / 登出 / 当前用户
import base64
import secrets
import httpx
from fastapi import APIRouter, Depends, Header, File, UploadFile
from sqlalchemy.orm import Session

from security.auth import create_token, require_auth
from database.db import Session as SessionModel, User, get_db
from core.errors import ApiError
from core.config import settings
from routers.schemas import LoginBody, ProfileBody, SchoolChallengeBody, SchoolLoginBody
from security.passwords import hash_password, verify_password
from security import school_auth

router = APIRouter()


def _user_dict(user: User) -> dict:
    return {"id": user.id, "username": user.username, "name": user.name, "role": user.role, "avatar": user.avatar}


@router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    if not body.username or not body.password:
        raise ApiError(400, "请输入用户名和密码")
    user = db.query(User).filter(User.username == body.username).first()
    if not user or user.username != settings.admin_username or user.role != 'admin' or not verify_password(body.password, user.password_hash):
        raise ApiError(401, "用户名或密码错误")
    token = create_token(db, user.id)
    return {"token": token, "user": _user_dict(user)}


@router.post('/school/challenge')
def school_challenge(body: SchoolChallengeBody):
    if body.username.strip() == settings.admin_username:
        raise ApiError(403, '该账号保留给管理员，请使用管理员登录')
    try:
        return school_auth.begin(body.username)
    except school_auth.SchoolAuthError as error:
        raise ApiError(400, str(error))
    except httpx.HTTPError:
        raise ApiError(503, '暂时无法连接学校认证系统')


@router.post('/school/login')
def school_login(body: SchoolLoginBody, db: Session = Depends(get_db)):
    if body.username.strip() == settings.admin_username:
        raise ApiError(403, '该账号保留给管理员，请使用管理员登录')
    try:
        username, cookies = school_auth.finish(body.challenge_id, body.username, body.password, body.captcha)
    except school_auth.SchoolAuthError as error:
        raise ApiError(401, str(error))
    except httpx.HTTPError:
        raise ApiError(503, '暂时无法连接学校认证系统')
    if username == settings.admin_username:
        raise ApiError(403, '该账号保留给管理员，请使用管理员登录')
    user = db.query(User).filter(User.username == username).first()
    if user and user.role == 'admin':
        raise ApiError(403, '管理员账号请使用管理员登录')
    if user and user.auth_source != 'school':
        raise ApiError(409, '旧账号尚未清理，暂时无法登录')
    if not user:
        user = User(username=username, name=username, password_hash=hash_password(secrets.token_urlsafe(32)),
                    role='user', auth_source='school')
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_token(db, user.id)
    school_auth.offer_transfer(token, user.id, cookies)
    # 同时在服务端留一份：网页端没有 Electron，空教室查询要靠后端代发请求。
    # 两份互不影响 —— offer_transfer 是给桌面端一次性取走的，这份是给网页端用的。
    school_auth.remember_session(user.id, cookies)
    return {'token': token, 'user': _user_dict(user)}


@router.post('/school/session')
def take_school_session(authorization: str = Header(default=''), user: User = Depends(require_auth)):
    token = authorization.removeprefix('Bearer ').strip()
    try:
        cookies = school_auth.take_transfer(token, user.id)
    except school_auth.SchoolAuthError as error:
        raise ApiError(401, str(error))
    return {'cookies': cookies}


@router.post("/logout")
def logout(authorization: str = Header(default=""), user: User = Depends(require_auth), db: Session = Depends(get_db)):
    token = (authorization or "").replace("Bearer ", "").strip()
    s = db.query(SessionModel).filter(SessionModel.token == token).first()
    if s:
        user_id = s.user_id
        db.delete(s)
        db.commit()
        # ★ 退出登录必须同时撤销「学校会话」：网页端查空教室用的是服务端内存里的
        #   教务 Cookie，只删 SAI 令牌会留下一条最长 2 小时的可用凭据 —— 退出后
        #   重新登录（甚至换人用同一账号）仍能不经学校认证直接查询。
        #   但多设备场景下 A 设备退出不该让 B 设备的查询失效，所以只在**该用户已无
        #   任何其它有效令牌**时才清理。
        remaining = db.query(SessionModel).filter(SessionModel.user_id == user_id).count()
        if remaining == 0:
            school_auth.clear_session(user_id)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(require_auth)):
    return _user_dict(user)


@router.patch('/profile')
def update_profile(body: ProfileBody, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise ApiError(400, '昵称不能为空')
    user.name = name
    db.commit()
    return _user_dict(user)


@router.put('/profile/avatar')
async def update_avatar(file: UploadFile = File(...), user: User = Depends(require_auth), db: Session = Depends(get_db)):
    content = await file.read(256 * 1024 + 1)
    if len(content) > 256 * 1024:
        raise ApiError(400, '头像文件不能超过 256 KB')
    mime = 'image/png' if content.startswith(b'\x89PNG\r\n\x1a\n') else (
        'image/jpeg' if content.startswith(b'\xff\xd8\xff') else '')
    if not mime:
        raise ApiError(400, '请使用 PNG 或 JPEG 头像')
    user.avatar = 'data:' + mime + ';base64,' + base64.b64encode(content).decode('ascii')
    db.commit()
    return _user_dict(user)


@router.delete('/profile/avatar')
def remove_avatar(user: User = Depends(require_auth), db: Session = Depends(get_db)):
    user.avatar = None
    db.commit()
    return _user_dict(user)
