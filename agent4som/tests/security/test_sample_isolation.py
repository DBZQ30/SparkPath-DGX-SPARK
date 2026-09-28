"""样本隔离与 PII 卫生（测试数据分层设计的安全门）。

覆盖（设计文档 docs/02-features/009-test-data-layering.md）：
- S10 真实样本目录不入库：git 跟踪文件不含 academicwarning/docs/ 与 data/ 下的文件
- S11 成绩单金标准 grade_golden.json 被 .gitignore 排除（含真实课程名/成绩）
- S12 git 跟踪文本文件无真实学号形状字面量（10 位纯数字以 2 开头；测试合成
     约定为 9 开头，如 9000000001）
- S13 历史清查中确认过的真实学生姓名未回流入库——黑名单本体存开发机
     gitignored 的 pii_blacklist.json（本文件不落真实标识，防"清查清单变泄露清单"）
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

MONOREPO_ROOT = Path(__file__).resolve().parents[3]
PII_BLACKLIST = MONOREPO_ROOT / "agent4som/academicwarning/docs/pii_blacklist.json"

pytestmark = pytest.mark.security

_TextExts = {".py", ".md", ".js", ".json", ".yaml", ".yml", ".sh", ".toml", ".html", ".txt"}

# 10 位纯数字、以 2 开头（真实学号形状）。合成值约定 9 开头，不触发本规则。
_SID_SHAPE = re.compile(r"(?<!\d)2\d{9}(?!\d)")


def _git(args: list[str]) -> str:
    # core.quotepath=false：非 ASCII 路径不加引号（引号包裹会让 startswith
    # 与 Path 读取双双漏检——2026-09-27 quotepath 盲区事故）
    return subprocess.run(["git", "-c", "core.quotepath=false", *args],
                          cwd=MONOREPO_ROOT, check=True,
                          capture_output=True, text=True).stdout


def _tracked_text_files() -> list[Path]:
    out = []
    for line in _git(["ls-files"]).splitlines():
        p = MONOREPO_ROOT / line
        if p.exists() and p.suffix.lower() in _TextExts:
            out.append(p)
    return out


# 唯一合法例外：学分结构 JSON（service.py 运行时加载的权威毕业要求资产，
# HANDOFF.md 记录在案当初 -f 强制入库；内容为 4 专业课程学分树，无学生个人信息）
_RUNTIME_ASSET_WHITELIST = {
    "agent4som/academicwarning/docs/"
    "大数据管理与应用-工商管理-工业工程-会计学-ACCA培养方案学分结构.json",
}


def test_no_real_samples_tracked():
    """S10：样本目录（academicwarning/docs/、agent4som/data/）零 git 跟踪文件
    （白名单例外见 _RUNTIME_ASSET_WHITELIST）。"""
    tracked = _git(["ls-files"]).splitlines()
    leaked = [f for f in tracked
              if (f.startswith("agent4som/academicwarning/docs/")
                  or f.startswith("agent4som/data/"))
              and f not in _RUNTIME_ASSET_WHITELIST]
    assert leaked == [], f"真实样本被 git 跟踪: {leaked}"


def test_grade_golden_json_gitignored():
    """S11：成绩单金标准 JSON（真实课程名/成绩）必须被 .gitignore 排除。"""
    if not (MONOREPO_ROOT / "agent4som/academicwarning/docs/grade_golden.json").exists():
        pytest.skip("开发机无金标准文件（已是隔离状态）")
    r = subprocess.run(["git", "check-ignore", "-q",
                        "agent4som/academicwarning/docs/grade_golden.json"],
                       cwd=MONOREPO_ROOT, check=False)
    assert r.returncode == 0, "grade_golden.json 未被 .gitignore 覆盖"


def test_no_real_sid_literals_in_tracked_files():
    """S12：跟踪文本文件无 2 开头 10 位学号形状字面量（合成约定 9 开头）。"""
    hits = []
    for p in _tracked_text_files():
        for i, line in enumerate(p.read_text(encoding="utf-8",
                                             errors="replace").splitlines(), 1):
            if _SID_SHAPE.search(line):
                hits.append(f"{p.relative_to(MONOREPO_ROOT)}:{i}")
    assert hits == [], f"疑似真实学号字面量: {hits[:10]}"


def test_no_scrubbed_real_names_in_tracked_files():
    """S13：历史清查确认的真实姓名不得回流（防回归）。

    黑名单在 gitignored 的 pii_blacklist.json（含 name 数组）——本测试文件与
    仓库任何跟踪文件都不落真实标识；干净检出无黑名单时 skip。"""
    if not PII_BLACKLIST.exists():
        pytest.skip("PII 黑名单缺失（开发机 gitignored 文件，见 docs/02-features/009-test-data-layering.md §3）")
    names = json.loads(PII_BLACKLIST.read_text(encoding="utf-8"))["names"]
    hits = []
    for p in _tracked_text_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for name in names:
            if name in text:
                hits.append(f"{p.relative_to(MONOREPO_ROOT)}: {name}")
    assert hits == [], f"真实姓名回流: {hits[:10]}"
