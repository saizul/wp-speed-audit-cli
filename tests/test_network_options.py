import pytest

from wp_speed_audit import audit, cli, network
from wp_speed_audit.audit import AuditResult


class _Raw:
    version = 11


class _Resp:
    url = "https://x.example/"
    status_code = 200
    history = []
    headers = {"Content-Encoding": "br"}
    text = "<html></html>"
    content = b"<html></html>"
    raw = _Raw()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_page_sends_custom_user_agent(monkeypatch):
    seen = []

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, **kwargs):
            seen.append((self.headers["User-Agent"], kwargs["timeout"]))
            return _Resp()

    monkeypatch.setattr(network.requests, "Session", FakeSession)
    network.fetch_page("https://x.example/", samples=2, timeout=7, user_agent="MyBot/1")
    assert seen == [("MyBot/1", 7), ("MyBot/1", 7)]


def test_default_user_agent_is_used(monkeypatch):
    calls = []
    monkeypatch.setattr(
        network.requests, "get", lambda url, **kw: calls.append(kw["headers"]["User-Agent"]) or _Resp()
    )
    assert network.supports_brotli("https://x.example/") is True
    assert network.supports_brotli("https://x.example/", user_agent="Z/2") is True
    assert calls == [network.USER_AGENT, "Z/2"]


def test_head_size_uses_user_agent(monkeypatch):
    seen = []

    class Head:
        headers = {"Content-Length": "123"}

    monkeypatch.setattr(
        network.requests, "head", lambda url, **kw: seen.append(kw["headers"]["User-Agent"]) or Head()
    )
    assert network.head_sizes(["https://x.example/a.png"], user_agent="UA/3") == {
        "https://x.example/a.png": 123
    }
    assert seen == ["UA/3"]


def test_run_audit_passes_user_agent_everywhere(monkeypatch):
    page = network.PageFetch(
        final_url="https://x.example/",
        status_code=200,
        redirects=0,
        headers={},
        html='<html><body><img src="/a.png"></body></html>',
        http_version="HTTP/1.1",
        ttfb_ms=[10.0],
        total_ms=[20.0],
    )
    got = {}
    monkeypatch.setattr(audit, "fetch_page", lambda url, **kw: got.setdefault("fetch", kw["user_agent"]) and page)
    monkeypatch.setattr(audit, "supports_brotli", lambda url, timeout, ua: got.setdefault("br", ua) and False)
    monkeypatch.setattr(audit, "head_sizes", lambda urls, **kw: got.setdefault("head", kw["user_agent"]) and {})
    audit.run_audit("https://x.example/", samples=1, user_agent="UA/9")
    assert got == {"fetch": "UA/9", "br": "UA/9", "head": "UA/9"}


def test_cli_forwards_user_agent_and_timeout(monkeypatch, capsys):
    got = {}

    def fake(url, **kw):
        got.update(kw)
        return AuditResult(url=url)

    monkeypatch.setattr(cli, "run_audit", fake)
    assert cli.main(["x.example", "--json", "--user-agent", "Bot/1", "--timeout", "5.5"]) == 0
    assert got["user_agent"] == "Bot/1"
    assert got["timeout"] == 5.5


def test_cli_default_user_agent(monkeypatch):
    got = {}
    monkeypatch.setattr(cli, "run_audit", lambda url, **kw: got.update(kw) or AuditResult(url=url))
    cli.main(["x.example", "--json"])
    assert got["user_agent"] == network.USER_AGENT


@pytest.mark.parametrize("value", ["0", "-3"])
def test_cli_rejects_non_positive_timeout(value):
    with pytest.raises(SystemExit):
        cli.main(["x.example", "--timeout", value])
