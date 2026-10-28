# -*- coding: utf-8 -*-
"""GUET 统一身份认证（CAS）客户端 —— 「空教室查询」的外部依赖层。

**这是 `backend/tools/` 里唯一会发网络请求、且结果非确定的模块。**
其余工具（`plan.py` / `search.py`）立的规矩是「纯函数、确定性、可 JSON 序列化」，
这里做不到 —— 所以单独成文件、边界划清：import 时**不做任何网络动作**，
失败一律**显式抛错**，绝不静默降级（铁律：静默 fallback 出过三次事故）。

## 协议（2026-10-10 对登录页与其 `encrypt.js` 实测，非推测）

登录页 `encrypt.js` 里的原函数（逐字抄录，别照记忆写）：

    function getAesString(n,f,c){            // n=明文, f=key(salt), c=iv
        f=CryptoJS.enc.Utf8.parse(f); c=CryptoJS.enc.Utf8.parse(c);
        return CryptoJS.AES.encrypt(n,f,{iv:c,mode:CBC,padding:Pkcs7}).toString()
    }
    function encryptAES(n,f){ return f? getAesString(randomString(64)+n, f, randomString(16)) : n }
    var $aes_chars="ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"

`toString()` 走 OpenSSL 格式，但 key 传的是 WordArray（非口令）→ **不掺 salt**，
所以产物就是 `base64(AES-CBC/PKCS7(前导64随机字符 + 明文, key=salt, iv=16随机字符))`。

两处**实测踩过**的坑：

1. salt 的 id 是 **`pwdEncryptSalt`**，不是 `pwdDefaultEncryptSalt`。页面上还有个
   **空值的 `encryptSalt`** 紧挨着 —— 抠错那个就得到空 key，AES 直接炸。
2. salt **每次会话都在变**（两次抓取实测 `FqCFiBEI5dSQ2dvo` vs `YZpXNDuixRnbBmbe`）
   → 必须每次从登录页现取，写死必挂。

**验证码是强制的**：`/authserver/checkNeedCaptcha.htl?username=<任意>` 恒返回
`{"isNeed":true}`（连瞎编的用户名也一样）。图是 80×30 四字符加干扰线，走 `ddddocr`。
"""

import base64
import logging
import re
import secrets
import threading
import time

import httpx
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.config import settings

logger = logging.getLogger(__name__)

CAS = "https://cas.guet.edu.cn/authserver"
LOGIN_PAGE = CAS + "/login"
SERVICE = "https://bkjwtest.guet.edu.cn/student/home"
# 与原版 `$aes_chars` 逐字相同：51 字符，去掉了易混的 I/L/O/0/1/9。
CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class CasError(RuntimeError):
    """登录过程中的**显式**失败。调用方据此给用户看「查不了」，而不是编答案。"""


# ── 密码加密 ────────────────────────────────────────────────────────────────

def random_string(n: int) -> str:
    # 原版用 Math.random；这里用 secrets（同分布，但不依赖伪随机种子）
    return "".join(secrets.choice(CHARS) for _ in range(n))


def encrypt_password(password: str, salt: str) -> str:
    """复刻 `encryptAES(password, salt)`。salt 是 16 字符 → AES-128。"""
    key = salt.encode("utf-8")
    iv = random_string(16).encode("utf-8")
    data = (random_string(64) + password).encode("utf-8")
    padder = sym_padding.PKCS7(128).padder()
    body = padder.update(data) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(enc.update(body) + enc.finalize()).decode()


# ── 登录页解析 ──────────────────────────────────────────────────────────────

def parse_login_form(html: str) -> tuple[str, str]:
    """抠出 **userNameLogin 那个表单** 的 (execution, salt)。

    页面里有 4 个表单（fido / 动态码 / 用户名 / …），各带一份 `execution`
    —— 定位 `value="userNameLogin"` **之后**那一个，不是随便取第一个。
    """
    salt = re.search(r'id="pwdEncryptSalt"[^>]*value="([^"]*)"', html)
    i = html.find('value="userNameLogin"')
    if i < 0:
        raise CasError("登录页里找不到 userNameLogin 表单 —— 页面结构变了？")
    ex = re.search(r'name="execution"[^>]*value="([^"]*)"', html[i:])
    if not salt or not salt.group(1) or not ex:
        raise CasError("缺 salt 或 execution（salt=%r）" % (salt.group(1) if salt else None))
    return ex.group(1), salt.group(1)


def _error_text(html: str) -> str:
    """从返回的登录页里抠出错误提示。

    ⚠️ 真实结构是**嵌套一层 span**：
        `<span id="showErrorTip" class="form-error"><span>该账号非常用账号或用户名密码有误</span></span>`
    —— 所以 `[^<]*` 那种写法一碰 `<` 就断，抠出空串（实测踩过）。

    ⚠️⚠️ **本页对「验证码错」与「密码错」返回同一句**（实测：正确验证码与
    故意传 `zzzz` 拿到的都是「该账号非常用账号或用户名密码有误」）。
    所以这个字符串**只能给人看，不能用来分流重试** —— 调用方只能「任何失败都重试」。

    抠不到就返回空串；调用方**绝不**据此判断成功，成功与否只看最终 URL。
    """
    for pat in (r'id="showErrorTip"[^>]*>\s*(?:<span[^>]*>)?\s*([^<]{1,120})',
                r'id="msg"[^>]*>\s*(?:<span[^>]*>)?\s*([^<]{1,120})',
                r'class="[^"]*form-error[^"]*"[^>]*>\s*(?:<span[^>]*>)?\s*([^<]{1,120})'):
        m = re.search(pat, html)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


# ── 验证码 ──────────────────────────────────────────────────────────────────

_ocr_engine = None
# ★ 必须是 **RLock**，不能是 Lock：`_ocr()` 自己也要拿这把锁（懒加载是双重检查写法），
#   而调用处（`login`）已经在锁里了 —— 用普通 Lock 就是**同线程二次获取 → 死锁**。
#   实测踩过：`cas.login()` 永久挂起、逐步测试却秒过，差别就在这。
_ocr_lock = threading.RLock()


def _ocr():
    """懒加载 ddddocr（装载 ~0.1s，之后单张 4~60ms）。

    ⚠️ 实例**不是**线程安全的（内部有状态），所以调用处要拿锁 —— 见 `login`。
    """
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_lock:
            if _ocr_engine is None:
                try:
                    import ddddocr                                # noqa: PLC0415
                except ImportError as e:
                    raise CasError(
                        "缺少验证码依赖，先装：pip install ddddocr") from e
                _ocr_engine = ddddocr.DdddOcr(show_ad=False)
    return _ocr_engine


# ── 登录 ────────────────────────────────────────────────────────────────────

def new_client() -> httpx.Client:
    """一个干净的会话。**不共享 cookie jar** —— 每次登录都是新会话（salt 也是新的）。"""
    return httpx.Client(follow_redirects=True,
                        timeout=httpx.Timeout(20.0, connect=10.0),
                        headers={"User-Agent": UA})


def login(client: httpx.Client, username: str, password: str,
          retries: int | None = None) -> str:
    """登录并在 `client` 上留下会话 cookie。成功返回最终 URL。

    **任何失败都换一张验证码重试**（不是「只在验证码错时重试」）：
    实测本页对「验证码错」与「密码错」返回**同一句**提示，分不开（见 `_error_text`）。
    重试用的是**全新 captcha + 全新 execution**，所以每轮都是独立机会 ——
    这正好对上 ddddocr 认这套图**大小写不稳**（W/w、J/j、P/p、S/s 分不清）的现实。
    代价：密码真错时会白跑 N 次；N=4 时这点代价可以接受。

    判定「成功」看的是**最终落到 SERVICE**，不是「页面里没有错误提示」——
    后者抠不到就等于没判据（那正是静默失败的温床）。
    """
    retries = settings.jw_captcha_retries if retries is None else retries
    last = ""
    for attempt in range(1, retries + 1):
        execution, salt = parse_login_form(
            client.get(LOGIN_PAGE, params={"service": SERVICE},
                       headers={"Referer": LOGIN_PAGE}).text)
        img = client.get(CAS + "/getCaptcha.htl",
                         params={"_": str(int(time.time() * 1000))},
                         headers={"Referer": LOGIN_PAGE}).content
        with _ocr_lock:
            code = _ocr().classification(img)

        r = client.post(LOGIN_PAGE, params={"service": SERVICE}, data={
            "username": username,
            "password": encrypt_password(password, salt),
            "captcha": code,
            "lt": "",
            "execution": execution,
            "_eventId": "submit",
            "cllt": "userNameLogin",
            "dllt": "generalLogin",
            "rememberMe": "true",
        }, headers={"Referer": LOGIN_PAGE})

        # ★ 判的是**最终 URL 的 host**，不是「URL 里含有这个域名」——
        #   登录页的 URL 本身就写着 `?service=https%3A%2F%2Fbkjwtest.guet.edu.cn%2F...`，
        #   用子串判等于**永远为真**（实测踩过：随便编个用户名也会「登录成功」）。
        if r.url.host == "bkjwtest.guet.edu.cn":
            logger.info("[cas] 登录成功（第 %d 次尝试）", attempt)
            return str(r.url)

        last = _error_text(r.text) or ("HTTP %d" % r.status_code)
        logger.warning("[cas] 登录未成功（第 %d/%d 次）：验证码=%r 提示=%r",
                       attempt, retries, code, last)
    raise CasError("登录失败（重试 %d 次）：%s" % (retries, last))


# ── 会话缓存 ────────────────────────────────────────────────────────────────

_SESSION: dict = {"client": None, "at": 0.0}
_SESSION_LOCK = threading.Lock()
_TTL = 20 * 60          # 20 分钟。登录要过一次验证码 + 一次 POST，别每条问答都重登。


def get_session() -> httpx.Client:
    """拿到一个已登录的会话（必要时新建）。凭据从 `settings` 读，**未配置即报错**。

    ⚠️ 凭据未配置时**显式抛 `CasError`** —— 调用方据此给「未配置」卡片，
    绝不静默返回空（那是本项目三次事故的共同病根）。
    """
    if not settings.jw_student_id or not settings.jw_password:
        raise CasError("未配置教务系统账号（JW_STUDENT_ID / JW_PASSWORD）")
    now = time.monotonic()
    if _SESSION["client"] is not None and now - _SESSION["at"] < _TTL:
        return _SESSION["client"]
    with _SESSION_LOCK:
        now = time.monotonic()
        if _SESSION["client"] is not None and now - _SESSION["at"] < _TTL:
            return _SESSION["client"]
        client = new_client()
        login(client, settings.jw_student_id, settings.jw_password)
        _SESSION.update(client=client, at=time.monotonic())
        return client