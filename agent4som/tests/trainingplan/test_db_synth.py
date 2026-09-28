"""trainingplan 存储层（db.py 门面 + db_* mixin 拆分）DAO 直接回归。

此前 TrainingPlanDB 只有样本链路（test_trainingplan.py 依赖 data/SmartGuide，
不随仓库分发而全 skip）与 identity/api 间接覆盖；此文件用 tmp_path 真库
离线回归：文档队列状态机、级联删除、幂等写入、适用年级口径、政策表、
绑定与原件登记。
"""
from __future__ import annotations

import pytest

from academicwarning.models import PlanCourse, PlanSemesterCourse
from trainingplan.db import ALL_GRADES, TrainingPlanDB
from trainingplan.models import (
    CreditNode,
    GraduationReq,
    ModeCourse,
    ModeRule,
    ModeScope,
    PlanDocument,
    PrereqEdge,
)


@pytest.fixture()
def db(tmp_path):
    tp = TrainingPlanDB(str(tmp_path / "tp.db"))
    yield tp
    tp.close()


def _doc(major="工商管理", year="2023级", **kw) -> PlanDocument:
    return PlanDocument(major=major, entry_year=year, **kw)


# ── 文档队列状态机：queued → parsing → done/failed，retry/recover ──


def test_queue_lifecycle(db):
    d1 = db.enqueue_document(_doc())
    d2 = db.enqueue_document(_doc())
    assert db.queue_summary() == {"total": 2, "queued": 2, "parsing": 0, "done": 0, "failed": 0}

    claimed = db.claim_next()
    assert claimed.id == d1 and claimed.parsed_status == "parsing"

    db.finish_document(d1, "done", note="ok", meta_extra={"rows": 3})
    doc = db.get_document(d1)
    assert doc.parsed_status == "done" and doc.in_file_meta["note"] == "ok"
    assert doc.in_file_meta["rows"] == 3

    # d2 领取后遗留 parsing → failed，queued 保留
    assert db.claim_next().id == d2
    assert db.recover_stale() == 1
    assert db.get_document(d2).error == "解析进程中断，可重试"
    assert db.retry(d2) is True
    assert db.get_document(d2).parsed_status == "queued"
    # 非 failed / 不存在 → 拒绝重试
    assert db.retry(d1) is False
    assert db.retry(9999) is False


def test_queue_items_positions(db):
    ids = [db.enqueue_document(_doc(year=y)) for y in ("2023级", "2024级", "2025级")]
    db.claim_next()                                   # 第一条 → parsing
    db.finish_document(ids[2], "done")                 # done 不占队列位
    items = {i["id"]: i for i in db.queue_items()}
    assert set(items) == set(ids[:2])                  # done 被排除
    assert items[ids[0]]["state"] == "parsing"
    assert "position" not in items[ids[0]]
    assert items[ids[0]]["elapsed_s"] is not None
    assert items[ids[1]]["position"] == 1 and items[ids[1]]["ahead"] == 0


def test_enqueue_applies_and_rejected(db):
    # 默认 applies_to = [主年级]；显式传入则透传
    assert db.get_document(db.enqueue_document(_doc())).applies_to == ["2023级"]
    assert db.get_document(db.enqueue_document(
        _doc(applies_to=["2023级", "2024级"]))).applies_to == ["2023级", "2024级"]
    # 拒绝件：不入队（queue_summary 不计 rejected），in_file_meta 记 error
    rid = db.insert_rejected(_doc(major="未知专业"), reason="专业未收录")
    r = db.get_document(rid)
    assert r.parsed_status == "rejected" and r.error == "专业未收录"
    assert db.queue_summary()["total"] == 2


def test_find_document_for_grade_and_available_grades(db):
    exact = db.enqueue_document(_doc(year="2023级", applies_to=["2023级"]))
    allg = db.enqueue_document(_doc(year="2020级", applies_to=[ALL_GRADES]))
    db.finish_document(exact, "done")
    db.finish_document(allg, "done")
    # 精确 applies_to 优先，其次"全部"
    assert db.find_document_for_grade("工商管理", "2023级").id == exact
    assert db.find_document_for_grade("工商管理", "2025级").id == allg
    assert db.find_document_for_grade("无此专业", "2023级") is None
    grades, has_all = db.available_grades("工商管理")
    assert grades == ["2023级"] and has_all is True
    # 空列表 = 重置为主年级；资料表则重置为"全部"
    assert db.set_document_applies_to(allg, []) is True
    assert db.get_document(allg).applies_to == ["2020级"]
    grades, has_all = db.available_grades("工商管理")
    assert grades == ["2023级", "2020级"] and has_all is False


def test_delete_document_cascades(db):
    did = db.enqueue_document(_doc())
    db.finish_document(did, "done")
    db.replace_plan_data(did, [CreditNode(did, "", "总", "毕业总学分", "148+8")],
                         [PlanCourse(plan_id=did, course_code="A1", course_name="管理学", credit=3.0,
                                course_type="专业核心课程", required_flag="必修", semester="1-1")],
                         [PlanSemesterCourse(plan_id=did, semester="1-1", course_code="A1",
                                            course_name="管理学", credit=3)],
                         [], [])
    db.replace_mode_data(did, [ModeRule(did, "科学研究型", "说明")], [], [])
    # 8 张产物子表全部清空（PLAN_CHILD_TABLES 外部也在复用，校验其可达）
    assert len(TrainingPlanDB.PLAN_CHILD_TABLES) == 8
    assert db.delete_document(did) is True
    assert db.get_document(did) is None
    assert db.get_credit_nodes(did) == [] and db.get_courses(did) == []
    assert db.get_mode_rules(did) == []
    assert db.delete_document(did) is False


# ── 方案数据写入/读取：幂等 + 过滤 + 课程备注合并 ──


def _seed_plan(db: TrainingPlanDB, did: int) -> None:
    db.replace_plan_data(
        did,
        [CreditNode(did, "", "课程教学", "总", "148"),
         CreditNode(did, "课程教学", "课程教学/专业核心", "核心", "40")],
        [PlanCourse(plan_id=did, course_code="A1", course_name="管理学", credit=3.0,
                                  course_type="专业核心课程", required_flag="必修", semester="1-1"),
         PlanCourse(plan_id=did, course_code="B2", course_name="运营管理", credit=3.0,
                                  course_type="专业核心课程", required_flag="必修", semester="2-1"),
         PlanCourse(plan_id=did, course_code="G1", course_name="体育", credit=1.0,
                                  course_type="公共课程", required_flag="必修", semester="1-2")],
        [PlanSemesterCourse(plan_id=did, semester="1-1", course_code="A1",
                           course_name="管理学", credit=3.0)],
        [PrereqEdge(did, "管理学", "运营管理", verified=1, verified_by="admin"),
         PrereqEdge(did, "管理学", "体育", verified=0)],
        [GraduationReq(did, "学分", "毕业总学分", "148", "学分")],
        course_notes={"A1": "研究生进阶"},
    )


def test_replace_plan_data_and_getters(db):
    did = db.enqueue_document(_doc())
    db.finish_document(did, "done")
    _seed_plan(db, did)

    assert [n["name"] for n in db.get_credit_nodes(did)] == ["总", "核心"]  # sort 排序
    core = db.get_courses(did, category="专业核心课程")
    assert [c["course_code"] for c in core] == ["A1", "B2"]
    assert [c["course_name"] for c in db.get_courses(did, semester="1-1")] == ["管理学"]
    assert core[0]["note"] == "研究生进阶"                     # course_notes 合并
    assert len(db.get_semester_courses(did)) == 1
    # 先修：默认只取已校对
    assert [(e["from_course_name"], e["to_course_name"])
            for e in db.get_prereq_edges(did)] == [("管理学", "运营管理")]
    assert len(db.get_prereq_edges(did, verified_only=False)) == 2
    assert db.get_graduation_reqs(did)[0]["item"] == "毕业总学分"

    # 幂等：重写更少行 → 无残留
    db.replace_plan_data(did, [], [PlanCourse(plan_id=did, course_code="A1", course_name="管理学",
                                              credit=3.0, course_type="专业核心课程",
                                              required_flag="必修", semester="1-1")], [], [], [])
    assert db.get_credit_nodes(did) == [] and db.get_semester_courses(did) == []
    assert len(db.get_courses(did)) == 1


def test_mode_data_and_update_course_notes(db):
    did = db.enqueue_document(_doc())
    db.finish_document(did, "done")
    db.replace_plan_data(
        did, [],
        [PlanCourse(plan_id=did, course_code="A1", course_name="管理学", credit=3.0,
                                  course_type="专业核心课程", required_flag="必修", semester="1-1"),
         PlanCourse(plan_id=did, course_code="B2", course_name="运营管理", credit=3.0,
                                  course_type="专业核心课程", required_flag="必修", semester="2-1")],
        [], [], [])
    db.replace_mode_data(
        did,
        [ModeRule(did, "科学研究型", "学分替代", "替代学分", "6", "学分"),
         ModeRule(did, "通用", "学期学分上限", "上限", "25", "学分")],
        [ModeCourse(did, "科学研究型", "研究生进阶", "R1", "科研课甲", 2.0)],
        [ModeScope(did, "交叉融合型", "自动化")],
    )
    assert [r["rule_type"] for r in db.get_mode_rules(did, mode="科学研究型")] == ["学分替代"]
    assert len(db.get_mode_rules(did)) == 2
    assert db.get_mode_courses(did, mode="科学研究型")[0]["course_name"] == "科研课甲"
    assert db.get_mode_scopes(did, mode="交叉融合型")[0]["allowed_major"] == "自动化"
    # name: 前缀与空编码跳过；只有真实编码命中更新
    n = db.update_course_notes(did, {"A1": "前沿交叉", "name:管理学": "x", "": "y"})
    assert n == 1
    by_code = {c["course_code"]: c for c in db.get_courses(did)}
    assert by_code["A1"]["note"] == "前沿交叉" and by_code["B2"]["note"] == ""


def test_set_prereq_verified_actions(db):
    did = db.enqueue_document(_doc())
    db.finish_document(did, "done")
    db.replace_plan_data(
        did, [], [], [],
        [PrereqEdge(did, "管理学", "运营管理"),
         PrereqEdge(did, "管理学", "统计学")], [])
    n = db.set_prereq_verified(did, [
        {"action": "confirm", "from": "管理学", "to": "运营管理"},
        {"action": "delete", "from": "管理学", "to": "统计学"},
        {"action": "add", "from": "统计学", "to": "运筹学"},
        {"from": "", "to": "跳过"},                       # 空端点不计
    ], verifier="admin")
    assert n == 3
    names = {(e["from_course_name"], e["to_course_name"]) for e
             in db.get_prereq_edges(did, verified_only=False)}
    assert names == {("管理学", "运营管理"), ("统计学", "运筹学")}
    manual = next(e for e in db.get_prereq_edges(did, verified_only=False)
                  if e["source"] == "manual")
    assert manual["verified"] == 1 and manual["verified_by"] == "admin"


# ── 政策：转专业 / 专业选择 ──


def test_transfer_policy(db):
    db.replace_transfer_policy("2026", [
        {"major": "工商管理", "entry_year": "2023级", "scope": "学院内", "quota": 2},
        {"major": "工业工程", "entry_year": "2023级", "scope": "跨院", "quota": 0},
    ], [
        {"rule_type": "志愿", "item": "志愿数", "value": "3"},
        {"rule_type": "时间", "item": "申请", "value": "5月"},
    ], source_doc_id=7)
    assert db.latest_transfer_policy_year() == "2026"
    rows = db.get_transfer_plans(major="工商管理")
    assert rows[0]["quota"] == 2 and rows[0]["source_doc_id"] == 7
    assert [r["item"] for r in db.get_transfer_rules(policy_year="2026", rule_type="时间")] \
        == ["申请"]
    # add_transfer_rules：同 (rule_type, item) 幂等替换
    assert db.add_transfer_rules("2026",
                                 [{"rule_type": "志愿", "item": "志愿数", "value": "2"}]) == 1
    assert len(db.get_transfer_rules("2026")) == 2
    assert db.get_transfer_rules(rule_type="志愿")[0]["value"] == "2"
    # 整年重写清空
    db.replace_transfer_policy("2026", [], [])
    assert db.get_transfer_plans("2026") == [] and db.get_transfer_rules("2026") == []


def test_major_selection_policy(db):
    db.replace_major_selection_policy("2025级", [
        {"major": "A", "quota": 4}, {"major": "B", "quota": 1},
    ], [{"rule_type": "录取", "item": "原则", "value": "绩点优先"}])
    assert db.latest_major_selection_entry_year() == "2025级"
    assert {p["major"]: p["quota"] for p
            in db.get_major_selection_plans("2025级")} == {"A": 4, "B": 1}
    assert db.get_major_selection_rules(entry_year="2025级",
                                        rule_type="录取")[0]["value"] == "绩点优先"
    assert db.get_major_selection_plans("2024级") == []
    # 空库 → latest 返回空串
    mem = TrainingPlanDB(":memory:")
    try:
        assert mem.latest_major_selection_entry_year() == ""
    finally:
        mem.close()


def test_delete_source_doc_cascades_policy(db):
    fid = db.upsert_source_doc({"file_name": "2026转专业细则.pdf", "file_hash": "h1",
                                "category": "政策", "text_content": "正文"})
    db.replace_transfer_policy("2026", [{"major": "A", "entry_year": "2023级",
                                          "scope": "学院内", "quota": 1}], [],
                               source_doc_id=fid)
    db.replace_major_selection_policy("2025级", [{"major": "A", "quota": 1}], [],
                                      source_doc_id=fid)
    assert db.delete_source_doc(fid) is True
    assert db.list_source_docs() == []
    assert db.get_transfer_plans("2026") == []
    assert db.get_major_selection_plans("2025级") == []
    assert db.delete_source_doc(fid) is False


# ── 学生侧：绑定 / 解读留痕 / 原件登记 ──


def test_bindings_and_interpret_run(db):
    db.record_interpret_run("u1", "工商管理", "2023级", {"k": 1})
    row = db.conn.execute("SELECT user_id, summary_json FROM plan_interpret_run").fetchone()
    assert row["user_id"] == "u1" and '{"k": 1}' in row["summary_json"]

    db.upsert_binding("miniapp", "u1", "9000000001", name="学生甲",
                      grade="2023级", major="工商管理")
    b = db.get_binding("miniapp", "u1")
    assert b.student_id == "9000000001" and b.status == "active"
    # 同 (platform, user_id) 覆盖，不新增行
    db.upsert_binding("miniapp", "u1", "9000000002", name="张三")
    b = db.get_binding("miniapp", "u1")
    assert b.student_id == "9000000002"
    assert db.conn.execute("SELECT COUNT(*) c FROM plan_student_binding").fetchone()["c"] == 1
    assert db.delete_binding("miniapp", "u1") is True
    assert db.get_binding("miniapp", "u1") is None
    assert db.delete_binding("miniapp", "u1") is False


def test_source_docs_registration(db):
    fid = db.upsert_source_doc({"file_name": "大纲.docx", "file_hash": "h1",
                                "category": "大纲", "size_bytes": 10, "ext": ".docx",
                                "text_chars": 5, "text_content": "大纲正文"})
    assert isinstance(fid, int)
    # 同 (文件名, 哈希) 幂等去重；不同哈希不合并
    assert db.upsert_source_doc({"file_name": "大纲.docx", "file_hash": "h1"}) is None
    fid2 = db.upsert_source_doc({"file_name": "大纲.docx", "file_hash": "h2",
                                 "category": "培养方案", "entry_year": "2023级",
                                 "applies_to": []})
    assert fid2 != fid
    docs = db.list_source_docs(category="大纲")
    assert docs[0]["file_name"] == "大纲.docx" and docs[0]["applies_to"] == [ALL_GRADES]
    assert db.get_source_doc_text(fid) == "大纲正文"
    assert db.get_source_doc_text(9999) == ""
    assert db.source_doc_stats() == {"大纲": 1, "培养方案": 1, "_total": 2}
    # 登记后补 applies_to：培养方案按主年级断言路径（set 空 → "全部"兜底）
    assert db.set_source_doc_applies_to(fid, []) is True
    assert db.list_source_docs()[0]["applies_to"] == [ALL_GRADES]
