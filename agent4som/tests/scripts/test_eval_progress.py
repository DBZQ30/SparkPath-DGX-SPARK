"""scripts/eval_progress.py 纯逻辑测试（此前零覆盖，离线）。

不跑真实评测、不依赖 harbor 现场：trial 目录用 tmp_path 构造，
claude 进程发现（_claude_pid）打桩注入。
"""
import importlib.util
import os
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "scripts" / "eval_progress.py"
_spec = importlib.util.spec_from_file_location("eval_progress_under_test", _SRC)
ep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ep)


def _mk(jobs: Path, job: str, name: str, *, reward: bool = False,
        age_min: float | None = None) -> Path:
    """建一个 trial：`jobs/<job>/<name>/agent/claude-code.txt`（+可选 reward）。"""
    trial = jobs / job / name
    agent = trial / "agent"
    agent.mkdir(parents=True)
    (agent / "claude-code.txt").write_text("x", encoding="utf-8")
    if age_min is not None:
        old = time.time() - age_min * 60
        os.utime(agent / "claude-code.txt", (old, old))
    if reward:
        (trial / "reward.json").write_text("{}", encoding="utf-8")
    return trial


def _jobs(base: Path) -> Path:
    jobs = base / "harbor" / "_harbor-jobs"
    jobs.mkdir(parents=True)
    return jobs


# ── 纯函数：_side / _case / _harbor_root ──────────────────────────


def test_side_and_case_regex():
    p = Path("/data/eval/harbor/_harbor-jobs/run-with-a1/c1__abc123")
    assert ep._side(p) == "with"
    assert ep._case(p) == "c1"
    # without 判定先于 with；preflight 独立；未知 → ?
    assert ep._side(Path("/h/_harbor-jobs/run-without-b1/t")) == "without"
    assert ep._side(Path("/h/_harbor-jobs/preflight-check/t")) == "preflight"
    assert ep._side(Path("/h/_harbor-jobs/zzz/t")) == "?"
    # 多段 __ 目录名：取倒数第二段；无 __ 用整名
    assert ep._case(Path("/h/j/c1__x__ab12")) == "x"
    assert ep._case(Path("/h/j/no_dunder")) == "no_dunder"


def test_harbor_root_newest_and_fallbacks(tmp_path):
    old = tmp_path / "a" / "_harbor-jobs"
    old.mkdir(parents=True)
    new = tmp_path / "b" / "_harbor-jobs"
    new.mkdir(parents=True)
    os.utime(old, (100, 100))
    os.utime(new, (200, 200))
    assert ep._harbor_root(tmp_path) == new.parent

    # 无 _harbor-jobs：有 harbor/ 目录 → 返回 base；否则 None
    base = tmp_path / "only_harbor"
    (base / "harbor").mkdir(parents=True)
    assert ep._harbor_root(base) == base
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert ep._harbor_root(empty) is None


# ── snapshot：归集与输出格式 ──────────────────────────────────────


def test_snapshot_no_harbor_yet(tmp_path):
    out = ep.snapshot(tmp_path / "not_there")
    assert out == "（尚未找到 harbor/，评测可能还在启动）"


def test_snapshot_counts_and_formats(tmp_path, monkeypatch):
    base = tmp_path / "out"
    jobs = _jobs(base)
    # done：with×2（含 attempt 计数来源）、without×1；preflight 排除
    _mk(jobs, "run-with", "c1__a1", reward=True, age_min=30)
    _mk(jobs, "run-without", "c2__a2", reward=True)
    _mk(jobs, "with-c3-attempt1", "c3__a3", reward=True)
    _mk(jobs, "with-c3-attempt2", "c4__a4", reward=True)
    _mk(jobs, "preflight-smoke", "pf__a5", reward=True)
    # 活跃：同用例两个 attempt 取最久；无 claude 进程的不算活跃
    _mk(jobs, "with-c4", "c4__a6", age_min=2)
    _mk(jobs, "with-c4", "c4__a7", age_min=9)
    _mk(jobs, "with-c5", "c5__a8", age_min=5)

    monkeypatch.setattr(ep, "_claude_pid",
                        lambda t: 4321 if "c4" in t.name else None)

    out = ep.snapshot(base)
    lines = out.splitlines()
    # head：已跑取最早起跑（c1 的 30min 前）；attempts 只数带编号的 1/2；
    # 用例=有 reward 的非 preflight trial（with c1/c3/c4 · without c2）；
    # 活跃=有进程的用例
    assert lines[0] == ("已跑 30.0min | attempts 2 | 用例 4"
                        "（with 3 · without 1） | 活跃 1")
    # 活跃行：c4 的最久 attempt（9min，pid=4321）
    assert len(lines) == 2
    active = lines[1]
    assert "9.0min" in active
    assert "with" in active and "c4" in active
    assert "(pid=4321)" in active


def test_snapshot_all_done_no_active_lines(tmp_path, monkeypatch):
    base = tmp_path / "out"
    jobs = _jobs(base)
    _mk(jobs, "run-with", "c1__a1", reward=True, age_min=0.1)
    monkeypatch.setattr(ep, "_claude_pid", lambda t: 999)
    out = ep.snapshot(base)
    assert out.endswith("| 活跃 0")
    assert "▸" not in out


def test_collect_trial_states_semantics(tmp_path, monkeypatch):
    base = tmp_path / "out"
    jobs = _jobs(base)
    done = _mk(jobs, "run-with", "c1__a1", reward=True, age_min=10)
    a1 = _mk(jobs, "with-c2", "c2__a2", age_min=3)
    a2 = _mk(jobs, "with-c2", "c2__a3", age_min=8)

    monkeypatch.setattr(ep, "_claude_pid", lambda t: 777)

    now = time.time()
    trials = [done, a1, a2]
    done_cases, active, started = ep._collect_trial_states(trials, now)
    assert set(done_cases) == {("with", "c1")}
    # 同用例多 attempt 取最久（a2 的 8min），并记 pid
    (dur, pid) = active[("with", "c2")]
    assert abs(dur - 8.0) < 0.1 and pid == 777
    # started 取全部 trial 最早起跑（done 的 10min 前）
    assert abs((now - started) / 60.0 - 10.0) < 0.1


def test_count_attempts_only_rewarded_attempt_jobs(tmp_path):
    base = tmp_path / "out"
    jobs = _jobs(base)
    _mk(jobs, "with-c1-attempt1", "c1__a1", reward=True)
    _mk(jobs, "with-c1-attempt1", "c1__a2")          # 同 attempt 无 reward
    _mk(jobs, "with-c2-attempt2", "c2__a3", reward=True)
    trials = sorted((jobs).rglob("*__*"))
    trials = [t for t in trials if (t / "agent").is_dir()]
    assert ep._count_attempts(trials) == 2
