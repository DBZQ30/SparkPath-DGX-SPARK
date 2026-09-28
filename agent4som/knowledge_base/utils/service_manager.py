"""ChromaDB + Gateway service lifecycle management.

Shared across admin_cli, batch_ingest_*, sync_jxtz — single source of truth
for starting/stopping chroma-server and hermes gateway.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time

logger = logging.getLogger(__name__)

import os as _os

_HERMES_BIN = _os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes")
_PROJECT_ROOT = _os.path.expanduser(_os.getenv("AGENT4SOM_HOME", ""))
if not _PROJECT_ROOT:
    raise RuntimeError("AGENT4SOM_HOME must be set to the project root directory")

# ── chroma-server ───────────────────────────────────────────────────────


def manage_chroma_server(action: str) -> bool:
    """Stop or start chroma-server via systemctl.

    On stop, also masks the service to prevent systemd Restart=always
    and Gateway auto-start from resurrecting chroma-server during
    PersistentClient writes.  On start, unmasks first.

    Returns True on success, False if systemd is unavailable or the command fails.
    """
    if action not in ("stop", "start"):
        raise ValueError(f"action must be 'stop' or 'start', got {action!r}")
    try:
        if action == "stop":
            # Mask first so neither systemd Restart=always nor Gateway
            # auto-start can resurrect chroma-server during PersistentClient writes.
            subprocess.run(
                ["sudo", "systemctl", "mask", "chroma-server"],
                capture_output=True, text=True, timeout=15, check=False,
            )
        else:
            subprocess.run(
                ["sudo", "systemctl", "unmask", "chroma-server"],
                capture_output=True, text=True, timeout=15, check=False,
            )
        result = subprocess.run(
            ["sudo", "systemctl", action, "chroma-server"],
            capture_output=True, text=True, timeout=30,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            logger.error("systemctl %s chroma-server 失败 (exit %d): %s",
                         action, result.returncode, stderr)
            return False
        label = "⏸️" if action == "stop" else "▶️"
        logger.info(f"{label}  chroma-server {action}")
        if action == "start":
            time.sleep(2)  # let it fully initialize
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning(f"⚠️  systemctl 不可用，请手动 {action} chroma-server")
        return False


def ensure_chroma_server() -> bool:
    """Ensure chroma-server is running. Start it if not.

    Used before HTTP-mode operations (admin_cli ingest, eval_ragas, etc.)
    to avoid silently falling back to embedded PersistentClient.

    Returns True if chroma-server is running (or was successfully started).

    .. note::

        Calls ``sudo systemctl start chroma-server`` — requires passwordless
        sudo for the chroma-server service.  This works for CLI scripts run by
        a sudo-capable user but will fail if called from the Gateway process
        (kb_init hook).  In that edge case chroma-server is expected to be
        running already (systemd ``After=chroma-server.service``), and if not
        the fallback to embedded PersistentClient is acceptable.
    """
    import requests

    host = os.environ.get("CHROMA_HOST", "127.0.0.1")
    port = os.environ.get("CHROMA_PORT", "8007")

    # Quick check: is it already running?
    try:
        r = requests.get(f"http://{host}:{port}/api/v2/heartbeat", timeout=3)
        if r.status_code == 200:
            return True
    except Exception:
        pass

    # Try to start it
    logger.warning("⚠️  chroma-server 未运行，正在自动启动...")
    if not manage_chroma_server("start"):
        return False

    # Wait for it to be ready (up to 60s)
    for _ in range(60):
        try:
            r = requests.get(f"http://{host}:{port}/api/v2/heartbeat", timeout=2)
            if r.status_code == 200:
                logger.info("✅ chroma-server 已就绪")
                return True
        except Exception:
            time.sleep(1)

    logger.error("❌ chroma-server 启动失败或未在 60s 内就绪")
    return False


# ── Gateway ─────────────────────────────────────────────────────────────


def _find_gateway_pid() -> int | None:
    """Return the PID of a running ``hermes gateway run`` process, or None."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "hermes gateway run"],
            capture_output=True, text=True, timeout=5,
            check=False,  # pgrep 无匹配时退出码 1，属正常语义
        )
        for pid_str in result.stdout.strip().split("\n"):
            if not pid_str.strip():
                continue
            pid = int(pid_str.strip())
            try:
                with open(f"/proc/{pid}/cmdline") as fh:
                    cmdline = fh.read()
                if "hermes" in cmdline and "gateway" in cmdline:
                    return pid
            except (FileNotFoundError, PermissionError):
                continue
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        logger.debug("Could not find gateway PID via pgrep", exc_info=True)
    return None


def stop_gateway() -> bool:
    """Stop a running gateway. Returns True if it was running (whether or not
    we successfully stopped it — used to decide whether to restart later).
    """
    pid = _find_gateway_pid()
    if pid is None:
        logger.info("ℹ️  Gateway 未在运行，无需停止")
        return False

    logger.info(f"⏸️  正在停止 Gateway (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        logger.info("  Gateway 已退出")
        return False

    # Wait up to 30s for graceful shutdown
    for _ in range(30):
        try:
            os.kill(pid, 0)
            time.sleep(1)
        except ProcessLookupError:
            logger.info("  Gateway 已停止")
            return True

    # Force kill if still running
    logger.info("  Gateway 未响应 SIGTERM，强制终止...")
    try:
        os.kill(pid, signal.SIGKILL)
        time.sleep(2)
    except ProcessLookupError:
        pass
    logger.info("  Gateway 已强制终止")
    return True


def start_gateway() -> None:
    """Start the gateway as a background process.

    Retries up to 3 times before raising.  Stderr is captured to a log file
    so startup crashes leave diagnostic traces.

    Raises:
        RuntimeError: if the gateway process is not detected after 3 attempts.
    """
    import os as _os
    _log_dir = _os.path.join(_PROJECT_ROOT, "data", "logs")
    _os.makedirs(_log_dir, exist_ok=True)

    cmd = [_HERMES_BIN, "gateway", "run", "--replace"]
    logger.info(f"▶️  正在启动 Gateway: {' '.join(cmd)}")

    _log_path = _os.path.join(_log_dir, "gateway_stderr.log")
    for attempt in range(3):
        with open(_log_path, "ab") as _stderr_fh:
            subprocess.Popen(
                cmd,
                cwd=_PROJECT_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=_stderr_fh,
                start_new_session=True,
            )
        time.sleep(3)  # let it initialize
        pid = _find_gateway_pid()
        if pid:
            logger.info(f"  Gateway 已启动 (PID {pid})")
            return
        if attempt < 2:
            logger.info(f"  Gateway start attempt {attempt+1}/3 failed, retrying...")

    raise RuntimeError(
        "Gateway 启动失败：3 次尝试后进程未检测到。"
        f"检查 stderr 日志: {_log_dir}/gateway_stderr.log"
    )
