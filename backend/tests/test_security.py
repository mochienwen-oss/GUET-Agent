# tests/test_security.py —— 密码哈希/校验 单元测试（纯标准库，任何环境可跑）
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from security.passwords import hash_password, verify_password  # noqa: E402


def test_hash_and_verify():
    h = hash_password("secret123")
    assert h.count(":") == 1
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


def test_hash_is_salted():
    # 同一密码两次哈希不同（盐不同），但都能校验通过
    h1 = hash_password("pass")
    h2 = hash_password("pass")
    assert h1 != h2
    assert verify_password("pass", h1)
    assert verify_password("pass", h2)


def test_verify_bad_format():
    assert not verify_password("pass", "")
    assert not verify_password("pass", "nocolon")
    assert not verify_password("pass", "too:many:parts")