"""密码哈希：PBKDF2-HMAC-SHA256 + 随机盐

为什么不存明文：config.json 就躺在 %APPDATA%\\Mythclass\\ 下，
当前用户可读可写。学生拿记事本一开就能看见密码——那这密码等于没设。

存储格式：

    pbkdf2_sha256$120000$<盐 hex>$<摘要 hex>

自带盐，所以两个机器上就算密码一样，存出来的哈希也不同。
"""

from __future__ import annotations

import hashlib
import hmac
import os

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 120_000          # 一体机上约 60~120 ms，够用又不至于卡手
SALT_BYTES = 16

DEFAULT_PASSWORD = "admin123"


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    """算一个带盐的哈希"""
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(stored: str, password: str) -> bool:
    """验密码。

    格式不对、字段缺失、被手动改坏，一律当失败——绝不抛异常，
    免得一个坏配置把客户端整个启动流程带崩。
    """
    if not stored or not password:
        return False

    parts = str(stored).split("$")
    if len(parts) != 4:
        return False

    algorithm, iterations, salt_hex, digest_hex = parts
    if algorithm != ALGORITHM:
        return False

    try:
        rounds = int(iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False

    if rounds <= 0 or not salt or not expected:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(actual, expected)


def looks_hashed(value: str) -> bool:
    """判断某个字符串是不是本模块产出的哈希（迁移老配置时用）"""
    return isinstance(value, str) and value.startswith(ALGORITHM + "$") and value.count("$") == 3
