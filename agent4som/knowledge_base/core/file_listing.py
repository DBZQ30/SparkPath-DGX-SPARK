"""知识库列表排序 —— 按入库时间，而非文件名。

教务通知文件名为 `jxtz_<不补零数字id>_<标题>.txt`，字典序与时间序不一致
（`jxtz_9991…` > `jxtz_10476…`），因此列表若能按文件名排序，会出现"最新入库的
反而排到最后/看不到"。这里统一按**每个文件最新一条上传记录的 `ingested_at`**
排序，并附文件名做稳定 tiebreak。
"""
from __future__ import annotations

from typing import Any

__all__ = ["sort_by_ingested"]


def _newest_ingested(item: dict[str, Any]) -> str:
    times = [str(u.get("ingested_at") or "") for u in (item.get("uploaders") or [])]
    return max(times) if times else ""


def sort_by_ingested(items: list[dict[str, Any]], order: str = "asc") -> list[dict[str, Any]]:
    """按最新入库时间排序 ``items``（``order`` 为 ``"asc"`` / ``"desc"``）。

    ``items`` 为列表端点聚合后的条目（含 ``filename`` 与 ``uploaders[].ingested_at``）。
    返回新列表，不修改入参。
    """
    return sorted(
        items,
        key=lambda it: (_newest_ingested(it), str(it.get("filename") or "")),
        reverse=(order == "desc"),
    )
