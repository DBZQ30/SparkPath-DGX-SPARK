#!/usr/bin/env python3
"""给**已安装**的 SkillEvaluator 判官打本地补丁：把「只重试 1 次」改成可配多次（带线性退避）。

为什么需要
----------
`tier3/harbor/templates/eval.py` 的判官调用只重试 **1** 次，而 `--n-attempts 1` 时：

    任一 trial 判官失败 → 该 trial Unscoreable → execution_status=failed → 整个 Tier 3 无聚合分

34 个 trial × 3 个必答判官 ≈ **100+ 次调用**，provider 一次瞬时 502 或一次返回不可解析
就废掉整轮（实测两次，每轮 45 分钟）。上游又没有暴露「重试次数」的环境变量。

补丁内容（幂等，可重复执行）
--------------------------
1. 在 `DEFAULT_JUDGE_MODEL` 前插入可配常量，并确保 `import time`：
     JUDGE_MAX_ATTEMPTS      = env `SKILL_EVAL_JUDGE_ATTEMPTS`       （默认 3）
     JUDGE_RETRY_BACKOFF_SEC = env `SKILL_EVAL_JUDGE_RETRY_BACKOFF`  （默认 2 秒，线性退避）
2. `_call_validated_json_judge`：1 次重试 → 最多 JUDGE_MAX_ATTEMPTS 次
3. `_judge_behavior`：上游注释 "One retry max" → 最多 JUDGE_MAX_ATTEMPTS 次

⚠️ 这是对**已安装工具**的本地补丁 —— `uv tool install` 升级/重装后会**被覆盖**，
   重新执行本脚本即可（脚本会自检是否已打过）。

用法
----
    python3 patch_skillevaluator_judge_retries.py            # 打补丁
    python3 patch_skillevaluator_judge_retries.py --check    # 只检查
    python3 patch_skillevaluator_judge_retries.py --revert   # 从备份还原
"""
from __future__ import annotations

import argparse
import pathlib
import py_compile
import shutil
import sys

MARK = "JUDGE_MAX_ATTEMPTS"          # 阶段1 幂等标记
MARK_TOKENS = "SKILL_EVAL_JUDGE_MAX_TOKENS"   # 阶段2 幂等标记
BACKUP_SUFFIX = ".pre-judge-retry-patch"

IMPORT_ANCHOR = "import shlex\nimport sys\n"
IMPORT_PATCHED = "import shlex\nimport sys\nimport time  # [local patch] judge retry backoff\n"

CONST_ANCHOR = 'DEFAULT_JUDGE_MODEL = "gpt-5.6-sol"\n'
CONST_PATCHED = (
    "# ── [local patch: skillevaluator-judge-retry] ──────────────────────────────\n"
    "# 上游判官只重试 1 次；provider 一次瞬时抖动就会让整个 Tier 3 无聚合分。\n"
    "# 可用 SKILL_EVAL_JUDGE_ATTEMPTS / SKILL_EVAL_JUDGE_RETRY_BACKOFF 覆盖。\n"
    "JUDGE_MAX_ATTEMPTS = max(1, int(os.environ.get(\"SKILL_EVAL_JUDGE_ATTEMPTS\", \"3\") or 3))\n"
    "JUDGE_RETRY_BACKOFF_SEC = max(\n"
    "    0.0, float(os.environ.get(\"SKILL_EVAL_JUDGE_RETRY_BACKOFF\", \"2\") or 2)\n"
    ")\n"
    "# ── [/local patch] ─────────────────────────────────────────────────────────\n"
    + CONST_ANCHOR
)

STRUCTURED_OLD = '''    parsed, error, provenance, validation_error = invoke(prompt)
    if error:
        return None, f"LLM judge error: {error}", provenance
    if validation_error is None:
        return parsed, None, provenance

    parsed, error, provenance, validation_error = invoke(prompt + _JUDGE_RETRY_REMINDER)
    if error:
        return None, f"LLM judge retry error: {error}", provenance
    if validation_error is not None:
        return None, f"{validation_error} after retry", provenance
    return parsed, None, provenance
'''

STRUCTURED_NEW = '''    # [local patch] 最多 JUDGE_MAX_ATTEMPTS 次（上游只重试 1 次）
    last = None
    for _attempt in range(JUDGE_MAX_ATTEMPTS):
        _prompt = prompt if _attempt == 0 else prompt + _JUDGE_RETRY_REMINDER
        parsed, error, provenance, validation_error = invoke(_prompt)
        last = (parsed, error, provenance, validation_error, _attempt + 1)
        if error is None and validation_error is None:
            return parsed, None, provenance
        if _attempt + 1 < JUDGE_MAX_ATTEMPTS and JUDGE_RETRY_BACKOFF_SEC:
            time.sleep(JUDGE_RETRY_BACKOFF_SEC * (_attempt + 1))
    parsed, error, provenance, validation_error, _used = last
    if error:
        _label = "LLM judge error" if _used == 1 else "LLM judge retry error"
        return None, f"{_label}: {error}", provenance
    return None, f"{validation_error} after {_used} attempts", provenance
'''

BEHAVIOR_OLD = '''    if score is None:
        # One retry max, with an explicit machine-readable-output reminder.
        retry_content, retry_error = call_public_llm(
            prompt + _BEHAVIOR_RETRY_REMINDER, max_tokens=BEHAVIOR_JUDGE_MAX_TOKENS
        )
        if not retry_error:
            parsed = _parse_judge_object(retry_content)
            attempts.append((retry_content or "", parsed))
            score = _behavior_payload_score(parsed, len(expected_behaviors))
'''

# ── 阶段 2：判官输出 token 上限（4096 会把长轨迹的判官输出截断 → 非法 JSON）──
TOKENS_ANCHOR = "STRUCTURED_JUDGE_MAX_TOKENS = 4096\n"
TOKENS_PATCHED = (
    "# [local patch] 4096 会把长轨迹的判官输出截断 → 解析失败（重试也无效）\n"
    "STRUCTURED_JUDGE_MAX_TOKENS = max(\n"
    "    512, int(os.environ.get(\"SKILL_EVAL_JUDGE_MAX_TOKENS\", \"8192\") or 8192)\n"
    ")\n"
)

# ── 阶段 3：attempt-merge 的 snapshot 拷贝要跳过 Claude Code 的 claude-tmp ──
# 背景：--n-attempts >= 2 时，runner 会把每个 attempt job 目录 copytree_secure 到快照；
# 而 Claude Code 跑 subagent 时会在 <trial>/agent/claude-tmp 下建符号链接，
# secure copy 拒绝任何软链 → 抛 UnsafeStagingError → Tier 3 被判 "skipped"（整轮丢弃）。
RUNNER_MARK = "[local patch] 跳过 Claude Code"
RUNNER_ANCHOR = "            copytree_secure(job_path, snapshot, allowed_root=job_resolved)\n"
RUNNER_PATCHED = (
    "            # [local patch] 跳过 Claude Code 自己的 claude-tmp\n"
    "            # （内含 subagent 软链：tasks/<hex>.output -> .../subagents/agent-<hex>.jsonl；\n"
    "            #   secure copy 拒绝软链，会让整个 Tier 3 被丢弃）\n"
    "            copytree_secure(\n"
    "                job_path,\n"
    "                snapshot,\n"
    "                allowed_root=job_resolved,\n"
    "                ignore=lambda _dirpath, names: [n for n in names if n == \"claude-tmp\"],\n"
    "            )\n"
)

BEHAVIOR_NEW = '''    # [local patch] 最多 JUDGE_MAX_ATTEMPTS 次（上游为 "One retry max"），带线性退避
    _behavior_attempt = 1
    while score is None and _behavior_attempt < JUDGE_MAX_ATTEMPTS:
        if JUDGE_RETRY_BACKOFF_SEC:
            time.sleep(JUDGE_RETRY_BACKOFF_SEC * _behavior_attempt)
        retry_content, retry_error = call_public_llm(
            prompt + _BEHAVIOR_RETRY_REMINDER, max_tokens=BEHAVIOR_JUDGE_MAX_TOKENS
        )
        _behavior_attempt += 1
        if not retry_error:
            parsed = _parse_judge_object(retry_content)
            attempts.append((retry_content or "", parsed))
            score = _behavior_payload_score(parsed, len(expected_behaviors))
'''


def find_template() -> pathlib.Path:
    try:
        import skillevaluator
    except ImportError:
        sys.exit("找不到 skillevaluator 包 —— 请用它的 venv 运行本脚本，"
                 "例如 ~/.local/share/uv/tools/skillevaluator/bin/python3")
    root = pathlib.Path(skillevaluator.__file__).parent
    tpl = root / "tier3" / "harbor" / "templates" / "eval.py"
    if not tpl.is_file():
        sys.exit(f"找不到模板文件: {tpl}")
    return tpl


def is_patched(text: str) -> bool:
    return MARK in text


def patch(tpl: pathlib.Path, check_only: bool) -> int:
    text = tpl.read_text(encoding="utf-8")
    need_retry = MARK not in text
    need_tokens = MARK_TOKENS not in text

    if not need_retry and not need_tokens:
        print(f"✅ 两个阶段都已打过补丁：{tpl}")
        return 0
    if check_only:
        print(f"❌ 未打补丁（重试阶段缺={need_retry}，token 上限阶段缺={need_tokens}）：{tpl}")
        return 1

    backup = tpl.with_name(tpl.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(tpl, backup)
        print(f"已备份原文件 → {backup}")

    if need_retry:
        for name, anchor in (("import", IMPORT_ANCHOR), ("常量", CONST_ANCHOR),
                             ("结构化判官", STRUCTURED_OLD), ("behavior 判官", BEHAVIOR_OLD)):
            if text.count(anchor) != 1:
                sys.exit(f"!! [阶段1] 锚点 '{name}' 未命中或命中多次（{text.count(anchor)} 次）——"
                         f"上游可能已改动，请人工核对 {tpl}")
        text = text.replace(IMPORT_ANCHOR, IMPORT_PATCHED, 1)
        text = text.replace(CONST_ANCHOR, CONST_PATCHED, 1)
        text = text.replace(STRUCTURED_OLD, STRUCTURED_NEW, 1)
        text = text.replace(BEHAVIOR_OLD, BEHAVIOR_NEW, 1)
        print("  [阶段1] 判官重试次数可配（默认 3）已应用")

    if need_tokens:
        if text.count(TOKENS_ANCHOR) != 1:
            sys.exit(f"!! [阶段2] 锚点 'STRUCTURED_JUDGE_MAX_TOKENS' 未命中或命中多次"
                     f"（{text.count(TOKENS_ANCHOR)} 次）—— 上游可能已改动，请人工核对 {tpl}")
        text = text.replace(TOKENS_ANCHOR, TOKENS_PATCHED, 1)
        print("  [阶段2] 判官输出上限 4096 → 8192（可配）已应用")

    tpl.write_text(text, encoding="utf-8")
    py_compile.compile(str(tpl), doraise=True)
    print(f"✅ 补丁已应用并通过语法检查：{tpl}")
    print("   判官重试默认 3 次（SKILL_EVAL_JUDGE_ATTEMPTS）、退避 2s（SKILL_EVAL_JUDGE_RETRY_BACKOFF）、"
          "输出上限 8192（SKILL_EVAL_JUDGE_MAX_TOKENS）")
    return 0


def revert(tpl: pathlib.Path) -> int:
    backup = tpl.with_name(tpl.name + BACKUP_SUFFIX)
    if not backup.exists():
        sys.exit(f"没有备份可还原：{backup}")
    shutil.copy2(backup, tpl)
    py_compile.compile(str(tpl), doraise=True)
    print(f"✅ 已从备份还原：{tpl}")
    return 0


def find_runner() -> pathlib.Path:
    try:
        import skillevaluator
    except ImportError:
        sys.exit("找不到 skillevaluator 包 —— 请用它的 venv 运行本脚本")
    p = pathlib.Path(skillevaluator.__file__).parent / "tier3" / "harbor" / "runner.py"
    if not p.is_file():
        sys.exit(f"找不到 runner 文件: {p}")
    return p


def patch_runner(tpl: pathlib.Path, check_only: bool) -> int:
    text = tpl.read_text(encoding="utf-8")
    if RUNNER_MARK in text:
        print(f"✅ 阶段3（attempt-merge snapshot 跳过 claude-tmp）已打过：{tpl}")
        return 0
    if check_only:
        print(f"❌ 阶段3 未打：{tpl}")
        return 1
    if text.count(RUNNER_ANCHOR) != 1:
        sys.exit(f"!! [阶段3] 锚点未命中或命中多次（{text.count(RUNNER_ANCHOR)} 次）——"
                 f"上游可能已改动，请人工核对 {tpl}")
    backup = tpl.with_name(tpl.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(tpl, backup)
        print(f"已备份原文件 → {backup}")
    tpl.write_text(text.replace(RUNNER_ANCHOR, RUNNER_PATCHED, 1), encoding="utf-8")
    py_compile.compile(str(tpl), doraise=True)
    print(f"✅ 阶段3 已应用并通过语法检查：{tpl}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="只检查是否已打补丁")
    ap.add_argument("--revert", action="store_true", help="从备份还原")
    args = ap.parse_args()

    tpl = find_template()
    if args.revert:
        return revert(tpl)
    rc1 = patch(tpl, args.check)
    rc2 = patch_runner(find_runner(), args.check)
    return max(rc1, rc2)


if __name__ == "__main__":
    sys.exit(main())
