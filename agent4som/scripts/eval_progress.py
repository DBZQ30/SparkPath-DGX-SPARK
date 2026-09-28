#!/usr/bin/env python3
"""SkillEvaluator Tier 3 评测**进度展示**（可独立跑，也可由 run_skill_eval.sh 后台启动）。

用法：
    python3 agent4som/scripts/eval_progress.py <eval_output_dir> [--interval 20] [--once]
    # <eval_output_dir> = skillevaluator 的 --output-dir（下面会有 harbor/）

输出（每 interval 一行，追加到 stdout；run_skill_eval.sh 会同时写入 <dir>/progress.log）：
    [12:34:56] 已跑 23.1min | 出分 27（with 11 · without 16）| 活跃 2
      ▸ 4.2min  with   plan-pos-compare
      ▸ 1.1min  without plan-doc-boundary
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_watchdog import _claude_pid, _find_trials, _trial_start


def _harbor_root(base: Path) -> Path | None:
    """在 base 下找最新的 `_harbor-jobs` 的父目录（= harbor）。"""
    cands = [p for p in base.rglob("_harbor-jobs") if p.is_dir()]
    if not cands:
        return base if (base / "harbor").exists() else None
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0].parent


def _side(trial: Path) -> str:
    m = re.search(r"_harbor-jobs/([^/]+)/", str(trial))
    job = m.group(1) if m else ""
    if "preflight" in job:
        return "preflight"
    return "without" if "without" in job else ("with" if "with" in job else "?")


def _case(trial: Path) -> str:
    """用例名 = 用例目录名里倒数第二段（目录名为 `<case>__<hex>` 或 `<job>__<case>__<hex>`）。"""
    name = trial.name
    if "__" in name:
        parts = name.split("__")
        if len(parts) >= 2:
            return parts[-2]
    return name


def _collect_trial_states(trials: list[Path], now: float):
    """按 (side, case) 归集：已完成（有 reward）与活跃（claude 进程在跑）。

    返回 (done_keys, active, started)：active 值为 (时长分钟, pid)，
    同用例多 attempt 取最久的；started 为全部 trial 的最早起跑时刻。"""
    done_cases: dict[tuple[str, str], int] = {}
    active: dict[tuple[str, str], tuple[float, int]] = {}
    started = 0.0
    for t in trials:
        side = _side(t)
        case = _case(t)
        has_reward = bool(list(t.rglob("reward.json")) or list(t.rglob("reward.txt")))
        st = _trial_start(t)
        if st and (started == 0.0 or st < started):
            started = st
        if has_reward:
            done_cases[(side, case)] = 1
            continue
        pid = _claude_pid(t)
        if pid:
            dur = (now - st) / 60.0 if st else 0.0
            prev = active.get((side, case))
            if prev is None or dur > prev[0]:
                active[(side, case)] = (dur, pid)
    return done_cases, active, started


def _count_attempts(trials: list[Path]) -> int:
    """只数"带 attempt 编号的 job"（聚合副本不计），避免重复。"""
    attempt_keys = set()
    for t in trials:
        m = re.search(r"-attempt(\d+)(?:/|$)", str(t))
        if not m:
            continue
        if list(t.rglob("reward.json")) or list(t.rglob("reward.txt")):
            attempt_keys.add((_side(t), _case(t), m.group(1)))
    return len(attempt_keys)


def snapshot(base: Path) -> str:
    harbor = _harbor_root(base)
    if harbor is None:
        return "（尚未找到 harbor/，评测可能还在启动）"
    trials = [t for t in _find_trials(harbor) if _side(t) != "preflight"]
    now = time.time()
    done_cases, active, started = _collect_trial_states(trials, now)
    by_side = {"with": 0, "without": 0}
    for side, _ in done_cases:
        by_side[side] = by_side.get(side, 0) + 1
    cases = sum(by_side.values())
    attempts = _count_attempts(trials)
    elapsed = (now - started) / 60.0 if started else 0.0
    head = (f"已跑 {elapsed:.1f}min | attempts {attempts} | 用例 {cases}"
            f"（with {by_side.get('with',0)} · without {by_side.get('without',0)}）"
            f" | 活跃 {len(active)}")
    lines = [head]
    for (side, case), (dur, pid) in sorted(active.items(), key=lambda kv: -kv[1][0])[:6]:
        lines.append(f"  ▸ {dur:5.1f}min  {side:7} {case}  (pid={pid})")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", help="评测输出目录（含 harbor/）")
    ap.add_argument("--interval", type=float, default=20.0)
    ap.add_argument("--once", action="store_true", help="只打印一次")
    ap.add_argument("--log", default="", help="同时追加写入该文件")
    args = ap.parse_args()
    base = Path(args.base).resolve()

    def emit(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        if args.log:
            try:
                with open(args.log, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    if args.once:
        print(snapshot(base))
        return 0
    try:
        while True:
            emit(snapshot(base))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
