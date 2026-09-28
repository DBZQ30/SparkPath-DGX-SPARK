"""SmartGuide 目录落库（本功能自建库，唯一目标 `data/training_plan.db`）。

**边界（2026-09-23 用户明确要求）**：
- 只写本功能的 `training_plan.db`（`plan_source_doc` + 结构化 `plan_*` 表）；
- **不写** 公共知识库（ChromaDB / `global` scope）；
- **不写** 学业预警的 `warning.db`。

内容分类：
- 培养方案 docx → 结构化解析（`plan_document` 等），同时登记原件
- 教学计划 PDF / 政策细则 / 课程大纲 / 清单 → 登记原件 + 抽取文本（`plan_source_doc`）
"""
from __future__ import annotations

import os
import re
from typing import Any

from .db import ALL_GRADES, TrainingPlanDB

# 分类规则（按文件名匹配，顺序敏感：先具体后兜底）
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("培养方案", ("专业培养方案",)),
    ("教学计划", ("指导性教学计划",)),
    ("政策", ("实施细则", "指导意见", "选课指南", "限选要求", "选课说明", "选课注意",
              "培养模式", "培养细则", "指南", "专业选择", "实施方案", "转专业")),
    ("清单", ("清单", "通识课程信息表")),
    ("大纲", ("大纲",)),
]


def classify(file_name: str) -> str:
    for cat, keys in _RULES:
        if any(k in file_name for k in keys):
            return cat
    # 兜底：以编号开头的课程文件（如 "10-ACCA经济法-高山行.docx"、"16、17、18-微观经济学.docx"）
    # 是课程大纲；文件名常不含"大纲"二字。
    if re.match(r"^\d", file_name):
        return "大纲"
    if file_name.lower().endswith((".xlsx", ".xls")):
        return "清单"
    return "其他"


def default_applies_to(file_name: str, category: str, entry_year: str = "") -> list[str]:
    """入库时的**初始**适用年级（之后由管理员在「入库文件与适用年级」页按需指定）。

    - 培养方案 → 其主年级（一份方案通常服务一个年级；一版多年级由管理员改）
    - 其他资料 → ["全部"]（默认年级无关；若某文件是给特定年级制定的，由管理员勾选）
    """
    if category == "培养方案" and entry_year:
        return [entry_year]
    return [ALL_GRADES]


def reclassify_all(db_path: str) -> dict[str, int]:
    """按当前规则重算已登记文件的分类（不改文本，无需重新抽取）。"""
    db = TrainingPlanDB(db_path)
    changed = 0
    try:
        rows = db.conn.execute("SELECT id, file_name, category FROM plan_source_doc").fetchall()
        for r in rows:
            new = classify(r["file_name"])
            if new != r["category"]:
                db.conn.execute("UPDATE plan_source_doc SET category=? WHERE id=?",
                                (new, r["id"]))
                changed += 1
        db.conn.commit()
        stats = db.source_doc_stats()
    finally:
        db.close()
    return {"changed": changed, "by_category": stats}


def _file_md5(path: str) -> str:
    from academicwarning.service import _file_md5 as _aw
    return _aw(path)


def _extract_text(path: str) -> tuple[str, str]:
    """抽取文本。返回 (text, note)。失败不抛出，返回 (\"\", 原因)。"""
    try:
        from knowledge_base.ingestion.parsers import read_file
        text = read_file(path) or ""
        return text, ""
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


def ingest_directory(directory: str, db_path: str, *,
                     with_text: bool = True,
                     enqueue_plans: bool = True) -> dict[str, Any]:
    """把目录下所有可索引文件登记进本功能库；培养方案额外入队结构化解析。"""
    from . import service

    db = TrainingPlanDB(db_path)
    result: dict[str, Any] = {"scanned": 0, "registered": 0, "skipped": 0,
                              "plans_enqueued": 0, "text_failed": [], "by_category": {}}
    try:
        files = sorted(
            os.path.join(directory, n) for n in os.listdir(directory)
            if os.path.isfile(os.path.join(directory, n))
            and not n.startswith(".")
        )
        for path in files:
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".docx", ".doc", ".pdf", ".xlsx", ".xls"):
                continue
            result["scanned"] += 1
            category = classify(name)
            result["by_category"][category] = result["by_category"].get(category, 0) + 1

            major = entry = ""
            if category == "培养方案":
                m, y = service.detect_plan_meta(path)
                major, entry = m or "", y or ""

            note = ""
            text = ""
            if with_text:
                text, err = _extract_text(path)
                if err:
                    note = f"文本抽取失败：{err}"
                    result["text_failed"].append((name, err))

            # 适用年级：培养方案绑定主年级；其他资料若文件名含年级则绑这些年级，
            # 否则视为年级无关（全部）——后续会有一批"给特定年级制定"的文档。
            applies_to = default_applies_to(name, category, entry)

            doc_id = db.upsert_source_doc({
                "category": category, "file_name": name, "file_path": os.path.abspath(path),
                "file_hash": _file_md5(path), "size_bytes": os.path.getsize(path),
                "ext": ext, "major": major, "entry_year": entry,
                "text_chars": len(text), "text_content": text, "note": note,
                "applies_to": applies_to,
            })
            if doc_id is None:
                result["skipped"] += 1
            else:
                result["registered"] += 1

            # 培养方案：额外入队结构化解析（含学分结构/毕业条件/先修图）
            if enqueue_plans and category == "培养方案":
                r = service.submit_upload(path, major=major, entry_year=entry,
                                          uploader="ingest-dir")
                if r.get("status") in ("queued", "done"):
                    result["plans_enqueued"] += 1
    finally:
        db.close()
    return result
