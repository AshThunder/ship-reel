import http.server
import threading

import pytest

from ship_reel.engine import netguard as N
from ship_reel.engine import plan as P

BLOCKED = [
    "127.0.0.1", "127.8.9.10", "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.0.1",
    "169.254.169.254", "169.254.1.1", "100.64.0.1", "100.127.255.254", "0.0.0.0", "224.0.0.1",
    "240.0.0.1", "255.255.255.255", "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254", "64:ff9b::7f00:1", "2002:7f00:1::1",
]
PUBLIC = ["93.184.216.34", "8.8.8.8", "1.1.1.1", "172.32.0.1", "100.128.0.1", "2606:4700:4700::1111",
          "::ffff:8.8.8.8"]


@pytest.mark.parametrize("ip", BLOCKED)
def test_blocked_ranges(ip):
    assert N.ip_blocked(ip)


@pytest.mark.parametrize("ip", PUBLIC)
def test_public_ranges(ip):
    assert not N.ip_blocked(ip)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost:3000", "localhost:8080", "http://app.localhost/",
    "http://10.0.0.8/", "https://172.16.5.4", "http://192.168.1.1/admin", "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.7/", "http://[::1]:8000/", "http://[fe80::1]/", "http://[::ffff:127.0.0.1]/",
    "http://0.0.0.0:8000/", "http://[::]/",
    # non-standard IP spellings
    "http://2130706433/", "http://0177.0.0.1/", "http://0x7f.0.0.1/", "http://127.1/", "http://0x7f000001/",
    # userinfo tricks
    "http://example.com@127.0.0.1/", "http://user:pw@example.com/",
    # hostnames resolving into blocked ranges (fake DNS in conftest)
    "https://internal.test/", "https://metadata.test/", "https://mixed.test/", "https://mapped.test/",
    "https://cgnat.test/",
])
def test_check_url_refuses(url):
    with pytest.raises(P.PlanError):
        P.check_url(url)


def test_public_host_passes():
    assert P.check_url("example.com") == "https://example.com"
    assert P.check_url("https://93.184.216.34/x") == "https://93.184.216.34/x"


def test_navigate_and_cta_checked():
    with pytest.raises(P.PlanError):
        P.normalize_plan({"url": "https://x.dev", "steps": [{"action": "navigate", "url": "http://169.254.169.254/"}]})


def test_opt_in_is_env_or_setting_only(monkeypatch):
    assert not N.private_hosts_allowed()
    monkeypatch.setenv(N.ENV_ALLOW, "1")
    assert P.check_url("http://localhost:3000") == "http://localhost:3000"
    monkeypatch.delenv(N.ENV_ALLOW)
    N.set_setting_reader(lambda: True)
    assert N.private_hosts_allowed()
    N.set_setting_reader(lambda: (_ for _ in ()).throw(RuntimeError("bad config")))
    assert not N.private_hosts_allowed()  # fails closed
    N.set_setting_reader(None)


def test_guard_decisions_and_cache(monkeypatch):
    calls = []

    def fake(host, port):
        calls.append(host)
        return {"internal.test": ["10.0.0.5"]}.get(host, ["93.184.216.34"])

    monkeypatch.setattr(N, "_getaddrinfo", fake)
    g = N.HostGuard()
    assert g.reason("https://example.com/a.js") is None
    assert g.reason("https://example.com/b.css") is None
    assert calls.count("example.com") == 1  # cached per host
    assert g.reason("https://internal.test/") is not None
    assert g.reason("http://127.0.0.1:9/") is not None
    assert g.reason("file:///etc/passwd") is not None
    assert g.reason("data:text/plain,hi") is None


# --- real browser: route abort and proxy (covers redirects) ------------------------------------

def _server(body=b"<p>secret</p>", redirect_to=None):
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            if redirect_to and self.path == "/r":
                self.send_response(302)
                self.send_header("Location", redirect_to)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, hits


def _browser_or_skip():
    pw = pytest.importorskip("playwright.sync_api")
    return pw.sync_playwright


def test_route_aborts_private_requests():
    sync_playwright = _browser_or_skip()
    from ship_reel.engine import browser as B
    srv, hits = _server()
    url = f"http://127.0.0.1:{srv.server_port}/"
    with sync_playwright() as p:
        try:
            br, ctx, page, net = B._new_page(p)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        try:
            with pytest.raises(N.BlockedHost):
                net.goto(page, url)
            # a public page's subresource / fetch to a private host is aborted too
            page = ctx.new_page()
            res = page.evaluate(f"fetch('{url}secret').then(() => 'ok', () => 'blocked')")
            assert res == "blocked"
            assert any("127.0.0.1" in b for b in net.guard.blocked)
        finally:
            br.close()
            net.close()
    srv.shutdown()
    assert hits == []  # nothing reached the private server


def test_proxy_blocks_redirect_hop_into_private_range(monkeypatch):
    """route() never sees redirect hops; the filtering proxy must stop them."""
    _browser_or_skip()
    priv, priv_hits = _server()
    # Pretend "public.test" is a public host whose redirect points at a loopback server.
    pub, pub_hits = _server(redirect_to=f"http://127.0.0.1:{priv.server_port}/secret")
    monkeypatch.setattr(N, "_getaddrinfo", lambda h, port: ["127.0.0.1"] if h == "public.test" else ["93.184.216.34"])
    real_blocked = N.ip_blocked
    # public.test resolves to 127.0.0.1 for the test server, so whitelist only that host/port pair
    orig_check = N.check_host

    def check(host, port=None, *, force=False):
        if host == "public.test" and port == pub.server_port:
            return ["127.0.0.1"]
        return orig_check(host, port, force=force)

    monkeypatch.setattr(N, "check_host", check)
    proxy = N.FilterProxy()
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy.server}))
    with pytest.raises(Exception):
        opener.open(f"http://public.test:{pub.server_port}/r", timeout=10).read()
    proxy.close()
    assert pub_hits == ["/r"] and priv_hits == []
    assert any(str(priv.server_port) in b for b in proxy.blocked)
    assert real_blocked("127.0.0.1")
    pub.shutdown()
    priv.shutdown()


def test_browser_redirect_into_private_range_blocked(monkeypatch):
    sync_playwright = _browser_or_skip()
    from ship_reel.engine import browser as B
    priv, priv_hits = _server()
    pub, pub_hits = _server(redirect_to=f"http://127.0.0.1:{priv.server_port}/secret")
    orig_check = N.check_host

    def check(host, port=None, *, force=False):
        if host == "public.test":
            return ["127.0.0.1"]  # stands in for a public site
        return orig_check(host, port, force=force)

    monkeypatch.setattr(N, "check_host", check)
    with sync_playwright() as p:
        try:
            br, ctx, page, net = B._new_page(p)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        try:
            with pytest.raises(N.BlockedHost, match="redirected"):
                net.goto(page, f"http://public.test:{pub.server_port}/r")
            assert "secret" not in page.content()
        finally:
            br.close()
            net.close()
    pub.shutdown()
    priv.shutdown()
    assert pub_hits == ["/r"] and priv_hits == []


def test_opt_in_lets_browser_reach_local_server(monkeypatch):
    sync_playwright = _browser_or_skip()
    from ship_reel.engine import browser as B
    monkeypatch.setenv(N.ENV_ALLOW, "1")
    srv, hits = _server()
    with sync_playwright() as p:
        try:
            br, ctx, page, net = B._new_page(p)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        try:
            net.goto(page, f"http://127.0.0.1:{srv.server_port}/")
            assert "secret" in page.content()
        finally:
            br.close()
            net.close()
    srv.shutdown()


def test_password_field_refused_at_record_time():
    sync_playwright = _browser_or_skip()
    from ship_reel.engine import browser as B
    with sync_playwright() as p:
        try:
            br, ctx, page, net = B._new_page(p)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        try:
            # selector text gives no hint it is a password box; the element type does
            page.set_content('<input id="code" aria-label="Access code" type="password">'
                             '<input id="q" aria-label="Search docs">')
            d = B.Driver(page, ctx, mode="dry")
            step = {"action": "type", "target": "css=#code", "value": "hunter2", "caption": "x", "dwell": 0.3}
            with pytest.raises(B.StepFailed, match="password"):
                d.do_step(step)
            assert page.input_value("#code") == ""
            ok = d.do_step({**step, "target": "css=#q", "value": "agents"})
            assert page.input_value("#q") == "agents" and ok is not None
        finally:
            br.close()
            net.close()
