#!/usr/bin/env python3
"""SkillEvaluator Tier 3 评测看门狗：检测并（可选）中止"卡住/乱逛过久"的 trial。

用法：
    python3 agent4som/scripts/eval_watchdog.py <harbor_jobs_root> \
        [--idle-min 4] [--max-trial-min 10] [--kill] [--interval 30] [--log FILE]

原理：
- 每个 trial 的 agent 活动写在 `<trial>/agent/claude-code.txt`（及 sessions/*.jsonl）。
- **idle** = 最近一次文件写入距今；**duration** = 首次发现该 trial 距今。
- 超过阈值 → 记一条事件；`--kill` 时按 cwd 找到该 trial 的 `claude` 进程并 SIGTERM
  （等价于让它尽快失败，评测继续；不会误杀判官/其他进程）。

退出：Ctrl-C。
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import time
from pathlib import Path
import contextlib


def _newest_mtime(paths: list[Path]) -> float:
    m = 0.0
    for p in paths:
        with contextlib.suppress(OSError):
            m = max(m, p.stat().st_mtime)
    return m


def _find_trials(root: Path) -> list[Path]:
    """trial 目录 = 含 agent/claude-code.txt 的最近目录（限定深度，避免遍历巨大夹具）。"""
    out = []
    patterns = [
        "*/*/_harbor-jobs/*/*/agent/claude-code.txt",
        "*/_harbor-jobs/*/*/agent/claude-code.txt",
        "_harbor-jobs/*/*/agent/claude-code.txt",
    ]
    for pat in patterns:
        for txt in root.glob(pat):
            out.append(txt.parent.parent)
        if out:
            break
    return out


def _active_activity(trial: Path) -> float:
    cands = [trial / "agent" / "claude-code.txt"]
    cands += list((trial / "agent" / "sessions").rglob("*.jsonl"))
    cands += list((trial / "agent").rglob("*.txt"))
    return _newest_mtime(cands)


def _trial_start(trial: Path) -> float:
    """用 `<trial>/agent` 下最早的文件时间近似起跑时刻（看门狗中途启动也能估准）。"""
    agent = trial / "agent"
    earliest = 0.0
    try:
        for p in agent.rglob("*"):
            if p.is_file():
                m = p.stat().st_mtime
                if earliest == 0.0 or m < earliest:
                    earliest = m
    except OSError:
        pass
    return earliest or _active_activity(trial)


def _claude_pid(trial: Path) -> int | None:
    """按进程 cwd 定位该 trial 下的 claude 进程。"""
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,comm"], capture_output=True, text=True,
            check=False,  # 列进程不期望失败码语义；异常由外层 except 兜底
        ).stdout
    except Exception:
        return None
    for line in out.splitlines()[1:]:
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid, comm = int(parts[0]), parts[1].strip()
        if comm != "claude":
            continue
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        if str(trial) in cwd:
            return pid
    return None


def _stop_existing(args) -> int:
    """停止模式：只杀 pidfile 里记录的那个 PID（彻底避免误杀）。"""
    if not args.pidfile or not os.path.isfile(args.pidfile):
        print("没有 pidfile（或未提供 --pidfile），无看门狗可停")
        return 0
    try:
        with open(args.pidfile) as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        print(f"pidfile 无效: {args.pidfile}")
        return 0
    cmd = ""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmd = fh.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
    except OSError:
        cmd = ""
    if "eval_watchdog.py" not in cmd:
        print(f"pid {pid} 不是看门狗（cmdline 不符），拒绝停止")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"已停止看门狗 pid={pid}")
    except OSError as exc:
        print(f"停止失败: {exc}")
    with contextlib.suppress(OSError):
        os.remove(args.pidfile)
    return 0


def _trial_stuck_reasons(trial: Path, now: float, args, first_seen: dict) -> list[str]:
    """超阈值原因列表（空 = 未卡住）。idle = 最近写入距今；dur = 起跑至今。"""
    idle = (now - _active_activity(trial)) / 60.0
    start = _trial_start(trial) or first_seen[str(trial)]
    dur = (now - start) / 60.0
    reasons = []
    if idle >= args.idle_min:
        reasons.append(f"idle {idle:.1f}min")
    if dur >= args.max_trial_min:
        reasons.append(f"dur {dur:.1f}min")
    return reasons


def _report_and_kill(trial: Path, root: Path, pid: int, key: str, reasons: list[str],
                     args, killed: set, emit) -> None:
    """报告卡住 trial；--kill 时 SIGTERM 其 claude 进程（等价于让它尽快失败，
    评测继续；不会误杀判官/其他进程）。"""
    tag = "/".join(reasons)
    emit(f"⚠ trial 卡住（{tag}）pid={pid} {trial.relative_to(root)}")
    if args.kill:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.add(key)
            emit(f"   → 已 SIGTERM pid={pid}（该 trial 将尽快失败，评测继续）")
        except OSError as exc:
            emit(f"   → kill 失败: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--idle-min", type=float, default=4.0, help="无写入超过 N 分钟视为 idle")
    ap.add_argument("--max-trial-min", type=float, default=10.0, help="单 trial 超过 N 分钟视为过久")
    ap.add_argument("--kill", action="store_true", help="超阈值时 SIGTERM 该 trial 的 claude 进程")
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--log", default="")
    ap.add_argument("--pidfile", default="", help="把自己 PID 写入该文件（供 --stop 精确停止）")
    ap.add_argument("--stop", action="store_true", help="按 pidfile 停止已有看门狗（不扫全表杀）")
    args = ap.parse_args()

    if args.stop:
        return _stop_existing(args)

    root = Path(args.root).resolve()
    if args.pidfile:
        try:
            with open(args.pidfile, "w") as fh:
                fh.write(str(os.getpid()))
        except OSError:
            pass

    def emit(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        if args.log:
            with open(args.log, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    first_seen: dict[str, float] = {}
    killed: set[str] = set()
    emit(f"watchdog 启动：root={root} idle>{args.idle_min}min max>{args.max_trial_min}min kill={args.kill}")

    try:
        while True:
            now = time.time()
            trials = _find_trials(root)
            for t in trials:
                key = str(t)
                if "runtime-preflight" in key:
                    continue
                # 仅在**该 trial 的 claude 进程仍在运行**时才判定（避免误报已完成/残留目录）
                pid = _claude_pid(t)
                if pid is None:
                    continue
                if list(t.rglob("reward.json")) or list(t.rglob("reward.txt")):
                    continue
                first_seen.setdefault(key, now)
                reasons = _trial_stuck_reasons(t, now, args, first_seen)
                if reasons and key not in killed:
                    _report_and_kill(t, root, pid, key, reasons, args, killed, emit)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        emit("watchdog 退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
