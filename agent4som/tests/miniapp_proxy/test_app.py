"""miniapp_proxy 路径归一化与转发测试（62 行，此前零覆盖）。

_target_path 是纯逻辑直接测；proxy 转发用 httpx MockTransport 拦截上游请求，
不依赖真实 Hermes 网关。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from miniapp_proxy import app as app_module
from miniapp_proxy.app import _target_path, app


# ── _target_path 纯逻辑 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        # 前缀剥离（长的优先匹配）
        ("/accapi/dgx-agentapi/api/methods/x", "/api/methods/x"),
        ("/accapi/api/methods/x", "/api/methods/x"),
        ("/accapi", "/"),
        ("/accapi/dgx-agentapi", "/"),
        # 重复斜杠折叠（多层反代实测出现的 ////api 形态）
        ("////api/methods/x", "/api/methods/x"),
        ("/accapi//api//methods/x", "/api/methods/x"),
        # 无前缀路径原样透传
        ("/api/miniapp/login", "/api/miniapp/login"),
        ("/health", "/health"),
        ("/", "/"),
    ],
)
def test_target_path_normalization(raw: str, expected: str):
    assert _target_path(raw) == expected


def test_target_path_does_not_strip_partial_prefix():
    """/accapix 不是 /accapi 前缀，不应被剥离（startswith(prefix + "/") 守卫）。"""
    assert _target_path("/accapix/api") == "/accapix/api"


# ── proxy 转发（httpx MockTransport 拦截上游） ─────────────────────


@pytest.fixture
def mock_upstream(monkeypatch):
    """替换全局 AsyncClient 为 MockTransport 版本，记录收到的请求。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    monkeypatch.setattr(
        app_module, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return seen


def test_proxy_strips_accapi_prefix(mock_upstream):
    with TestClient(app) as tc:
        r = tc.get("/accapi/dgx-agentapi/api/methods/list")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # 上游收到的是剥离前缀、折叠斜杠后的规范路径
    assert mock_upstream[0].url.path == "/api/methods/list"


def test_proxy_forwards_method_body_and_query(mock_upstream):
    with TestClient(app) as tc:
        r = tc.post(
            "/accapi/api/miniapp/login?instance_id=jwc",
            json={"code": "abc"},
            headers={"Authorization": "Bearer tok"},
        )
    assert r.status_code == 200
    upstream = mock_upstream[0]
    assert upstream.method == "POST"
    assert str(upstream.url).endswith("/api/miniapp/login?instance_id=jwc")
    assert upstream.headers["authorization"] == "Bearer tok"
    assert b'"code"' in upstream.read()


def test_proxy_returns_502_when_upstream_down(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("hermes gateway unavailable")

    monkeypatch.setattr(
        app_module, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with TestClient(app) as tc:
        r = tc.get("/api/methods/list")
    assert r.status_code == 502
    assert r.json() == {"error": "hermes gateway unavailable"}


def test_proxy_passes_status_code_through(mock_upstream):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    app_module.client._transport.handler = handler  # type: ignore[attr-defined]
    with TestClient(app) as tc:
        r = tc.get("/accapi/api/whatever")
    assert r.status_code == 404
