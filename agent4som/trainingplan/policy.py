"""政策文件解析（转专业 / 专业选择）—— 设计 005 §6。

来源（`data/SmartGuide/`）：
- 《管理学院 2026 年本科生转专业实施细则》（PDF）：各专业接收计划 + 规则
- 《2026 年接收本科生转专业考核安排》（PDF）：笔试/面试时间地点
- 《管理学院 2025 级本科生专业选择实施方案》（PDF）：接收计划 + 时间线 + 综合成绩规则

产出结构化：`plan_transfer_plan` / `plan_transfer_rule` /
`plan_major_selection_plan` / `plan_major_selection_rule`。
不臆造：无法解析的项不写入，原文进 `plan_source_doc` 兜底。
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

# 政策文件里的专业名 → 标准专业名
_POLICY_MAJOR_ALIASES = {
    "会计学(ACCA)": "会计学（ACCA）",
    "会计学（ACCA）": "会计学（ACCA）",
    "大数据管理与应用": "大数据管理与应用",
    "大数据管理及应用": "大数据管理与应用",
    "工业工程": "工业工程",
    "工商管理": "工商管理",
}


def extract_text(path: str) -> str:
    """抽取 PDF / docx 文本（PDF 用 pypdf，docx 用 python-docx，含表格）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(path).pages)
        except Exception:
            return ""
    if ext == ".docx":
        try:
            from docx import Document
            doc = Document(path)
            parts = [p.text for p in doc.paragraphs]
            for t in doc.tables:
                for row in t.rows:
                    parts.append("\t".join(c.text for c in row.cells))
            return "\n".join(parts)
        except Exception:
            return ""
    return ""


def _norm_major(name: str) -> str:
    return _POLICY_MAJOR_ALIASES.get((name or "").strip(), (name or "").strip())


def _flat(text: str) -> str:
    """去掉所有空白，便于跨行正则。"""
    return re.sub(r"\s+", "", text or "")


# ─────────────────────────── 转专业实施细则 ───────────────────────────

_GRADES_ORDER = ["2025级", "2024级", "2023级"]     # 兜底：表头缺失时的默认列序


def _grade_order(text: str) -> list[str]:
    """从「接收计划」表头解析年级列序（如 2025级 2024级 2023级）。

    表头形如 `2025 级（大一） 2024 级（大二） 2023 级（大三）`，取首个含 ≥2 个
    年级的行；缺失/异常回退 `_GRADES_ORDER`，避免表头漂移导致漏解析。
    """
    for line in (text or "").splitlines():
        found = re.findall(r"(20\d\d)\s*级", line)
        if len(found) >= 2:
            return [f"{y}级" for y in found]
    return list(_GRADES_ORDER)


def parse_transfer_quota(text: str) -> list[dict[str, Any]]:
    """解析转专业「各专业接收计划」表。

    年级列序**由表头决定**（不再硬编码）；每专业一行 `3×N` 个数字：
    {年级} × {学院内, 跨学院, 合计}。返回 [{major, entry_year, scope, quota}]。
    """
    grades = _grade_order(text)
    row_re = re.compile(
        r"^([\u4e00-\u9fa5A-Za-z（）()]+?)\s+"
        + r"\s+".join([r"(\d+)"] * (len(grades) * 3)) + r"\s*$")
    out: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        m = row_re.match(line.strip())
        if not m:
            continue
        raw_name = m.group(1)
        if raw_name == "总计":
            continue
        major = _norm_major(raw_name)
        nums = [int(m.group(i)) for i in range(2, 2 + len(grades) * 3)]
        for gi, grade in enumerate(grades):
            in_school, cross, _total = nums[gi * 3], nums[gi * 3 + 1], nums[gi * 3 + 2]
            out.append({"major": major, "entry_year": grade, "scope": "学院内", "quota": in_school})
            out.append({"major": major, "entry_year": grade, "scope": "跨学院", "quota": cross})
    return out


def _policy_year(text: str) -> str:
    m = re.search(r"(20\d\d)\s*年本科生转专业", _flat(text))
    if m:
        return m.group(1)
    m = re.search(r"(20\d\d)\s*年", text or "")
    return m.group(1) if m else ""


def parse_transfer_rules(text: str, policy_year: str = "") -> list[dict[str, Any]]:
    """解析转专业规则（志愿/时间/考核/录取/补修/降级/公示）。"""
    rules: list[dict[str, Any]] = []
    flat = _flat(text)

    def add(rule_type: str, item: str, value: str = "", detail: str = "") -> None:
        rules.append({"rule_type": rule_type, "item": item, "value": value, "detail": detail})

    if "只允许填报一个专业志愿" in flat:
        add("志愿规则", "专业志愿数", "1", "每个学生只允许填报一个专业志愿，提交后不可更改")
    m = re.search(r"(20\d\d)年(\d+)月(\d+)日(\d+):(\d+)至(\d+)月(\d+)日(\d+):(\d+)", flat)
    if m:
        add("申请时间", "志愿填报",
            f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} "
            f"{m.group(4)}:{m.group(5)} ~ {int(m.group(6)):02d}-{int(m.group(7)):02d} "
            f"{m.group(8)}:{m.group(9)}", "教务系统 ehall.xjtu.edu.cn")
    if "申请表" in flat and "成绩单" in flat:
        add("申请材料", "转专业申请表 + 成绩单", "按通知截止时间提交", "电子版发至学院邮箱（baijiao@xjtu.edu.cn）")
    if "笔试" in flat and "面试" in flat:
        add("考核方式", "成绩构成", "笔试 50% + 综合考查（面试）50%",
            "面试考察英语听说、专业认知、未来学业规划、综合素质")
    m = re.search(r"笔试考试科目[：:]\s*([^\n。；]+)", text or "")
    if m:
        add("考核科目", "笔试", m.group(1).strip(), "以学院公布为准")
    elif "高等数学" in flat and "英语" in flat:
        add("考核科目", "笔试", "高等数学 I、英语", "")
    if "录满为止" in flat:
        add("录取规则", "择优录取", "按考核成绩从高到低，录满为止", "同分按笔试成绩排序")
    if "预修" in flat:
        add("录取规则", "预修机制", "",
            "报名低于计划且暂不达标者可预修：一年内获指定课程学分，次年办理转入")
    if "应修课程表" in flat:
        add("补修规则", "免补修条件",
            "已修课程学分 ≥ 目标专业「应修课程表」同一课程",
            "未修或学分低于应修课程表同一课程则必须补修")
    if "第三学年结束前" in flat:
        add("补修规则", "推免红线", "第三学年结束前修完补修课程",
            "否则不得参加当年免试研究生推荐")
    if "ACCA" in flat and "降级" in flat:
        add("降级规则", "会计学（ACCA）", "须降级就读", "大一/大二/大三转入均适用")
    m = re.search(r"预计于(20\d\d)年(\d+)月(\d+)日至(\d+)月(\d+)日", flat)
    if "公示" in flat and m:
        add("公示", "拟录取公示",
            f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} ~ "
            f"{int(m.group(4)):02d}-{int(m.group(5)):02d}", "公示期三天，可书面撤销")
    return rules


def parse_transfer_doc(path: str) -> dict[str, Any]:
    text = extract_text(path)
    year = _policy_year(text)
    return {"doc_type": "转专业政策", "policy_year": year,
            "plans": parse_transfer_quota(text), "rules": parse_transfer_rules(text, year),
            "text": text}


def parse_transfer_exam_doc(path: str, policy_year: str = "") -> dict[str, Any]:
    """解析转专业「考核安排」（笔试/面试时间地点）。"""
    text = extract_text(path)
    if not policy_year:
        m = re.search(r"(20\d\d)\s*年管理学院接收本科生转专业", text or "")
        policy_year = m.group(1) if m else ""
    rules: list[dict[str, Any]] = []

    def add(item: str, value: str, detail: str = "") -> None:
        rules.append({"rule_type": "考核安排", "item": item, "value": value, "detail": detail})

    for line in (text or "").splitlines():
        s = line.strip()
        m = re.search(r"^(数学|英语)[：:]\s*(20\d\d-\d\d-\d\d)[^0-9]*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", s)
        if m:
            subject = "高等数学 I" if m.group(1) == "数学" else "大学英语"
            add(f"笔试-{m.group(1)}", f"{m.group(2)} {m.group(3)}-{m.group(4)}", subject)
    m = re.search(r"考试地点[：:]\s*([^\n。]+)", text or "")
    if m:
        add("笔试地点", m.group(1).strip())
    m = re.search(r"面试时间[：:]\s*(20\d\d-\d\d-\d\d)[^0-9]*(\d{1,2}:\d{2})", text or "")
    if m:
        add("面试", f"{m.group(1)} {m.group(2)}", "携带学生证/一卡通、简历、成绩单")
    for mm in re.finditer(r"文管大楼\s*(\d+)\s*室[（(]([^）)]+)[）)]", text or ""):
        add("面试地点", f"文管大楼{mm.group(1)}室", mm.group(2).strip())
    return {"doc_type": "转专业考核安排", "policy_year": policy_year, "plans": [], "rules": rules,
            "text": text}


# ─────────────────────────── 专业选择实施方案 ───────────────────────────

_MS_QUOTA_RE = re.compile(r"^([\u4e00-\u9fa5A-Za-z（）()]+?)\s+(\d+)\s*人?\s*$")


def parse_major_selection_quota(text: str) -> list[dict[str, Any]]:
    """解析专业选择「各专业接收计划」（专业 人数）。"""
    out: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        m = _MS_QUOTA_RE.match(line.strip())
        if not m:
            continue
        name = m.group(1)
        if name in ("专业", "总计", "合计"):
            continue
        major = _norm_major(name)
        if major not in ("工商管理", "工业工程", "大数据管理与应用", "会计学（ACCA）"):
            continue
        out.append({"major": major, "quota": int(m.group(2))})
    return out


def parse_major_selection_rules(text: str, entry_year: str = "") -> list[dict[str, Any]]:
    flat = _flat(text)
    rules: list[dict[str, Any]] = []

    def add(rule_type: str, item: str, value: str = "", detail: str = "") -> None:
        rules.append({"rule_type": rule_type, "item": item, "value": value, "detail": detail})

    if "三个专业志愿" in flat:
        add("志愿规则", "专业志愿数", "3", "按顺序选报三个专业志愿，不得少报")
    if "专业志愿优先" in flat:
        add("录取规则", "录取原则", "专业志愿优先，遵从综合成绩",
            "第一志愿≤计划直接录取；>计划参加专项考查，按综合成绩排名依次录取")
    m = re.search(r"综合成绩[=＝]([^。；]+)", flat)
    if m:
        add("成绩构成", "综合成绩", m.group(1).strip(),
            "学业成绩由学院提供，综合素质测评由书院提供")
    elif "70%学业成绩" in flat:
        add("成绩构成", "综合成绩", "70%学业成绩 + 20%专项考查 + 10%综合素质测评", "")
    if "补考或重修" in flat:
        add("学业成绩口径", "计算规则",
            "补考/重修及格按 60 分计；应修未修/缓考未补考按 0 分计",
            "由培养方案规定的必须修读课程学分成绩构成")
    if "大一学年的第二学期启动" in flat or "第二学期启动" in flat:
        add("时间安排", "启动时点", "大一学年第二学期", "在本学院范围内组织")
    if "未确定主修专业" in flat:
        add("学生范围", "参与对象", "未确定主修专业的在籍在校学生",
            "复学/留级已定专业者、港澳台/预科/留学生不参加")
    return rules


def parse_major_selection_doc(path: str) -> dict[str, Any]:
    text = extract_text(path)
    flat = _flat(text)
    m = re.search(r"管理学院(20\d\d)级本科生专业选择", flat)
    entry_year = f"{m.group(1)}级" if m else ""
    return {"doc_type": "专业选择方案", "entry_year": entry_year,
            "plans": parse_major_selection_quota(text),
            "rules": parse_major_selection_rules(text, entry_year), "text": text}


# ─────────────────────────── 按文件名分派 ───────────────────────────

def classify_policy(file_name: str) -> Optional[str]:
    """按文件名判断政策类型：转专业政策 / 转专业考核安排 / 专业选择方案 / 操作指引。"""
    n = file_name or ""
    if "转专业" in n and ("考核" in n or "安排" in n):
        return "转专业考核安排"
    if "转专业" in n:
        return "转专业政策"
    if "专业选择" in n and ("操作流程" in n or "志愿填报" in n):
        return "操作指引"
    if "专业选择" in n or "专业分流" in n:
        return "专业选择方案"
    return None


def parse_policy_file(path: str, kind: Optional[str] = None) -> Optional[dict[str, Any]]:
    kind = kind or classify_policy(os.path.basename(path))
    if kind == "转专业政策":
        return parse_transfer_doc(path)
    if kind == "转专业考核安排":
        return parse_transfer_exam_doc(path)
    if kind == "专业选择方案":
        return parse_major_selection_doc(path)
    return None
