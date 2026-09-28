"""service_manager 服务生命周期测试（220 行，此前零覆盖）。

离线：systemctl/pgrep/requests 全部 mock，不真启停任何服务。
模块导入需要 AGENT4SOM_HOME，在 import 前设置。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

os.environ.setdefault("AGENT4SOM_HOME", os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import pytest

from knowledge_base.utils import service_manager as sm


# ── manage_chroma_server ────────────────────────────────────────────


def test_manage_chroma_server_rejects_invalid_action():
    with pytest.raises(ValueError, match="action must be"):
        sm.manage_chroma_server("restart")


def test_manage_chroma_server_masks_before_stop(monkeypatch):
    """stop 顺序：先 mask（防 systemd 自动拉起）再 stop。"""
    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    monkeypatch.setattr(sm.time, "sleep", lambda *_: None)

    assert sm.manage_chroma_server("stop") is True
    assert commands[0][-2:] == ["mask", "chroma-server"]
    assert commands[1][-2:] == ["stop", "chroma-server"]


def test_manage_chroma_server_unmasks_before_start(monkeypatch):
    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    monkeypatch.setattr(sm.time, "sleep", lambda *_: None)

    assert sm.manage_chroma_server("start") is True
    assert commands[0][-2:] == ["unmask", "chroma-server"]
    assert commands[1][-2:] == ["start", "chroma-server"]


def test_manage_chroma_server_failure_returns_false(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unit not found")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    monkeypatch.setattr(sm.time, "sleep", lambda *_: None)

    assert sm.manage_chroma_server("stop") is False


# ── ensure_chroma_server ────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_ensure_chroma_server_running_short_circuits(monkeypatch):
    """心跳已通 → 直接 True，不调 systemctl。"""
    import requests as requests_mod

    called = []

    def fake_get(url, timeout):
        called.append(url)
        return _FakeResponse(200)

    monkeypatch.setattr(requests_mod, "get", fake_get)
    # ensure_chroma_server 内部局部 import requests，拿到的是同一模块对象，
    # 直接 patch 即可（不要 reload —— reload 会重定义模块内容、冲掉 patch）

    assert sm.ensure_chroma_server() is True
    assert called and "heartbeat" in called[0]


def test_ensure_chroma_server_unreachable_returns_false(monkeypatch):
    """心跳不通 + systemctl 启动失败（非部署机）→ False，不抛异常。"""
    import requests as requests_mod

    def fake_get(url, timeout):
        raise ConnectionError("refused")

    monkeypatch.setattr(requests_mod, "get", fake_get)

    def fail_start(action):
        return False

    monkeypatch.setattr(sm, "manage_chroma_server", fail_start)
    assert sm.ensure_chroma_server() is False


# ── _find_gateway_pid ───────────────────────────────────────────────


def test_find_gateway_pid_none_when_no_process(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    assert sm._find_gateway_pid() is None


def _spawn(cmdline_tail: list[str]) -> subprocess.Popen:
    """起一个真实子进程（cmdline 可控），供 /proc/{pid}/cmdline 校验。"""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", *cmdline_tail],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_cmdline(proc: subprocess.Popen, needle: str, timeout: float = 5.0) -> bool:
    """等待子进程 /proc/cmdline 就绪且含 needle。

    本机（OpenEuler 6.6 内核 + posix_spawn 路径）Popen 返回时 cmdline 有约
    1/4 概率仍为空串（exec 尚未生效），测试桩绕过 pgrep 直喂 PID 会读到空
    cmdline → 间歇性假失败（实测 500 次复现 129 次）。轮询至就绪后再断言。
    生产路径无此竞态：pgrep -f 本身按 cmdline 匹配，返回 PID 时必然已就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(f"/proc/{proc.pid}/cmdline", "rb") as fh:
                if needle.encode() in fh.read():
                    return True
        except (FileNotFoundError, PermissionError):
            pass
        time.sleep(0.05)
    return False


def test_find_gateway_pid_filters_non_gateway_process(monkeypatch):
    """pgrep 结果里的 PID 若 /proc/cmdline 不含 hermes+gateway 则不采纳。

    用真实子进程而非 os.getpid()：本进程 cmdline 会带上
    tests/hermes_overlay、tests/gateway 等路径，反而撞上子串匹配。
    """
    proc = _spawn([])
    try:
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, 0, stdout=str(proc.pid) + "\n", stderr="")

        monkeypatch.setattr(sm.subprocess, "run", fake_run)
        assert sm._find_gateway_pid() is None
    finally:
        proc.kill()
        proc.wait()


def test_find_gateway_pid_matches_real_gateway_cmdline(monkeypatch):
    """cmdline 含 hermes + gateway 的 PID 被采纳。"""
    proc = _spawn(["hermes", "gateway", "run"])
    try:
        assert _wait_cmdline(proc, "hermes")   # 等 exec 生效，防空 cmdline 竞态
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, 0, stdout=str(proc.pid) + "\n", stderr="")

        monkeypatch.setattr(sm.subprocess, "run", fake_run)
        assert sm._find_gateway_pid() == proc.pid
    finally:
        proc.kill()
        proc.wait()
