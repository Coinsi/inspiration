"""Pinned HTTPS protocol tests: DNS, TCP and TLS are simulated; no gateway is contacted."""

import io
import ipaddress
import socket
import ssl
import threading
import time
import traceback
from decimal import Decimal
from urllib.parse import parse_qs

import pytest

from app.core.errors import ProviderError
from app.modules.billing import easypay_http as transport

BASE = "https://gateway.example.com/pay"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(transport.socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(transport.socket, "socket", forbidden)


def record(address):
    family = socket.AF_INET if ipaddress.ip_address(address).version == 4 else socket.AF_INET6
    target = (address, 443) if family == socket.AF_INET else (address, 443, 0, 0)
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", target


class FakeSocket:
    def __init__(self, raw, family):
        self.raw = raw
        self.family = family
        self.sent = b""
        self.destination = None
        self.closed = False
        self.handshake = False

    def settimeout(self, value):
        assert 0 < value <= transport._TIMEOUT

    def connect(self, destination):
        self.destination = destination

    def do_handshake(self):
        self.handshake = True

    def sendall(self, data):
        self.sent += data

    def makefile(self, _mode):
        return io.BytesIO(self.raw)

    def shutdown(self, _how):
        self.closed = True

    def close(self):
        self.closed = True


def http_fixture(monkeypatch, body=b'{"code":1}', headers=None, status=200, addresses=None):
    headers = headers if headers is not None else {"Content-Length": str(len(body))}
    header_lines = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    raw = f"HTTP/1.1 {status} Response\r\n{header_lines}\r\n".encode() + body
    sockets, resolutions, names = [], [], []

    def resolve(host, port, family, kind, protocol):
        resolutions.append((host, port, family, kind, protocol))
        return [record(value) for value in (addresses or ["8.8.8.8"])]

    def create_socket(family, kind, protocol):
        assert kind == socket.SOCK_STREAM and protocol == socket.IPPROTO_TCP
        sock = FakeSocket(raw, family)
        sockets.append(sock)
        return sock

    class Context:
        def wrap_socket(self, sock, *, server_hostname, do_handshake_on_connect):
            assert do_handshake_on_connect is False
            names.append(server_hostname)
            return sock

    monkeypatch.setattr(transport.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(transport.socket, "socket", create_socket)
    monkeypatch.setattr(transport, "_tls_context", Context)
    return sockets, resolutions, names


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://Gateway.Example.com:443/", "https://gateway.example.com"),
        ("https://gateway.example.com/pay/submit.php", BASE),
        ("https://gateway.example.com/pay/mapi.php/", BASE),
        ("https://gateway.example.com/pay/api.php", BASE),
        ("https://gateway.example.com/pay/v1.2/", BASE + "/v1.2"),
        ("https://xn--bcher-kva.example.com/api.php", "https://xn--bcher-kva.example.com"),
    ],
)
def test_normalize_base_without_dns(value, expected):
    assert transport.normalize_api_base(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://gateway.example.com",
        "https://gateway.example.com:80",
        "https://8.8.8.8",
        "https://[2606:4700:4700::1111]",
        "https://user:secret@gateway.example.com",
        "https://gateway.example.com?key=secret",
        "https://gateway.example.com?",
        "https://gateway.example.com#",
        "https://gateway.example.com:0443",
        "https://localhost",
        "https://a.local",
        "https://a.internal",
        "https://a.test",
        "https://a.invalid",
        "https://gateway.example.com.",
        "https://gateway..example.com",
        "https://-bad.example.com",
        "https://127.1",
        "https://0x7f000001",
        "https://2130706433",
        "https://0177.0.0.1",
        "https://gateway.example.com\\@127.0.0.1",
        "https://gateway.example.com/%2e%2e/",
        "https://gateway.example.com/../",
        "https://gateway.example.com/a/./b",
        "https://gateway.example.com/a//b",
        "https://gateway.example.com/a;key=secret",
        "https://gateway.example.com/路径",
        "https://gateway.example.com/\n",
        " https://example.com",
        "https://gateway.example.com:\t443",
        "https://gateway.example.com/%252f",
        None,
    ],
)
def test_reject_ambiguous_or_nonpublic_bases_without_echoing_secrets(value):
    with pytest.raises(ProviderError) as error:
        transport.normalize_api_base(value)
    assert error.value.message == "Invalid EasyPay gateway address"
    assert error.value.detail == {}
    assert "secret" not in "".join(traceback.format_exception(error.value.with_traceback(None)))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.1.1",
        "192.168.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "192.0.2.1",
        "198.18.0.1",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "::",
        "fe80::1",
        "fec0::1",
        "fc00::1",
        "ff02::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",
        "::ffff:8.8.8.8",
        "64:ff9b::7f00:1",
        "2002:7f00:1::",
        "2001::1",
    ],
)
def test_rejects_every_unsafe_dns_answer_even_mixed_with_public_address(monkeypatch, address):
    sockets, resolutions, _ = http_fixture(monkeypatch, addresses=["8.8.8.8", address])
    with pytest.raises(ProviderError):
        transport.request_json(BASE, "api.php", query={"key": "private-secret"})
    assert len(resolutions) == 1
    assert sockets == []


def test_numeric_connection_uses_original_tls_sni_host_and_ignores_environment_proxy(monkeypatch):
    sockets, resolutions, names = http_fixture(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://secret@127.0.0.1:9999")
    monkeypatch.setenv("ALL_PROXY", "http://secret@127.0.0.1:9999")
    result = transport.request_json(BASE, "api.php", query={"act": "order", "key": "a&b?"})
    assert result == {"code": 1}
    assert len(resolutions) == len(sockets) == 1
    assert resolutions[0][0] == "gateway.example.com"
    assert sockets[0].destination == ("8.8.8.8", 443)
    assert names == ["gateway.example.com"]
    assert sockets[0].handshake and sockets[0].closed
    request = sockets[0].sent.decode()
    assert request.startswith("GET /pay/api.php?act=order&key=a%26b%3F HTTP/1.1\r\n")
    assert "Host: gateway.example.com\r\n" in request
    assert "CONNECT " not in request and "Proxy-Authorization" not in request


def test_rebinding_is_checked_on_each_request_and_never_re_resolved_for_connect(monkeypatch):
    sockets, _, _ = http_fixture(monkeypatch)
    hosts = []

    def resolve(host, *_args):
        hosts.append(host)
        return [record("8.8.8.8" if len(hosts) == 1 else "127.0.0.1")]

    monkeypatch.setattr(transport.socket, "getaddrinfo", resolve)
    transport.request_json(BASE, "api.php")
    with pytest.raises(ProviderError):
        transport.request_json(BASE, "api.php")
    assert hosts == ["gateway.example.com", "gateway.example.com"]
    assert len(sockets) == 1 and sockets[0].destination == ("8.8.8.8", 443)


def test_public_ipv6_is_pinned_without_scope_or_resolver_port(monkeypatch):
    sockets, _, names = http_fixture(monkeypatch, addresses=["2606:4700:4700::1111"])
    transport.request_json(BASE, "mapi.php", form={})
    assert sockets[0].family == socket.AF_INET6
    assert sockets[0].destination == ("2606:4700:4700::1111", 443, 0, 0)
    assert names == ["gateway.example.com"]
    assert sockets[0].sent.startswith(b"POST /pay/mapi.php HTTP/1.1")


def test_post_supports_separate_get_and_form_parameters_with_exact_decimal_json(monkeypatch):
    sockets, _, _ = http_fixture(monkeypatch, b'{"code":1,"money":0.01,"ok":true}')
    data = transport.request_json(
        BASE,
        "api.php",
        query={"act": "refund"},
        form={"pid": "123", "key": "secret&+", "money": "0.01"},
    )
    header, body = sockets[0].sent.split(b"\r\n\r\n", 1)
    assert header.startswith(b"POST /pay/api.php?act=refund HTTP/1.1")
    assert b"Content-Type: application/x-www-form-urlencoded" in header
    assert parse_qs(body.decode()) == {"pid": ["123"], "key": ["secret&+"], "money": ["0.01"]}
    assert data["money"] == Decimal("0.01") and type(data["code"]) is int
    assert type(data["ok"]) is bool


@pytest.mark.parametrize(
    "endpoint",
    [
        "submit.php",
        "/api.php",
        "../api.php",
        "api.php?key=x",
        "https://127.0.0.1/api.php",
        None,
        [],
    ],
)
def test_only_fixed_endpoints_are_allowed(endpoint):
    with pytest.raises(ProviderError):
        transport.request_json(BASE, endpoint)


@pytest.mark.parametrize(
    "body",
    [
        b'{"a":1,"a":2}',
        b'{"a":{"key":1,"key":2}}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":-Infinity}',
        b"[]",
        b"true",
        b"null",
        b"1",
        b'{"a":1} trailing',
        b"\xff",
        b'{"a":',
    ],
)
def test_requires_strict_utf8_json_object_without_duplicate_or_nonfinite_values(monkeypatch, body):
    http_fixture(monkeypatch, body)
    with pytest.raises(ProviderError):
        transport.request_json(BASE, "api.php")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 400, 500])
def test_redirects_and_errors_are_not_followed_or_echoed(monkeypatch, status):
    sockets, resolutions, _ = http_fixture(
        monkeypatch,
        b"response-private-secret",
        status=status,
        headers={"Location": "https://127.0.0.1/private-secret"},
    )
    with pytest.raises(ProviderError) as error:
        transport.request_json(BASE, "api.php", query={"key": "query-private-secret"})
    formatted = "".join(traceback.format_exception(error.value.with_traceback(None)))
    assert "private-secret" not in formatted and "127.0.0.1" not in formatted
    assert len(sockets) == len(resolutions) == 1


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        (b"", {"Content-Length": str(transport._MAX_BODY + 1)}),
        (b"x" * (transport._MAX_BODY + 1), {}),
        (b"{}", {"Content-Encoding": "gzip"}),
        (b"{}", {"Content-Length": "20"}),
        (b"{}", {"Content-Length": "2", "Transfer-Encoding": "chunked"}),
    ],
    ids=["declared-large", "stream-large", "compressed", "truncated", "ambiguous"],
)
def test_response_size_encoding_and_framing_are_bounded(monkeypatch, body, headers):
    sockets, _, _ = http_fixture(monkeypatch, body, headers=headers)
    with pytest.raises(ProviderError):
        transport.request_json(BASE, "api.php")
    assert sockets[0].closed


def test_chunked_json_and_exact_body_limit_are_supported(monkeypatch):
    body = b"{}" + b" " * (transport._MAX_BODY - 2)
    chunk = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
    http_fixture(monkeypatch, chunk, headers={"Transfer-Encoding": "chunked"})
    assert transport.request_json(BASE, "api.php") == {}


def test_duplicate_content_lengths_are_rejected(monkeypatch):
    sockets, _, _ = http_fixture(monkeypatch)
    original_makefile = FakeSocket.makefile

    def makefile(sock, mode):
        sock.raw = sock.raw.replace(b"\r\n\r\n", b"\r\nContent-Length: 10\r\n\r\n")
        return original_makefile(sock, mode)

    monkeypatch.setattr(FakeSocket, "makefile", makefile)
    with pytest.raises(ProviderError):
        transport.request_json(BASE, "api.php")
    assert sockets[0].closed


def test_worker_capacity_exhaustion_rejects_without_network(monkeypatch):
    monkeypatch.setattr(transport, "_WORKERS", threading.BoundedSemaphore(0))
    with pytest.raises(ProviderError):
        transport.request_json(BASE, "api.php")


def test_tls_context_verifies_certificates_and_hostname_without_key_logging(monkeypatch):
    monkeypatch.setenv("SSLKEYLOGFILE", "must-not-create-this-secret-file")
    context = transport._tls_context()
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.keylog_filename is None


def test_tls_failure_is_not_retried_and_does_not_leak_exception(monkeypatch):
    sockets, _, _ = http_fixture(monkeypatch)

    class Context:
        def wrap_socket(self, *_args, **_kwargs):
            raise ssl.SSLCertVerificationError("private-secret-certificate")

    monkeypatch.setattr(transport, "_tls_context", Context)
    with pytest.raises(ProviderError) as error:
        transport.request_json(BASE, "api.php", form={"key": "private-secret"})
    assert "private-secret" not in "".join(
        traceback.format_exception(error.value.with_traceback(None))
    )
    assert len(sockets) == 1 and sockets[0].closed and not sockets[0].sent


def test_dns_deadline_is_bounded_and_late_resolution_cannot_connect(monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    monkeypatch.setattr(transport, "_TIMEOUT", 0.03)

    def resolve(*_args):
        release.wait(2)
        finished.set()
        return [record("8.8.8.8")]

    monkeypatch.setattr(transport.socket, "getaddrinfo", resolve)
    start = time.monotonic()
    try:
        with pytest.raises(ProviderError):
            transport.request_json(BASE, "api.php")
        assert time.monotonic() - start < 0.5
    finally:
        release.set()
        assert finished.wait(1)


def test_slow_response_is_interrupted_at_total_deadline(monkeypatch):
    sockets, _, _ = http_fixture(monkeypatch)
    monkeypatch.setattr(transport, "_TIMEOUT", 0.05)
    release = threading.Event()
    entered = threading.Event()
    finished = threading.Event()
    original_makefile = FakeSocket.makefile

    class SlowBody(io.BytesIO):
        def read(self, *_args):
            entered.set()
            release.wait(2)
            finished.set()
            return b""

    def makefile(sock, mode):
        stream = original_makefile(sock, mode)
        return SlowBody(stream.getvalue())

    def shutdown(sock, _how):
        sock.closed = True
        release.set()

    monkeypatch.setattr(FakeSocket, "makefile", makefile)
    monkeypatch.setattr(FakeSocket, "shutdown", shutdown)
    start = time.monotonic()
    with pytest.raises(ProviderError):
        transport.request_json(BASE, "api.php", form={"key": "secret"})
    assert entered.is_set() and finished.wait(1)
    assert time.monotonic() - start < 0.5
    assert sockets[0].closed
