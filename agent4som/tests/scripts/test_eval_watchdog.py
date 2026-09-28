"""scripts/eval_watchdog.py 单元测试（此前零覆盖，离线 + 本地子进程）。

不依赖真实评测夹具：trial 目录用 tmp_path 构造；--kill / --stop 用本地
短命子进程验证（cmdline 就绪按审计 §9 教训先轮询再断言）。
"""
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

_SRC = Path(__file__).resolve().parents[2] / "scripts" / "eval_watchdog.py"
_spec = importlib.util.spec_from_file_location("eval_watchdog_under_test", _SRC)
wd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wd)


def _wait_cmdline(pid: int, needle: str, timeout: float = 5.0) -> bool:
    """Popen 返回 ≠ exec 已生效（OpenEuler posix_spawn 窗口），先等 /proc 就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                if needle.encode() in fh.read():
                    return True
        except (FileNotFoundError, PermissionError):
            pass
        time.sleep(0.05)
    return False


def _make_trial(root: Path, name="t1") -> Path:
    trial = root / "_harbor-jobs" / "run1" / name
    agent = trial / "agent"
    agent.mkdir(parents=True)
    (agent / "claude-code.txt").write_text("log", encoding="utf-8")
    return trial


def _args(**kw):
    base = {"idle_min": 4.0, "max_trial_min": 10.0, "kill": False}
    base.update(kw)
    return SimpleNamespace(**base)


# ── 目录/时间基元 ──────────────────────────────────────────────────


def test_newest_mtime_ignores_missing(tmp_path):
    gone = tmp_path / "gone.txt"
    real = tmp_path / "real.txt"
    real.write_text("x", encoding="utf-8")
    assert wd._newest_mtime([gone, real]) == real.stat().st_mtime
    assert wd._newest_mtime([gone]) == 0.0


def test_find_trials_three_depths(tmp_path):
    t1 = _make_trial(tmp_path, "a")
    found = wd._find_trials(tmp_path)
    assert found == [t1]
    # 深层结构（root/harbor/_harbor-jobs/...）也能命中
    deep = tmp_path / "h" / "_harbor-jobs" / "r2" / "b"
    (deep / "agent").mkdir(parents=True)
    (deep / "agent" / "claude-code.txt").write_text("x", encoding="utf-8")
    assert deep in wd._find_trials(tmp_path)


def test_trial_start_earliest_file(tmp_path):
    trial = _make_trial(tmp_path)
    old = time.time() - 3600
    os.utime(trial / "agent" / "claude-code.txt", (old, old))
    assert abs(wd._trial_start(trial) - old) < 5


# ── 阈值判定 ───────────────────────────────────────────────────────


def test_stuck_reasons_idle_and_duration(tmp_path):
    trial = _make_trial(tmp_path)
    now = time.time()
    first_seen = {}
    first_seen.setdefault(str(trial), now)

    # 新鲜活动 + 刚起步 → 无原因
    assert wd._trial_stuck_reasons(trial, now, _args(), first_seen) == []

    # idle 超阈：最近写入 10 分钟前（dur 阈值放宽到 30，单因素判定）
    os.utime(trial / "agent" / "claude-code.txt", (now - 600, now - 600))
    reasons = wd._trial_stuck_reasons(trial, now, _args(max_trial_min=30.0),
                                      first_seen)
    assert len(reasons) == 1 and reasons[0].startswith("idle 10.0min")

    # dur 超阈但活动新鲜：旧文件（起跑 30 分钟前）+ 新 session 写入（30 秒前）
    sess = trial / "agent" / "sessions" / "s.jsonl"
    sess.parent.mkdir()
    sess.write_text("{}", encoding="utf-8")
    os.utime(sess, (now - 30, now - 30))
    os.utime(trial / "agent" / "claude-code.txt", (now - 1800, now - 1800))
    reasons = wd._trial_stuck_reasons(trial, now, _args(), first_seen)
    assert [r.split()[0] for r in reasons] == ["dur"]

    # 双因素：唯一文件既是最旧也是最新（10 分钟前，两阈值均为默认 10）
    sess.unlink()
    os.utime(trial / "agent" / "claude-code.txt", (now - 600, now - 600))
    reasons = wd._trial_stuck_reasons(trial, now, _args(), first_seen)
    assert [r.split()[0] for r in reasons] == ["idle", "dur"]

    # 看门狗中途启动：_trial_start 为 0 时回落到 first_seen
    first_seen[str(trial)] = now - 1200
    broken = trial / "agent" / "claude-code.txt"
    broken.unlink()
    reasons = wd._trial_stuck_reasons(trial, now, _args(), first_seen)
    # _active_activity 无文件 → 0 → idle 巨大；start 回落 first_seen → dur 20min
    assert any(r.startswith("dur 20.0min") for r in reasons)


# ── 报告与终止 ─────────────────────────────────────────────────────


def test_report_and_kill_warn_only_and_kill(tmp_path):
    trial = _make_trial(tmp_path)
    root = tmp_path
    lines = []
    emit = lines.append
    killed: set = set()

    wd._report_and_kill(trial, root, 4242, str(trial), ["idle 9.9min"],
                        _args(kill=False), killed, emit)
    assert any("trial 卡住（idle 9.9min）pid=4242" in l for l in lines)
    assert killed == set()   # 未开 --kill 不记账（每轮重新告警）

    # --kill：对真实短命子进程发 SIGTERM，记账防重复
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        wd._report_and_kill(trial, root, proc.pid, str(trial), ["dur 12.0min"],
                            _args(kill=True), killed, emit)
        assert str(trial) in killed
        assert proc.wait(timeout=5) is not None
    finally:
        proc.kill()

    # kill 失败分支：不存在的 PID → 告警不抛异常
    wd._report_and_kill(trial, root, 999999, str(trial), ["dur 12.0min"],
                        _args(kill=True), set(), emit)
    assert any("kill 失败" in l for l in lines)


def test_stop_existing_branches(tmp_path, capsys):
    # 无 pidfile
    assert wd._stop_existing(SimpleNamespace(pidfile="")) == 0
    assert "没有 pidfile" in capsys.readouterr().out

    # pidfile 内容无效
    bad = tmp_path / "bad.pid"
    bad.write_text("not-a-pid", encoding="utf-8")
    assert wd._stop_existing(SimpleNamespace(pidfile=str(bad))) == 0
    assert "pidfile 无效" in capsys.readouterr().out

    # cmdline 不符（普通 sleep 进程不是看门狗）→ 拒绝停止
    # 注意：不能用本 pytest 进程——其 cmdline 含 "eval_watchdog.py"（测试文件名），
    # 会被误判为看门狗而 SIGTERM 掉测试进程本身。
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert _wait_cmdline(proc.pid, "time.sleep")
        not_wd = tmp_path / "not_wd.pid"
        not_wd.write_text(str(proc.pid), encoding="utf-8")
        assert wd._stop_existing(SimpleNamespace(pidfile=str(not_wd))) == 0
        assert "拒绝停止" in capsys.readouterr().out
        assert not_wd.exists()   # 拒绝时不删 pidfile
        assert proc.poll() is None   # 进程未被杀
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_stop_existing_kills_real_watchdog(tmp_path, capsys):
    """对真实看门狗子进程：SIGTERM + 删除 pidfile。"""
    pf = tmp_path / "wd.pid"
    proc = subprocess.Popen(
        [sys.executable, str(_SRC), str(tmp_path), "--interval", "5",
         "--pidfile", str(pf)])
    try:
        deadline = time.time() + 10
        while not pf.exists() and time.time() < deadline:
            time.sleep(0.1)
        assert pf.exists()
        assert _wait_cmdline(proc.pid, "eval_watchdog.py")   # §9：先等 cmdline 就绪
        assert wd._stop_existing(SimpleNamespace(pidfile=str(pf))) == 0
        assert "已停止看门狗" in capsys.readouterr().out
        assert not pf.exists()
        assert proc.wait(timeout=5) is not None
    finally:
        proc.kill()
        proc.wait(timeout=5)
