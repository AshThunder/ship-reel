"""Network guard: keep Ship Reel's browser on the public internet.

Every URL the model can supply (start url, navigate steps, cta_url) is checked here, and every
request the headless browser makes is filtered twice: :class:`HostGuard` (a Playwright
``context.route`` handler with a per-host DNS cache) aborts requests to blocked hosts, and
:class:`FilterProxy` (a local forward proxy Chromium is launched behind) checks every connection,
including redirect hops and websockets that ``route`` never sees, and pins it to the vetted IP so
DNS rebinding cannot swap in a private address. Hosts that resolve to loopback, private, link-local (including the
169.254.169.254 cloud metadata address), CGNAT, unspecified, multicast or reserved ranges, or the
IPv4-mapped / NAT64 forms of those, are refused.

Recording a local dev server is possible only when the user opts in outside the model's reach:
the ``allow_private_hosts`` plugin setting or the ``SHIPREEL_ALLOW_PRIVATE_HOSTS=1`` environment
variable. No tool argument can turn this off.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import threading
from typing import Callable
from urllib.parse import urlsplit

ENV_ALLOW = "SHIPREEL_ALLOW_PRIVATE_HOSTS"

_BLOCKED_NETS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8",        # unspecified / "this network"
    "10.0.0.0/8",       # RFC1918
    "100.64.0.0/10",    # CGNAT
    "127.0.0.0/8",      # loopback
    "169.254.0.0/16",   # link-local, cloud metadata
    "172.16.0.0/12",    # RFC1918
    "192.0.0.0/24",     # IETF protocol assignments
    "192.0.2.0/24",     # TEST-NET-1
    "192.168.0.0/16",   # RFC1918
    "198.18.0.0/15",    # benchmarking
    "198.51.100.0/24",  # TEST-NET-2
    "203.0.113.0/24",   # TEST-NET-3
    "224.0.0.0/4",      # multicast
    "240.0.0.0/4",      # reserved, incl. 255.255.255.255
    "::/128",           # unspecified
    "::1/128",          # loopback
    "fc00::/7",         # unique local
    "fe80::/10",        # link-local
    "fec0::/10",        # deprecated site-local
    "ff00::/8",         # multicast
    "100::/64",         # discard
    "2001:db8::/32",    # documentation
)]
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

_setting_reader: Callable[[], object] | None = None


class BlockedHost(ValueError):
    """Raised when a URL points at a host Ship Reel must not reach."""


def set_setting_reader(fn: Callable[[], object] | None) -> None:
    """Called by register(): reads the user's ``allow_private_hosts`` plugin setting."""
    global _setting_reader
    _setting_reader = fn


def _truthy(v: object) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def private_hosts_allowed() -> bool:
    """User opt-in only (env or plugin setting). Off by default."""
    if _truthy(os.environ.get(ENV_ALLOW, "")):
        return True
    if _setting_reader is not None:
        try:
            return _truthy(_setting_reader())
        except Exception:  # noqa: BLE001 - a broken config read must fail closed
            return False
    return False


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if int(ip) >> 32 == 0 and int(ip) > 1:  # deprecated IPv4-compatible ::a.b.c.d
        return ipaddress.IPv4Address(int(ip))
    return None


def ip_blocked(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if this address is loopback/private/link-local/CGNAT/unspecified/multicast/reserved."""
    addr = ipaddress.ip_address(str(ip).split("%", 1)[0]) if not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else ip
    if isinstance(addr, ipaddress.IPv6Address):
        v4 = _embedded_v4(addr)
        if v4 is not None and ip_blocked(v4):
            return True
    if any(addr in n for n in _BLOCKED_NETS if n.version == addr.version):
        return True
    return not addr.is_global or addr.is_multicast or addr.is_reserved


_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$", re.I)


def literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a URL host that is an IP literal. Refuses non-canonical forms browsers would still
    accept as IPs (decimal 2130706433, octal 0177.0.0.1, hex 0x7f.1, short 127.1)."""
    h = host.strip().lower().rstrip(".")
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if ":" in h:
        if "%" in h:
            raise BlockedHost(f"IPv6 zone ids are not allowed: {host!r}")
        try:
            return ipaddress.IPv6Address(h)
        except ValueError as exc:
            raise BlockedHost(f"not a valid IPv6 host: {host!r}") from exc
    labels = h.split(".")
    if labels and _NUMERIC_LABEL.match(labels[-1]):
        # A numeric last label means a browser would treat the host as an IPv4 address.
        if len(labels) == 4 and all(re.fullmatch(r"0|[1-9][0-9]{0,2}", p) for p in labels):
            try:
                return ipaddress.IPv4Address(h)
            except ValueError:
                pass
        raise BlockedHost(f"non-standard IP address host {host!r}; use a hostname or a dotted IPv4 address")
    return None


def _getaddrinfo(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [i[4][0] for i in infos]


def check_host(host: str, port: int | None = None, *, force: bool = False) -> list[str]:
    """Raise BlockedHost unless every address ``host`` resolves to is public; return the
    vetted addresses (empty when the user allowed private hosts)."""
    if not host:
        raise BlockedHost("url has no host")
    if private_hosts_allowed() and not force:
        return []
    h = host.strip().lower().rstrip(".")
    if h in ("localhost",) or h.endswith(".localhost"):
        raise BlockedHost(f"{host!r} is a local address; Ship Reel only records public pages")
    lit = literal_ip(h)
    if lit is not None:
        addrs = [str(lit)]
    else:
        try:
            addrs = _getaddrinfo(h, port or 443)
        except (socket.gaierror, UnicodeError, OSError) as exc:
            raise BlockedHost(f"could not resolve {host!r}: {exc}") from exc
        if not addrs:
            raise BlockedHost(f"could not resolve {host!r}")
    for a in addrs:
        if ip_blocked(a):
            raise BlockedHost(
                f"{host!r} resolves to {a}, a private, local or reserved address; Ship Reel only records "
                f"public pages (set {ENV_ALLOW}=1 or the allow_private_hosts plugin setting to record a local dev server)")
    return addrs


def check_url(url: str) -> None:
    """Validate the host part of an absolute http(s) URL. Raises BlockedHost."""
    parts = urlsplit(url)
    if "\\" in url:
        raise BlockedHost("backslashes are not allowed in urls")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise BlockedHost("urls with user:password@ are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise BlockedHost(f"bad port in {url!r}") from exc
    check_host(parts.hostname or "", port)


class HostGuard:
    """Per-browser-context request filter with a per-host DNS cache."""

    PASS_SCHEMES = ("data", "blob", "about")

    def __init__(self) -> None:
        self.allow_all = private_hosts_allowed()
        self._cache: dict[str, str | None] = {}
        self._lock = threading.Lock()
        self.blocked: list[str] = []      # request URLs aborted

    def reason(self, url: str) -> str | None:
        """None if the request may go out, else why it is blocked."""
        try:
            parts = urlsplit(url)
        except ValueError:
            return "unparseable url"
        scheme = parts.scheme.lower()
        if scheme in self.PASS_SCHEMES:
            return None
        if scheme not in ("http", "https", "ws", "wss"):
            return f"scheme {scheme!r} not allowed"
        if self.allow_all:
            return None
        key = (parts.netloc or "").lower()
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        try:
            check_url(url)
            why = None
        except BlockedHost as exc:
            why = str(exc)
        with self._lock:
            self._cache[key] = why
        return why

    # Playwright handlers --------------------------------------------------
    def handle_route(self, route) -> None:
        url = route.request.url
        why = self.reason(url)
        if why is None:
            route.continue_()
        else:
            self.blocked.append(url[:200])
            route.abort("blockedbyclient")

    def handle_ws(self, ws) -> None:
        if self.reason(ws.url) is None:
            ws.connect_to_server()
        else:
            self.blocked.append(ws.url[:200])
            ws.close()

    def install(self, ctx) -> None:
        ctx.route("**/*", self.handle_route)
        if hasattr(ctx, "route_web_socket"):
            ctx.route_web_socket(re.compile(r".*"), self.handle_ws)

    def explain(self, exc: BaseException) -> BaseException:
        """Turn Chromium's ERR_BLOCKED_BY_CLIENT into the guard's reason when we caused it."""
        msg = str(exc)
        if self.blocked and ("BLOCKED_BY_CLIENT" in msg or "ERR_TUNNEL" in msg or "ERR_PROXY" in msg):
            why = self.reason(self.blocked[-1]) or "blocked"
            return BlockedHost(why)
        return exc


# ---------------------------------------------------------------------------
# Filtering forward proxy. Playwright's route() never sees redirect hops, and Chromium resolves
# names itself (DNS rebinding), so the browser is pointed at this local proxy: every connection,
# including each redirect hop and websocket, is checked here and pinned to the vetted address.

class FilterProxy:
    MAX_HEAD = 64 * 1024

    def __init__(self) -> None:
        self.blocked: list[str] = []
        self._cache: dict[tuple[str, int], tuple[list[str], str | None]] = {}
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        self.port = self._sock.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._accept_loop, daemon=True).start()

    @property
    def server(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass

    def _vet(self, host: str, port: int) -> list[str]:
        key = (host.lower(), port)
        with self._lock:
            hit = self._cache.get(key)
        if hit is None:
            try:
                hit = (check_host(host, port, force=True), None)
            except BlockedHost as exc:
                hit = ([], str(exc))
            with self._lock:
                self._cache[key] = hit
        if hit[1]:
            self.blocked.append(f"{host}:{port}")
            raise BlockedHost(hit[1])
        return hit[0]

    def _connect(self, host: str, port: int) -> socket.socket:
        last: Exception | None = None
        for ip in self._vet(host, port):
            try:
                return socket.create_connection((ip, port), timeout=20)
            except OSError as exc:
                last = exc
        raise OSError(f"could not connect to {host}:{port}: {last}")

    def _accept_loop(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    BLOCK_HEADER = "X-Ship-Reel-Blocked"

    @classmethod
    def _reply(cls, conn: socket.socket, status: str) -> None:
        body = b"Blocked by Ship Reel: not a public address." if status.startswith("403") else b""
        extra = f"{cls.BLOCK_HEADER}: 1\r\nContent-Type: text/plain\r\n" if body else ""
        try:
            conn.sendall(f"HTTP/1.1 {status}\r\n{extra}Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        except OSError:
            pass

    def _serve(self, conn: socket.socket) -> None:
        upstream = None
        try:
            conn.settimeout(30)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(8192)
                if not chunk:
                    return
                buf += chunk
                if len(buf) > self.MAX_HEAD:
                    return self._reply(conn, "431 Request Header Fields Too Large")
            head, rest = buf.split(b"\r\n\r\n", 1)
            lines = head.decode("latin-1").split("\r\n")
            method, target, version = (lines[0].split(" ") + ["", ""])[:3]
            if method.upper() == "CONNECT":
                host, _, port_s = target.rpartition(":")
                host = host.strip("[]")
                port = int(port_s or 443)
                try:
                    upstream = self._connect(host, port)
                except BlockedHost:
                    return self._reply(conn, "403 Forbidden")
                except OSError:
                    return self._reply(conn, "502 Bad Gateway")
                conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
            else:
                parts = urlsplit(target)
                if parts.scheme != "http" or not parts.hostname:
                    return self._reply(conn, "400 Bad Request")
                if "@" in parts.netloc:
                    return self._reply(conn, "403 Forbidden")
                try:
                    upstream = self._connect(parts.hostname, parts.port or 80)
                except BlockedHost:
                    return self._reply(conn, "403 Forbidden")
                except OSError:
                    return self._reply(conn, "502 Bad Gateway")
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                keep = [h for h in lines[1:] if h.split(":", 1)[0].strip().lower()
                        not in ("proxy-connection", "proxy-authorization", "connection", "keep-alive")]
                out = "\r\n".join([f"{method} {path} {version}", *keep, "Connection: close"]) + "\r\n\r\n"
                upstream.sendall(out.encode("latin-1") + rest)
            self._pipe(conn, upstream)
        except Exception:  # noqa: BLE001 - one bad connection must not kill the proxy
            pass
        finally:
            for s in (conn, upstream):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass

    @staticmethod
    def _pipe(a: socket.socket, b: socket.socket) -> None:
        a.settimeout(None)
        b.settimeout(None)

        def pump(src: socket.socket, dst: socket.socket) -> None:
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        t = threading.Thread(target=pump, args=(b, a), daemon=True)
        t.start()
        pump(a, b)
        t.join(timeout=60)
