"""WeChat mini program HTTP transport for Hermes' native Gateway agent."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
import uuid
import zipfile
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
_UPLOAD_CONTEXT_MAX_CHARS = 12_000
_UPLOAD_ALLOWED_EXTENSIONS = {".docx", ".xlsx", ".csv", ".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg"}
_UPLOAD_MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# 原文预览截断长度（§4.7.2：只返回前 2 万字，`truncated=true` 时前端提示）
_KNOWLEDGE_PREVIEW_LIMIT = 20_000

# 学籍信息（"我的档案"）字段：student_archive 列名 → Excel 表头别名。
# 表头按名称定位（顺序/多余列忽略），与 设计文档的字段表一一对应。
_STUDENT_ARCHIVE_FIELDS: list[tuple[str, tuple[str, ...]]] = [
    ("student_id", ("学号", "工号")),
    ("name", ("姓名",)),
    ("gender", ("性别",)),
    ("birth_date", ("出生日期",)),
    ("ethnicity", ("民族",)),
    ("political_status", ("政治面貌",)),
    ("residence_college", ("所属书院", "书院")),
    ("grade", ("年级",)),
    ("school", ("学院",)),
    ("department", ("系",)),
    ("host_department", ("托管院系",)),
    ("major", ("专业",)),
    ("major_direction", ("专业方向",)),
    ("study_years", ("学制",)),
    ("class_name", ("班级",)),
    ("enrolled", ("是否在籍",)),
    ("enroll_date", ("入学日期",)),
    ("enroll_grade", ("入学年级",)),
    ("enroll_major", ("入学专业",)),
    ("phone", ("个人手机", "手机号", "手机号码", "电话")),
    ("email", ("个人邮箱", "邮箱")),
    ("emergency_contact", ("紧急联系人",)),
    ("emergency_phone", ("紧急联系方式",)),
]
_STUDENT_ARCHIVE_COLUMNS = [col for col, _ in _STUDENT_ARCHIVE_FIELDS]
# 档案可编辑字段（不含主键 student_id；管理员 PUT 时按此校验）
_STUDENT_ARCHIVE_EDITABLE = [c for c in _STUDENT_ARCHIVE_COLUMNS if c != "student_id"]
# 学生"我的档案"展示字段顺序（中文标签 + 列名）
_STUDENT_ARCHIVE_DISPLAY = [
    ("学号", "student_id"), ("姓名", "name"), ("性别", "gender"),
    ("出生日期", "birth_date"), ("民族", "ethnicity"), ("政治面貌", "political_status"),
    ("所属书院", "residence_college"), ("年级", "grade"), ("学院", "school"),
    ("系", "department"), ("托管院系", "host_department"), ("专业", "major"),
    ("专业方向", "major_direction"), ("学制", "study_years"), ("班级", "class_name"),
    ("是否在籍", "enrolled"), ("入学日期", "enroll_date"), ("入学年级", "enroll_grade"),
    ("入学专业", "enroll_major"), ("个人手机", "phone"), ("个人邮箱", "email"),
    ("紧急联系人", "emergency_contact"), ("紧急联系方式", "emergency_phone"),
]


def _strip_markup(text: str) -> str:
    """回复正文 → 纯文本（执行轨迹的结论按纯文本渲染）。"""
    plain = re.sub(r"<[^>]+>", "", str(text or ""))
    plain = plain.replace("|", " ")
    plain = re.sub(r"[ \t]+", " ", plain)
    plain = re.sub(r"\n\s*\n+", "\n", plain)
    return plain.strip()


def _runtime_value(value: Any, *, name: str, default: Any) -> str:
    """Resolve one adapter setting without relying on Gateway YAML expansion."""
    raw = str(default if value in (None, "") else value)
    expanded = os.path.expandvars(raw)
    if re.search(r"\$\{[^}]+}", expanded):
        raise ValueError(f"unresolved environment variable in miniapp {name}: {expanded}")
    return expanded


class _KnowledgeDeleteError(Exception):
    """知识库删除失败（设计 §4.2）：``code`` 映射到 HTTP 状态与错误体。"""

    def __init__(self, code: str, **detail: Any):
        super().__init__(code)
        self.code = code
        self.detail = detail

    def payload(self) -> dict[str, Any]:
        return {"error": self.code, **self.detail}


class MiniappAdapter(BasePlatformAdapter):
    # 小程序端不支持下发给已发送消息做编辑（前端只做整条渲染），
    # 必须声明 False，否则 hermes 会按「可编辑」走流式：先发带流式光标(" ▉")
    # 的中间态，send() 会用它解析等待中的 future —— 结果是 /api/chat 拿到
    # 半截文本、完整文本反而落到 inbox。声明 False 后 hermes 走 send-final-only。
    SUPPORTS_MESSAGE_EDITING = False

    # 声明需要 agent 活动事件流（执行轨迹，见 miniprogram docs §9）：
    # 网关据此在「工具展示关闭」时也挂上 tool_progress_callback，并把每个事件
    # 旁路到 on_agent_activity。其他平台不声明 → 完全不受影响。
    wants_agent_activity = True

    def __init__(self, config: PlatformConfig, **_kwargs: Any):
        super().__init__(config=config, platform=Platform("miniapp"))
        extra = getattr(config, "extra", {}) or {}
        self.host = _runtime_value(extra.get("host"), name="host", default="127.0.0.1")
        self.port = int(_runtime_value(extra.get("port"), name="port", default=8010))
        self.response_timeout = float(extra.get("response_timeout_seconds") or 120)
        self.allowed_origins = {
            item.strip()
            for item in _runtime_value(
                extra.get("allowed_origins"), name="allowed_origins", default=""
            ).split(",")
            if item.strip()
        }
        self.upload_max_bytes = int(extra.get("upload_max_bytes") or _UPLOAD_MAX_BYTES)
        self._upload_root = _upload_root(extra)
        self._db_path = _db_path(extra)
        self._pending: dict[str, deque[asyncio.Future]] = defaultdict(deque)
        # 执行轨迹缓冲：chat_id → {started, query, events, pending_tool}
        self._activity: dict[str, dict[str, Any]] = {}
        # 最近一次已完成的 trace（chat_id → trace/None）：全屏实时页在收尾瞬间
        # 缓冲已被 activity_take 取走，用它仍能拿到最终结果（含 result/end）。
        self._last_trace: dict[str, Optional[dict[str, Any]]] = {}
        self._runner = None
        self._site = None
        from knowledge_base.repository.sqlite_metadata import DatabaseManager
        DatabaseManager(self._db_path).initialize()
        self._initialize_storage()

    # ── 执行轨迹（agent 活动事件 → /api/chat 响应的 trace 字段）────────────
    # 展示契约见 miniprogram `docs/UI-DESIGN-SYSTEM.md` §9.3：
    #   { t, type: reasoning | skill | tool.start | tool.done | result | end }
    # 事件来自网关上与「工具展示开关」无关的旁路钩子（gateway/run.py
    # progress_callback / reasoning_callback → on_agent_activity），因此这里是真实执行事实。
    _TOOL_TITLE_KEYS = ("command", "cmd", "path", "file_path", "query", "url", "name")
    # 单回合思考文本上限：reasoning 增量可能很长，封顶防止轨迹体积失控。
    _THINK_MAX_CHARS = 6000

    def activity_begin(self, chat_id: str, query: str = "") -> None:
        """一次 run 开始时开缓冲（在 handle_message 之前调用）。"""
        self._activity[chat_id] = {"started": time.monotonic(), "query": query, "events": []}
        self._last_trace.pop(chat_id, None)

    def activity_take(self, chat_id: str, answer: str = "") -> Optional[dict[str, Any]]:
        """取走并清空缓冲，组装成小程序端契约的 trace；无有效事件则返回 None。"""
        buf = self._activity.pop(chat_id, None)
        if not buf:
            self._last_trace[chat_id] = None
            return None
        events = buf["events"]
        duration_ms = self._elapsed_ms(buf)
        if answer:
            usage = buf.get("usage") or {}
            events.append({
                "t": duration_ms,
                "type": "result",
                "answer": answer,
                # 真实指标：工具/Skill 从事件里数；tokens 由网关透传的本次 run 用量
                # 提供（缺则 None → 前端诚实显示「—」，不编数字）。
                "metrics": {
                    "tools": sum(1 for e in events if e["type"] == "tool.start"),
                    "skills": sum(1 for e in events if e["type"] == "skill"),
                    "tokens": usage.get("completion_tokens"),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "reasoning_tokens": usage.get("reasoning_tokens"),
                    "seconds": round(duration_ms / 1000, 1),
                },
            })
        # 只有「result」一个事件说明这一轮没有可展示的执行过程，不下发 trace
        if not [e for e in events if e["type"] != "result"]:
            self._last_trace[chat_id] = None
            return None
        # 结束标记：与展示契约一致。也让前端播放器的收尾（指标结算）晚于 result 一拍。
        events.append({"t": duration_ms, "type": "end"})
        trace = {
            "id": f"run_{int(buf['started'])}",
            "title": "本次执行",
            "query": buf.get("query", ""),
            "durationMs": duration_ms,
            "events": events,
            # GPU / VRAM / 吞吐由设备侧单独提供；网关不产生这些指标
            "telemetry": [],
        }
        # 缓存最终 trace，供全屏实时页在缓冲被取走后的收尾瞬间继续读取
        self._last_trace[chat_id] = trace
        return trace

    def activity_abort(self, chat_id: str) -> None:
        self._activity.pop(chat_id, None)

    @staticmethod
    def _elapsed_ms(buf: dict[str, Any]) -> int:
        return int((time.monotonic() - buf["started"]) * 1000)

    @classmethod
    def _tool_label(cls, tool_name: str, args: Any, preview: Any) -> str:
        """给工具调用一个人类可读的一行标题（优先真实命令 / 路径）。"""
        if isinstance(args, dict):
            for key in cls._TOOL_TITLE_KEYS:
                value = args.get(key)
                if value:
                    return str(value).strip().splitlines()[0][:80]
        text = str(preview or "").strip().splitlines()[0]
        return (text or tool_name)[:80]

    def on_agent_activity(self, event_type: str, tool_name: str = None, preview: str = None,
                          args: Any = None, chat_id: str = "", **kwargs: Any) -> None:
        """收集 agent 活动（网关 opt-in 钩子，见 gateway/run.py）。

        · `thinking.delta`：模型推理增量（网关 reasoning_callback 转发）→ 真流式「思考」；
        · `usage`：本次 run 的 token 用量（网关在 run 结束后透传）；
        · `skill_view` 的工具调用即「命中 Skill」；
        · `reasoning.available`（=模型可见输出，常为最终答案）不再当思考，否则
          「思考执行」会显示答案原文。
        所有事件与展示设置无关。
        """
        buf = self._activity.get(chat_id)
        if buf is None:
            return
        t = self._elapsed_ms(buf)

        if event_type == "thinking.delta":
            text = str(preview or "")
            if text:
                used = int(buf.get("think_chars") or 0)
                if used < self._THINK_MAX_CHARS:
                    text = text[: self._THINK_MAX_CHARS - used]
                    buf["events"].append({"t": t, "type": "reasoning", "text": text})
                    buf["think_chars"] = used + len(text)
            return

        if event_type == "usage":
            if isinstance(args, dict):
                buf["usage"] = dict(args)
            return

        # 不再把可见输出当思考（见 docstring）
        if event_type == "reasoning.available":
            return

        if tool_name == "_thinking":
            return

        if event_type == "tool.started" and tool_name:
            label = self._tool_label(tool_name, args, preview)
            if tool_name == "skill_view":
                # 「命中 Skill」单独成一拍，不再作为工具行重复出现
                skill = ""
                if isinstance(args, dict):
                    skill = str(args.get("name") or "").strip()
                buf["events"].append({
                    "t": t, "type": "skill", "name": skill or label, "source": "skill_view",
                })
                return
            buf["pending_tool"] = label
            buf["events"].append({"t": t, "type": "tool.start", "tool": tool_name, "title": label})
            return

        if event_type == "tool.completed" and tool_name:
            if tool_name == "skill_view":
                return
            label = buf.get("pending_tool") or self._tool_label(tool_name, args, preview)
            duration = kwargs.get("duration")
            try:
                duration_s = round(float(duration), 2) if duration is not None else 0
            except (TypeError, ValueError):
                duration_s = 0
            buf["events"].append({
                "t": t, "type": "tool.done", "title": label, "duration_s": duration_s,
            })

    def _resolve_username(self, user_id: str) -> str:
        try:
            with sqlite3.connect(str(self._methods_db_path())) as conn:
                row = conn.execute(
                    "SELECT username FROM user_profiles WHERE platform='miniapp' AND user_id=?",
                    (user_id,)
                ).fetchone()
            return row[0] if row else "游客"
        except Exception:
            return "游客"

    async def connect(self, is_reconnect: bool = False, **kwargs) -> bool:
        try:
            from aiohttp import web
        except ImportError:
            logger.error("miniapp platform requires aiohttp")
            return False
        app = web.Application(client_max_size=self.upload_max_bytes + 1024 * 1024)
        app.router.add_get("/health", self._health)
        app.router.add_post("/api/miniapp/login", self._login)
        app.router.add_post("/api/miniapp/history", self._history)
        # 当前 run 的实时进度（聊天页轮询用）：只读内存活动缓冲，不触发推理
        app.router.add_get("/api/miniapp/run-progress", self._run_progress)
        # 全屏页在运行中轮询的「实时执行轨迹」（读同一缓冲，运行中给实时事件）
        app.router.add_get("/api/miniapp/run-trace", self._run_trace)
        app.router.add_post("/api/chat", self._chat)
        app.router.add_post("/api/uploads", self._upload)
        app.router.add_get("/api/messages", self._messages)
        app.router.add_options("/{tail:.*}", self._options)
        app.router.add_get("/api/methods/identity", self._method_identity)
        app.router.add_get("/api/methods/profile", self._method_profile)
        app.router.add_put("/api/methods/profile", self._method_profile_update)
        app.router.add_get("/api/methods/admission-profile", self._method_admission_profile)
        app.router.add_put("/api/methods/admission-profile", self._method_admission_profile_update)
        app.router.add_post("/api/methods/phone-bind", self._method_phone_bind)
        app.router.add_get("/api/methods/phone-whitelist", self._method_phone_whitelist_list)
        app.router.add_post("/api/methods/phone-whitelist", self._method_phone_whitelist_add)
        app.router.add_delete("/api/methods/phone-whitelist", self._method_phone_whitelist_delete)
        app.router.add_post("/api/methods/phone-whitelist/import", self._method_phone_whitelist_import)
        # 011：学生/教师/管理员分开导入（角色由入口固定，不再依赖文件内角色列）
        app.router.add_post("/api/methods/phone-whitelist/import/student",
                            self._method_phone_whitelist_import_student)
        app.router.add_post("/api/methods/phone-whitelist/import/teacher",
                            self._method_phone_whitelist_import_teacher)
        app.router.add_post("/api/methods/phone-whitelist/import/admin",
                            self._method_phone_whitelist_import_admin)
        app.router.add_delete("/api/methods/phone-whitelist/batch", self._method_phone_whitelist_batch_delete)
        # 011：学生学籍档案（我的档案）——学生只读；管理员列表/编辑
        app.router.add_get("/api/methods/student-archive", self._method_student_archive_list)
        app.router.add_put("/api/methods/student-archive", self._method_student_archive_update)
        app.router.add_post("/api/methods/student-archive/link", self._method_student_archive_link)
        app.router.add_get("/api/methods/knowledge", self._method_knowledge_list)
        app.router.add_delete("/api/methods/knowledge", self._method_knowledge_delete)
        app.router.add_delete("/api/methods/knowledge/batch", self._method_knowledge_batch_delete)
        app.router.add_get("/api/methods/knowledge/orphans", self._method_knowledge_orphans)
        app.router.add_get("/api/methods/knowledge/content", self._method_knowledge_content)
        app.router.add_get("/api/methods/knowledge/audit", self._method_knowledge_audit)
        app.router.add_post("/api/methods/apply-auth", self._method_apply_auth)
        app.router.add_get("/api/methods/unread", self._method_unread)
        app.router.add_get("/api/methods/certified-users", self._method_certified_users)
        app.router.add_post("/api/methods/certified-users/change-role", self._method_certified_users_change_role)
        app.router.add_get("/api/methods/teacher-auths", self._method_teacher_auths)
        app.router.add_post("/api/methods/teacher-auths/approve", self._method_teacher_auths_approve)
        app.router.add_post("/api/methods/teacher-auths/reject", self._method_teacher_auths_reject)
        app.router.add_get("/api/methods/admin-auths", self._method_admin_auths)
        app.router.add_post("/api/methods/admin-auths/approve", self._method_admin_auths_approve)
        app.router.add_post("/api/methods/admin-auths/reject", self._method_admin_auths_reject)
        app.router.add_post("/api/methods/change-role", self._method_change_role)
        app.router.add_get("/api/methods/jxtz-sync", self._method_jxtz_sync_get)
        app.router.add_post("/api/methods/jxtz-sync/toggle", self._method_jxtz_sync_toggle)
        app.router.add_post("/api/methods/jxtz-sync/schedule", self._method_jxtz_sync_schedule)
        app.router.add_post("/api/methods/jxtz-sync/run", self._method_jxtz_sync_run)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        self._running = True
        logger.info("Miniapp platform listening on http://%s:%s", self.host, self.port)
        return True

    async def disconnect(self) -> None:
        self._running = False
        for queue in self._pending.values():
            for future in queue:
                if not future.done():
                    future.cancel()
        self._pending.clear()
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = self._site = None

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[dict[str, Any]] = None) -> SendResult:
        queue = self._pending.get(str(chat_id))
        if queue:
            # 只有网关标记为「最终答案」的 send（hermes_final，见 platforms/base.py
            # 的 _final_thread_metadata）才算本轮结束。运行途中的状态消息（心跳
            # 「⏳ Working — N min」、工具前旁白、通知、审批提示…）都不带该标记 ——
            # 绝不能据此 resolve /api/chat 的 future：否则 _chat 会提前 activity_take，
            # 取到半截轨迹并清空缓冲，后续工具/推理事件全部丢弃（表现为回答是状态
            # 文本、走流程/思考执行为空）。这是「首条 send 即完成」设计的根因修复。
            if not (isinstance(metadata, dict) and metadata.get("hermes_final")):
                return SendResult(success=True, message_id=uuid.uuid4().hex)
            while queue:
                future = queue.popleft()
                if not future.done():
                    future.set_result(str(content))
                    if not queue:
                        self._pending.pop(str(chat_id), None)
                    return SendResult(success=True, message_id=uuid.uuid4().hex)
        try:
            self._store_inbox(str(chat_id), str(content))
            return SendResult(success=True, message_id=uuid.uuid4().hex)
        except Exception as exc:
            logger.exception("miniapp inbox delivery failed")
            return SendResult(success=False, error=str(exc), retryable=True)

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"id": str(chat_id), "name": str(chat_id), "type": "dm"}

    async def _health(self, request):
        from aiohttp import web
        return self._response(request, {"status": "ok", "service": "miniapp-native"})

    async def _login(self, request):
        from aiohttp import ClientSession, ClientTimeout, web
        body = await _json_body(request)
        code = str(body.get("code") or "").strip()
        if not code:
            raise web.HTTPBadRequest(text=json.dumps({"error": "code is required"}))
        appid = os.getenv("WECHAT_MINIAPP_APPID", "").strip()
        secret = os.getenv("WECHAT_MINIAPP_APPSECRET", "").strip()
        if not appid or not secret:
            raise web.HTTPServiceUnavailable(text=json.dumps({"error": "miniapp credentials are not configured"}))
        async with ClientSession(timeout=ClientTimeout(total=10)) as client:
            async with client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={"appid": appid, "secret": secret, "js_code": code, "grant_type": "authorization_code"},
            ) as response:
                payload = await response.json(content_type=None)
        if payload.get("errcode") not in (None, 0) or not payload.get("openid"):
            return self._response(request, {
                "error": "wechat code2session failed", "errcode": payload.get("errcode"),
                "errmsg": payload.get("errmsg"),
            }, status=400)
        openid = str(payload["openid"])
        token = _issue_token(openid)
        return self._response(request, {
            "openid": openid, "user_id": openid,
            "session_id": f"miniapp_{openid}", "session_token": token,
        })

    async def _history(self, request):
        from aiohttp import web
        token_openid = self._authorize(request)
        body = await _json_body(request)
        body_openid = str(body.get("openid") or "").strip()
        openid = body_openid or token_openid
        if body_openid and body_openid != token_openid:
            raise web.HTTPForbidden(text=json.dumps({"error": "openid mismatch"}))
        messages = await asyncio.to_thread(self._load_history_from_state_db, openid)
        return self._response(request, {
            "openid": openid,
            "session_id": f"miniapp_{openid}",
            "messages": messages,
            "actions": self._actions(),
        })

    async def _run_progress(self, request):
        """当前 run 的实时进度（供聊天页轮询：让用户知道在思考还是在跑工具）。

        只读 on_agent_activity 写入的内存缓冲，不触发任何推理；令牌鉴权，只能看自己的 run。
        """
        openid = self._authorize(request)
        buf = self._activity.get(openid)
        if not buf:
            return self._response(request, {
                "running": False, "elapsed_s": 0,
                "stage": "idle", "tool": "", "detail": "",
                "tools": 0, "skills": 0,
            })
        events = buf.get("events") or []
        stage, tool, detail = self._progress_snapshot(events)
        return self._response(request, {
            "running": True,
            "elapsed_s": round(time.monotonic() - buf.get("started", time.monotonic()), 1),
            "stage": stage,
            "tool": tool,
            "detail": detail,
            "tools": sum(1 for e in events if e.get("type") == "tool.start"),
            "skills": sum(1 for e in events if e.get("type") == "skill"),
        })

    async def _run_trace(self, request):
        """实时执行轨迹（全屏页在运行中轮询）：读内存缓冲，不触发推理。

        运行中返回当前事件；结束后返回缓存的最终 trace（含 result/end），
        避免收尾瞬间缓冲已被 activity_take 取走、页面停在没结果的半截。
        """
        openid = self._authorize(request)
        buf = self._activity.get(openid)
        if buf is not None:
            return self._response(request, {
                "running": True,
                "query": buf.get("query", ""),
                "elapsedMs": self._elapsed_ms(buf),
                "events": list(buf.get("events") or []),
            })
        last = self._last_trace.get(openid)
        if last:
            return self._response(request, {
                "running": False,
                "query": last.get("query", ""),
                "elapsedMs": last.get("durationMs", 0),
                "events": last.get("events", []),
            })
        return self._response(request, {"running": False, "query": "", "elapsedMs": 0, "events": []})

    @staticmethod
    def _progress_snapshot(events: list) -> tuple:
        """把最近一条活动事件翻译成阶段：tool / skill / thinking / starting。"""
        if not events:
            return "starting", "", ""
        last = events[-1]
        kind = last.get("type")
        if kind == "tool.start":
            return "tool", str(last.get("tool") or ""), str(last.get("title") or "")[:60]
        if kind == "tool.done":
            return "thinking", "", ""
        if kind == "skill":
            return "skill", "", str(last.get("name") or "")[:60]
        if kind == "reasoning":
            return "thinking", "", ""
        return "starting", "", ""

    async def _chat(self, request):
        from aiohttp import web
        openid = self._authorize(request)
        body = await _json_body(request)
        message = str(body.get("message") or "").strip()
        try:
            attachments = self._attachments_for_request(openid, body.get("attachment_ids"))
        except ValueError as exc:
            raise web.HTTPBadRequest(text=json.dumps({"error": str(exc)})) from exc
        if not message and not attachments:
            raise web.HTTPBadRequest(text=json.dumps({"error": "message or attachment_ids is required"}))
        if not message:
            message = "我上传了一份材料，请根据附件内容协助我。"
        if attachments:
            message = _attachment_context(attachments, message)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[openid].append(future)
        source = self.build_source(
            chat_id=openid, chat_name=openid, chat_type="dm", user_id=openid, user_name=openid,
            message_id=uuid.uuid4().hex,
        )
        event = MessageEvent(
            source=source, text=message,
            message_type=MessageType.DOCUMENT if attachments else MessageType.TEXT,
            message_id=source.message_id,
            media_urls=[item["path"] for item in attachments],
            media_types=[item["mime_type"] for item in attachments],
            raw_message={"transport": "miniapp", "request_id": request.headers.get("X-Request-Id", "")},
        )
        # 注入身份上下文：工具层（web/terminal/知识库）的角色门读 QueryContext
        from knowledge_base.core.query_context import inject_context, QueryContext
        inject_context(QueryContext(user_id=openid, platform="miniapp"))
        self.activity_begin(openid, query=message)
        await self.handle_message(event)
        try:
            reply = await asyncio.wait_for(future, timeout=self.response_timeout)
        except asyncio.TimeoutError:
            _discard_future(self._pending, openid, future)
            self.activity_abort(openid)
            return self._response(request, {
                "reply": '当前处理时间较长，请稍后重试；如需人工帮助，可发送"转人工"。',
                "session_id": f"miniapp_{openid}", "retryable": True,
                "actions": self._actions(),
            }, status=504)
        messages = self._take_inbox(openid)
        # 执行轨迹：取走本次 run 的活动事件（真实；无事件时为 None，不下发字段）
        trace = self.activity_take(openid, answer=_strip_markup(reply))
        # Convert markdown to HTML for frontend rendering
        try:
            import markdown
            reply = markdown.markdown(reply, extensions=['tables', 'fenced_code'])
        except Exception:
            pass
        payload = {
            "reply": reply, "session_id": f"miniapp_{openid}",
            "messages": messages, "actions": self._actions(),
        }
        if trace:
            payload["trace"] = trace
        return self._response(request, payload)

    async def _upload(self, request):
        from aiohttp import web

        openid = self._authorize(request)
        from knowledge_base.auth.role_store import resolve_role
        role = resolve_role("miniapp", openid)
        scope = str(request.rel_url.query.get("scope") or "").strip()
        if not scope:
            scope = f"users/{openid}"
        # 写侧矩阵 A：所有角色可传个人库；teacher+ 可传教师库；admin/owner 可传公共库
        # （矩阵只留一处：`_knowledge_scopes`；guest 为空集 → 403，D11）
        _allowed_scopes = self._knowledge_scopes(role, openid)
        if scope not in _allowed_scopes:
            raise web.HTTPForbidden(text=json.dumps({
                "error": "permission denied",
                "role": role,
                "required": sorted(_allowed_scopes),
            }))
        if not request.content_type.startswith("multipart/"):
            raise web.HTTPBadRequest(text=json.dumps({"error": "multipart file upload is required"}))
        reader = await request.multipart()
        part = None
        while candidate := await reader.next():
            if candidate.name == "file" and candidate.filename:
                part = candidate
                break
        if part is None:
            raise web.HTTPBadRequest(text=json.dumps({"error": "file is required"}))
        filename = _safe_upload_name(part.filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in _UPLOAD_ALLOWED_EXTENSIONS:
            raise web.HTTPUnsupportedMediaType(text=json.dumps({
                "error": "unsupported file type", "supported_extensions": sorted(_UPLOAD_ALLOWED_EXTENSIONS),
            }))

        self._cleanup_expired_uploads()
        directory = self._upload_directory(openid)
        target = directory / filename
        temporary = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha256()
        written = 0
        try:
            with temporary.open("wb") as handle:
                while chunk := await part.read_chunk(64 * 1024):
                    written += len(chunk)
                    if written > self.upload_max_bytes:
                        raise web.HTTPRequestEntityTooLarge(text=json.dumps(
                            {"error": f"file too large (max {self.upload_max_bytes} bytes)"}))
                    digest.update(chunk)
                    handle.write(chunk)
            os.chmod(temporary, 0o600)
            _validate_upload_content(temporary, suffix)
            temporary.replace(target)
        except ValueError as exc:
            temporary.unlink(missing_ok=True)
            _rmdir_if_empty(directory)
            raise web.HTTPUnsupportedMediaType(text=json.dumps({"error": str(exc)})) from exc
        except Exception:
            temporary.unlink(missing_ok=True)
            _rmdir_if_empty(directory)
            raise

        attachment_id = uuid.uuid4().hex
        extracted_text = _extract_upload_text(target, suffix)
        self._store_upload(
            attachment_id=attachment_id, user_id=openid, path=target, filename=filename,
            mime_type=_UPLOAD_MIME_TYPES[suffix], size_bytes=written, sha256=digest.hexdigest(),
            extracted_text=extracted_text,
        )
        # 入库含 MinerU/嵌入等长耗时同步调用，必须离开事件循环，否则会阻塞整个网关
        ingest_result = await asyncio.to_thread(self._auto_ingest, openid, target, scope)
        return self._response(request, {
            "attachment": {
                "id": attachment_id, "name": filename, "size_bytes": written,
                "readable": bool(extracted_text),
                "ingested": ingest_result.get("ingested", False),
                "ingest_status": ingest_result.get("status", ""),
                "ingest_nodes": ingest_result.get("node_count", 0),
                "ingest_detail": ingest_result.get("detail", ""),
                "scope": scope,
            }
        }, status=201)

    def _auto_ingest(self, user_id: str, file_path: Path, scope: str) -> dict[str, Any]:
        try:
            from knowledge_base.bootstrap import create_chroma_repository, create_ingestion_orchestrator
            chroma_path = os.environ.get("CHROMA_DB_PATH", "data/chroma")
            repo = create_chroma_repository(chroma_path=chroma_path)
            orchestrator = create_ingestion_orchestrator(repo=repo)
            result = orchestrator.ingest_file(
                user_id=user_id, file_path=str(file_path),
                scope=scope, platform="miniapp", source="file",
            )
            return {
                "ingested": result.status.value in ("ingested", "replaced", "skipped"),
                "status": result.status.value,
                "node_count": result.node_count,
                "detail": result.error_detail or result.warning or "",
            }
        except Exception as exc:
            logger.warning("miniapp auto-ingest failed for %s: %s", file_path.name, exc)
            return {"ingested": False, "status": "error", "node_count": 0, "detail": str(exc)[:200]}

    async def _messages(self, request):
        openid = self._authorize(request)
        return self._response(request, {"messages": self._take_inbox(openid), "actions": self._actions()})

    async def _options(self, request):
        from aiohttp import web
        return self._response(request, {}, status=204)

    # ── Methods API ─────────────────────────────────────────────────

    def _require_role(self, openid: str, allowed: set) -> str:
        """Resolve role and raise 403 if not in allowed set."""
        from knowledge_base.auth.role_store import resolve_role
        role = resolve_role("miniapp", openid)
        if role not in allowed:
            from aiohttp import web
            raise web.HTTPForbidden(text=json.dumps({
                "error": "permission denied",
                "role": role,
                "required": sorted(allowed),
            }))
        return role

    def _assign_user_role(self, operator_role: str, user_id: str, role: str) -> None:
        """Apply an authorized role change and keep the UI profile in sync."""
        from aiohttp import web

        from knowledge_base.auth import role_store
        from knowledge_base.auth.guards import can_assign_role

        guard = can_assign_role(operator_role, role)
        if not guard.allowed:
            raise web.HTTPForbidden(text=json.dumps({"error": guard.reason}))
        if not role_store.set_role("miniapp", user_id, role):
            raise ValueError(f"unsupported role: {role}")
        status = f"{role}_approved" if role in {"teacher", "admin"} else role
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.execute(
                """
                INSERT INTO user_profiles(platform, user_id, status)
                VALUES ('miniapp', ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    status=excluded.status, updated_at=datetime('now')
                """,
                (user_id, status),
            )

    def _sync_auth_profile(self, auth_type: str, auth_request: Any) -> None:
        """Mirror the latest mini-program auth state for display, not authorization."""
        if auth_type not in {"teacher", "admin"} or auth_request is None:
            return
        status = f"{auth_type}_{auth_request.status}"
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.execute(
                """
                INSERT INTO user_profiles(
                    platform, user_id, name, staff_id, reason, status,
                    raw_json, reviewed_by, reviewed_at, review_note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    name=excluded.name, staff_id=excluded.staff_id,
                    reason=excluded.reason, status=excluded.status,
                    raw_json=excluded.raw_json, reviewed_by=excluded.reviewed_by,
                    reviewed_at=excluded.reviewed_at, review_note=excluded.review_note,
                    updated_at=datetime('now')
                """,
                (
                    auth_request.platform, auth_request.user_id,
                    auth_request.name, auth_request.staff_id,
                    getattr(auth_request, "reason", "") or "", status,
                    auth_request.raw_json or "{}", auth_request.reviewed_by,
                    auth_request.reviewed_at, auth_request.review_note,
                ),
            )

    def _auth_request_status(self, auth_type: str, request_id: int) -> str:
        """审核前读取申请状态；申请不存在或类型不符时返回空串。"""
        from knowledge_base.repository.sqlite_metadata import (
            AdminAuthRequestDAO, DatabaseManager, TeacherAuthRequestDAO,
        )
        db = DatabaseManager(self._methods_db_path())
        dao = TeacherAuthRequestDAO(db) if auth_type == "teacher" else AdminAuthRequestDAO(db)
        row = dao.get_by_id(request_id)
        return (row.status or "") if row else ""

    def _sync_auth_decision(self, auth_type: str, request_id: int) -> Any:
        from knowledge_base.repository.sqlite_metadata import (
            AdminAuthRequestDAO, DatabaseManager, TeacherAuthRequestDAO,
        )
        db = DatabaseManager(self._methods_db_path())
        dao = TeacherAuthRequestDAO(db) if auth_type == "teacher" else AdminAuthRequestDAO(db)
        decided = dao.get_by_id(request_id)
        self._sync_auth_profile(auth_type, decided)
        return decided


    def _methods_db_path(self) -> Path:
        return self._db_path


    async def _method_identity(self, request):
        openid = self._authorize(request)
        from knowledge_base.auth.role_store import resolve_role
        role = resolve_role("miniapp", openid)
        auth_status = ""
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            # Profile completion is not identity approval. Effective roles come
            # only from the trusted RBAC store after review or an admin change.
            row = conn.execute(
                "SELECT status FROM user_profiles WHERE platform='miniapp' AND user_id=?",
                (openid,)
            ).fetchone()
            if row:
                auth_status = row[0] or ""
        phone, phone_verified = "", False
        try:
            self._ensure_admission_profile_columns()
            with sqlite3.connect(str(self._methods_db_path())) as conn:
                prow = conn.execute(
                    "SELECT phone, phone_verified FROM admission_profiles WHERE platform='miniapp' AND user_id=?",
                    (openid,)
                ).fetchone()
                if prow:
                    phone = str(prow[0] or "")
                    phone_verified = bool(prow[1])
        except Exception:
            logger.warning("failed to read phone profile for %s", openid, exc_info=True)
        return self._response(request, {
            "role": role, "openid": openid,
            "username": self._resolve_username(openid),
            "auth_status": auth_status,
            "phone": phone,
            "phone_verified": phone_verified,
        })

    async def _method_unread(self, request):
        openid = self._authorize(request)
        from knowledge_base.auth.role_store import resolve_role
        role = resolve_role("miniapp", openid)
        # 认证待办角标（仅 admin/owner；复用既有 DAO，避免新 SQL）
        auth_teacher_pending = auth_admin_pending = 0
        if role in ("admin", "owner"):
            from knowledge_base.repository.sqlite_metadata import (
                AdminAuthRequestDAO, DatabaseManager, TeacherAuthRequestDAO,
            )
            db = DatabaseManager(self._methods_db_path())
            auth_teacher_pending = len(TeacherAuthRequestDAO(db).list_pending(None))
            auth_admin_pending = len(AdminAuthRequestDAO(db).list_pending(None))
        return self._response(request, {
            "unread": auth_teacher_pending + auth_admin_pending,
            "auth_teacher_pending": auth_teacher_pending,
            "auth_admin_pending": auth_admin_pending,
        })


    async def _method_apply_auth(self, request):
        openid = self._authorize(request)
        body = await _json_body(request)
        auth_type = str(body.get("auth_type") or "teacher").strip()
        name = str(body.get("name") or "").strip()
        staff_id = str(body.get("staff_id") or "").strip()
        reason = str(body.get("reason") or "").strip()
        if auth_type not in ("teacher", "admin"):
            from aiohttp import web
            raise web.HTTPBadRequest(text=json.dumps({"error": "auth_type must be teacher or admin"}))
        if not name or not staff_id:
            from aiohttp import web
            raise web.HTTPBadRequest(text=json.dumps({"error": "name and staff_id are required"}))
        try:
            from knowledge_base.repository.sqlite_metadata import DatabaseManager, TeacherAuthRequestDAO, AdminAuthRequestDAO
            from knowledge_base.auth.role_store import resolve_role
            db = DatabaseManager(self._methods_db_path())
            role_label = "教师" if auth_type == "teacher" else "管理员"

            # 学生/访客不得自助申请：身份只能由管理员通过电话白名单或「变更身份」授予
            current_role = resolve_role("miniapp", openid)
            if current_role not in ("teacher", "admin", "owner"):
                return self._response(request, {
                    "success": False,
                    "reply": "当前身份无法自助申请认证，如需教师或管理员权限请联系管理员。",
                    "auth_type": auth_type, "status": "role_not_allowed",
                })

            # Check if user already has this role
            if auth_type == "teacher" and current_role in ("teacher", "admin", "owner"):
                return self._response(request, {
                    "success": False, "reply": f"您已是{role_label}，无需重复申请。",
                    "auth_type": auth_type, "status": "already_approved",
                })
            if auth_type == "admin" and current_role in ("admin", "owner"):
                return self._response(request, {
                    "success": False, "reply": f"您已是{role_label}，无需重复申请。",
                    "auth_type": auth_type, "status": "already_approved",
                })

            # Check existing auth request status
            if auth_type == "teacher":
                existing = TeacherAuthRequestDAO(db).get_latest_for_user("miniapp", openid)
            else:
                existing = AdminAuthRequestDAO(db).get_latest_for_user("miniapp", openid)

            if existing:
                if existing.status and "pending" in existing.status:
                    return self._response(request, {
                        "success": False, "reply": f"您的{role_label}认证正在审核中，请耐心等待。",
                        "auth_type": auth_type, "status": "pending",
                    })
                if existing.status and "approved" in existing.status:
                    return self._response(request, {
                        "success": False, "reply": f"您已是{role_label}，无需重复申请。",
                        "auth_type": auth_type, "status": "already_approved",
                    })
                # status == "rejected" -> allow re-apply

            # 教师与管理员申请共用 user_profiles 一行（UNIQUE(platform,user_id)），
            # 另一类型仍在审核中时提交会覆盖掉前一份申请，这里直接拒绝
            if auth_type == "teacher":
                other = AdminAuthRequestDAO(db).get_latest_for_user("miniapp", openid)
                other_label = "管理员"
            else:
                other = TeacherAuthRequestDAO(db).get_latest_for_user("miniapp", openid)
                other_label = "教师"
            if other and other.status and "pending" in other.status:
                return self._response(request, {
                    "success": False,
                    "reply": f"您的{other_label}认证正在审核中，请等待审核完成后再申请{role_label}认证。",
                    "auth_type": auth_type, "status": "conflicting_pending",
                })

            if auth_type == "teacher":
                created = TeacherAuthRequestDAO(db).create_or_update_pending(
                    platform="miniapp", user_id=openid, name=name, staff_id=staff_id,
                    raw={"reason": reason, "source": "miniapp_methods"},
                )
            else:
                created = AdminAuthRequestDAO(db).create_or_update_pending(
                    platform="miniapp", user_id=openid, name=name, staff_id=staff_id,
                    reason=reason, raw={"source": "miniapp_methods"},
                )
            self._sync_auth_profile(auth_type, created)
            return self._response(request, {
                "success": True,
                "reply": f"已提交{role_label}认证申请，请等待管理员审核。",
                "auth_type": auth_type,
            })
        except Exception as e:
            logger.warning("apply_auth failed: %s", e, exc_info=True)
        return self._response(request, {
            "success": False,
            "reply": "提交失败，请稍后重试",
        }, status=500)

    async def _method_certified_users(self, request):
        """GET /api/methods/certified-users — list approved teachers and admins."""
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        from knowledge_base.auth.role_store import resolve_role
        users = []
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT user_id, username, name, staff_id FROM user_profiles "
                "WHERE platform='miniapp' ORDER BY user_id"
            ).fetchall()
            for row in rows:
                uid = row["user_id"]
                role = resolve_role("miniapp", uid)
                if role not in {"teacher", "admin", "owner"}:
                    continue
                users.append({
                    "user_id": uid, "username": row["username"] or "游客",
                    "name": row["name"] or "",
                    "staff_id": row["staff_id"] or "",
                    "role": role,
                })
        users.sort(key=lambda u: (0 if u["role"] in ("admin", "owner") else 1, u["username"]))
        return self._response(request, {"users": users, "count": len(users)})

    async def _method_certified_users_change_role(self, request):
        """POST /api/methods/certified-users/change-role — RBAC-protected role change."""
        openid = self._authorize(request)
        operator_role = self._require_role(openid, {"admin", "owner"})
        from aiohttp import web
        body = await _json_body(request)
        target_user = str(body.get("user_id") or "").strip()
        new_role = str(body.get("role") or "").strip()
        if not target_user or not new_role:
            raise web.HTTPBadRequest(text=json.dumps({"error": "user_id and role are required"}))
        if new_role not in ("guest", "student", "teacher", "admin"):
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid role"}))

        self._assign_user_role(operator_role, target_user, new_role)
        return self._response(request, {"success": True, "reply": f"已将 {target_user} 的角色设置为 {new_role}"})

    async def _method_teacher_auths(self, request):
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        from knowledge_base.repository.sqlite_metadata import DatabaseManager, TeacherAuthRequestDAO
        db = DatabaseManager(self._methods_db_path())
        pending = TeacherAuthRequestDAO(db).list_pending(None)
        items = []
        for r in pending:
            items.append({
                "id": r.id, "name": r.name, "staff_id": r.staff_id,
                "created_at": r.created_at, "status": r.status,
            })
        if not items:
            return self._response(request, {"reply": "当前没有待审核的老师认证申请。", "items": []})
        return self._response(request, {"reply": f"共{len(items)}条", "items": items})

    async def _method_teacher_auths_approve(self, request):
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        body = await _json_body(request)
        request_id = int(body.get("request_id") or 0)
        if not request_id:
            from aiohttp import web
            raise web.HTTPBadRequest(text=json.dumps({"error": "request_id is required"}))
        before = self._auth_request_status("teacher", request_id)
        from management_flow.service import ManagementService, ManagementCommand
        svc = ManagementService(self._methods_db_path())
        result = svc.process_command(platform="miniapp", operator_id=openid,
                                     operator_role="admin", command=ManagementCommand("approve_teacher", request_id=request_id))
        decided = self._sync_auth_decision("teacher", request_id)
        return self._response(request, {
            "success": bool("pending" in before and decided
                            and "approved" in (decided.status or "")),
            "reply": result.reply_text,
        })

    async def _method_teacher_auths_reject(self, request):
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        body = await _json_body(request)
        request_id = int(body.get("request_id") or 0)
        reason = str(body.get("reason") or "").strip()
        if not request_id:
            from aiohttp import web
            raise web.HTTPBadRequest(text=json.dumps({"error": "request_id is required"}))
        before = self._auth_request_status("teacher", request_id)
        from management_flow.service import ManagementService, ManagementCommand
        svc = ManagementService(self._methods_db_path())
        result = svc.process_command(platform="miniapp", operator_id=openid,
                                     operator_role="admin", command=ManagementCommand("reject_teacher", request_id=request_id, reason=reason))
        decided = self._sync_auth_decision("teacher", request_id)
        return self._response(request, {
            "success": bool("pending" in before and decided
                            and "rejected" in (decided.status or "")),
            "reply": result.reply_text,
        })

    async def _method_admin_auths(self, request):
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        from knowledge_base.repository.sqlite_metadata import DatabaseManager, AdminAuthRequestDAO
        db = DatabaseManager(self._methods_db_path())
        pending = AdminAuthRequestDAO(db).list_pending(None)
        items = []
        for r in pending:
            items.append({
                "id": r.id, "name": r.name, "staff_id": r.staff_id,
                "reason": r.reason, "created_at": r.created_at, "status": r.status,
            })
        if not items:
            return self._response(request, {"reply": "当前没有待审核的管理员认证申请。", "items": []})
        return self._response(request, {"reply": f"共{len(items)}条", "items": items})

    async def _method_admin_auths_approve(self, request):
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        body = await _json_body(request)
        request_id = int(body.get("request_id") or 0)
        if not request_id:
            from aiohttp import web
            raise web.HTTPBadRequest(text=json.dumps({"error": "request_id is required"}))
        before = self._auth_request_status("admin", request_id)
        from management_flow.service import ManagementService, ManagementCommand
        svc = ManagementService(self._methods_db_path())
        result = svc.process_command(platform="miniapp", operator_id=openid,
                                     operator_role="admin", command=ManagementCommand("approve_admin", request_id=request_id))
        decided = self._sync_auth_decision("admin", request_id)
        return self._response(request, {
            "success": bool("pending" in before and decided
                            and "approved" in (decided.status or "")),
            "reply": result.reply_text,
        })

    async def _method_admin_auths_reject(self, request):
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        body = await _json_body(request)
        request_id = int(body.get("request_id") or 0)
        reason = str(body.get("reason") or "").strip()
        if not request_id:
            from aiohttp import web
            raise web.HTTPBadRequest(text=json.dumps({"error": "request_id is required"}))
        before = self._auth_request_status("admin", request_id)
        from management_flow.service import ManagementService, ManagementCommand
        svc = ManagementService(self._methods_db_path())
        result = svc.process_command(platform="miniapp", operator_id=openid,
                                     operator_role="admin", command=ManagementCommand("reject_admin", request_id=request_id, reason=reason))
        decided = self._sync_auth_decision("admin", request_id)
        return self._response(request, {
            "success": bool("pending" in before and decided
                            and "rejected" in (decided.status or "")),
            "reply": result.reply_text,
        })

    async def _method_change_role(self, request):
        openid = self._authorize(request)
        operator_role = self._require_role(openid, {"admin", "owner"})
        body = await _json_body(request)
        target_user = str(body.get("user_id") or "").strip()
        target_role = str(body.get("role") or "").strip()
        if not target_user or target_role not in ("teacher", "admin", "student", "guest"):
            from aiohttp import web
            raise web.HTTPBadRequest(text=json.dumps({"error": "user_id and valid role are required"}))
        self._assign_user_role(operator_role, target_user, target_role)
        return self._response(request, {"success": True, "reply": f"已将 {target_user} 的角色设置为 {target_role}"})


    async def _method_profile(self, request):
        """GET /api/methods/profile"""
        openid = self._authorize(request)
        import sqlite3
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            row = conn.execute(
                "SELECT username FROM user_profiles WHERE platform='miniapp' AND user_id=?",
                (openid,)
            ).fetchone()
        username = row[0] if row else "游客"
        return self._response(request, {"username": username})

    async def _method_profile_update(self, request):
        """PUT /api/methods/profile"""
        openid = self._authorize(request)
        body = await _json_body(request)
        username = str(body.get("username") or "").strip()
        if not username or len(username) > 50:
            from aiohttp import web
            raise web.HTTPBadRequest(text=json.dumps({"error": "username must be 1-50 characters"}))
        import sqlite3
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.execute("""
                INSERT INTO user_profiles (platform, user_id, username)
                VALUES ('miniapp', ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET username=excluded.username, updated_at=datetime('now')
            """, (openid, username))
            conn.commit()
        return self._response(request, {"username": username})

    async def _method_admission_profile(self, request):
        """GET /api/methods/admission-profile

        011：响应新增 ``archive``（学籍档案，学生只读）与 ``archive_match``
        （``phone`` / ``student_id`` / ``null``）。手机号优先，学号兜底。
        """
        openid = self._authorize(request)
        import sqlite3
        self._ensure_admission_profile_columns()
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT name, phone, is_student_or_staff, student_staff_id, gender, age, undergraduate_school, learning_experience "
                "FROM admission_profiles WHERE platform='miniapp' AND user_id=?",
                (openid,)
            ).fetchone()
        if not row:
            return self._response(request, {"has_profile": False,
                                            "archive": None, "archive_match": None})
        archive, archive_match = self._lookup_archive_for_openid(
            openid, phone=str(row["phone"] or ""), student_id=str(row["student_staff_id"] or ""))
        return self._response(request, {
            "has_profile": True,
            "name": row["name"] or "",
            "phone": row["phone"] or "",
            "isStudentOrStaff": row["is_student_or_staff"] or "",
            "studentStaffId": row["student_staff_id"] or "",
            "gender": row["gender"] or "",
            "age": row["age"],
            "school": row["undergraduate_school"] or "",
            "experience": row["learning_experience"] or "",
            "archive": archive,
            "archive_match": archive_match,
        })

    def _archive_row_to_dict(self, row) -> dict:
        return {col: (row[col] if row[col] is not None else "") for col in _STUDENT_ARCHIVE_COLUMNS}

    def _lookup_archive_for_openid(self, openid: str, phone: str = "", student_id: str = ""):
        """按手机号优先、学号兜底匹配学籍档案；返回 (archive_dict|None, match_key|None)。"""
        import sqlite3
        self._ensure_student_archive_table()
        if not phone and not student_id:
            with sqlite3.connect(str(self._methods_db_path())) as conn:
                prow = conn.execute(
                    "SELECT phone, student_staff_id FROM admission_profiles "
                    "WHERE platform='miniapp' AND user_id=?", (openid,)).fetchone()
            if prow:
                phone = str(prow[0] or "")
                student_id = str(prow[1] or "")
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            if phone:
                row = conn.execute(
                    "SELECT * FROM student_archive WHERE phone=? LIMIT 1", (phone,)).fetchone()
                if row:
                    return self._archive_row_to_dict(row), "phone"
            if student_id:
                row = conn.execute(
                    "SELECT * FROM student_archive WHERE student_id=? LIMIT 1", (student_id,)).fetchone()
                if row:
                    return self._archive_row_to_dict(row), "student_id"
        return None, None

    async def _method_admission_profile_update(self, request):
        """PUT /api/methods/admission-profile"""
        openid = self._authorize(request)
        import sqlite3
        from aiohttp import web
        body = await _json_body(request)

        name = str(body.get("name") or "").strip()
        phone = str(body.get("phone") or "").strip()
        is_sos = str(body.get("isStudentOrStaff") or "").strip()
        sid = str(body.get("studentStaffId") or "").strip()
        gender = str(body.get("gender") or "").strip()
        age_raw = body.get("age")
        school = str(body.get("school") or "").strip()
        experience = str(body.get("experience") or "").strip()

        # Validation
        if not name:
            raise web.HTTPBadRequest(text=json.dumps({"error": "请填写姓名"}))
        if len(name) > 16:
            raise web.HTTPBadRequest(text=json.dumps({"error": "姓名最多16个字符"}))
        if not phone:
            raise web.HTTPBadRequest(text=json.dumps({"error": "请填写电话"}))
        if len(phone) > 16:
            raise web.HTTPBadRequest(text=json.dumps({"error": "电话最多16个字符"}))
        if is_sos not in ("是", "否"):
            raise web.HTTPBadRequest(text=json.dumps({"error": "请选择是否为在校学生/职工"}))
        if is_sos == "是":
            if not sid:
                raise web.HTTPBadRequest(text=json.dumps({"error": "请填写学/工号"}))
            if len(sid) > 16:
                raise web.HTTPBadRequest(text=json.dumps({"error": "学/工号最多16个字符"}))
        if gender and gender not in ("男", "女"):
            raise web.HTTPBadRequest(text=json.dumps({"error": "性别只能为男或女"}))
        age = None
        if age_raw is not None and str(age_raw).strip():
            try:
                age = int(age_raw)
            except (ValueError, TypeError):
                raise web.HTTPBadRequest(text=json.dumps({"error": "年龄必须为整数"}))
            if age < 1 or age > 150:
                raise web.HTTPBadRequest(text=json.dumps({"error": "年龄必须在1-150之间"}))
        if school and len(school) > 16:
            raise web.HTTPBadRequest(text=json.dumps({"error": "毕业院校最多16个字符"}))
        if experience and len(experience) > 1024:
            raise web.HTTPBadRequest(text=json.dumps({"error": "项目/学习经历最多1024个字符"}))

        self._ensure_admission_profile_columns()
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.execute("""
                INSERT INTO admission_profiles (platform, user_id, name, phone, is_student_or_staff, student_staff_id, gender, age, undergraduate_school, learning_experience)
                VALUES ('miniapp', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    name=excluded.name, phone=excluded.phone,
                    is_student_or_staff=excluded.is_student_or_staff,
                    student_staff_id=excluded.student_staff_id,
                    gender=excluded.gender, age=excluded.age,
                    undergraduate_school=excluded.undergraduate_school,
                    learning_experience=excluded.learning_experience,
                    updated_at=datetime('now')
            """, (openid, name, phone, is_sos, sid if is_sos == "是" else "", gender, age, school, experience))
            conn.commit()
        return self._response(request, {"reply": "档案已保存"})

    async def _method_phone_bind(self, request):
        """POST /api/methods/phone-bind — 绑定并验证手机号。

        真实模式（默认）：body {code} → 微信 phonenumber.getuserphonenumber 换号。
        dev 模式（PHONE_VERIFY_MODE=dev）：body {mock_phone} 直接绑定，跳过微信验证，
        供开发者工具/开发环境联调。
        """
        from aiohttp import ClientSession, ClientTimeout, web
        openid = self._authorize(request)
        body = await _json_body(request)
        verify_mode = os.getenv("PHONE_VERIFY_MODE", "").strip().lower()
        phone = ""
        if verify_mode == "dev":
            phone = str(body.get("mock_phone") or "").strip()
            if not phone:
                raise web.HTTPBadRequest(text=json.dumps({"error": "mock_phone is required in dev mode"}))
        else:
            code = str(body.get("code") or "").strip()
            if not code:
                raise web.HTTPBadRequest(text=json.dumps({"error": "code is required"}))
            appid = os.getenv("WECHAT_MINIAPP_APPID", "").strip()
            secret = os.getenv("WECHAT_MINIAPP_APPSECRET", "").strip()
            if not appid or not secret:
                raise web.HTTPServiceUnavailable(text=json.dumps({"error": "miniapp credentials are not configured"}))
            async with ClientSession(timeout=ClientTimeout(total=10)) as client:
                async with client.get(
                    "https://api.weixin.qq.com/cgi-bin/token",
                    params={"grant_type": "client_credential", "appid": appid, "secret": secret},
                ) as response:
                    token_payload = await response.json(content_type=None)
            access_token = str(token_payload.get("access_token") or "")
            if not access_token:
                raise web.HTTPBadGateway(text=json.dumps({
                    "error": "wechat access_token failed",
                    "errcode": token_payload.get("errcode"), "errmsg": token_payload.get("errmsg"),
                }))
            async with ClientSession(timeout=ClientTimeout(total=10)) as client:
                async with client.post(
                    "https://api.weixin.qq.com/wxa/business/getuserphonenumber",
                    params={"access_token": access_token},
                    json={"code": code},
                ) as response:
                    payload = await response.json(content_type=None)
            if payload.get("errcode") not in (None, 0):
                raise web.HTTPBadRequest(text=json.dumps({
                    "error": "wechat getPhoneNumber failed",
                    "errcode": payload.get("errcode"), "errmsg": payload.get("errmsg"),
                }))
            phone = str(payload.get("phone_info", {}).get("purePhoneNumber") or "").strip()
            if not phone:
                raise web.HTTPBadGateway(text=json.dumps({"error": "phone number missing in wechat response"}))
        if not re.fullmatch(r"1\d{10}", phone):
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid phone number"}))
        self._ensure_admission_profile_columns()
        import sqlite3
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.execute(
                """INSERT INTO admission_profiles (platform, user_id, phone, phone_verified, updated_at)
                   VALUES ('miniapp', ?, ?, 1, datetime('now'))
                   ON CONFLICT(platform, user_id)
                   DO UPDATE SET phone=excluded.phone, phone_verified=1, updated_at=datetime('now')""",
                (openid, phone),
            )
            conn.commit()
        # 电话白名单命中 → 写入 roles.json 持久化身份（白名单是开户通道，roles.json 是事实来源）
        whitelist_role = self._whitelist_role_for_phone(phone)
        if whitelist_role:
            try:
                from knowledge_base.auth.role_store import set_role
                set_role("miniapp", openid, whitelist_role)
            except Exception:
                logger.exception("failed to persist whitelist role for %s", openid)
        return self._response(request, {
            "phone": phone, "phone_verified": True,
            "whitelist_role": whitelist_role or "",
        })

    # ── 电话白名单（方案 A：手机号 → 身份自动匹配）──────────────

    def _whitelist_role_for_phone(self, phone: str) -> str | None:
        """Return the effective whitelist role for a verified phone, or None."""
        if not phone:
            return None
        import sqlite3
        try:
            with sqlite3.connect(str(self._methods_db_path())) as conn:
                row = conn.execute(
                    "SELECT role FROM phone_whitelist WHERE phone=? ORDER BY id DESC LIMIT 1",
                    (phone,)
                ).fetchone()
            return str(row[0]) if row else None
        except (OSError, sqlite3.Error):
            logger.exception("failed to read phone whitelist")
            return None

    def _sync_whitelist_roles(self, phones=None) -> dict:
        """白名单 → roles.json 同步（最新为准，2026-09-08）。

        同一手机号可存在于多份名单（批次），生效角色取**最新一条**条目；对已绑定
        手机号（admission_profiles.phone_verified=1）的用户写入 roles.json——可升可降。
        未绑定的白名单条目计入 candidates，待其绑定手机时由 _method_phone_bind 生效。
        不在名单里的用户不受影响。
        """
        import sqlite3
        if phones is not None and not phones:
            return {"changed": 0, "unchanged": 0, "candidates": 0}
        try:
            from knowledge_base.auth.role_store import resolve_role, set_role
            with sqlite3.connect(str(self._methods_db_path())) as conn:
                conn.row_factory = sqlite3.Row
                if phones is None:
                    wl_rows = conn.execute(
                        "SELECT phone, role FROM phone_whitelist ORDER BY id").fetchall()
                else:
                    placeholders = ",".join("?" * len(phones))
                    wl_rows = conn.execute(
                        f"SELECT phone, role FROM phone_whitelist WHERE phone IN ({placeholders}) "
                        "ORDER BY id", list(phones)).fetchall()
                bound = {str(r[1]): str(r[0]) for r in conn.execute(
                    "SELECT user_id, phone FROM admission_profiles "
                    "WHERE platform='miniapp' AND phone_verified=1 AND phone IS NOT NULL")}
            effective = {}
            for row in wl_rows:                      # id 升序 → 后到的条目覆盖先前的
                effective[str(row["phone"])] = str(row["role"] or "")
            changed = unchanged = candidates = 0
            for phone, wl_role in effective.items():
                openid = bound.get(phone)
                if not openid:
                    candidates += 1
                elif resolve_role("miniapp", openid) == wl_role:
                    unchanged += 1
                elif set_role("miniapp", openid, wl_role):
                    changed += 1
                else:
                    unchanged += 1
            if changed:
                logger.info("whitelist role sync: changed=%d unchanged=%d candidates=%d",
                            changed, unchanged, candidates)
            return {"changed": changed, "unchanged": unchanged, "candidates": candidates}
        except Exception:
            logger.exception("whitelist → roles.json sync failed")
            return {"changed": 0, "unchanged": 0, "candidates": 0, "error": "sync failed"}

    def _resync_phones_after_delete(self, phones: list) -> dict:
        """批次/单条删除后重算受影响手机号的生效身份。

        仍有条目的手机号走 _sync_whitelist_roles（回落到更早的名单）；条目已清空的
        手机号按「无白名单」处理，已绑定时撤销显式角色。
        """
        import sqlite3
        from knowledge_base.auth.role_store import unset_role
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            bound = {str(r[1]): str(r[0]) for r in conn.execute(
                "SELECT user_id, phone FROM admission_profiles "
                "WHERE platform='miniapp' AND phone_verified=1 AND phone IS NOT NULL")}
            remaining = []
            for phone in phones:
                row = conn.execute(
                    "SELECT 1 FROM phone_whitelist WHERE phone=? LIMIT 1", (phone,)).fetchone()
                if row:
                    remaining.append(phone)
        result = self._sync_whitelist_roles(remaining) if remaining else {
            "changed": 0, "unchanged": 0, "candidates": 0}
        demoted = 0
        for phone in phones:
            openid = bound.get(phone)
            if openid and phone not in remaining:
                demoted += 1 if unset_role("miniapp", openid) else 0
        result["demoted"] = demoted
        return result

    # 「降级」提示权重：仅用于导入/删除的二次确认，不参与任何写入门槛
    _ROLE_WEIGHT = {"student": 0, "teacher": 1, "admin": 2, "owner": 3}

    def _bound_openids_by_phone(self) -> dict:
        """已绑定手机号 → openid（platform=miniapp）。"""
        import sqlite3
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            return {str(r[1]): str(r[0]) for r in conn.execute(
                "SELECT user_id, phone FROM admission_profiles "
                "WHERE platform='miniapp' AND phone_verified=1 AND phone IS NOT NULL")}

    def _preview_import_changes(self, last_by_phone: dict) -> dict:
        """导入预览（不写库）：每个已绑定用户的身份变化 + 降级名单。

        to = 文件里的角色；from = 当前 roles.json 身份（与 _sync_whitelist_roles 同口径）。
        """
        from knowledge_base.auth.role_store import resolve_role
        bound = self._bound_openids_by_phone()
        changes, downgrades, candidates = [], [], 0
        for phone, (rn, name, staff, role) in last_by_phone.items():
            openid = bound.get(phone)
            if not openid:
                candidates += 1
                continue
            current = resolve_role("miniapp", openid)
            if current == role:
                continue
            item = {"phone": phone, "name": name, "from": current, "to": role}
            changes.append(item)
            if self._ROLE_WEIGHT.get(role, 0) < self._ROLE_WEIGHT.get(current, 0):
                downgrades.append(item)
        return {"changes": changes, "downgrades": downgrades, "candidates": candidates}

    def _preview_delete_changes(self, phones: list, exclude_batch: str = "") -> dict:
        """删除预览（不写库）：受影响号码的身份变化 + 降级 + 「彻底消失」名单。

        exclude_batch 为空 → 单条删除语义（移除该号码全部条目）；
        否则 → 批次删除语义（移除该批次条目，回落到其他名单）。
        """
        import sqlite3
        from knowledge_base.auth.role_store import resolve_role
        bound = self._bound_openids_by_phone()
        changes, downgrades, vanish = [], [], []
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            for phone in phones:
                if exclude_batch:
                    row = conn.execute(
                        "SELECT role FROM phone_whitelist WHERE phone=? AND batch_id<>? "
                        "ORDER BY id DESC LIMIT 1", (phone, exclude_batch)).fetchone()
                else:
                    row = None
                after = str(row[0]) if row else None
                openid = bound.get(phone)
                if not openid:
                    continue
                current = resolve_role("miniapp", openid)
                item = {"phone": phone, "from": current, "to": after or "student",
                        "only_here": after is None}
                if after is None:
                    vanish.append(item)
                if current == item["to"]:
                    continue
                changes.append(item)
                if self._ROLE_WEIGHT.get(item["to"], 0) < self._ROLE_WEIGHT.get(current, 0):
                    downgrades.append(item)
        return {"changes": changes, "downgrades": downgrades, "vanish": vanish}

    # ── Knowledge base management（设计 §4：列表 / 删除 / 批量 / 孤儿扫描）──

    _KNOWLEDGE_ROLES = {"student", "teacher", "admin", "owner"}

    def _knowledge_scopes(self, role: str, openid: str) -> set:
        """调用者可列 / 可删的 scope 集合（§4.6 矩阵，与写侧同源）。

        guest 与未知角色为空集：无个人库，不可列、不可删、不可传（D11）。
        """
        if role not in self._KNOWLEDGE_ROLES:
            return set()
        scopes = {f"users/{openid}"}
        if role in ("teacher", "admin", "owner"):
            scopes.add("teachers")
        if role in ("admin", "owner"):
            scopes.add("global")
        return scopes

    @staticmethod
    def _knowledge_repository():
        from knowledge_base.repository.chroma_repository import ChromaRepository
        return ChromaRepository.instance()

    def _knowledge_identity(self, request) -> tuple[str, str]:
        """鉴权（无 token → 401）+ 解析角色；scope 权限另由 `_knowledge_scope_guard` 校验。

        DELETE 端点的 scope 在 body 里，必须先鉴权再解析 body——否则无 token 的
        空 body 会先撞上 400，与白名单 DELETE 端点的 401 口径不一致（§8 路由探活）。
        """
        from knowledge_base.auth.role_store import resolve_role
        openid = self._authorize(request)
        return openid, resolve_role("miniapp", openid)

    def _knowledge_scope_guard(self, openid: str, role: str, scope: str) -> str:
        """校验 *scope* 在调用者矩阵内（403）；空值默认 ``users/{openid}``（同 `_upload`）。"""
        from aiohttp import web
        scope = str(scope or "").strip() or f"users/{openid}"
        allowed = self._knowledge_scopes(role, openid)
        if scope not in allowed:
            raise web.HTTPForbidden(text=json.dumps({
                "error": "permission denied", "role": role, "required": sorted(allowed),
            }))
        return scope

    def _knowledge_actor(self, request, scope: str = "") -> tuple[str, str, str]:
        """鉴权 + scope 校验；返回 ``(openid, role, scope)``（§4.1 / §4.5）。"""
        openid, role = self._knowledge_identity(request)
        return openid, role, self._knowledge_scope_guard(openid, role, scope)

    @staticmethod
    def _knowledge_http_error(exc: _KnowledgeDeleteError):
        from aiohttp import web
        classes = {
            "chroma_unavailable": web.HTTPServiceUnavailable,
            "partial_delete": web.HTTPInternalServerError,
            "file_not_found": web.HTTPNotFound,
        }
        cls = classes.get(exc.code, web.HTTPInternalServerError)
        return cls(text=json.dumps(exc.payload()))

    @staticmethod
    def _knowledge_sync_bm25(scope: str, filename: str) -> None:
        """best-effort 同步进程内 BM25 索引（§4.3）：失败不影响删除结果。"""
        try:
            from knowledge_base.retrieval.bm25_search import get_bm25_index
            get_bm25_index().remove_file(scope, filename)
        except Exception:
            logger.warning("BM25 index sync failed after delete (scope=%s file=%s)",
                           scope, filename, exc_info=True)

    @staticmethod
    def _knowledge_write_audit(*, openid: str, role: str, scope: str, filename: str,
                               node_count: int, metadata_rows_removed: int, op: str) -> None:
        """best-effort 审计（§4.2）：写入失败不得让已成功的删除变成 500。"""
        try:
            from knowledge_base.retrieval.response_verifier import get_audit_logger
            audit = get_audit_logger()
            if audit is None:
                logger.warning("audit logger unavailable; kb_delete dropped (scope=%s file=%s)",
                               scope, filename)
                return
            audit.log_event(
                event_type="kb_delete", user_id=openid, role=role,
                filename=filename, scope=scope, node_count=node_count,
                detail={"op": op, "metadata_rows_removed": metadata_rows_removed},
            )
        except Exception:
            logger.warning("audit write failed after delete (scope=%s file=%s)",
                           scope, filename, exc_info=True)

    def _knowledge_preview(self, scope: str, filename: str) -> dict[str, Any]:
        """§4.2 第 2 步预览（只读，不写任何东西）。"""
        from knowledge_base.core.display_names import resolve_display_name
        repo = self._knowledge_repository()
        try:
            node_count = repo.count_nodes(scope, filename, raise_on_error=True)
            source = repo.get_file_source(scope, filename)
        except Exception as exc:
            logger.warning("knowledge preview failed (scope=%s file=%s)", scope, filename, exc_info=True)
            raise _KnowledgeDeleteError("chroma_unavailable") from exc
        rows = [r for r in repo.list_file_metadata_by_scope(scope) if r["filename"] == filename]
        return {
            "scope": scope, "filename": filename,
            "display_name": resolve_display_name(scope, filename),
            "node_count": node_count, "source": source,
            "metadata_rows": [
                {"user_id": r["user_id"], "ingested_at": r["ingested_at"]} for r in rows
            ],
        }

    def _knowledge_delete_one(self, scope: str, filename: str, *,
                              openid: str, role: str, op: str) -> dict[str, Any]:
        """删除单个文件：Chroma 节点 → 元数据行 → BM25 → 审计（§4.2 第 1、3–6 步）。

        同步函数（Chroma HTTP + SQLite 写），调用方必须走 ``asyncio.to_thread``。
        失败抛 ``_KnowledgeDeleteError``；失败时**保留元数据行**以便重试。
        """
        repo = self._knowledge_repository()
        try:
            node_count = repo.count_nodes(scope, filename, raise_on_error=True)
        except Exception as exc:
            logger.warning("knowledge delete: count failed (scope=%s file=%s)",
                           scope, filename, exc_info=True)
            raise _KnowledgeDeleteError("chroma_unavailable") from exc
        rows = [r for r in repo.list_file_metadata_by_scope(scope) if r["filename"] == filename]
        if node_count == 0 and not rows:
            raise _KnowledgeDeleteError("file_not_found")
        if node_count:
            repo.delete_nodes(scope, filename)  # 返回值恒 True，不作为成功判据
            try:
                remaining = repo.count_nodes(scope, filename, raise_on_error=True)
            except Exception as exc:
                logger.warning("knowledge delete: re-count failed (scope=%s file=%s)",
                               scope, filename, exc_info=True)
                raise _KnowledgeDeleteError("partial_delete", nodes_remaining=node_count) from exc
            if remaining:
                logger.warning("knowledge delete: partial delete (scope=%s file=%s remaining=%d)",
                               scope, filename, remaining)
                raise _KnowledgeDeleteError("partial_delete", nodes_remaining=remaining)
        for row in rows:
            repo.delete_file_metadata(row["user_id"], filename, scope)
        self._knowledge_sync_bm25(scope, filename)
        self._knowledge_write_audit(openid=openid, role=role, scope=scope, filename=filename,
                                    node_count=node_count, metadata_rows_removed=len(rows), op=op)
        return {
            "filename": filename, "deleted": True,
            "nodes_removed": node_count, "metadata_rows_removed": len(rows),
        }

    def _knowledge_orphan_scan(self, scope: str) -> dict[str, Any]:
        """扫描「元数据有行、Chroma 0 节点」的残留行（§4.5，只读）。"""
        from knowledge_base.core.display_names import resolve_display_name
        repo = self._knowledge_repository()
        try:
            if repo.count_collection_nodes() == 0:
                raise _KnowledgeDeleteError("chroma_unavailable")
            counts = repo.list_source_files(scope)
        except _KnowledgeDeleteError:
            raise
        except Exception as exc:
            logger.warning("knowledge orphan scan failed (scope=%s)", scope, exc_info=True)
            raise _KnowledgeDeleteError("chroma_unavailable") from exc
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in repo.list_file_metadata_by_scope(scope):
            grouped.setdefault(row["filename"], []).append(
                {"user_id": row["user_id"], "ingested_at": row["ingested_at"]})
        orphans = [
            {"filename": filename, "display_name": resolve_display_name(scope, filename),
             "uploaders": uploaders}
            for filename, uploaders in grouped.items() if filename not in counts
        ]
        return {"scanned_nodes": sum(counts.values()), "orphans": orphans}

    def _knowledge_personal_extras(self, openid: str, role: str, scope: str,
                                   filenames: list[str]) -> tuple[dict[str, int] | None, dict[str, str]]:
        """个人库的配额与逐文件入库状态（§4.7.1，同步；调用方走 ``asyncio.to_thread``）。

        admin / owner 豁免计数（`quota_manager.py:26-28`）→ 配额返回 ``None``。
        单文件计数失败**不写进结果**，调用方据此省略 `status` 字段——不能把
        「查不到」显示成「空」。
        """
        from knowledge_base.core.quota_manager import QuotaManager
        repo = self._knowledge_repository()
        quota = None
        if role not in ("admin", "owner"):
            quota = {"used": repo.get_user_file_count(openid),
                     "limit": QuotaManager(repo).max_user_files}
        statuses: dict[str, str] = {}
        for filename in filenames:
            try:
                empty = repo.count_nodes(scope, filename, raise_on_error=True) == 0
            except Exception:
                logger.warning("knowledge list: count failed (scope=%s file=%s)",
                               scope, filename, exc_info=True)
                continue
            statuses[filename] = "empty" if empty else "ok"
        return quota, statuses

    async def _method_knowledge_list(self, request):
        """GET /api/methods/knowledge?scope=…&order=asc|desc — 列出已入库文件（§4.1 / §4.7.1）"""
        from aiohttp import web
        from knowledge_base.core.display_names import is_jxtz, resolve_display_name
        openid, role, scope = self._knowledge_actor(request, str(request.rel_url.query.get("scope") or ""))
        order_raw = str(request.rel_url.query.get("order") or "asc").strip().lower()
        if order_raw not in ("asc", "desc"):
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_order"}))
        rows = self._knowledge_repository().list_file_metadata_by_scope(scope, order_raw)
        files: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = files.setdefault(row["filename"], {
                "filename": row["filename"],
                "display_name": resolve_display_name(scope, row["filename"]),
                "source": "jxtz" if is_jxtz(row["filename"]) else "file",
                "node_count": None,  # 逐文件计数是 N+1，只在删除预览里算（§4.1）
                "uploaders": [],
            })
            item["uploaders"].append({"user_id": row["user_id"], "ingested_at": row["ingested_at"]})
        quota = None
        if scope == f"users/{openid}":  # 配额与入库状态只对个人库填充（§4.7.1）
            quota, statuses = await asyncio.to_thread(
                self._knowledge_personal_extras, openid, role, scope, list(files))
            for filename, status in statuses.items():
                files[filename]["status"] = status
        # 按入库时间排序：教务通知文件名是 jxtz_<不补零数字id>_…，字典序≠时间序
        # （jxtz_9991 > jxtz_10476），按文件名排会把最新入库的排到最后/看不到。
        from knowledge_base.core.file_listing import sort_by_ingested
        ordered = sort_by_ingested(list(files.values()), order_raw)
        return self._response(request, {
            "success": True, "scope": scope, "total": len(ordered),
            "quota": quota, "files": ordered,
        })

    async def _method_knowledge_content(self, request):
        """GET /api/methods/knowledge/content?scope=…&filename=… — 原文只读预览（§4.7.2）"""
        from aiohttp import web
        from knowledge_base.core.display_names import resolve_display_name
        _, _, scope = self._knowledge_actor(request, str(request.rel_url.query.get("scope") or ""))
        filename_raw = request.rel_url.query.get("filename")
        if not isinstance(filename_raw, str) or not filename_raw.strip():
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_filename"}))
        filename = filename_raw.strip()
        try:
            documents = await asyncio.to_thread(
                self._knowledge_repository().get_file_documents, scope, filename)
        except Exception as exc:
            logger.warning("knowledge content failed (scope=%s file=%s)", scope, filename, exc_info=True)
            raise self._knowledge_http_error(_KnowledgeDeleteError("chroma_unavailable")) from exc
        if not documents:
            raise self._knowledge_http_error(_KnowledgeDeleteError("file_not_found"))
        text = "\n".join(documents)
        return self._response(request, {
            "success": True, "filename": filename,
            "display_name": resolve_display_name(scope, filename),
            "text": text[:_KNOWLEDGE_PREVIEW_LIMIT],
            "truncated": len(text) > _KNOWLEDGE_PREVIEW_LIMIT,
            "node_count": len(documents),
        })

    async def _method_knowledge_audit(self, request):
        """GET /api/methods/knowledge/audit?limit=… — 删除操作历史（§4.7.3，仅 admin/owner）"""
        from aiohttp import web
        _, role = self._knowledge_identity(request)
        if role not in ("admin", "owner"):
            raise web.HTTPForbidden(text=json.dumps({
                "error": "permission denied", "role": role, "required": ["admin", "owner"]}))
        limit_raw = str(request.rel_url.query.get("limit") or "").strip()
        if limit_raw and not (limit_raw.isdigit() and int(limit_raw) >= 1):
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_limit"}))
        limit = int(limit_raw) if limit_raw else 50
        from knowledge_base.retrieval.response_verifier import get_audit_logger
        audit = get_audit_logger()
        if audit is None:
            logger.warning("knowledge audit: audit logger unavailable")
            raise web.HTTPServiceUnavailable(text=json.dumps({"error": "audit_unavailable"}))
        try:
            rows = await asyncio.to_thread(audit.query, limit, "kb_delete")
        except Exception as exc:
            logger.warning("knowledge audit query failed", exc_info=True)
            raise web.HTTPServiceUnavailable(text=json.dumps({"error": "audit_unavailable"})) from exc
        events = [{
            "time": row.get("timestamp"), "user_id": row.get("user_id"),
            "role": row.get("role"), "scope": row.get("scope"),
            "filename": row.get("filename"), "node_count": row.get("node_count"),
            "detail": row.get("detail") or {},
        } for row in rows]
        return self._response(request, {"success": True, "events": events})

    async def _method_knowledge_delete(self, request):
        """DELETE /api/methods/knowledge — 删除文件（§4.2）"""
        from aiohttp import web
        openid, role = self._knowledge_identity(request)
        body = await _json_body(request)
        scope_raw, filename_raw = body.get("scope"), body.get("filename")
        if not isinstance(scope_raw, str) or not scope_raw.strip():
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_scope"}))
        if not isinstance(filename_raw, str) or not filename_raw.strip():
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_filename"}))
        filename = filename_raw.strip()
        scope = self._knowledge_scope_guard(openid, role, scope_raw)
        if body.get("dry_run"):
            try:
                preview = await asyncio.to_thread(self._knowledge_preview, scope, filename)
            except _KnowledgeDeleteError as exc:
                raise self._knowledge_http_error(exc) from exc
            return self._response(request, {"success": True, "dry_run": True, "preview": preview})
        try:
            result = await asyncio.to_thread(
                self._knowledge_delete_one, scope, filename,
                openid=openid, role=role, op="single")
        except _KnowledgeDeleteError as exc:
            raise self._knowledge_http_error(exc) from exc
        return self._response(request, {"success": True, **result})

    async def _method_knowledge_batch_delete(self, request):
        """DELETE /api/methods/knowledge/batch — 批量删除（§4.4）"""
        from aiohttp import web
        openid, role = self._knowledge_identity(request)
        body = await _json_body(request)
        scope_raw, filenames_raw = body.get("scope"), body.get("filenames")
        if not isinstance(scope_raw, str) or not scope_raw.strip():
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_scope"}))
        if not isinstance(filenames_raw, list):
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_filename"}))
        filenames: list[str] = []
        for item in filenames_raw:
            if not isinstance(item, str) or not item.strip():
                raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_filename"}))
            if item.strip() not in filenames:
                filenames.append(item.strip())
        if len(filenames) > 50:
            raise web.HTTPBadRequest(text=json.dumps({"error": "too_many_files", "max": 50}))
        scope = self._knowledge_scope_guard(openid, role, scope_raw)
        if body.get("dry_run"):
            items, total_nodes, by_source = [], 0, {}
            for filename in filenames:
                try:
                    preview = await asyncio.to_thread(self._knowledge_preview, scope, filename)
                except _KnowledgeDeleteError as exc:
                    raise self._knowledge_http_error(exc) from exc
                items.append(preview)
                total_nodes += preview["node_count"]
                source = preview["source"] or "file"
                by_source[source] = by_source.get(source, 0) + preview["node_count"]
            return self._response(request, {
                "success": True, "dry_run": True, "scope": scope,
                "total_nodes": total_nodes, "by_source": by_source, "items": items,
            })
        results = []
        for filename in filenames:
            try:
                one = await asyncio.to_thread(
                    self._knowledge_delete_one, scope, filename,
                    openid=openid, role=role, op="batch")
                results.append({"filename": filename, "ok": True,
                                "nodes_removed": one["nodes_removed"],
                                "metadata_rows_removed": one["metadata_rows_removed"]})
            except _KnowledgeDeleteError as exc:
                results.append({"filename": filename, "ok": False, **exc.payload()})
        return self._response(request, {
            "success": True, "scope": scope,
            "deleted": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "results": results,
        })

    async def _method_knowledge_orphans(self, request):
        """GET /api/methods/knowledge/orphans?scope=… — 扫描孤儿元数据（§4.5）"""
        _, _, scope = self._knowledge_actor(request, str(request.rel_url.query.get("scope") or ""))
        try:
            result = await asyncio.to_thread(self._knowledge_orphan_scan, scope)
        except _KnowledgeDeleteError as exc:
            raise self._knowledge_http_error(exc) from exc
        return self._response(request, {"success": True, "scope": scope, **result})

    async def _method_phone_whitelist_list(self, request):
        """GET /api/methods/phone-whitelist — 白名单列表（admin/owner）"""
        from aiohttp import web
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        import sqlite3
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT phone, staff_id, name, role, note, updated_at, batch_id
                   FROM phone_whitelist w
                   WHERE id = (SELECT MAX(id) FROM phone_whitelist WHERE phone = w.phone)
                   ORDER BY updated_at DESC"""
            ).fetchall()
            batches = conn.execute(
                """SELECT b.batch_id, b.label, b.created_at,
                          (SELECT COUNT(*) FROM phone_whitelist w WHERE w.batch_id = b.batch_id) AS count
                   FROM phone_whitelist_batch b ORDER BY b.id DESC"""
            ).fetchall()
        return self._response(request, {
            "items": [dict(r) for r in rows],
            "count": len(rows),
            "batches": [dict(r) for r in batches],
        })

    async def _method_phone_whitelist_add(self, request):
        """POST /api/methods/phone-whitelist — 添加/更新白名单条目（admin/owner）"""
        from aiohttp import web
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        body = await _json_body(request)
        phone = str(body.get("phone") or "").strip()
        staff_id = str(body.get("staff_id") or "").strip()
        name = str(body.get("name") or "").strip()
        role = str(body.get("role") or "student").strip()
        note = str(body.get("note") or "").strip()
        if not re.fullmatch(r"1\d{10}", phone):
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid phone number"}))
        if role not in {"student", "teacher", "admin", "owner"}:
            raise web.HTTPBadRequest(text=json.dumps({"error": "role must be student/teacher/admin/owner"}))
        import sqlite3
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.execute(
                """INSERT INTO phone_whitelist (batch_id, phone, staff_id, name, role, note, updated_at)
                   VALUES ('manual', ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(phone, batch_id) DO UPDATE SET
                     staff_id=excluded.staff_id, name=excluded.name,
                     role=excluded.role, note=excluded.note, updated_at=datetime('now')""",
                (phone, staff_id, name, role, note),
            )
            conn.commit()
        role_sync = self._sync_whitelist_roles([phone])
        return self._response(request, {"success": True, "phone": phone, "role": role,
                                        "role_sync": role_sync})

    async def _method_phone_whitelist_delete(self, request):
        """DELETE /api/methods/phone-whitelist — 彻底移除该号码（admin/owner）。

        删除它在**所有**名单里的条目；已绑定用户的身份按「无白名单」回退。
        """
        from aiohttp import web
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        body = await _json_body(request)
        phone = str(body.get("phone") or "").strip()
        if not phone:
            raise web.HTTPBadRequest(text=json.dumps({"error": "phone is required"}))
        if body.get("dry_run"):
            # 预览：不删任何数据，只回报身份变化，供前端二次确认
            return self._response(request, {"dry_run": True,
                                            "preview": self._preview_delete_changes([phone])})
        import sqlite3
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.execute("DELETE FROM phone_whitelist WHERE phone=?", (phone,))
            conn.commit()
        role_sync = self._resync_phones_after_delete([phone])
        return self._response(request, {"success": True, "phone": phone,
                                        "role_sync": {"demoted": role_sync.get("demoted", 0)}})

    async def _method_phone_whitelist_batch_delete(self, request):
        """DELETE /api/methods/phone-whitelist/batch — 删除一份导入名单（admin/owner）。

        条目按批次删除；受影响号码若在其他名单里还有条目，身份**回落到那份名单**。
        """
        from aiohttp import web
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        body = await _json_body(request)
        batch_id = str(body.get("batch_id") or "").strip()
        if not batch_id:
            raise web.HTTPBadRequest(text=json.dumps({"error": "batch_id is required"}))
        import sqlite3
        if body.get("dry_run"):
            # 预览：不删任何数据，只回报身份变化与「删除后彻底消失」的人
            with sqlite3.connect(str(self._methods_db_path())) as conn:
                phones = [str(r[0]) for r in conn.execute(
                    "SELECT DISTINCT phone FROM phone_whitelist WHERE batch_id=?", (batch_id,))]
            return self._response(request, {
                "dry_run": True, "phones": len(phones),
                "preview": self._preview_delete_changes(phones, exclude_batch=batch_id)})
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            phones = [str(r[0]) for r in conn.execute(
                "SELECT DISTINCT phone FROM phone_whitelist WHERE batch_id=?", (batch_id,))]
            conn.execute("DELETE FROM phone_whitelist WHERE batch_id=?", (batch_id,))
            conn.execute("DELETE FROM phone_whitelist_batch WHERE batch_id=?", (batch_id,))
            conn.commit()
        role_sync = self._resync_phones_after_delete(phones)
        return self._response(request, {"success": True, "batch_id": batch_id,
                                        "phones": len(phones), "role_sync": role_sync})

    async def _method_phone_whitelist_import(self, request):
        """POST /api/methods/phone-whitelist/import — 兼容旧版：文件内「角色」列决定身份。

        011 起前端改用 /import/student|teacher|admin 三个入口，本端点保留兼容。
        """
        return await self._import_whitelist(request)

    async def _method_phone_whitelist_import_student(self, request):
        """POST /api/methods/phone-whitelist/import/student — 学籍信息导入（全部学生）。

        文件为学籍信息表（「学号/姓名/个人手机」+ 档案字段）：① upsert
        ``student_archive``（学号为主键）；② 有合法手机号的行写 ``phone_whitelist``
        （role=student）。
        """
        return await self._import_whitelist(request, fixed_role="student", student_mode=True)

    async def _method_phone_whitelist_import_teacher(self, request):
        """POST /api/methods/phone-whitelist/import/teacher — 教师名单导入（全部教师）。"""
        return await self._import_whitelist(request, fixed_role="teacher")

    async def _method_phone_whitelist_import_admin(self, request):
        """POST /api/methods/phone-whitelist/import/admin — 管理员名单导入（全部管理员）。"""
        return await self._import_whitelist(request, fixed_role="admin")

    async def _import_whitelist(self, request, *, fixed_role=None, student_mode=False):
        """共享实现：解析 Excel → phone_whitelist（+ student_archive）→ 同步身份。

        011：学生/教师/管理员分开入口，角色由 ``fixed_role`` 固定（文件内角色列被
        忽略）；``student_mode=True`` 按学籍信息表解析并额外 upsert
        ``student_archive``（无手机号的行也入档案，只是不进白名单）。
        """
        from aiohttp import web
        import re as _re
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        # 兜底建列/建表：全新库首次导入时也能走 preview/sync
        self._ensure_admission_profile_columns()
        self._ensure_student_archive_table()
        if not (request.content_type or "").startswith("multipart/"):
            raise web.HTTPBadRequest(text=json.dumps({"error": "multipart file upload is required"}))
        reader = await request.multipart()
        part = None
        label = ""
        dry_run = ""
        import io as _io
        buf = _io.BytesIO()
        written = 0
        while candidate := await reader.next():
            if candidate.name == "file" and candidate.filename:
                part = candidate
                fname = str(part.filename or "")
                if not fname.lower().endswith((".xlsx", ".xls")):
                    raise web.HTTPUnsupportedMediaType(text=json.dumps(
                        {"error": "unsupported file type", "supported": [".xlsx", ".xls"]}))
                while chunk := await part.read_chunk(64 * 1024):
                    written += len(chunk)
                    if written > self.upload_max_bytes:
                        raise web.HTTPRequestEntityTooLarge(text=json.dumps(
                            {"error": f"file too large (max {self.upload_max_bytes} bytes)"}))
                    buf.write(chunk)
            elif candidate.name == "label":
                # 客户端显式传的名单名（multipart filename 对中文会乱码）
                label = (await candidate.text())[:100].strip()
            elif candidate.name == "dry_run":
                dry_run = (await candidate.text()).strip().lower()
        if part is None:
            raise web.HTTPBadRequest(text=json.dumps({"error": "file is required"}))
        buf.seek(0)

        # 角色值域（中文/英文双兼容；空 = 默认 student；fixed_role 时忽略文件值）
        role_map = {"学生": "student", "教师": "teacher", "老师": "teacher",
                    "管理员": "admin", "负责人": "owner",
                    "student": "student", "teacher": "teacher",
                    "admin": "admin", "owner": "owner"}
        def norm_phone(v):
            if v is None:
                return ""
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            t = str(v).strip()
            if t.isdigit():
                return t
            return ""

        def norm_text(v):
            if v is None:
                return ""
            if hasattr(v, "strftime"):
                return v.strftime("%Y-%m-%d")
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            return str(v).strip()

        try:
            rows = self._parse_archive_sheet(buf)
        except Exception as exc:
            raise web.HTTPBadRequest(text=json.dumps(
                {"error": f"无法解析 Excel：{exc}"})) from exc
        if len(rows) < 2:
            return self._response(request, {"imported": 0, "updated": 0, "skipped": 0,
                                            "failed": 0, "errors": [],
                                            "archive_imported": 0, "archive_updated": 0})
        hdr = [str(c or "").strip() for c in rows[0]]
        idx = {}
        if student_mode:
            # 学籍信息表：按 011 字段表（含别名）定位列
            for col, aliases in _STUDENT_ARCHIVE_FIELDS:
                for i, h in enumerate(hdr):
                    if h in aliases:
                        idx[col] = i
                        break
            if "student_id" not in idx:
                raise web.HTTPBadRequest(text=json.dumps(
                    {"error": "缺少「学号」列（表头请与学籍信息模板一致）"}))
        else:
            for i, h in enumerate(hdr):
                if h == "姓名":
                    idx["name"] = i
                elif h in ("电话", "手机号", "手机号码", "个人手机"):
                    idx["phone"] = i
                elif h in ("工号", "学号"):
                    idx["staff"] = i
                elif h == "角色":
                    idx["role"] = i
            if "phone" not in idx:
                raise web.HTTPBadRequest(text=json.dumps(
                    {"error": "缺少「电话/手机号」列（表头请与模板一致）"}))
        if len(rows) - 1 > 5000:
            raise web.HTTPBadRequest(text=json.dumps(
                {"error": f"行数超限（>5000 行，共 {len(rows) - 1} 行）"}))

        def _cell(row, key):
            """取表头定位到的列值；该列不存在或该行缺列时返回 None。"""
            i = idx.get(key)
            return row[i] if (i is not None and i < len(row)) else None

        last_by_phone = {}
        archives = {}
        errors = []
        failed = skipped = 0
        for rn, row in enumerate(rows[1:], start=2):   # rn = Excel 行号（含表头）
            if row is None or all(c is None or str(c).strip() == "" for c in row):
                skipped += 1
                continue
            if student_mode:
                sid = norm_text(_cell(row, "student_id"))
                if not sid:
                    failed += 1
                    if len(errors) < 100:
                        errors.append({"row": rn, "reason": "缺少学号"})
                    continue
                record = {col: norm_text(_cell(row, col)) for col in _STUDENT_ARCHIVE_COLUMNS}
                record["student_id"] = sid
                archives[sid] = record
                phone = norm_phone(_cell(row, "phone"))
                if not _re.fullmatch(r"1\d{10}", phone):
                    # 档案已入库；无合法手机号的行不进白名单（非错误）
                    continue
                last_by_phone[phone] = (rn, record.get("name", ""), sid, "student")
                continue
            phone = norm_phone(_cell(row, "phone"))
            if not _re.fullmatch(r"1\d{10}", phone):
                failed += 1
                if len(errors) < 100:
                    errors.append({"row": rn, "reason": f"电话格式无效：{_cell(row, 'phone') or ''}"})
                continue
            name = norm_text(_cell(row, "name"))
            staff = norm_text(_cell(row, "staff"))
            if fixed_role:
                role = fixed_role
            else:
                raw_role = norm_text(_cell(row, "role"))
                role = role_map.get(raw_role, "") if raw_role else "student"
                if not role:
                    failed += 1
                    if len(errors) < 100:
                        errors.append({"row": rn, "reason": f"角色值无效：{raw_role}（可选：学生/教师/管理员/负责人）"})
                    continue
            last_by_phone[phone] = (rn, name, staff, role)

        preview_only = dry_run in ("1", "true", "yes")
        existing = set()
        batch_id = "" if preview_only else (uuid.uuid4().hex if last_by_phone else "")
        archive_imported = archive_updated = 0
        if last_by_phone or (student_mode and archives):
            import sqlite3
            if student_mode:
                self._ensure_student_archive_table()
            with sqlite3.connect(str(self._methods_db_path())) as conn:
                if last_by_phone:
                    phones = list(last_by_phone)
                    placeholders = ",".join("?" * len(phones))
                    existing = {r[0] for r in conn.execute(
                        f"SELECT phone FROM phone_whitelist WHERE phone IN ({placeholders})",
                        phones).fetchall()}
                if student_mode and archives:
                    ids = list(archives)
                    placeholders = ",".join("?" * len(ids))
                    existing_ids = {r[0] for r in conn.execute(
                        f"SELECT student_id FROM student_archive WHERE student_id IN ({placeholders})",
                        ids).fetchall()}
                    archive_imported = len(ids) - len(existing_ids)
                    archive_updated = len(existing_ids)
                if not preview_only:
                    if last_by_phone:
                        conn.execute(
                            """INSERT INTO phone_whitelist_batch (batch_id, label, created_by)
                               VALUES (?, ?, ?)""",
                            (batch_id, label, openid))
                        for phone, (rn, name, staff, role) in last_by_phone.items():
                            conn.execute(
                                """INSERT INTO phone_whitelist (batch_id, phone, staff_id, name, role, note, updated_at)
                                   VALUES (?, ?, ?, ?, ?, '', datetime('now'))
                                   ON CONFLICT(phone, batch_id) DO UPDATE SET
                                     staff_id=excluded.staff_id, name=excluded.name,
                                     role=excluded.role, note=excluded.note,
                                     updated_at=datetime('now')""",
                                (batch_id, phone, staff, name, role))
                    if student_mode and archives:
                        cols = _STUDENT_ARCHIVE_COLUMNS
                        assignments = ", ".join(
                            f"{c}=excluded.{c}" for c in cols if c != "student_id")
                        for sid, record in archives.items():
                            conn.execute(
                                f"""INSERT INTO student_archive
                                      ({', '.join(cols)}, source_batch_id, updated_at)
                                    VALUES ({', '.join('?' * len(cols))}, ?, datetime('now'))
                                    ON CONFLICT(student_id) DO UPDATE SET
                                      {assignments},
                                      source_batch_id=excluded.source_batch_id,
                                      updated_at=datetime('now')""",
                                (*[record.get(c, "") for c in cols], batch_id))
                    conn.commit()
        imported = sum(1 for ph in last_by_phone if ph not in existing)
        updated = len(last_by_phone) - imported
        if preview_only:
            # 预览：只解析与计算，不写库不调 sync —— 供前端「降级二次确认」使用
            return self._response(request, {
                "dry_run": True, "imported": imported, "updated": updated,
                "skipped": skipped, "failed": failed, "errors": errors,
                "archive_imported": archive_imported, "archive_updated": archive_updated,
                "preview": self._preview_import_changes(last_by_phone)})
        role_sync = self._sync_whitelist_roles(list(last_by_phone))
        return self._response(request, {"imported": imported, "updated": updated,
                                        "skipped": skipped, "failed": failed,
                                        "errors": errors, "batch_id": batch_id,
                                        "archive_imported": archive_imported,
                                        "archive_updated": archive_updated,
                                        "role_sync": role_sync})

    def _ensure_admission_profile_columns(self):
        import sqlite3
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            for col, col_type in [
                ("is_student_or_staff", "TEXT"),
                ("student_staff_id", "TEXT"),
                ("phone_verified", "INTEGER NOT NULL DEFAULT 0"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE admission_profiles ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass  # column already exists
            conn.commit()

    # ── 学生学籍档案（我的档案，011）──────────────────────────────────

    def _ensure_student_archive_table(self):
        """建表（幂等）：学籍信息由管理员经白名单学生导入写入，学生只读。"""
        import sqlite3
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS student_archive (
                    student_id TEXT PRIMARY KEY,
                    name TEXT, gender TEXT, birth_date TEXT, ethnicity TEXT,
                    political_status TEXT, residence_college TEXT, grade TEXT,
                    school TEXT, department TEXT, host_department TEXT,
                    major TEXT, major_direction TEXT, study_years TEXT,
                    class_name TEXT, enrolled TEXT, enroll_date TEXT,
                    enroll_grade TEXT, enroll_major TEXT, phone TEXT, email TEXT,
                    emergency_contact TEXT, emergency_phone TEXT,
                    source_batch_id TEXT DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_student_archive_phone ON student_archive(phone)")
            conn.commit()

    def _parse_archive_sheet(self, buf):
        """解析学籍信息 Excel（.xlsx 用 openpyxl；.xls 需 xlrd）。

        返回 ``(headers, rows)``；``headers`` 为表头文本列表，``rows`` 为数据行。
        """
        head = buf.read(4)
        buf.seek(0)
        is_xls = head[:4] == b"\xd0\xcf\x11\xe0"
        if is_xls:
            try:
                import xlrd
            except ImportError as exc:  # pragma: no cover - 环境缺依赖
                from aiohttp import web
                raise web.HTTPBadRequest(text=json.dumps({
                    "error": "无法解析 .xls（服务端缺少 xlrd）。请另存为 .xlsx 后重试"})) from exc
            book = xlrd.open_workbook(file_contents=buf.read())
            sheet = book.sheet_by_index(0)
            rows = []
            for r in range(sheet.nrows):
                row = []
                for c in range(sheet.ncols):
                    cell = sheet.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            dt = xlrd.xldate_as_datetime(cell.value, book.datemode)
                            row.append(dt.strftime("%Y-%m-%d"))
                        except Exception:
                            row.append(cell.value)
                    else:
                        row.append(cell.value)
                rows.append(row)
        else:
            import openpyxl
            wb = openpyxl.load_workbook(buf, read_only=True, data_only=True)
            ws = wb[wb.sheetnames[0]]
            rows = list(ws.iter_rows(values_only=True))
            wb.close()
        return rows

    async def _method_student_archive_list(self, request):
        """GET /api/methods/student-archive — 学籍档案查询（admin/owner）。

        query: keyword（姓名/学号/手机号模糊）、limit（默认 200，上限 500）。
        """
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        keyword = (request.query.get("keyword") or "").strip()
        try:
            limit = int(request.query.get("limit") or 200)
        except (TypeError, ValueError):
            limit = 200
        limit = max(1, min(limit, 500))
        self._ensure_student_archive_table()
        import sqlite3
        cols = ", ".join(_STUDENT_ARCHIVE_COLUMNS)
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            if keyword:
                like = f"%{keyword}%"
                rows = conn.execute(
                    f"SELECT {cols} FROM student_archive "
                    "WHERE name LIKE ? OR student_id LIKE ? OR phone LIKE ? "
                    "ORDER BY student_id LIMIT ?",
                    (like, like, like, limit)).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {cols} FROM student_archive ORDER BY student_id LIMIT ?",
                    (limit,)).fetchall()
        return self._response(request, {
            "items": [self._archive_row_to_dict(r) for r in rows],
            "count": len(rows),
            "limit": limit,
        })

    async def _method_student_archive_update(self, request):
        """PUT /api/methods/student-archive — 管理员修改学籍档案。

        body: {student_id, ...可编辑字段}；仅更新传入字段；学号（主键）不可改。
        """
        from aiohttp import web
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        body = await _json_body(request)
        student_id = str(body.get("student_id") or "").strip()
        if not student_id:
            raise web.HTTPBadRequest(text=json.dumps({"error": "student_id is required"}))
        updates = {}
        for col in _STUDENT_ARCHIVE_EDITABLE:
            if col in body:
                val = body.get(col)
                updates[col] = "" if val is None else str(val).strip()
        if not updates:
            raise web.HTTPBadRequest(text=json.dumps({"error": "没有可更新的字段"}))
        self._ensure_student_archive_table()
        import sqlite3
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            exists = conn.execute(
                "SELECT 1 FROM student_archive WHERE student_id=?", (student_id,)).fetchone()
            if not exists:
                raise web.HTTPNotFound(text=json.dumps({"error": "未找到该学号的学籍信息"}))
            assignments = ", ".join(f"{c}=?" for c in updates)
            conn.execute(
                f"UPDATE student_archive SET {assignments}, updated_at=datetime('now') "
                "WHERE student_id=?", (*updates.values(), student_id))
            conn.commit()
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"SELECT {', '.join(_STUDENT_ARCHIVE_COLUMNS)} FROM student_archive "
                "WHERE student_id=?", (student_id,)).fetchone()
        return self._response(request, {"success": True,
                                        "archive": self._archive_row_to_dict(row)})

    async def _method_student_archive_link(self, request):
        """POST /api/methods/student-archive/link — 学生绑定学号以匹配档案。

        body: {student_id}；命中档案则写入 admission_profiles.student_staff_id，
        作为手机号之外的兜底匹配键。
        """
        from aiohttp import web
        openid = self._authorize(request)
        body = await _json_body(request)
        student_id = str(body.get("student_id") or "").strip()
        if not student_id:
            raise web.HTTPBadRequest(text=json.dumps({"error": "请填写学号"}))
        self._ensure_student_archive_table()
        import sqlite3
        with sqlite3.connect(str(self._methods_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"SELECT {', '.join(_STUDENT_ARCHIVE_COLUMNS)} FROM student_archive "
                "WHERE student_id=?", (student_id,)).fetchone()
            if not row:
                raise web.HTTPNotFound(text=json.dumps({"error": "未找到该学号对应的学籍信息，请联系管理员"}))
            self._ensure_admission_profile_columns()
            conn.execute(
                """INSERT INTO admission_profiles (platform, user_id, student_staff_id, updated_at)
                   VALUES ('miniapp', ?, ?, datetime('now'))
                   ON CONFLICT(platform, user_id) DO UPDATE SET
                     student_staff_id=excluded.student_staff_id, updated_at=datetime('now')""",
                (openid, student_id))
            conn.commit()
        return self._response(request, {"success": True,
                                        "archive": self._archive_row_to_dict(row)})

    # ── 教务通知同步管理（admin/owner）────────────────────────────────

    def _find_jxtz_job(self) -> Optional[dict[str, Any]]:
        """按 name 找 jxtz-sync job；同名多条取 next_run_at 最早的一条。

        不调用 ``resolve_job_ref``：它对同名会直接抛 ``AmbiguousJobReference``，
        根本走不到「取最早」。
        """
        from cron.jobs import list_jobs
        jobs = [j for j in list_jobs(include_disabled=True)
                if (j.get("name") or "") == "jxtz-sync"]
        if not jobs:
            return None
        if len(jobs) > 1:
            logger.warning("multiple cron jobs named 'jxtz-sync': %s",
                           [j.get("id") for j in jobs])
            jobs.sort(key=lambda j: (j.get("next_run_at") is None, j.get("next_run_at") or ""))
        return jobs[0]

    def _jxtz_find_run(self, run_id: str) -> Optional[dict[str, Any]]:
        for rec in reversed(_read_jxtz_runs()):
            if rec.get("run_id") == run_id:
                return rec
        return None

    def _jxtz_append_timeout_record(self, run_id: str, openid: str) -> None:
        """端点补写超时合成记录（SIGKILL 下脚本无法自理，见设计 R2）。"""
        record = {
            "run_id": run_id,
            "run_at": datetime.now().astimezone().isoformat(),
            "trigger": "manual",
            "by": openid,
            "status": "failed",
            "new": 0, "ok": 0, "fail": 0, "skip": 0,
            "error": "手动同步超时（600s，结果未知）",
            "items": [],
        }
        try:
            path = _jxtz_runs_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("failed to append synthetic jxtz timeout record")

    def _jxtz_run_sync(self, openid: str, run_id: str) -> dict[str, Any]:
        """同步执行真脚本（在线程中调用）；返回 {code, ...} 供 handler 映射 HTTP。"""
        repo = os.getenv("AGENT4SOM_REPO", "").strip()
        repo_path = Path(repo or Path.cwd())
        argv = [
            str(repo_path / "venv" / "bin" / "python"),
            str(repo_path / "scripts" / "sync_jxtz.py"),
            "--trigger", "manual", "--by", openid, "--run-id", run_id,
        ]
        try:
            proc = subprocess.run(argv, cwd=str(repo_path), timeout=600)
        except subprocess.TimeoutExpired:
            # 先确认没有本次真实记录，再补写合成记录（避免边界重复，见设计 V2）
            if self._jxtz_find_run(run_id) is None:
                self._jxtz_append_timeout_record(run_id, openid)
            return {"code": 504, "error": "sync_timeout"}
        except OSError as exc:
            logger.exception("failed to launch jxtz sync")
            return {"code": 500, "error": "sync_failed", "detail": type(exc).__name__}
        record = self._jxtz_find_run(run_id)
        if record is None:
            # 无本次记录：flock 拒绝（exit 0）或异常死亡（非 0）
            if proc.returncode == 0:
                return {"code": 409, "error": "already_running"}
            return {"code": 500, "error": "sync_failed", "detail": "脚本异常退出"}
        if record.get("status") == "failed":
            return {"code": 500, "error": "sync_failed", "detail": record.get("error") or ""}
        return {"code": 200, "result": record}

    async def _method_jxtz_sync_get(self, request):
        from aiohttp import web
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        job = self._find_jxtz_job()
        if job is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "job_not_found"}))
        paused = job.get("state") == "paused"
        runs = _read_jxtz_runs()
        return self._response(request, {
            "success": True,
            "enabled": not paused,
            "schedule_time": _cron_expr_to_hhmm(job.get("schedule")),
            "next_run_at": None if paused else job.get("next_run_at"),
            "last_run": runs[-1] if runs else None,
            "recent_runs": list(reversed(runs[-5:])),
        })

    async def _method_jxtz_sync_toggle(self, request):
        from aiohttp import web
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        body = await _json_body(request)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_enabled"}))
        job = self._find_jxtz_job()
        if job is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "job_not_found"}))
        from cron.jobs import pause_job, resume_job
        updated = resume_job(job["id"]) if enabled else pause_job(job["id"])
        if not updated:
            raise web.HTTPInternalServerError(text=json.dumps({"error": "update_failed"}))
        paused = updated.get("state") == "paused"
        return self._response(request, {
            "success": True,
            "enabled": not paused,
            "next_run_at": None if paused else updated.get("next_run_at"),
        })

    async def _method_jxtz_sync_schedule(self, request):
        from aiohttp import web
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        body = await _json_body(request)
        time_str = str(body.get("time") or "").strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_str):
            raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_time"}))
        hour, minute = time_str.split(":")
        expr = f"{int(minute)} {int(hour)} * * *"
        job = self._find_jxtz_job()
        if job is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "job_not_found"}))
        from cron.jobs import update_job
        updated = update_job(job["id"], {"schedule": expr})
        if not updated:
            raise web.HTTPInternalServerError(text=json.dumps({"error": "update_failed"}))
        paused = updated.get("state") == "paused"
        return self._response(request, {
            "success": True,
            "schedule_time": time_str,
            "next_run_at": None if paused else updated.get("next_run_at"),
        })

    async def _method_jxtz_sync_run(self, request):
        from aiohttp import web
        openid = self._authorize(request)
        self._require_role(openid, {"admin", "owner"})
        # 手动同步不依赖 job 是否注册（D4：关闭/误删 job 后仍可手动触发）
        run_id = uuid.uuid4().hex
        outcome = await asyncio.to_thread(self._jxtz_run_sync, openid, run_id)
        code = outcome.get("code")
        if code == 200:
            return self._response(request, {"success": True, "result": outcome["result"]})
        if code == 409:
            raise web.HTTPConflict(text=json.dumps({"error": "already_running"}))
        if code == 504:
            raise web.HTTPGatewayTimeout(text=json.dumps({"error": "sync_timeout"}))
        raise web.HTTPInternalServerError(text=json.dumps(
            {"error": "sync_failed", "detail": outcome.get("detail", "")}))

    def _authorize(self, request) -> str:
        from aiohttp import web
        raw = request.headers.get("Authorization", "")
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else ""
        openid = _verify_token(token)
        if not openid:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "invalid or expired session token"}))
        return openid

    def _actions(self) -> list[dict[str, str]]:
        # 学业规划助手不提供人工联系入口（原人工联系入口已移除）
        return []

    def _load_history_from_state_db(self, openid: str) -> list[dict[str, Any]]:
        """从 Hermes state.db 读取该 openid 的最新会话历史，不创建新数据库。"""
        state_db = get_hermes_home() / "state.db"
        if not state_db.exists():
            return []
        try:
            with sqlite3.connect(f"file:{state_db}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT id FROM sessions WHERE source = ? AND chat_id = ? "
                    "ORDER BY started_at DESC LIMIT 1",
                    ("miniapp", str(openid)),
                )
                row = cursor.fetchone()
                if not row:
                    return []
                session_id = row["id"]
                cursor = conn.execute(
                    "SELECT role, content, timestamp FROM messages "
                    "WHERE session_id = ? AND active = 1 AND role IN ('user', 'assistant') "
                    "ORDER BY id",
                    (session_id,),
                )
                messages = []
                for msg_row in cursor.fetchall():
                    content = self._decode_state_content(msg_row["content"])
                    if msg_row["role"] == "assistant":
                        # 工具调用轮次的 assistant 消息没有可见文本（content 为空），
                        # 历史里返回会渲染成空气泡 —— 跳过。
                        if not str(content or "").strip():
                            continue
                        content = _md_to_html(str(content))
                    messages.append({
                        "role": msg_row["role"],
                        "content": content,
                        "timestamp": msg_row["timestamp"],
                    })
                return messages
        except Exception:
            logger.exception("failed to load history from state.db for %s", openid)
            return []

    @staticmethod
    def _decode_state_content(content: Any) -> Any:
        """还原 state.db 中可能以 \\x00json: 前缀存储的结构化内容。"""
        if isinstance(content, str) and content.startswith("\x00json:"):
            try:
                decoded = json.loads(content[len("\x00json:"):])
            except (json.JSONDecodeError, TypeError):
                return content
            if isinstance(decoded, list):
                return "\n".join(
                    part.get("text", "") for part in decoded if isinstance(part, dict)
                )
            return decoded
        return content

    def _response(self, request, payload: dict[str, Any], status: int = 200):
        from aiohttp import web
        headers = {"Vary": "Origin"}
        origin = request.headers.get("Origin", "")
        if origin and origin in self.allowed_origins:
            headers.update({
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Request-Id",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            })
        return web.json_response(payload, status=status, headers=headers)

    def _initialize_storage(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._upload_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS miniapp_message_inbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, message_text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    delivered_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS miniapp_uploads (
                    attachment_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    extracted_text TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'uploaded',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    expires_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_miniapp_uploads_lookup
                ON miniapp_uploads(user_id, status, expires_at)
            """)
        self._ensure_student_archive_table()

    def _upload_directory(self, user_id: str) -> Path:
        now = datetime.now(timezone.utc)
        user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
        directory = self._upload_root / f"{now:%Y}" / f"{now:%m}" / user_hash / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        return directory

    def _store_upload(
        self, *, attachment_id: str, user_id: str, path: Path, filename: str,
        mime_type: str, size_bytes: int, sha256: str, extracted_text: str,
    ) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT INTO miniapp_uploads
                    (attachment_id, user_id, path, filename, mime_type, size_bytes, sha256, extracted_text, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+1 day'))
            """, (attachment_id, user_id, str(path), filename, mime_type, size_bytes, sha256, extracted_text))

    def _attachments_for_request(self, user_id: str, value: Any) -> list[dict[str, str]]:
        if value in (None, ""):
            return []
        if not isinstance(value, list) or not value or len(value) > 3:
            raise ValueError("attachment_ids must contain one to three upload IDs")
        ids = [str(item).strip() for item in value]
        if any(not re.fullmatch(r"[0-9a-f]{32}", item) for item in ids) or len(set(ids)) != len(ids):
            raise ValueError("attachment_ids are invalid")
        placeholders = ",".join("?" for _ in ids)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"""
                SELECT attachment_id, path, filename, mime_type, extracted_text
                FROM miniapp_uploads
                WHERE user_id=? AND status='uploaded' AND expires_at > datetime('now')
                  AND attachment_id IN ({placeholders})
            """, [user_id, *ids]).fetchall()
            if len(rows) != len(ids):
                raise ValueError("one or more uploaded files are unavailable")
            conn.execute(
                f"UPDATE miniapp_uploads SET status='attached' WHERE attachment_id IN ({placeholders})",
                ids,
            )
        by_id = {str(row["attachment_id"]): dict(row) for row in rows}
        return [by_id[item] for item in ids]

    def _cleanup_expired_uploads(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT path FROM miniapp_uploads WHERE expires_at <= datetime('now')").fetchall()
            conn.execute("DELETE FROM miniapp_uploads WHERE expires_at <= datetime('now')")
        root = self._upload_root.resolve()
        for row in rows:
            try:
                path = Path(str(row["path"])).resolve()
                if path.is_relative_to(root):
                    path.unlink(missing_ok=True)
                    _rmdir_if_empty(path.parent)
            except OSError:
                continue

    def _store_inbox(self, user_id: str, text: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("INSERT INTO miniapp_message_inbox(user_id, message_text) VALUES (?, ?)", (user_id, text))

    def _take_inbox(self, user_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, message_text, created_at FROM miniapp_message_inbox WHERE user_id=? AND status='pending' ORDER BY id LIMIT 50",
                (user_id,),
            ).fetchall()
            if rows:
                conn.executemany(
                    "UPDATE miniapp_message_inbox SET status='delivered', delivered_at=datetime('now') WHERE id=?",
                    [(row["id"],) for row in rows],
                )
        return [{"id": row["id"], "text": _md_to_html(row["message_text"]), "created_at": row["created_at"]} for row in rows]

def _md_to_html(text: str) -> str:
    try:
        import markdown
        return markdown.markdown(text or '', extensions=['tables', 'fenced_code'])
    except Exception:
        return text or ''

def _upload_root(extra: dict[str, Any]) -> Path:
    raw = str(extra.get("upload_root") or "").strip()
    repo = os.getenv("AGENT4SOM_REPO", "").strip()
    if raw:
        return Path(os.path.expandvars(raw.replace("${AGENT4SOM_REPO}", repo))).expanduser()
    return Path(repo or Path.home()) / "data/miniapp-uploads"


def _safe_upload_name(name: str) -> str:
    value = Path(str(name or "upload").replace("\x00", "")).name
    value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()
    if not value or value in {".", ".."}:
        value = "upload"
    return value[:180]


def _validate_upload_content(path: Path, suffix: str) -> None:
    data = path.read_bytes()
    if suffix in {".txt", ".csv", ".md"}:
        if b"\x00" in data[:8192]:
            raise ValueError("text file contains binary data")
        return
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("file content does not match its extension")
        return
    if suffix in {".png", ".jpg", ".jpeg"}:
        magic = b"\x89PNG\r\n\x1a\n" if suffix == ".png" else b"\xff\xd8\xff"
        if not data.startswith(magic):
            raise ValueError("file content does not match its extension")
        return
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            total_uncompressed = sum(item.file_size for item in infos)
            if total_uncompressed > _UPLOAD_MAX_BYTES * 3:
                raise ValueError("compressed document expands beyond the safe limit")
            names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise ValueError("file content does not match its extension") from exc
    required = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
    if required not in names:
        raise ValueError("file content does not match its extension")


def _extract_upload_text(path: Path, suffix: str) -> str:
    try:
        if suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            paragraphs = []
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            for paragraph in root.iter(f"{namespace}p"):
                value = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t")).strip()
                if value:
                    paragraphs.append(value)
            return "\n".join(paragraphs)[:_UPLOAD_CONTEXT_MAX_CHARS]
        if suffix in {".txt", ".csv", ".md"}:
            raw = path.read_bytes()[:_UPLOAD_CONTEXT_MAX_CHARS * 4]
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    return raw.decode(encoding).strip()[:_UPLOAD_CONTEXT_MAX_CHARS]
                except UnicodeDecodeError:
                    continue
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(path))
                text = "\n".join((page.extract_text() or "") for page in reader.pages)
                return text.strip()[:_UPLOAD_CONTEXT_MAX_CHARS]
            except Exception:
                return ""
        if suffix == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            rows = list(wb[wb.sheetnames[0]].iter_rows(values_only=True))
            wb.close()
            lines = []
            for row in rows[:80]:
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    lines.append(" | ".join(cells))
            return "\n".join(lines)[:_UPLOAD_CONTEXT_MAX_CHARS]
    except Exception:
        logger.warning("miniapp upload text extraction failed: file=%s", path.name, exc_info=True)
    return ""


def _attachment_context(attachments: list[dict[str, str]], message: str) -> str:
    sections = []
    for item in attachments:
        header = f"[用户上传附件：{item['filename']}]"
        extracted = str(item.get("extracted_text") or "").strip()
        if extracted:
            sections.append(f"{header}\n附件可读取文本如下：\n{extracted}")
        else:
            sections.append(f"{header}\n附件已收到，但未提取到可读文本。")
    return "\n\n".join([*sections, message])


def _rmdir_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


async def _json_body(request) -> dict[str, Any]:
    from aiohttp import web
    try:
        body = await request.json()
    except Exception as exc:
        raise web.HTTPBadRequest(text=json.dumps({"error": "invalid JSON body"})) from exc
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text=json.dumps({"error": "JSON body must be an object"}))
    return body


def _secret() -> bytes:
    value = os.getenv("MINIAPP_SESSION_SECRET", "").strip() or os.getenv("ADMISSION_CONTEXT_SECRET", "").strip()
    if not value:
        raise RuntimeError("MINIAPP_SESSION_SECRET is not configured")
    return value.encode("utf-8")


def _issue_token(openid: str, ttl_seconds: int = 86400) -> str:
    payload = json.dumps({"sub": openid, "exp": int(time.time()) + ttl_seconds}, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(_secret(), encoded, hashlib.sha256).digest()
    return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def _verify_token(token: str) -> str:
    try:
        encoded, raw_signature = token.split(".", 1)
        expected = hmac.new(_secret(), encoded.encode(), hashlib.sha256).digest()
        signature = base64.urlsafe_b64decode(raw_signature + "=" * (-len(raw_signature) % 4))
        if not hmac.compare_digest(expected, signature):
            return ""
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if int(payload.get("exp") or 0) < int(time.time()):
            return ""
        return str(payload.get("sub") or "").strip()
    except Exception:
        return ""


def _jxtz_runs_path() -> Path:
    """运行记录路径：由 $AGENT4SOM_REPO 锚定（不得用 __file__——跑的是部署副本）。"""
    repo = os.getenv("AGENT4SOM_REPO", "").strip()
    return Path(repo or Path.home()) / "data/jxtz_sync_runs.jsonl"


def _read_jxtz_runs() -> list[dict[str, Any]]:
    """读取运行记录：逐行解析、跳过坏行；不存在/不可读 → 空列表。"""
    path = _jxtz_runs_path()
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError:
        logger.warning("jxtz runs unreadable: %s", path, exc_info=True)
        return []
    return records


def _cron_expr_to_hhmm(schedule: Any) -> Optional[str]:
    """从 cron 表达式反解 HH:MM；非 5 段简单数字表达式 → None（容错，不抛）。"""
    if not isinstance(schedule, dict) or schedule.get("kind") != "cron":
        return None
    parts = str(schedule.get("expr") or "").strip().split()
    if len(parts) != 5 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    return f"{int(parts[1]):02d}:{int(parts[0]):02d}"


def _db_path(extra: dict[str, Any]) -> Path:
    raw = str(extra.get("sqlite_path") or "").strip()
    repo = os.getenv("AGENT4SOM_REPO", "").strip()
    if raw:
        return Path(os.path.expandvars(raw.replace("${AGENT4SOM_REPO}", repo))).expanduser()
    return Path(repo or Path.home()) / "data/sqlite/miniapp.db"


def _discard_future(queues, user_id: str, future: asyncio.Future) -> None:
    queue = queues.get(user_id)
    if not queue:
        return
    try:
        queue.remove(future)
    except ValueError:
        pass
    if not queue:
        queues.pop(user_id, None)


def check_requirements() -> bool:
    try:
        import aiohttp  # noqa: F401
        return True
    except ImportError:
        return False


def validate_config(config: PlatformConfig) -> bool:
    """Return True when the native platform can be constructed.

    Hermes' platform registry treats this as a boolean predicate, not as a
    validation-error collection.  Returning an empty list would therefore
    incorrectly disable a correctly configured adapter.
    """
    del config
    return bool(
        os.getenv("WECHAT_MINIAPP_APPID", "").strip()
        and os.getenv("WECHAT_MINIAPP_APPSECRET", "").strip()
        and (
            os.getenv("MINIAPP_SESSION_SECRET", "").strip()
            or os.getenv("ADMISSION_CONTEXT_SECRET", "").strip()
        )
    )


def register(ctx) -> None:
    ctx.register_platform(
        name="miniapp", label="Miniapp Platform",
        adapter_factory=lambda cfg: MiniappAdapter(cfg),
        check_fn=check_requirements, validate_config=validate_config,
        required_env=["WECHAT_MINIAPP_APPID", "WECHAT_MINIAPP_APPSECRET"],
        # HTTP requests reach this adapter only after _authorize validates the
        # signed session token issued from wx.login/code2session. Hermes' generic
        # DM pairing would be a second, unrelated identity check.
        allow_all_env="MINIAPP_ALLOW_ALL_USERS",
        emoji="📱", pii_safe=False, max_message_length=12000,
        platform_hint="This is the teaching-affairs WeChat mini program. Keep one concise final reply per turn.",
    )
