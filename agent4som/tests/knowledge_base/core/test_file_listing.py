"""知识库列表排序：按入库时间（而非文件名）—— 回归 jxtz 最新通知看不到的问题。"""

from knowledge_base.core.file_listing import sort_by_ingested


def _item(name: str, times: list[str]) -> dict:
    return {"filename": name, "uploaders": [{"ingested_at": t} for t in times]}


def test_sort_by_ingested_asc_desc():
    items = [
        _item("jxtz_9991_a.txt", ["2026-07-17 07:03:32"]),   # 字典序最大，但时间最早
        _item("jxtz_10476_b.txt", ["2026-09-22 20:00:42"]),  # 字典序最小，但时间最新
        _item("jxtz_10472_c.txt", ["2026-09-21 04:00:00"]),
    ]
    assert [i["filename"] for i in sort_by_ingested(items, "asc")] == [
        "jxtz_9991_a.txt", "jxtz_10472_c.txt", "jxtz_10476_b.txt"]
    assert [i["filename"] for i in sort_by_ingested(items, "desc")] == [
        "jxtz_10476_b.txt", "jxtz_10472_c.txt", "jxtz_9991_a.txt"]


def test_sort_uses_newest_uploader_and_stable_tiebreak():
    items = [
        _item("b.txt", ["2026-01-01 00:00:00", "2026-05-01 00:00:00"]),
        _item("a.txt", ["2026-05-01 00:00:00"]),
        _item("c.txt", []),
    ]
    desc = [i["filename"] for i in sort_by_ingested(items, "desc")]
    assert desc[0] == "b.txt"      # 取最新一条上传记录
    assert desc[-1] == "c.txt"     # 无记录排最后
    assert sort_by_ingested(items, "asc")[0]["filename"] == "c.txt"
    same = [_item("x.txt", ["2026-05-01 00:00:00"]), _item("a.txt", ["2026-05-01 00:00:00"])]
    assert [i["filename"] for i in sort_by_ingested(same, "asc")] == ["a.txt", "x.txt"]


def test_sort_does_not_mutate_input():
    items = [_item("b.txt", ["2026-05-01"]), _item("a.txt", ["2026-01-01"])]
    sort_by_ingested(items, "desc")
    assert [i["filename"] for i in items] == ["b.txt", "a.txt"]
