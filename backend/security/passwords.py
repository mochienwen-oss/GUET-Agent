# security.py —— 密码哈希与校验（Python 标准库 hashlib.scrypt，零额外依赖）
import hashlib
import hmac
import secrets


def hash_password(password: str, salt: str = "") -> str:
    """生成加盐哈希，返回 'salt:hash' 格式字符串。"""
    salt = salt or secrets.token_hex(16)
    h = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=16384, r=8, p=1, dklen=64,
    ).hex()
    return f"{salt}:{h}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码：从存储串取出 salt 重算，恒时比较。"""
    parts = (stored or "").split(":")
    if len(parts) != 2:
        return False
    salt, _ = parts
    return hmac.compare_digest(hash_password(password, salt), stored)