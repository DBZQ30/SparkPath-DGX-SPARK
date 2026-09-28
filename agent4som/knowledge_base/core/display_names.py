"""Display-name resolution for knowledge-base files (design D8 + D10).

Shared by the miniapp adapter (list / preview / delete preview) and the
cross-repo ``query_kb.py`` retrieval tool so both render the same name for
the same file.  It lives in ``knowledge_base/`` because ``query_kb.py`` is in
a different git repository and cannot import the adapter — two copies would
drift (review L7).
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# 教务通知的文件名形如 jxtz_<record_id>_<标题>.txt（sync_jxtz.py:360-390）。
JXTZ_PREFIX_RE = re.compile(r"^jxtz_(\d+)_")

# 抓取账本，与 quota.db 同源解析：CHROMA_DB_PATH 的同级目录（bootstrap.py:133）。
LEDGER_FILENAME = "jxtz_notices.jsonl"

_ledger_cache: dict = {"mtime": None, "index": {}}


def _ledger_path() -> str:
    chroma_path = os.environ.get("CHROMA_DB_PATH", "data/chroma")
    return os.path.join(os.path.dirname(chroma_path), LEDGER_FILENAME)


def _ledger_index() -> dict[str, str]:
    """Return ``record_id -> original title`` from the ledger, cached by mtime.

    The ledger has no ``record_id`` column — it is derived from the ``url``
    tail with the same rule the ingestion side uses (``sync_jxtz.py:360``).
    """
    path = _ledger_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _ledger_cache["mtime"] == mtime:
        return _ledger_cache["index"]

    index: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record_id = entry.get("url", "").rstrip("/").split("/")[-1].replace(".htm", "")
                title = entry.get("title", "")
                if record_id and title:
                    index.setdefault(record_id, title)
    except OSError:
        logger.warning("jxtz ledger unreadable: %s", path, exc_info=True)
        return {}

    _ledger_cache["mtime"] = mtime
    _ledger_cache["index"] = index
    return index


def is_jxtz(source_file: str) -> bool:
    """True when *source_file* is an auto-fetched jxtz notice."""
    return bool(JXTZ_PREFIX_RE.match(source_file))


def resolve_display_name(scope: str, source_file: str) -> str:
    """Return the user-facing name for *source_file* in *scope*.

    jxtz notices use the original title from the fetch ledger; when the
    ledger has no entry the filename with the ``jxtz_<id>_`` prefix and
    extension stripped is used.  Everything else keeps its filename.

    The ledger is only consulted for ``global`` — jxtz ingestion writes to
    ``global`` exclusively (§2.8), so a personal file that happens to start
    with ``jxtz_`` must not borrow a public notice's title.
    """
    match = JXTZ_PREFIX_RE.match(source_file)
    if not match:
        return source_file
    if scope == "global":
        title = _ledger_index().get(match.group(1))
        if title:
            return title
    return JXTZ_PREFIX_RE.sub("", os.path.splitext(source_file)[0], count=1)
