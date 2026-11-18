"""Validate destinations for account-supplied model connections before contacting them."""

import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.errors import CapabilityUnsupported, ProviderError
from app.modules.generation.provider_templates import listing


def validate_endpoint(db, endpoint, row=None, *, resolve=False):
    if row and row.imported_endpoint and endpoint == row.imported_endpoint:
        return
    if any(t["endpoint"] == endpoint for t in listing(db)):
        return

    url = urlsplit(endpoint)
    host = (url.hostname or "").lower().rstrip(".")
    unavailable = "个人服务需要公网 HTTPS 地址。自托管内网服务可使用管理员配置的服务商地址。"
    if url.scheme != "https" or not host or "%" in host:
        raise CapabilityUnsupported(unavailable)
    try:
        port = url.port or 443
    except ValueError:
        raise CapabilityUnsupported("服务地址的端口无效") from None
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        if "." not in host or host.endswith((".localhost", ".local", ".internal")):
            raise CapabilityUnsupported(unavailable) from None
        if not resolve:
            return
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(record[4][0]) for record in records]
        except (OSError, ValueError):
            raise ProviderError("无法解析服务地址，请核对供应商域名") from None
    if not addresses or any(not ip.is_global or ip.is_multicast for ip in addresses):
        raise CapabilityUnsupported(unavailable)
