"""EasyPay-only HTTPS transport: checked DNS, pinned sockets, and no proxy/redirects."""

import http.client
import ipaddress
import json
import queue
import re
import socket
import ssl
import threading
import time
from decimal import Decimal
from urllib.parse import urlencode, urlsplit

from app.core.errors import ProviderError

_TIMEOUT = 15.0
_MAX_BODY = 1024 * 1024
_MAX_REQUEST = 64 * 1024
_ENDPOINTS = {"mapi.php", "api.php"}
_ADDRESS_ERROR = "Invalid EasyPay gateway address"
_REQUEST_ERROR = "EasyPay request could not be confirmed; query the original order before retrying"
# An OS resolver can ignore socket timeouts. Bound both caller latency and stranded workers.
_WORKERS = threading.BoundedSemaphore(8)
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+\Z")
_TRANSITION_NETWORKS = tuple(
    map(
        ipaddress.ip_network,
        (
            "::ffff:0:0/96",
            "64:ff9b::/96",
            "64:ff9b:1::/48",
            "2001::/32",
            "2002::/16",
        ),
    )
)


def normalize_api_base(value: str) -> str:
    """Normalize a public DNS HTTPS base without resolving it or accepting ambiguous paths."""
    try:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 2048
            or not value.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or any(char in value for char in "\\%?#")
        ):
            raise ValueError
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        labels = host.split(".")
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.netloc.lower() not in (host, host + ":443")
            or len(host) > 253
            or len(labels) < 2
            or any(not _LABEL.fullmatch(label) for label in labels)
            or not re.fullmatch(r"[a-z][a-z0-9-]*", labels[-1])
            or labels[-1]
            in {
                "localhost",
                "local",
                "internal",
                "lan",
                "home",
                "test",
                "invalid",
                "example",
                "onion",
            }
        ):
            raise ValueError
        path = parsed.path
        if "//" in path:
            raise ValueError
        parts = path.strip("/").split("/") if path.strip("/") else []
        if any(part in (".", "..") or not _SEGMENT.fullmatch(part) for part in parts):
            raise ValueError
        if parts and parts[-1].lower() in {"submit.php", "mapi.php", "api.php"}:
            parts.pop()
        return "https://" + host + ("/" + "/".join(parts) if parts else "")
    except (ValueError, TypeError, UnicodeError):
        raise ProviderError(_ADDRESS_ERROR) from None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _resolve(host: str) -> tuple[int, tuple]:
    records = socket.getaddrinfo(
        host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP
    )
    addresses = []
    for family, kind, protocol, _canonical, address in records:
        if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM:
            raise ValueError
        if protocol not in (0, socket.IPPROTO_TCP) or "%" in address[0]:
            raise ValueError
        ip = ipaddress.ip_address(address[0])
        if (
            not ip.is_global
            or ip.is_private
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_unspecified
            or (ip.version == 6 and ip.is_site_local)
            or (ip.version == 6 and any(ip in network for network in _TRANSITION_NETWORKS))
            or (ip.version == 4) != (family == socket.AF_INET)
        ):
            raise ValueError
        # Reconstruct the numeric destination; do not trust resolver port/scope fields.
        numeric = (str(ip), 443) if family == socket.AF_INET else (str(ip), 443, 0, 0)
        if (family, numeric) not in addresses:
            addresses.append((family, numeric))
    if not addresses:
        raise ValueError
    # No automatic retries: a payment mutation must never be submitted twice by the transport.
    return addresses[0]


class _SocketOwner:
    def __init__(self, deadline: float):
        self.deadline = deadline
        self.lock = threading.Lock()
        self.sock = None
        self.cancelled = False

    def attach(self, sock):
        with self.lock:
            if self.cancelled or time.monotonic() >= self.deadline:
                sock.close()
                raise TimeoutError
            self.sock = sock

    def close(self):
        with self.lock:
            self.cancelled = True
            sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def _tls_context() -> ssl.SSLContext:
    # Unlike create_default_context(), this does not enable SSLKEYLOGFILE from the environment.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    return context


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: tuple[int, tuple], owner: _SocketOwner):
        super().__init__(host, 443, timeout=_remaining(owner.deadline), context=_tls_context())
        self._address = address
        self._owner = owner
        self.set_debuglevel(0)

    def connect(self):
        family, destination = self._address
        raw = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self._owner.attach(raw)
        raw.settimeout(_remaining(self._owner.deadline))
        # Numeric sockaddr + the matching socket family prevents a second DNS resolution.
        raw.connect(destination)
        wrapped = self._context.wrap_socket(
            raw, server_hostname=self.host, do_handshake_on_connect=False
        )
        self._owner.attach(wrapped)
        self.sock = wrapped
        wrapped.settimeout(_remaining(self._owner.deadline))
        wrapped.do_handshake()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError


def _encode(params: dict[str, str] | None) -> str:
    if params is None:
        return ""
    if not isinstance(params, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in params.items()
    ):
        raise ValueError
    encoded = urlencode(params)
    if len(encoded) > _MAX_REQUEST:
        raise ValueError
    return encoded


def _perform(host, target, body, owner):
    _remaining(owner.deadline)
    address = _resolve(host)
    _remaining(owner.deadline)
    connection = _PinnedHTTPSConnection(host, address, owner)
    try:
        headers = {
            "Host": host,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "User-Agent": "Inspiration-EasyPay",
        }
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection.request(
            "POST" if body is not None else "GET", target, body=body, headers=headers
        )
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise ValueError
        lengths = response.headers.get_all("Content-Length", [])
        transfers = response.headers.get_all("Transfer-Encoding", [])
        encodings = response.headers.get_all("Content-Encoding", [])
        if (
            len(lengths) > 1
            or len(transfers) > 1
            or (lengths and transfers)
            or (transfers and transfers[0].lower().strip() != "chunked")
            or (encodings and encodings != ["identity"])
            or (
                lengths and (not re.fullmatch(r"[0-9]+", lengths[0]) or int(lengths[0]) > _MAX_BODY)
            )
        ):
            raise ValueError
        raw = response.read(_MAX_BODY + 1)
        if len(raw) > _MAX_BODY or (lengths and len(raw) != int(lengths[0])):
            raise ValueError
        _remaining(owner.deadline)
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=Decimal,
            parse_constant=_reject_constant,
        )
        if not isinstance(data, dict):
            raise ValueError
        return data
    finally:
        connection.close()


def request_json(
    base_url: str,
    endpoint: str,
    *,
    query: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
) -> dict:
    """One bounded direct HTTPS request; all failure details remain inside this module."""
    base = normalize_api_base(base_url)
    try:
        if endpoint not in _ENDPOINTS:
            raise ValueError
        parsed = urlsplit(base)
        encoded_query, encoded_form = _encode(query), _encode(form)
        target = parsed.path + "/" + endpoint + ("?" + encoded_query if encoded_query else "")
        body = encoded_form.encode("ascii") if form is not None else None
    except (TypeError, ValueError, UnicodeError):
        raise ProviderError(_REQUEST_ERROR) from None
    if not _WORKERS.acquire(blocking=False):
        raise ProviderError(_REQUEST_ERROR)
    owner = _SocketOwner(time.monotonic() + _TIMEOUT)
    result = queue.Queue(maxsize=1)

    def run():
        try:
            data = _perform(parsed.hostname, target, body, owner)
            result.put((True, data))
        except Exception:
            # Neither chained exceptions nor provider-controlled text cross the worker boundary.
            result.put((False, None))
        finally:
            owner.close()
            _WORKERS.release()

    worker = threading.Thread(target=run, name="easypay-https", daemon=True)
    try:
        worker.start()
    except RuntimeError:
        _WORKERS.release()
        raise ProviderError(_REQUEST_ERROR) from None
    try:
        success, data = result.get(timeout=_remaining(owner.deadline))
        _remaining(owner.deadline)
    except (queue.Empty, TimeoutError):
        success, data = False, None
    finally:
        owner.close()
    if not success:
        raise ProviderError(_REQUEST_ERROR)
    return data
