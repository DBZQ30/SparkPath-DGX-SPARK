"""Structure-aware document chunker.

Splits documents at section/heading boundaries rather than fixed line counts.
Falls back to line-based splitting for sections exceeding max_chunk_lines.
"""

from __future__ import annotations
from typing import List, Tuple

import os
import re


def _is_heading(line: str) -> bool:
    """Detect section heading lines."""
    stripped = line.strip()
    if not stripped:
        return False
    # Markdown: ## ... or # ... (1-6 hashes)
    if re.match(r"^#{1,6}\s", stripped):
        return True
    # Chinese numbered: 一、二、三、...   or  1. 2. (1). (2)
    if re.match(r"^[一二三四五六七八九十百千]+[、.．]\s*\S", stripped):
        return True
    # Numbered Chinese heading: "1. 标题" (number + dot + SPACE + text)
    # NOT list items: "1.内容" (no space after dot)
    if re.match(r"^\d+\s*[、.．]\s+\S", stripped):
        return True
    # Chinese chapter-level headings: 第一章, 第二章, 第1章
    if re.match(r"^第[一二三四五六七八九十百千\d]+章", stripped):
        return True
    # Chinese article-level headings: 第一条, 第二条, ...
    # These ARE structural boundaries in 规章制度/管理办法 documents.
    # Without them, the entire document becomes a single giant section
    # that gets truncated by max_chunk_chars, losing critical policy content.
    if re.match(r"^第[一二三四五六七八九十百千\d]+条", stripped):
        return True
    # English: Chapter 1, Section 2, Part 3, Article 4
    if re.match(r"^(Chapter|Section|Part|Article)\s+\d", stripped, re.IGNORECASE):
        return True
    # Underline-style headings (=== or --- on a line by themselves)
    return bool(re.match(r"^[=\-]{3,}$", stripped))


def _is_chapter_level(heading: str) -> bool:
    """Return True if *heading* starts a new top-level section.

    Chapter-level: 第X章, 一、二、三、, Markdown ##, underline-style.
    Article-level (第X条) is NOT chapter-level — it nests under chapters.
    """
    stripped = heading.strip()
    if re.match(r"^#{1,6}\s", stripped):
        return True
    if re.match(r"^[一二三四五六七八九十百千]+[、.．]", stripped):
        return True
    if re.match(r"^第[一二三四五六七八九十百千\d]+章", stripped):
        return True
    if re.match(r"^[=\-]{3,}$", stripped):
        return True
    if re.match(r"^(Chapter|Section|Part)\s+\d", stripped, re.IGNORECASE):
        return True
    # Numbered headings with space (e.g. "1. 标题") are chapter-level
    return bool(re.match(r"^\d+\s*[、.．]\s+\S", stripped))


def _split_into_sections(text: str) -> List[Tuple[str, str, str]]:
    """Split *text* into [(heading, section_path, content), ...] tuples.

    Lines matching heading patterns start a new section.  Text before the
    first heading becomes the preamble section with empty heading.

    *section_path* accumulates heading hierarchy for contextual chunk
    prefixes (Contextual Retrieval pattern).
    """
    lines = text.splitlines()
    sections: List[Tuple[str, str, str]] = []
    current_heading = ""
    path_stack: List[str] = []  # [chapter, article, ...]
    current_lines: List[str] = []

    for line in lines:
        if _is_heading(line):
            # Save previous section
            section_path = " > ".join(path_stack) if path_stack else ""
            sections.append((
                current_heading,
                section_path,
                "\n".join(current_lines).strip(),
            ))
            # Clean heading text
            heading_text = line.strip()
            heading_text = re.sub(r"^#{1,6}\s+", "", heading_text)
            heading_text = re.sub(r"^第[一二三四五六七八九十百千\d]+[章节条款条]\s*", "", heading_text)
            heading_text = heading_text.rstrip("=").rstrip("-").strip()
            original = line.strip()
            if not heading_text:
                heading_text = original

            # Update heading hierarchy
            if _is_chapter_level(original):
                path_stack = [heading_text]
            else:
                # Sub-level heading (e.g. article 第X条) — nest under chapter
                if not path_stack:
                    path_stack = [heading_text]
                else:
                    # Replace the last sub-level (keep only 1 chapter + 1 article level)
                    if len(path_stack) >= 2:
                        path_stack[-1] = heading_text
                    else:
                        path_stack.append(heading_text)

            current_heading = heading_text
            current_lines = []
        else:
            current_lines.append(line)

    section_path = " > ".join(path_stack) if path_stack else ""
    sections.append((current_heading, section_path, "\n".join(current_lines).strip()))

    # Remove only truly empty sections (no heading AND no content)
    return [(h, p, c) for h, p, c in sections if h or c]


def chunk_document(
    content: str,
    filename: str,
    scope: str,
    source_hash: str = "",
    parser_version: str = "semantic-v1",
    max_chunk_lines: int = 200,
    max_chunk_chars: int = 1500,
    chunk_overlap: int = 4,
    max_chunk_tokens: int = 480,
    source: str = "",
) -> List[dict]:
    """Chunk *content* into RawIndexNode-like dicts using section boundaries.

    Each section becomes one chunk.  Sections longer than *max_chunk_lines*
    lines or *max_chunk_chars* characters are further split with overlap
    using line-based fallback.

    *max_chunk_tokens* (default 480) ensures chunks stay within the embedding
    model's 512-token limit with ~30 tokens of headroom for the contextual
    prefix. Chinese text: 1 char ≈ 1.5-2 tokens, so 480 tokens ≈ 320 chars
    (safe) to 800 chars (generous).  Used only as a hard ceiling — smaller
    chunks are preferred.
    """

    # Hard ceiling: never let a chunk body exceed ~480 tokens for embedding quality
    _token_ceiling_chars = max_chunk_tokens  # conservative: ~1 token/char for mixed text

    sections = _split_into_sections(content)
    chunks: List[dict] = []
    prev_body_lines: List[str] = []

    for heading, section_path, body in sections:
        body_lines = body.splitlines()

        needs_split = (
            len(body_lines) > max_chunk_lines
            or len(body) > max_chunk_chars
        )

        if not needs_split:
            chunks.append(_make_chunk(heading, body, filename, scope, source_hash, parser_version, section_path, source))
            prev_body_lines = body_lines
        else:
            # Line-based fallback for oversized sections with overlap
            for i in range(0, len(body_lines), max_chunk_lines - chunk_overlap):
                segment = "\n".join(body_lines[i : i + max_chunk_lines])
                if not segment.strip():
                    continue
                # Hard ceiling for embedding model
                if len(segment) > _token_ceiling_chars:
                    seg_lines = segment.splitlines()
                    trimmed = []
                    char_count = 0
                    for sl in seg_lines:
                        if char_count + len(sl) + 1 > _token_ceiling_chars:
                            break
                        trimmed.append(sl)
                        char_count += len(sl) + 1
                    segment = "\n".join(trimmed)
                if not segment.strip():
                    continue
                chunk_heading = f"{heading}:chunk-{i // (max_chunk_lines - chunk_overlap)}" if heading else f"chunk-{i // (max_chunk_lines - chunk_overlap)}"
                chunks.append(_make_chunk(chunk_heading, segment, filename, scope, source_hash, parser_version, section_path, source))
            prev_body_lines = body_lines

    # Post-process: trim any chunk that exceeds the token ceiling
    trimmed_chunks = []
    for c in chunks:
        content_text = c.get("content", "")
        if len(content_text) > _token_ceiling_chars + 100:  # +100 headroom for prefix
            # Trim body while preserving prefix
            lines = content_text.splitlines()
            kept = []
            count = 0
            for l in lines:
                if count + len(l) + 1 > _token_ceiling_chars + 80:
                    break
                kept.append(l)
                count += len(l) + 1
            c["content"] = "\n".join(kept)
            # Update anchor_text too
            c["anchor_text"] = c["content"][:200]
        trimmed_chunks.append(c)

    return trimmed_chunks


def _tier_from_scope(scope: str) -> str:
    """Derive the conflict-resolution tier from the storage scope.

    Mapping:
      - ``global``          → ``"global"``   (highest authority)
      - ``teachers``        → ``"assistant"`` (authority, previously assistants/)
      - ``users/{id}``      → ``"user"``      (personal, lowest authority)
    """
    if scope == "global":
        return "global"
    if scope == "teachers" or scope.startswith("assistants/"):
        return "assistant"
    if scope.startswith("users/"):
        return "user"
    return "user"


def _make_chunk(
    heading: str,
    body: str,
    filename: str,
    scope: str,
    source_hash: str,
    parser_version: str,
    section_path: str = "",
    source: str = "",
) -> dict:
    import hashlib
    body_stripped = body.strip()
    # Prepend heading and document context for both embedding accuracy and LLM context
    inner = f"{heading}\n{body_stripped}" if heading else body_stripped
    # Contextual prefix: clean filename (strip internal IDs and extensions)
    import re as _re
    clean_name = _re.sub(r'^.*jxtz_\d+_', '', os.path.basename(filename))
    clean_name = _re.sub(r'\.(txt|pdf|docx|doc|pptx|xlsx)$', '', clean_name)
    # Anthropic Contextual Retrieval (2024): reduces retrieval failures by 49-67%.
    # Include section hierarchy path (e.g. "第二章 导师聘任 > 第二条") so
    # the embedding carries structural context — helps disambiguate chunks
    # from different sections of the same document and match query intent
    # to the right document region.
    prefix = (f"[文档: {clean_name} | {section_path}]" if section_path
              else f"[文档: {clean_name}]")
    full_content = f"{prefix}\n{inner}"
    lines = full_content.splitlines()
    return {
        "source_tier": _tier_from_scope(scope),
        "source": source,
        "source_file": filename,
        "source_path": scope,
        "content": full_content,
        "section_title": heading if heading else None,
        "section_path": section_path if section_path else None,
        "anchor_text": full_content[:200],
        "anchor_locator": f"L1-L{len(lines)}",
        "source_hash": source_hash or hashlib.sha256(full_content.encode()).hexdigest(),
        "parser_version": parser_version,
    }
