#!/usr/bin/env python3
"""安全版 pkill —— 彻底避免"pkill 误杀自己"。

为什么需要：`pkill -f <pattern>`（或 `ps|grep|kill` 管道）会把**执行该命令的 shell 自己**
也匹配上（因为它的命令行里就含那个 pattern），于是把自己杀掉。

本工具：
- 按 `<regex...>` 匹配 `/proc/<pid>/cmdline`（多个正则需**同时**命中，便于 AND 条件）；
- **排除**：自身 pid、父进程链（一路到 init）、以及任何 cmdline 含 `safe_pkill` 的进程；
- 默认 `--dry-run` 之外才真正 `SIGTERM`；打印每个命中与动作。

用法：
    python3 scripts/safe_pkill.py eval_watchdog.py            # 干跑，列出会杀谁
    python3 scripts/safe_pkill.py --kill eval_watchdog.py     # 真杀
    python3 scripts/safe_pkill.py --kill --signal TERM eval_watchdog.py --log
"""
from __future__ import annotations

import argparse
import os
import re
import signal


def _cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\x00", b" ").decode("utf-8", "ignore").strip()
    except OSError:
        return ""


def _parents(pid: int) -> set[int]:
    """返回 pid 及其所有祖先 pid（避免误杀调用链上的进程）。"""
    chain = set()
    cur = pid
    for _ in range(64):
        if cur <= 1:
            break
        chain.add(cur)
        try:
            with open(f"/proc/{cur}/stat", "rb") as fh:
                data = fh.read().decode("utf-8", "ignore")
            # stat: pid (comm) state ppid ...  comm 可能含空格/括号，取最后一个 ')'
            after = data[data.rfind(")") + 2:].split()
            cur = int(after[1])
        except (OSError, ValueError, IndexError):
            break
    chain.add(1)
    return chain


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("patterns", nargs="+", help="匹配 /proc/<pid>/cmdline 的正则（需全部命中）")
    ap.add_argument("--kill", action="store_true", help="真正发送信号（默认只干跑）")
    ap.add_argument("--signal", default="TERM", help="TERM/KILL/INT…（默认 TERM）")
    ap.add_argument("--exclude", action="append", default=[], help="额外排除的正则")
    args = ap.parse_args()

    sig = getattr(signal, f"SIG{args.signal.upper()}", signal.SIGTERM)
    regs = [re.compile(p) for p in args.patterns]
    excl = [re.compile(p) for p in args.exclude]
    protected = _parents(os.getpid())
    protected.add(os.getpid())

    hits = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        if pid in protected:
            continue
        cmd = _cmdline(pid)
        if not cmd or "safe_pkill" in cmd:
            continue
        if any(r.search(cmd) for r in excl):
            continue
        if all(r.search(cmd) for r in regs):
            hits.append((pid, cmd))

    if not hits:
        print("没有命中进程（已排除自身/父进程链）")
        return 0

    for pid, cmd in hits:
        action = "SIGTERM" if args.kill else "会杀(干跑)"
        print(f"  [{action}] pid={pid}  {cmd[:120]}")
        if args.kill:
            try:
                os.kill(pid, sig)
            except OSError as exc:
                print(f"      → 失败: {exc}")
    if not args.kill:
        print("（干跑：加 --kill 才真正发送信号）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
