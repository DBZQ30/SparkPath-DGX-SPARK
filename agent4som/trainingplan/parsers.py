"""培养方案解析层。

复用 `academicwarning.parsers.parse_plan`（课程总表 + 分学期推荐课表），
新增：
- 学分结构（Table 0）
- 毕业/授学位硬条件（§五 + Table 0 总计）
- 先修关系图抽取（docx media → PNG；EMF 经 soffice 转换）
- 先修关系 VL 解析（qwen3-vl → nodes/edges）

设计 §3：不臆造数字；无法解析的项进"未识别"清单，由上层降级。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Optional

from .models import CreditNode, GraduationReq

# ─────────────────────────── 通用清洗 / 归一 ───────────────────────────

_ROMAN = {"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V",
          "Ⅵ": "VI", "Ⅶ": "VII", "Ⅷ": "VIII", "Ⅸ": "IX", "Ⅹ": "X"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def normalize_course_name(name: str) -> str:
    """课程名归一化：全角→半角、罗马数字→拉丁、去符号/空白。用于 VL 节点与课程表匹配。"""
    s = name or ""
    # 全角字母数字/括号 → 半角
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    s = "".join(out)
    for r, l in _ROMAN.items():
        s = s.replace(r, l)
    s = re.sub(r"[◆▲△◇*\s（）()\-—·、,，.。/]", "", s)
    return s.lower()


# ─────────────────────────── 课程表 / 推荐课表（复用） ───────────────────────────

def display_course_name(name: str) -> str:
    """展示用课程名清洗（不改动 academicwarning 的解析口径）。

    `parse_plan` 对推荐课表的合并单元格会产出粘连名（"体育-1体育-3"），
    这里仅做展示层拆分：检测重复的模式（如 X-1X-3）→ "X-1/X-3"。
    """
    s = (name or "").strip()
    m = re.match(r"^(.*?[-－]\d)(.*?[-－]\d)$", s)
    if m and m.group(1) and m.group(2) and m.group(1)[0] == m.group(2)[0]:
        return f"{m.group(1)}/{m.group(2)}"
    return s


def display_credit(text: str) -> str:
    """展示用学分清洗："148+（8）" → "148+8"。"""
    s = str(text or "")
    s = re.sub(r"[（(]\s*(\d+)\s*[）)]", r"+\1", s)
    return s.replace("++", "+")


def parse_plan_data(path: str):
    """复用 academicwarning 的 parse_plan，返回 (meta, courses, sem_courses, bad_sems)。"""
    from academicwarning.parsers import parse_plan
    return parse_plan(path)


# ─────────────────────────── 学分结构（Table 0） ───────────────────────────

_CREDIT_RE = re.compile(r"^[（(]?[\d.]+(?:[+＋][（(]?[\d.]+[）)]?)?[）)]?$")


def _is_credit(v: str) -> bool:
    return bool(_CREDIT_RE.match(_clean(v)))


def _parse_credit_value(v: str) -> Optional[float]:
    """从 '37' / '148+（8）' / '（8）' 取主数字。"""
    m = re.search(r"[\d.]+", _clean(v))
    return float(m.group()) if m else None


def _credit_rows(t0) -> list[tuple[list[str], str, str]]:
    """Table 0 行归一：(去重标签, 小计, 占比)；空行/表头行/无小计行剔除。"""
    rows: list[tuple[list[str], str, str]] = []
    for row in t0.rows:
        cells = [_clean(c.text) for c in row.cells]   # 合并单元格重复文本 → 标签去重
        if not any(cells):
            continue
        labels = []
        for c in cells[:4]:
            if c and (not labels or labels[-1] != c):
                labels.append(c)
        if not labels or labels[0] in ("课程类别",):
            continue
        credit, pct = cells[-2], cells[-1]
        if not credit:
            continue
        rows.append((labels, credit, pct))
    return rows


def _group_credit_rows(rows: list[tuple[list[str], str, str]])         -> list[tuple[list[str], str, str, list[list[str]]]]:
    """分组：连续相同 小计（且同首标签）→ 一组；组内取标签公共前缀。"""
    groups: list[tuple[list[str], str, str, list[list[str]]]] = []
    i = 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1][1] == rows[i][1] \
                and rows[j + 1][0][0] == rows[i][0][0]:
            j += 1
        group = rows[i:j + 1]
        prefix = list(group[0][0])
        for lab, _, _ in group[1:]:
            k = 0
            while k < len(prefix) and k < len(lab) and prefix[k] == lab[k]:
                k += 1
            prefix = prefix[:k]
        groups.append((prefix, group[0][1], group[0][2], [g[0] for g in group]))
        i = j + 1
    return groups


def _nodes_from_groups(groups) -> tuple[list[CreditNode], dict[str, Any]]:
    """分组 → 结构节点（category 标注顶层归类）+ 顶层数字
    （total/practice/extra_practice；course_teaching 由调用方补齐）。"""
    nodes: list[CreditNode] = []
    sort = 0

    def add(parent: str, name: str, credit: str, category: str, note: str = ""):
        nonlocal sort
        p = f"{parent}/{name}" if parent else name
        nodes.append(CreditNode(plan_id=0, parent_path=parent, path=p, name=name,
                                credit=credit, credit_note=note, category=category,
                                sort=sort))
        sort += 1

    top: dict[str, Any] = {}
    for prefix, credit, pct, group_labels in groups:
        cat = prefix[0]
        cat_norm = re.sub(r"[（(].*$", "", cat)
        if cat_norm == "毕业要求":
            top["total"] = credit
            add("", "毕业总学分", credit, "毕业要求")
        elif cat_norm == "课程教学" and len(prefix) >= 2:
            add("课程教学", prefix[-1], credit, "课程教学", note=f"占比{pct}" if pct else "")
        elif cat_norm == "课程教学" and len(prefix) == 1:
            # 合并小计（如"专业大类基础 + 专业课程"）——按组内二级标签命名
            names = list(dict.fromkeys(l[1] for l in group_labels if len(l) >= 2))
            add("课程教学", " + ".join(names) or "课程教学子项", credit, "课程教学",
                note=f"占比{pct}" if pct else "")
        elif cat_norm == "集中实践":
            add("", "集中实践", credit, "集中实践", note=f"占比{pct}" if pct else "")
            top["practice"] = _parse_credit_value(credit)
        elif cat_norm == "课外实践":
            add("", "课外实践", credit, "课外实践")
            top["extra_practice"] = _parse_credit_value(credit)
    return nodes, top


def parse_credit_structure(path: str) -> tuple[list[CreditNode], dict[str, Any]]:
    """解析 Table 0（学分结构）。

    返回 (nodes, top)：
      nodes — 结构节点（含顶层与 Table 0 分组），category 标注顶层归类
      top   — 可靠推导的顶层数字 {total, course_teaching, practice, extra_practice, pct...}
    """
    from docx import Document

    doc = Document(path)
    t0 = doc.tables[0]
    groups = _group_credit_rows(_credit_rows(t0))
    nodes, top = _nodes_from_groups(groups)

    total = _parse_credit_value(str(top.get("total", "")))
    practice = top.get("practice")
    if total is not None and practice is not None:
        top["course_teaching"] = round(total - practice, 2)
        # 顶层节点补齐（若 Table 0 未给出课程教学分组小计）
        if not any(n.name == "课程教学" for n in nodes):
            nodes.append(CreditNode(
                plan_id=0, parent_path="", path="课程教学", name="课程教学",
                credit=f"{top['course_teaching']:g}", credit_note="",
                category="课程教学", sort=len(nodes)))
    return nodes, top


# ─────────────────────────── 毕业/授学位硬条件（§五） ───────────────────────────

def parse_graduation_reqs(path: str, top: dict[str, Any] | None = None) -> list[GraduationReq]:
    from docx import Document

    doc = Document(path)
    text = "\n".join(p.text for p in doc.paragraphs)
    reqs: list[GraduationReq] = []
    sort = 0

    def add(req_type: str, item: str, value: str = "", unit: str = "", detail: str = ""):
        nonlocal sort
        reqs.append(GraduationReq(plan_id=0, req_type=req_type, item=item, value=value,
                                  unit=unit, detail=detail, sort=sort))
        sort += 1

    m = re.search(r"学制[：:]\s*([\d.]+)\s*年", text)
    if m:
        add("其他", "学制", m.group(1), "年")

    m = re.search(r"授予学位[：:]\s*([^\n，。；]+?学位)", text)
    if m:
        add("学位", "授予学位", m.group(1).strip())

    total = None
    m = re.search(r"规定的([\d.]+)\s*学分", text)
    if m:
        total = m.group(1)
    if not total and top and top.get("total"):
        mm = re.search(r"[\d.]+", str(top["total"]))
        total = mm.group() if mm else None
    if total:
        add("学分", "毕业总学分", total, "学分")

    m = re.search(r"课外实践(\d+)\s*学分", text)
    if m:
        add("学分", "课外实践", m.group(1), "学分")
    elif top and top.get("extra_practice") is not None:
        add("学分", "课外实践", f"{top['extra_practice']:g}", "学分")

    m = re.search(r"创新创业[”\"']?类?课程不少于(\d+)\s*学分", text)
    if m:
        add("其他", "创新创业类课程", f"≥{m.group(1)}", "学分")
    m = re.search(r"美育课程不少于(\d+)\s*学分", text)
    if m:
        add("其他", "美育课程", f"≥{m.group(1)}", "学分")
    m = re.search(r"劳动教育不少于(\d+)\s*学时", text)
    if m:
        add("其他", "劳动教育", f"≥{m.group(1)}", "学时")

    return reqs


# ─────────────────────────── 先修关系图抽取 ───────────────────────────

def _explicit(value):
    if isinstance(value, int):
        return False
    return str(value).lower() in ("false", "0", "no")

# 先修图识别：只有"先修关系图"才是我们要的图（会计学第一张是"专业核心课程拓扑图"、
# 工业工程 EMF 为流程图，都不是先修关系图）。2026-09-23 修正。
_SKIP_PARENT_KEYWORDS = ("拓扑", "流程", "课程体系", "培养路径", "路径图")
_SKIP_CHILD_KEYWORDS = ("拓扑", "流程")


def _prereq_is_course_node(name: str, course_names: set) -> bool:
    n = normalize_course_name(name)
    if not n:
        return False
    for c in course_names:
        cn = normalize_course_name(c)
        if not cn:
            continue
        if cn in n or n in cn:
            return True
    return False


def _rid_media_map(z) -> dict[str, str]:
    """rels 里 image 关系的 rId → zip 内路径映射（rels 缺失 → 空）。"""
    from xml.etree import ElementTree as ET
    try:
        rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
    except KeyError:
        return {}
    rid_to_media: dict[str, str] = {}
    for rel in rels:
        if rel.get("Type", "").endswith("/image"):
            rid_to_media[rel.get("Id")] = rel.get("Target", "").replace("media/", "word/media/")
    return rid_to_media


def _iter_media_in_section(root, ns: dict, rid_to_media: dict[str, str]) -> list[dict[str, str]]:
    """按文档顺序抽取「专业课程先修关系图」章节内出现的图片。

    章节从正文出现「专业课程先修关系图」起，到下一个中文序号章节标题
    （不含"先修"字样）止；内嵌 `<a:blip>` 与 VML `<v:imagedata>` 都认
    （工业工程的先修图是 Visio/OLE，只认 blip 会漏，2026-09-23 修正）。"""
    import re as _re
    body = root.find("w:body", ns)
    in_section = False
    out: list[dict[str, str]] = []
    for el in body.iter():
        tag = el.tag.split("}")[-1]
        if tag == "p":
            text = "".join(t.text or "" for t in el.iter("{%s}t" % ns["w"]))
            if "专业课程先修关系图" in text:
                in_section = True
                continue
            if in_section and _re.match(r"^[一二三四五六七八九十]+、", text.strip()) and "先修" not in text:
                in_section = False
        if not in_section:
            continue
        # 普通内嵌图片：<a:blip r:embed="rIdX"/>
        if tag == "blip":
            rid = el.get("{%s}embed" % ns["r"]) or el.get("{%s}link" % ns["r"])
            media = rid_to_media.get(rid or "")
            if media:
                out.append({"media": media, "rId": rid})
        # Visio/OLE 等对象：<w:object><v:imagedata r:id="rIdX"/>（VML，非 blip）
        elif tag == "imagedata":
            rid = el.get("{%s}id" % ns["r"])
            media = rid_to_media.get(rid or "")
            if media:
                out.append({"media": media, "rId": rid})
    return out


def extract_prereq_candidates(path: str) -> list[dict[str, str]]:
    """抽取"第八章 专业课程先修关系图"下的图片。

    依据 docx 正文中「八、专业课程先修关系图」到「一、学分认定…」之间的
    `<w:drawing>` 出现的 rId 顺序，只保留该章节内的图片；并排除标题含
    "拓扑/流程"的图。返回 [{media, rId}]。
    """
    import zipfile
    from xml.etree import ElementTree as ET

    ns = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    }
    with zipfile.ZipFile(path) as z:
        doc_xml = z.read("word/document.xml")
        rid_to_media = _rid_media_map(z)
    root = ET.fromstring(doc_xml)
    return _iter_media_in_section(root, ns, rid_to_media)


def materialize_prereq_image(docx_path: str, media: str, out_dir: str, doc_id: int) -> str:
    """把先修图另存为 PNG 静态资源（服务端托管），返回路径。EMF 走 soffice 转换。"""
    import zipfile

    dest_dir = os.path.join(out_dir, str(doc_id))
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(docx_path))[0]
    ext = os.path.splitext(media)[1].lower()
    raw = os.path.join(dest_dir, f"{base}_prereq{ext}")
    with zipfile.ZipFile(docx_path) as z, open(raw, "wb") as fh:
        fh.write(z.read(media))
    if ext in (".png", ".jpg", ".jpeg"):
        return raw
    return _convert_emf_to_png(raw, dest_dir) or raw


def _convert_emf_to_png(emf_path: str, out_dir: str) -> Optional[str]:
    try:
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "png", "--outdir", out_dir, emf_path],
            check=True, capture_output=True, timeout=180,
        )
        png = os.path.splitext(emf_path)[0] + ".png"
        return png if os.path.exists(png) else None
    except Exception:
        return None


# ─────────────────────────── 先修关系 VL 解析 ───────────────────────────

_PREREQ_PROMPT = (
    "这是一张高校课程先修关系图。横轴是学期泳道（1上/1下/2上/2下/3上/3下），"
    "箭头 A→B 表示 A 是先修课、B 是后续课。请只输出 JSON，格式："
    '{"nodes":[{"name":"课程名","semester":"1上"}],'
    '"edges":[{"from":"课程名","to":"课程名"}]}。'
    "要求：列出图中所有课程节点（含没有连线的），以及所有箭头。"
    "不要输出解释，不要用 markdown 代码块。"
)

# VL 输出上限：会计学先修图节点多（含 ACCA 英文名），3000 会被截断 → 边全丢。
# 2026-09-23 修正：提高到 8000。
_PREREQ_MAX_TOKENS = int(os.environ.get("TRAINING_PLAN_VL_MAX_TOKENS", "8000"))


def _vl_chat(image_path: str, prompt: str) -> str:
    """VL 调用：复用 knowledge_base 的 `parse_with_vl`（已含大图缩放、超时、错误兜底）。

    2026-09-23 修正：不再自建 HTTP 调用（重复造轮子，且缺失大图缩放会导致 GPU OOM）。
    """
    from knowledge_base.ingestion.parsers import parse_with_vl
    return parse_with_vl(image_path, prompt=prompt)


def _extract_json(text: str) -> Optional[dict]:
    s = text.strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.MULTILINE).strip()
    try:
        return json.loads(s)
    except Exception:
        # 截断兜底：从最后一个完整对象处补括号
        m = re.search(r"\{.*\}", s, re.DOTALL)
        cand = m.group() if m else s
        for fix in (cand, cand.rstrip().rstrip(",") + "]}", cand.rstrip().rstrip(",") + "}]}"):
            try:
                return json.loads(fix)
            except Exception:
                continue
        return None


def parse_prereq_vl(image_path: str, course_names: Optional[list[str]] = None) -> dict[str, Any]:
    """VL 解析先修图 → {nodes, edges, unmatched, is_prereq_graph}。

    nodes/edges 的 name 做归一化匹配（匹配到课程表则回填标准课程名）。
    is_prereq_graph：节点多为课程名才认作先修图（排除拓扑/流程图误识别）。
    """
    raw = _vl_chat(image_path, _PREREQ_PROMPT)
    data = _extract_json(raw) or {"nodes": [], "edges": []}
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []

    # 课程名归一化索引
    index: dict[str, str] = {}
    for n in (course_names or []):
        index.setdefault(normalize_course_name(n), n)

    def match(name: str) -> tuple[str, bool]:
        key = normalize_course_name(name)
        if key in index:
            return index[key], True
        # 包含匹配（VL 可能多/少字）
        for k, v in index.items():
            if k and (k in key or key in k):
                return v, True
        return name, False

    out_nodes, unmatched = [], []
    for nd in nodes:
        name = str(nd.get("name", "")).strip()
        if not name:
            continue
        std, ok = match(name)
        out_nodes.append({"name": std, "raw_name": name, "semester": nd.get("semester", ""), "matched": ok})
        if not ok:
            unmatched.append(name)

    out_edges = []
    for e in edges:
        frm, to = str(e.get("from", "")).strip(), str(e.get("to", "")).strip()
        if not frm or not to:
            continue
        sf, okf = match(frm)
        st, okt = match(to)
        out_edges.append({"from": sf, "to": st, "raw_from": frm, "raw_to": to,
                          "matched": bool(okf and okt)})
        if not okf:
            unmatched.append(frm)
        if not okt:
            unmatched.append(to)

    # 先修图判定：节点中命中课程表的比例
    matched_nodes = sum(1 for n in out_nodes if n["matched"])
    is_prereq = bool(out_nodes) and (matched_nodes / len(out_nodes)) >= 0.5 and len(out_edges) > 0

    return {"nodes": out_nodes, "edges": out_edges, "unmatched": sorted(set(unmatched)),
            "is_prereq_graph": is_prereq, "matched_ratio": (matched_nodes / len(out_nodes)) if out_nodes else 0.0,
            "raw": raw[:2000]}


# ─────────────────────── 培养模式规则（四路径，设计 005 §4） ───────────────────────
#
# 来源：培养方案正文「以上为常规型培养课程清单，此外科学研究型和交叉融合型培养要求为…」
# 之后的段落（①科学研究型-本学科研究生进阶课程清单 / ②交叉融合型-跨选课程专业范围要求 /
# ③创新创业型培养模式），以及「（六）课程要求」里的每学期学分上限。
# 逐专业不同（如学期上限：工商/大数据=25，工业工程=27）。

_MODE_CREDIT_RE = re.compile(r"修读\s*([\d.]+)\s*学分")
_MODE_INNO_CREDIT_RE = re.compile(r"(?:可)?替换集中实践学分\s*([\d.]+)")
_MODE_INNO_FALLBACK_RE = re.compile(r"创新创业成果可替换集中实践学分不少于\s*([\d.]+)\s*学分")
_CREDIT_LIMIT_RE = re.compile(r"每学期修读课程原则上不超过\s*([\d.]+)\s*学分")
_CREDIT_LIMIT_NOTE_RE = re.compile(
    r"前一学期学分绩高于\s*([\d.]+)\s*的学生可适当超出\s*([\d.]+)\s*学分")
_CROSS_MAJOR_RE = re.compile(r"^(.+?)专业(?:的)?(?:专业)?核心课和专业选修课$")
_CROSS_STOP_RE = re.compile(r"^[①②③]|^（[一二三四五六七八九十]+）|集中实践")


def _docx_lines(path: str) -> list[str]:
    """培养方案正文段落（去空行）——培养模式段在正文段落中（非表格）。"""
    from docx import Document
    doc = Document(path)
    return [p.text.strip() for p in doc.paragraphs if p.text.strip()]


def _parse_mode_course_line(s: str) -> Optional[dict[str, Any]]:
    """培养模式课程行：`082038\\t管理研究方法论I\\t2学分` / `082002 高级统计分析 2学分`。"""
    s = (s or "").strip()
    if not s or "学分" not in s:
        return None
    m = re.match(r"^([A-Za-z0-9]{4,})[\s\t]+(.+?)[\s\t]*([\d.]+)\s*学分$", s)
    if m:
        return {"course_code": m.group(1), "course_name": m.group(2).strip(),
                "credit": float(m.group(3))}
    m = re.match(r"^(.+?)[\s\t]+([\d.]+)\s*学分$", s)
    if m and "专业" not in m.group(1):
        return {"course_code": "", "course_name": m.group(1).strip(),
                "credit": float(m.group(2))}
    return None


def _mode_credit(m: Optional[re.Match]) -> Optional[float]:
    """模式行学分（修读 X 学分 / 替换集中实践学分 X）→ float；无匹配 None。"""
    return float(m.group(1)) if m else None


def _mode_research_courses(lines: list[str], i: int) -> list[dict[str, Any]]:
    """科学研究型段：自 i+1 向后扫描到 ①②③/下一模式段前的课程行。"""
    courses = []
    for j in range(i + 1, len(lines)):
        s = lines[j]
        if s.startswith("①") or s.startswith("②") or s.startswith("③") \
                or "交叉融合型" in s or "创新创业型" in s:
            break
        c = _parse_mode_course_line(s)
        if c:
            courses.append(c)
    return courses


def _mode_cross_majors(lines: list[str], i: int) -> list[str]:
    """交叉融合型段：自 i+1 向后扫描可跨选专业（"<专业>专业核心课和专业选修课"）。"""
    majors = []
    for j in range(i + 1, len(lines)):
        s = lines[j]
        if s.startswith("②") or s.startswith("③") or "创新创业型" in s \
                or _CROSS_STOP_RE.search(s):
            break
        m = _CROSS_MAJOR_RE.match(s)
        if m:
            majors.append(m.group(1).strip())
    return majors


def _credit_limit_from(line: str) -> Optional[dict[str, Any]]:
    """每学期修读学分上限 + 例外注（前一学期学分绩高于 X 的学生可适当超出 Y 学分）。"""
    m = _CREDIT_LIMIT_RE.search(line)
    if not m:
        return None
    note = ""
    mn = _CREDIT_LIMIT_NOTE_RE.search(line)
    if mn:
        note = f"前一学期学分绩高于{mn.group(1)}可适当超出{mn.group(2)}学分"
    return {"value": float(m.group(1)), "note": note}


def _inno_fallback(lines: list[str]) -> Optional[dict[str, Any]]:
    """创新创业型兜底：③ 段未抽到 → 用附件5正文（"不少于6学分（除毕业设计和军训外）"）。"""
    for line in lines:
        m = _MODE_INNO_FALLBACK_RE.search(line)
        if m:
            return {"credit": float(m.group(1)), "detail": line}
    return None


def parse_mode_rules(path: str) -> dict[str, Any]:
    """解析四路径培养模式规则。

    返回：
      {
        "modes": {
          "科学研究型": {"credit": 6.0, "courses": [{course_code,course_name,credit}], "detail": ...},
          "交叉融合型": {"credit": 6.0, "majors": [...], "detail": ...},
          "创新创业型": {"credit": 6.0, "detail": ...},
        },
        "credit_limit": {"value": 25.0, "note": "前一学期学分绩高于90可适当超出2学分"} | None,
        "apply_node": "大三第二学期",
      }
    无法解析的项不臆造（缺失即不出现）。
    """
    lines = _docx_lines(path)
    out: dict[str, Any] = {"modes": {}, "credit_limit": None, "apply_node": ""}

    for i, line in enumerate(lines):
        if "科学研究型" in line and "研究生进阶" in line:
            out["modes"]["科学研究型"] = {
                "credit": _mode_credit(_MODE_CREDIT_RE.search(line)),
                "courses": _mode_research_courses(lines, i), "detail": line}
        elif "交叉融合型" in line and "跨选课程专业范围" in line:
            out["modes"]["交叉融合型"] = {
                "credit": _mode_credit(_MODE_CREDIT_RE.search(line)),
                "majors": _mode_cross_majors(lines, i), "detail": line}
        elif "创新创业型培养模式" in line and "替换集中实践" in line:
            out["modes"]["创新创业型"] = {
                "credit": _mode_credit(_MODE_INNO_CREDIT_RE.search(line)),
                "detail": line}
        if "每学期修读" in line and "不超过" in line:
            limit = _credit_limit_from(line)
            if limit:
                out["credit_limit"] = limit

    if "创新创业型" not in out["modes"]:
        fb = _inno_fallback(lines)
        if fb:
            out["modes"]["创新创业型"] = fb

    # 申请节点：三类培养模式均需在大三第二学期提交书面申请（三类培养模式实施细则）
    out["apply_node"] = "大三第二学期"
    return out


def _note_columns(hdr: list[str]) -> tuple[int | None, int | None, int | None]:
    """课程总表「备注」列定位：返回 (code_i, note_i, name_i)。

    表头不含课程编码或备注列时返回 (None, None, None)；中文课程名称列为
    可选（无编码行的 name: 兜底键依赖它）。"""
    if not any("课程编码" in h for h in hdr) or not any("备注" in h for h in hdr):
        return None, None, None
    code_i = next((i for i, h in enumerate(hdr) if "课程编码" in h), None)
    note_i = next((i for i, h in enumerate(hdr) if "备注" in h), None)
    name_i = next((i for i, h in enumerate(hdr) if h == "中文课程名称"), None)
    return code_i, note_i, name_i


def _collect_row_notes(table, code_i: int, note_i: int, name_i: int | None,
                       notes: dict[str, str]) -> None:
    """逐行抽取备注写入 notes（编码键 + name:<课程名> 兜底键，空备注跳过）。"""
    for row in table.rows[1:]:
        cells = [c.text.strip() for c in row.cells]
        if note_i >= len(cells):
            continue
        note = _clean(cells[note_i])
        if not note:
            continue
        code = _clean(cells[code_i]) if code_i < len(cells) else ""
        name = _clean(cells[name_i]) if (name_i is not None and name_i < len(cells)) else ""
        if code:
            notes[code] = note
        if name:
            notes.setdefault(f"name:{name}", note)


def parse_course_notes(path: str) -> dict[str, str]:
    """抽取课程总表「课程信息备注」列（研究生进阶 / 前沿交叉 / 创新创业 / 教学改革 / 双语…）。

    返回 {course_code: note}（另附 {"name:<课程名>": note} 供无编码行兜底）。
    """
    from docx import Document

    doc = Document(path)
    notes: dict[str, str] = {}
    for t in doc.tables:
        if not t.rows:
            continue
        hdr = [_clean(c.text) for c in t.rows[0].cells]
        code_i, note_i, name_i = _note_columns(hdr)
        if code_i is None or note_i is None:
            continue
        _collect_row_notes(t, code_i, note_i, name_i, notes)
    return notes
