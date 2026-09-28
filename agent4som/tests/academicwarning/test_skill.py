"""SKILL.md 结构完整性测试。"""
import os
import re

import pytest   # L3：skip 分支需要，缺失会 NameError

PATH = "shared_skills/academic-warning/SKILL.md"


def test_skill_frontmatter():
    if not os.path.exists(PATH):
        pytest.skip("SKILL.md 未创建")
    text = open(PATH, encoding="utf-8").read()
    assert text.startswith("---")
    m = re.search(r"^name: (.+)$", text, re.M)
    assert m and m.group(1).strip() == "academic-warning"
    assert "description:" in text
    assert "python -m academicwarning.cli" in text  # 调用入口
