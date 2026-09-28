"""小程序 VM 入口代理：/accapi/* → hermes gateway (127.0.0.1:8010)。

宿主 nginx 将 /accapi/ 转发到本服务（VM :8000），本服务再转发给
VM 内 hermes 的 miniapp-platform 原生适配器。兼容带/不带 /accapi 前缀的路径。
"""

import logging
import re

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="miniapp-proxy", docs_url=None, redoc_url=None)

UPSTREAM = "http://127.0.0.1:8010"
# 需要剥离的入口前缀，**长的必须排在前面**（先匹配 /accapi/dgx-agentapi 再 /accapi）
STRIP_PREFIXES = ("/accapi/dgx-agentapi", "/accapi")

# LLM 生成耗时长，上游（hermes）连接不设超时，由宿主 nginx read_timeout 控制
client = httpx.AsyncClient(timeout=None)


def _target_path(path: str) -> str:
    """归一化后剥离入口前缀，其余路径原样转发。

    归一化的必要性：请求要穿过「校园网关 -> acc-svr nginx -> SSH 隧道」多层反代，
    实测出现过 ``////api/methods/x`` 这种重复斜杠（前缀被逐层替换所致）。
    这里统一折叠重复斜杠并剥离已知前缀，使上游始终收到规范的 ``/api/...``。
    """
    path = re.sub(r"/{2,}", "/", path)
    for prefix in STRIP_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return path[len(prefix):] or "/"
    return path


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy(path: str, request: Request) -> Response:
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    try:
        upstream = await client.request(
            request.method,
            UPSTREAM + _target_path(request.url.path),
            params=request.query_params,
            headers=headers,
            content=await request.body(),
        )
    except httpx.RequestError as exc:
        logger.error("upstream hermes request failed: %s", exc)
        return JSONResponse({"error": "hermes gateway unavailable"}, status_code=502)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers={
            k: v
            for k, v in upstream.headers.items()
            if k.lower() not in ("content-length", "content-encoding", "transfer-encoding", "connection")
        },
    )
