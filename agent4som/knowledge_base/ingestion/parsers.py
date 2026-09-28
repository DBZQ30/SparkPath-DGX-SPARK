"""File parsing layer for the ingestion pipeline.

Handles MinerU, VL model, pdfminer, python-docx, python-pptx,
and legacy format (.doc) extraction.
"""

from __future__ import annotations

import logging
import os
import time

import requests
import contextlib

logger = logging.getLogger(__name__)

# Minimum Chinese character count for a MinerU result to be considered valid.
_MINERU_MIN_CHINESE_CHARS: int = 10

def _is_mineru_quality_result(md: str) -> bool:
    """Return True if *md* passes the MinerU quality gate.

    Rejects results that match known failure patterns:
    - Pure image reference: ``![](images/xxx.jpg)``
    - Whitespace only

    Also requires at least ``_MINERU_MIN_CHINESE_CHARS`` Chinese characters.
    """
    import re as _re
    if not md or not md.strip():
        return False
    # Known failure patterns
    if _re.fullmatch(r"!\[.*\]\(images/[^)]+\)\s*", md.strip()):
        logger.info("MinerU quality gate: pure image reference, rejecting")
        return False
    if _re.fullmatch(r"\s*", md.strip()):
        logger.info("MinerU quality gate: whitespace only, rejecting")
        return False
    chinese_chars = len(_re.findall(r"[一-鿿]", md))
    if chinese_chars < _MINERU_MIN_CHINESE_CHARS:
        logger.info(
            "MinerU quality gate: %d Chinese chars < %d threshold",
            chinese_chars, _MINERU_MIN_CHINESE_CHARS,
        )
        return False
    return True

# MinerU PDF/image parsing config
_MINERU_URL = os.environ.get("MINERU_URL", "")
_MINERU_KEY = os.environ.get("MINERU_API_KEY", os.environ.get("QWEN_API_KEY", ""))
_MINERU_POLL_INTERVAL = int(os.environ.get("MINERU_POLL_INTERVAL", "2"))
_MINERU_POLL_TIMEOUT = int(os.environ.get("MINERU_POLL_TIMEOUT", "300"))
_MINERU_RETRIES = int(os.environ.get("MINERU_RETRIES", "10"))
_MINERU_RETRY_MAX_BACKOFF = int(os.environ.get("MINERU_RETRY_MAX_BACKOFF", "120"))
_MINERU_CONNECT_RETRIES = int(os.environ.get("MINERU_CONNECT_RETRIES", "3"))
_GPU_RETRY_BACKOFF = int(os.environ.get("GPU_RETRY_BACKOFF", "5"))

# VL model fallback for images that MinerU cannot parse
_VL_URL = os.environ.get("VL_MODEL_URL", "")
_VL_MODEL = os.environ.get("VL_MODEL_NAME", "")
_VL_KEY = os.environ.get("VL_API_KEY", os.environ.get("QWEN_API_KEY", ""))
_VL_MAX_TOKENS = int(os.environ.get("VL_MAX_TOKENS", "1024"))
# qwen3 系默认走推理链（thinking）。OCR 场景只需要直读文字，推理链纯属浪费：
# 实测 27B 在 max_tokens 较小时推理链吃光预算 → content=None → 被误判为 OCR 失败，
# 触发上层多策略重试（单份成绩单最多 9×学生数 次调用），是解析慢的主因之一。
_VL_ENABLE_THINKING = os.environ.get("VL_ENABLE_THINKING", "0").strip().lower() in (
    "1", "true", "yes", "on")

_VL_PROMPT = (
    "请用中文详细描述这张图片中的文字内容、表格、图表和关键信息。"
    "如果是文档扫描件，请提取所有可读的文字。"
    "如果是表格，请用Markdown表格格式输出。"
    "如果是图表，请描述图表展示的趋势和数据。"
)

# PDF-to-image VL fallback: max pages to process (0 = all pages)
_PDF_VL_MAX_PAGES = int(os.environ.get("PDF_VL_MAX_PAGES", "10"))

def _parse_pdf_with_vl(path: str) -> str:
    """Parse a scanned PDF by converting pages to images and sending to VL.

    Used as a fallback when MinerU is unavailable and pdfminer returns
    empty text (typical for image-based / scanned PDFs).  Limited to
    ``_PDF_VL_MAX_PAGES`` pages to bound latency.

    Returns empty string on any error so callers can fall further back.
    """
    try:
        from pdf2image import convert_from_path
    except ImportError:
        logger.warning("pdf2image not installed, cannot use VL for PDF fallback")
        return ""

    try:
        images = convert_from_path(
            path, dpi=200,
            first_page=1,
            last_page=_PDF_VL_MAX_PAGES or None,
        )
    except Exception as exc:
        logger.warning("pdf2image failed for %s: %s", path, exc)
        return ""

    if not images:
        return ""

    parts: list[str] = []
    for i, img in enumerate(images, 1):
        # Save page to a temp file and reuse _parse_with_vl
        import tempfile as _tf
        with _tf.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            tmp_path = tmp.name
        try:
            page_text = _parse_with_vl(tmp_path)
            if page_text and len(page_text.strip()) > 10:
                parts.append(f"## 第{i}页\n\n{page_text}")
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

    if parts:
        logger.info(
            "VL PDF fallback: %d/%d pages extracted for %s",
            len(parts), len(images), os.path.basename(path),
        )
        return "\n\n".join(parts)
    return ""

def parse_with_vl(path: str, prompt: str | None = None) -> str:
    """公开别名：academicwarning 包复用 VL 图片理解（L3 约束，勿删）。
    prompt 可覆盖默认通用描述提示词（如成绩单学号提取需专用提示词）。"""
    return _parse_with_vl(path, prompt=prompt)


def _parse_with_vl(path: str, prompt: str | None = None) -> str:
    """Parse an image with the VL (Vision-Language) model.

    Used as a fallback when MinerU cannot handle an image (e.g. complex
    charts, posters, handwritten notes).  Returns empty string on any
    error so callers can fall further back.

    Images > 5MB are resized before sending to avoid GPU OOM on the
    VL server.
    """
    if not _VL_URL or not _VL_MODEL:
        return ""

    try:
        import base64
        from io import BytesIO
        from PIL import Image

        ext = os.path.splitext(path)[1].lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".bmp": "image/bmp",
                    ".tiff": "image/tiff", ".tif": "image/tiff"}
        mime = mime_map.get(ext, "image/jpeg")

        img = Image.open(path)
        file_size = os.path.getsize(path)

        # Resize large images to avoid VL server OOM (>5MB or 4096px)
        max_dim = 4096
        if file_size > 5 * 1024 * 1024 or max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
            logger.info("VL: resized %s from %dx%d (%.1fMB) for VL model",
                        os.path.basename(path),
                        *img.size, file_size / 1024 / 1024)

        buf = BytesIO()
        img_fmt = "JPEG" if ext in {".jpg", ".jpeg"} else "PNG"
        img.save(buf, format=img_fmt, quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        # Token budget check: base64 length / 3 ≈ token count.
        # VL model context is 4096 tokens; keep image under ~2700 tokens
        # to leave room for prompt + response.
        _MAX_B64_BYTES = 200_000  # ~2667 tokens
        if len(b64) > _MAX_B64_BYTES:
            logger.info(
                "VL: base64 too large (%d bytes), reducing quality for %s",
                len(b64), os.path.basename(path),
            )
            # Step 1: lower JPEG quality
            buf2 = BytesIO()
            img.save(buf2, format=img_fmt, quality=50)
            b64 = base64.b64encode(buf2.getvalue()).decode("utf-8")

        if len(b64) > _MAX_B64_BYTES:
            # Step 2: further reduce dimensions
            logger.info(
                "VL: still too large (%d bytes), downscaling for %s",
                len(b64), os.path.basename(path),
            )
            img.thumbnail((2048, 2048), Image.LANCZOS)
            buf3 = BytesIO()
            img.save(buf3, format=img_fmt, quality=50)
            b64 = base64.b64encode(buf3.getvalue()).decode("utf-8")
            logger.info(
                "VL: final size %d bytes (%dx%d) for %s",
                len(b64), *img.size, os.path.basename(path),
            )

        img.close()

        payload = {
            "model": _VL_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": prompt or _VL_PROMPT},
                ],
            }],
            "max_tokens": _VL_MAX_TOKENS,
        }
        if not _VL_ENABLE_THINKING:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        retries = _MINERU_CONNECT_RETRIES
        for attempt in range(retries):
            try:
                resp = requests.post(
                    f"{_VL_URL}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {_VL_KEY}"},
                    json=payload,
                    timeout=120,
                )
                if resp.status_code >= 500:
                    wait = _GPU_RETRY_BACKOFF
                    logger.info("VL server error (5xx), retrying in %ds (attempt %d/%d)", wait, attempt + 1, retries)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                if content and len(content.strip()) > 10:
                    return content
                logger.warning("VL model returned no usable content for %s", path)
                return ""
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt < retries - 1:
                    wait = _GPU_RETRY_BACKOFF
                    logger.info("VL unreachable (%s), retrying in %ds (attempt %d/%d)", e, wait, attempt + 1, retries)
                    time.sleep(wait)
                else:
                    raise
        return ""
    except Exception as exc:
        logger.warning("VL parsing failed for %s: %s", path, exc)
        return ""

def _mineru_submit(path: str, base: str, auth: dict, submit_timeout: int) -> dict | None:
    """提交解析任务（409 冲突/连接错误指数退避重试）。失败返回 ``None``。"""
    data = None
    retries = _MINERU_RETRIES
    max_backoff = _MINERU_RETRY_MAX_BACKOFF
    connect_retries = _MINERU_CONNECT_RETRIES
    for attempt in range(retries):
        try:
            with open(path, "rb") as fh:
                resp = requests.post(
                    f"{base}/file_parse",
                    headers=auth,
                    files={"files": (os.path.basename(path), fh)},
                    timeout=submit_timeout,
                )
            if resp.status_code == 409:
                wait = min(2 ** attempt, max_backoff)
                logger.info("MinerU busy (409), retrying in %ds (attempt %d/%d)", wait, attempt + 1, retries)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < connect_retries:
                wait = _GPU_RETRY_BACKOFF
                logger.info("MinerU unreachable (%s), retrying in %ds (attempt %d/%d)", e, wait, attempt + 1, connect_retries)
                time.sleep(wait)
            else:
                raise
    else:
        logger.warning("MinerU still busy after %d retries for %s", retries, path)
        return None
    return data


def _extract_mineru_markdown(results: dict, path: str, api_tag: str) -> str:
    """从 MinerU results 提取首个通过质量门的 ``md_content``；空串 = 无可用内容。"""
    for file_result in results.values():
        md = file_result.get("md_content", "")
        if md and len(md.strip()) > 20:
            if _is_mineru_quality_result(md):
                return md
            logger.info("MinerU (%s) quality gate rejected for %s, falling back", api_tag, path)
    logger.warning("MinerU (%s) returned no usable content for %s", api_tag, path)
    return ""


def _mineru_poll_until_done(task_id: str, base: str, auth: dict) -> bool:
    """轮询任务状态直到 completed；``False`` = failed / 超时。"""
    deadline = time.time() + _MINERU_POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(_MINERU_POLL_INTERVAL)
        for poll_attempt in range(3):
            try:
                status_resp = requests.get(
                    f"{base}/tasks/{task_id}",
                    headers=auth,
                    timeout=10,
                )
                status_resp.raise_for_status()
                break
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
                if poll_attempt == 2:
                    raise
                time.sleep(2 ** poll_attempt)
        status_data = status_resp.json()
        if status_data.get("status") == "completed":
            return True
        if status_data.get("status") == "failed":
            logger.warning("MinerU task %s failed: %s", task_id, status_data.get("error", "unknown"))
            return False
    logger.warning("MinerU task %s timed out after %ds", task_id, _MINERU_POLL_TIMEOUT)
    return False


def _mineru_fetch_result(task_id: str, base: str, auth: dict) -> dict:
    """拉取已完成任务的解析结果（瞬时网络错误重试 3 次）。"""
    result_resp = None
    for fetch_attempt in range(3):
        try:
            result_resp = requests.get(
                f"{base}/tasks/{task_id}/result",
                headers=auth,
                timeout=30,
            )
            result_resp.raise_for_status()
            break
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
            if fetch_attempt == 2:
                raise
            time.sleep(2 ** fetch_attempt)
    return result_resp.json()


def _parse_with_mineru(path: str) -> str:
    """Parse *path* (PDF or image) with MinerU, returning Markdown content.

    Submits the file, polls for completion, and returns the extracted text
    with layout preserved (headings, tables, etc.).  Returns empty string
    on any error so callers can fall back to the legacy pipeline.

    Handles MinerU's single-worker limitation: retries on 409 Conflict.
    """
    submit_timeout = 6000  # 100-minute submit timeout

    if not _MINERU_URL:
        return ""  # MinerU not configured, caller falls back

    # Derive base URL from submit endpoint (strip /file_parse)
    _base = _MINERU_URL.rstrip("/")
    if _base.endswith("/file_parse"):
        _base = _base[:-len("/file_parse")]
    _auth = {"Authorization": f"Bearer {_MINERU_KEY}"}

    try:
        # 1. Submit (with retry for 409, connection errors, and timeouts)
        data = _mineru_submit(path, _base, _auth, submit_timeout)
        if data is None:
            return ""

        # ── Sync API (results returned directly, no task_id) ──
        if "results" in data:
            return _extract_mineru_markdown(data["results"], path, "sync")

        # ── Async API (task_id → poll → fetch result) ──
        task_id = data.get("task_id")
        if not task_id:
            logger.warning("MinerU returned no task_id or results for %s", path)
            return ""

        # 2. Poll (with retry for transient network errors)
        if not _mineru_poll_until_done(task_id, _base, _auth):
            return ""

        # 3. Fetch result (with retry for transient network errors)
        result = _mineru_fetch_result(task_id, _base, _auth)
        return _extract_mineru_markdown(result.get("results", {}), path, "async")
    except Exception as exc:
        logger.warning("MinerU parsing failed for %s: %s", path, exc)
        return ""

# ── 格式解析器（read_file 的分发目标） ──────────────────────────
# 约定：返回 None = 该格式全部解析器失败，回落默认文本读取；
#       返回 ""   = 确认无法解析（调用方按 ERROR 处理，防二进制入库）。

_PDF_IMAGE_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}


def _read_pdf_or_image(path: str, ext: str) -> str | None:
    """PDF/图片解析链：MinerU →（图片 VL / PDF pdfminer→VL 逐页）。"""
    result = _parse_with_mineru(path)
    if result:
        return result

    if ext != ".pdf":
        # MinerU unavailable or failed — for images, try VL model
        logger.info("MinerU unavailable for image %s, trying VL model", path)
        result = _parse_with_vl(path)
        if result:
            return result
        # Both MinerU and VL failed — image cannot be parsed without GPU.
        # Return empty to trigger ERROR in caller, preventing raw binary ingest.
        logger.warning("Both MinerU and VL unavailable for image %s — cannot parse", path)
        return ""

    # For PDFs, try basic pdfminer, then VL page-by-page as last resort
    logger.info("MinerU unavailable for %s, trying pdfminer fallback", path)
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(path)
        if text and len(text.strip()) > 20:
            return text
    except Exception:
        logger.warning("pdfminer fallback failed for %s", path)

    # pdfminer returned empty or failed — likely a scanned PDF.
    # Try VL model page-by-page as last resort.
    logger.info("pdfminer empty for %s, trying VL PDF fallback", path)
    result = _parse_pdf_with_vl(path)
    if result:
        return result
    return None


def _read_docx(path: str) -> str | None:
    """.docx：python-docx 优先，ImportError 回退 zipfile 手解 document.xml。"""
    try:
        from docx import Document
        doc = Document(path)
        paragraphs = [p.text for p in doc.paragraphs]
        table_texts = []
        for table in doc.tables:
            for row in table.rows:
                row_texts = [cell.text for cell in row.cells]
                table_texts.append(" | ".join(row_texts))
        parts = []
        if paragraphs:
            parts.append("\n".join(paragraphs))
        if table_texts:
            parts.append("\n--- TABLE DATA ---\n" + "\n".join(table_texts))
        return "\n".join(parts)
    except ImportError:
        import zipfile
        try:
            with zipfile.ZipFile(path) as z:
                # Zip bomb defence: check compression ratio before reading.
                info = z.getinfo("word/document.xml")
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > 100:
                        logger.warning("DOCX zip bomb suspected for %s (ratio=%.0f:1), refusing", path, ratio)
                        return ""
                if info.file_size > 50 * 1024 * 1024:  # 50 MB limit
                    logger.warning("DOCX document.xml too large for %s (%.1f MB), refusing", path, info.file_size / 1024 / 1024)
                    return ""
                xml_content = z.read("word/document.xml").decode("utf-8", errors="replace")
            import re
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", xml_content)).strip()
        except Exception:
            logger.warning("DOCX zip fallback failed for %s", path)
            return None
    except Exception:
        logger.warning("DOCX all parsers failed for %s, falling back to raw read", path)
        return None


def _read_pptx(path: str) -> str | None:
    """.pptx：python-pptx 提取文本框与表格。"""
    try:
        from pptx import Presentation
        prs = Presentation(path)
        texts = []
        for slide in prs.slides:
            parts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        t = para.text.strip()
                        if t:
                            parts.append(t)
                if shape.has_table:
                    table = shape.table
                    for row in table.rows:
                        row_texts = [cell.text.strip() for cell in row.cells]
                        parts.append(" | ".join(row_texts))
            if parts:
                texts.append("\n".join(parts))
        return "\n\n---\n\n".join(texts)
    except Exception:
        logger.warning("PPTX parsing failed for %s", path)
        return None


def _read_doc(path: str) -> str:
    """.doc（OLE2 旧格式）：antiword CLI，失败回退 errors=replace 明文读取。"""
    import subprocess
    import re as _re_doc
    try:
        result = subprocess.run(
            ["antiword", path],
            capture_output=True, text=True, timeout=30,
            check=True,  # 非零退出码按解析失败处理（except 回退 raw read）
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout
            # Collapse form-layout whitespace: antiword pads table cells
            # with trailing spaces that inflate char count 10x, causing
            # max_chunk_chars truncation to lose content.
            # - trim trailing whitespace per line
            # - collapse 3+ consecutive spaces to 2 (form column padding)
            # - collapse 4+ consecutive blank lines to 3
            text = "\n".join(line.rstrip() for line in text.splitlines())
            text = _re_doc.sub(r" {3,}", "  ", text)
            return _re_doc.sub(r"\n{4,}", "\n\n\n", text)
    except Exception:
        logger.warning("antiword failed for %s, falling back to raw read", path)
    # antiword failed — try reading as plain text with errors="replace"
    # (some .doc files have enough embedded ASCII to be partially readable)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def read_file(path: str) -> str:
    """Read a file as text (UTF-8). Supports .docx, .pdf, images, and more."""
    ext = os.path.splitext(path)[1].lower()

    if ext in _PDF_IMAGE_EXTS:
        result = _read_pdf_or_image(path, ext)
    elif ext == ".docx":
        result = _read_docx(path)
    elif ext == ".pptx":
        result = _read_pptx(path)
    elif ext == ".doc":
        return _read_doc(path)
    else:
        result = None
    if result is not None:
        return result

    # Default: plain text read (.txt, .md, etc.)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()

