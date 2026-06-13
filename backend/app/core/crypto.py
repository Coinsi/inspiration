"""供应商密钥加密:配置 Fernet 密钥则加密,否则开发态明文(utf-8)。"""
import base64

from app.core.config import settings

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is None and settings.credentials_fernet_key:
        from cryptography.fernet import Fernet

        _fernet = Fernet(settings.credentials_fernet_key.encode())
    return _fernet


def encrypt(text: str) -> bytes:
    f = _get_fernet()
    if f:
        return f.encrypt(text.encode())
    return base64.b64encode(text.encode())  # 开发态:仅 base64,非真加密


def decrypt(data: bytes | None) -> str | None:
    if not data:
        return None
    f = _get_fernet()
    if f:
        return f.decrypt(data).decode()
    return base64.b64decode(data).decode()
