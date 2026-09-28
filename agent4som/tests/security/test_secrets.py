"""密钥管理（Secrets Management）测试：git 跟踪文件扫描 + .env 卫生。

覆盖：
- S01 无真实密钥被 git 跟踪（sk- 式 API key / PEM 私钥块）
- S02 .env 已被 .gitignore 排除，绝不入库
- S03 .env.example 值均为占位符（不含真实凭证）
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

MONOREPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.security

# sk- 式 API key（≥20 位连续字母数字，排除 sk-test / sk-xxx 等占位写法）：
# Python 语法（? negative lookbehind of word）简化为结果过滤。
_SK_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
_PEM_BEGIN = "-----BEGIN"
_PEM_END = "-----END"

_PLACEHOLDER_HINTS = ("test", "xxxx", "xxx", "example", "your", "<", "placeholder", "changeme")


def _git_tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=MONOREPO_ROOT,
        capture_output=True, text=True, check=True,
    ).stdout
    return [MONOREPO_ROOT / line for line in out.splitlines() if line.strip()]


def _is_text_file(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\x00" not in fh.read(8192)
    except OSError:
        return False


def _looks_placeholder(match: str) -> bool:
    low = match.lower()
    return any(hint in low for hint in _PLACEHOLDER_HINTS)


# ── S01：git 跟踪文件无真实密钥 ────────────────────────────────────


def test_no_api_key_style_secrets_in_tracked_files():
    files = _git_tracked_files()
    assert files, "git ls-files 不应为空（仓库损坏？）"
    offenders: list[str] = []
    for f in files:
        if not _is_text_file(f):
            continue
        try:
            if f.stat().st_size > 2 * 1024 * 1024:
                continue
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _SK_PATTERN.finditer(content):
            if not _looks_placeholder(m.group(0)):
                offenders.append(f"{f.relative_to(MONOREPO_ROOT)}: {m.group(0)[:16]}…")
    assert offenders == [], "跟踪文件中发现疑似真实 API key:\n" + "\n".join(offenders)


def test_no_pem_private_key_bodies_in_tracked_files():
    """要求 BEGIN 与 END 标记同时出现（防把“检测代码里的标记字符串”误报）。"""
    offenders: list[str] = []
    for f in _git_tracked_files():
        if not _is_text_file(f):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not (content.count(_PEM_BEGIN) and content.count(_PEM_END)):
            continue
        for m in re.finditer(
                r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[A-Za-z0-9+/=\s]{40,}-----END [A-Z0-9 ]*PRIVATE KEY-----",
                content):
            offenders.append(f"{f.relative_to(MONOREPO_ROOT)}: {m.group(0)[:40]}…")
    assert offenders == [], "跟踪文件中发现 PEM 私钥块:\n" + "\n".join(offenders)


# ── S02：.env 排除 ─────────────────────────────────────────────────


def test_env_file_is_gitignored():
    r = subprocess.run(["git", "check-ignore", "agent4som/.env"],
                       cwd=MONOREPO_ROOT, capture_output=True, text=True,
                       check=False)  # 退出码 1 = 未忽略，由下方断言判定
    assert r.returncode == 0, ".env 未被 .gitignore 排除"


def test_env_file_not_tracked():
    tracked = {f.name for f in _git_tracked_files()}
    assert ".env" not in tracked, ".env 出现在 git 跟踪列表！"


# ── S03：.env.example 占位符 ───────────────────────────────────────


def test_env_example_values_are_placeholders():
    """模板中所有 *_KEY / *_TOKEN / *_SECRET / *_PASSWORD 值仍是占位符。"""
    example = MONOREPO_ROOT / "agent4som" / ".env.example"
    assert example.is_file(), ".env.example 缺失"
    secret_re = re.compile(
        r"^(?P<name>[A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)\s*=\s*(?P<value>.+)$")
    bad: list[str] = []
    for line in example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = secret_re.match(line)
        if not m:
            continue
        value = m.group("value").strip().strip('"').strip("'")
        # 占位符样式：<...>、your/test/xxx，或公认的本地默认值
        if not value:
            continue
        # 非占位符且长得像随机凭证（无空格、长度≥16）→ 可疑
        if (not any(hint in value.lower() for hint in _PLACEHOLDER_HINTS)
                and len(value) >= 16 and " " not in value):
            bad.append(line)
    assert bad == [], ".env.example 存在疑似真实凭证:\n" + "\n".join(bad)
