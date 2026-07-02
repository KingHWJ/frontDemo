"""
secret_crypto — Fernet 对称加解密工具

调用链位置：
  本模块 ← repositories.py（加解密 GitLab 密码，写入/读取 rc_gitlab_credential）
  注意：auth.py 内部有一份等价的 Fernet 派生逻辑（AuthSessionStore._fernet），
  两处实现一致但未复用，修改密钥派生规则时需同步更新。

对外暴露：
  - fernet_key()    → 派生 Fernet 密钥字节，被 encrypt_secret / decrypt_secret 调用
  - encrypt_secret(value) → 加密明文，返回密文字符串
  - decrypt_secret(ciphertext) → 解密密文，返回明文字符串
"""
from __future__ import annotations

import base64
import hashlib

from app.config import settings


def fernet_key() -> bytes:
    """基于 COOKIE_ENCRYPTION_KEY 生成 Fernet 密钥，统一加密敏感元数据。

    派生规则：
      - 若配置值经 urlsafe_b64decode 后正好 32 字节，视为合法 Fernet key 直接使用；
      - 否则用 SHA-256 哈希后再 urlsafe_b64encode 生成 32 字节 key。

    被 encrypt_secret / decrypt_secret 调用；也与 auth.py AuthSessionStore._fernet 逻辑一致。
    """
    raw_key = settings.cookie_encryption_key
    if not raw_key:
        raise RuntimeError("请先在 .env 中配置 COOKIE_ENCRYPTION_KEY。")
    try:
        decoded = base64.urlsafe_b64decode(raw_key.encode("utf-8"))
        # 合法 Fernet key 必须是 32 字节的 urlsafe base64 编码值
        if len(decoded) == 32:
            return raw_key.encode("utf-8")
    except Exception:
        pass
    # 非 32 字节则走 SHA-256 派生，确保输出始终满足 Fernet 要求
    return base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())


def encrypt_secret(value: str) -> str:
    """加密明文字符串，返回 Fernet 密文。

    被 repositories.save_gitlab_credential 调用，
    用于加密 GitLab 密码后写入 rc_gitlab_credential.password_ciphertext。
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - 真实环境依赖
        raise RuntimeError("缺少 cryptography 依赖，请先安装 requirements.txt。") from exc
    return Fernet(fernet_key()).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """解密 Fernet 密文，返回明文字符串。

    被 repositories.get_saved_gitlab_config 调用，
    用于解密 rc_gitlab_credential.password_ciphertext 中的 GitLab 密码。
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - 真实环境依赖
        raise RuntimeError("缺少 cryptography 依赖，请先安装 requirements.txt。") from exc
    return Fernet(fernet_key()).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
