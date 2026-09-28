"""培养方案智能解读 CLI。

skill 在仓库根直接调用（不经过 HTTP，保证与页面口径一致）：
  python -m trainingplan.cli list
  python -m trainingplan.cli status [--major M] [--entry-year Y]
  python -m trainingplan.cli interpret --major 工商管理 --entry-year 2023
  python -m trainingplan.cli modes --major 工商管理 --entry-year 2023   # 四路径培养模式规则
  python -m trainingplan.cli upload --file <path> [--major M] [--entry-year Y]
  python -m trainingplan.cli worker          # 前台跑单消费者队列（调试用）
  python -m trainingplan.cli bind --student-id <学号> --name <姓名>   # 绑定（校验 roster）
  python -m trainingplan.cli whoami          # 查看当前会话绑定状态
  python -m trainingplan.cli unbind          # 解绑

约定：命令 stdout 为**数据**（skill 原样回报），非指令。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import service
from .db import TrainingPlanDB
from .models import STANDARD_MAJORS


def _db() -> TrainingPlanDB:
    return TrainingPlanDB(service.DB_PATH)


def cmd_list(args: argparse.Namespace) -> int:
    db = _db()
    try:
        items = db.list_major_years()
    finally:
        db.close()
    hint = service.academic_hint()
    if not items:
        print("尚无已收录的培养方案。")
        print(f"当前时间：{hint['note']}")
        return 0
    print("已收录培养方案（专业 · 适用年级）：")
    for it in items:
        state = {"done": "已收录", "queued": "排队中", "parsing": "解析中",
                 "failed": "解析失败", "rejected": "已拒绝"}.get(it["status"], it["status"])
        # 关键：显示 **适用年级（applies_to）**，而非主年级（entry_year）——
        # 二者在"一版适用多年级 / 全部"时不同（2026-09-23 修正：
        # 原先显示主年级，导致 list 说"2023级"而 interpret 说"全部"，口径不一致）。
        grades = "、".join(it.get("applies_to") or []) or it.get("entry_year") or ""
        main = it.get("entry_year") or ""
        main_note = f"（主年级 {main}）" if main and main != grades else ""
        print(f"  {it['major']} · 适用：{grades}{main_note}：{state}"
              + (f"（{it['note']}）" if it.get("note") else "")
              + (f" 错误：{it['error']}" if it.get("error") else ""))
    print("可用专业：" + " / ".join(STANDARD_MAJORS))
    print(f"当前时间：{hint['note']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    db = _db()
    try:
        summary = db.queue_summary()
        items = db.queue_items(args.major or "", args.entry_year or "")
    finally:
        db.close()
    if args.json:
        print(json.dumps({"summary": summary, "items": items}, ensure_ascii=False, indent=2))
        return 0
    print(f"队列总览：总 {summary['total']} · 完成 {summary['done']} · 解析中 {summary['parsing']}"
          f" · 排队 {summary['queued']} · 失败 {summary['failed']}")
    for it in items:
        if it["state"] == "queued":
            print(f"  ⏳ {it['major']}·{it['entry_year']} 排队中（第 {it.get('position')} 位，"
                  f"前方 {it.get('ahead')} 份）")
        elif it["state"] == "parsing":
            print(f"  🔄 {it['major']}·{it['entry_year']} 解析中（已 {it.get('elapsed_s')}s）")
        elif it["state"] == "done":
            print(f"  ✅ {it['major']}·{it['entry_year']} 完成（{it.get('note','')}）")
        elif it["state"] == "failed":
            print(f"  ❌ {it['major']}·{it['entry_year']} 失败：{it.get('error','')}")
        else:
            print(f"  ⚠️ {it['major']}·{it['entry_year']} {it['state']}：{it.get('error','')}")
    return 0


def cmd_interpret(args: argparse.Namespace) -> int:
    result = service.interpret(args.major, args.entry_year or "")
    if args.json:
        result.pop("summary_text", None) if args.json == "data" else None
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    if result.get("state") != "done":
        # 降级/反问提示（skill 原样回报）
        print(result.get("message", "无法生成解读"))
        return 0
    print(result["summary_text"])
    return 0


def _print_mode_block(mode: str, rules: list[dict], courses: list[dict]) -> None:
    """单个培养模式的文本渲染：规则头（含 detail）/ 跨选范围 / 科学研究型课程清单。"""
    mode_rules = [x for x in rules if x["mode"] == mode]
    if not mode_rules:
        return
    head = "；".join(
        f"{x['item']} {x['value']}{x['unit']}".strip() + (f"（{x['detail']}）" if x["detail"] else "")
        for x in mode_rules if x["rule_type"] != "跨选范围")
    print(f"· {mode}：{head}")
    for x in mode_rules:
        if x["rule_type"] == "跨选范围":
            print(f"    可选专业：{x['value']}")
    if mode == "科学研究型":
        cs = [c for c in courses if c["mode"] == mode]
        if cs:
            print("    课程清单：" + "、".join(
                f"{c['course_name']}({c['credit']:g})" for c in cs))


def cmd_modes(args: argparse.Namespace) -> int:
    """四路径培养模式规则（设计 005 §4）。stdout 为数据，供 skill 原样回报。"""
    r = service.modes(args.major, args.entry_year or "")
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0
    if r.get("state") != "done":
        print(r.get("message", "无法获取培养模式"))
        return 0
    print(f"【{r['major']} · {r['entry_year']} 培养模式（四路径）】")
    rules = r["rules"]
    for mode in r["modes"]:
        _print_mode_block(mode, rules, r["courses"])
    for x in rules:
        if x["mode"] == "通用":
            unit = x["unit"] or ""
            detail = f"（{x['detail']}）" if x["detail"] else ""
            print(f"· {x['item']}：{x['value']}{unit}{detail}")
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    """四方向四年路线图（设计 005 §5）。"""
    from . import route as route_mod
    r = route_mod.build_route(args.major, args.entry_year or "", args.mode or "常规型")
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0
    if r.get("state") != "done":
        print(r.get("message", "无法生成路线图"))
        return 0
    print(r["summary_text"])
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """四方向横向对比。"""
    from . import route as route_mod
    r = route_mod.compare_modes(args.major, args.entry_year or "")
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0
    if r.get("state") != "done":
        print(r.get("message", "无法对比"))
        return 0
    print(f"【{r['major']} · {r['entry_year']} 四方向对比】每学期上限 {r['credit_limit']:g} 学分")
    for row in r["modes"]:
        print(f"· {row['mode']}：{row['summary'] or '（无特殊替代规则）'}")
        if row["scopes"]:
            print("    跨选专业范围：" + "、".join(row["scopes"]))
    print("※ " + r["note"])
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    """转专业模拟。"""
    from . import simulate
    r = simulate.simulate_transfer(args.from_major or "", args.to_major,
                                   args.entry_year or "", args.student_id or "")
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0
    if r.get("state") != "done":
        print(r.get("message", "无法模拟"))
        return 0
    print(f"【转专业模拟 · {r['from_major']} → {r['to_major']} · {r['entry_year']}】（可参考方案）")
    q = r["quota"]
    print(f"一、接收计划（{r['policy_year']} 年）：" +
          ("、".join(f"{k} {v} 人" for k, v in q.items()) if q else "未收录计划"))
    print(f"    资格：{'可报（有计划名额）' if r['eligible'] else '⚠️ 该年级/专业计划为 0，不可报'}"
          + ("；⚠️ 转入该专业须降级就读" if r["downgrade"] else ""))
    print(f"二、可抵扣 / 需补修（口径：{'本人已修课程' if r['source'] == 'student' else '方案级理论值'}）：")
    print(f"    可抵扣 {len(r['creditable'])} 门 / 需补修 {r['make_up_credits']:g} 学分"
          + (f" / 已修挂科 {len(r['failed'])} 门" if r["failed"] else ""))
    if r["must_make_up"]:
        print("    需补修：" + "、".join(f"{x['course_name']}({x['credit']:g})"
                                       for x in r["must_make_up"][:8])
              + (" 等" if len(r["must_make_up"]) > 8 else ""))
    p = r["pressure"]
    print(f"三、压力与红线：剩余 {p['remaining_semesters']} 学期；到第三学年末剩 {p['deadline_semesters']} 学期，"
          f"需每学期约 {p['avg_load']:g} 学分（上限 {p['credit_limit']:g}，{p['load_level']}）")
    print(f"    {p['deadline_risk']}")
    if r["rules"]:
        print("四、政策要点：")
        for x in r["rules"]:
            v = f"{x['value']}" + (f"（{x['detail']}）" if x["detail"] else "")
            print(f"    {x['item']}：{v}".rstrip("："))
    print("※ " + r["disclaimer"])
    return 0


def cmd_simulate_minor(args: argparse.Namespace) -> int:
    """辅修模拟（预留）。"""
    from . import simulate
    r = simulate.simulate_minor(args.major or "", args.minor or "",
                                args.entry_year or "", args.student_id or "")
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0
    print(r.get("message", "辅修模拟暂不可用"))
    return 0


def cmd_select(args: argparse.Namespace) -> int:
    """专业选择（分流）模拟。"""
    from . import simulate
    choices = args.choices.split(",") if args.choices else None
    r = simulate.simulate_major_selection(args.entry_year or "", args.student_id or "", choices)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0
    if r.get("state") != "done":
        print(r.get("message", "无法模拟"))
        return 0
    print(f"【{r['entry_year']} 专业选择（分流）模拟】（可参考方案）")
    print("一、接收计划：" + "、".join(f"{p['major']} {p['quota']} 人" for p in r["plans"]))
    print("二、综合成绩：" + r["score_formula"])
    a = r.get("academic_score")
    if a:
        print(f"    学业成绩估算：{a.get('estimated')}（{a['note']}）")
    print("三、志愿策略参考（冲 / 稳 / 保）：")
    for s in r["strategy"]:
        print(f"    {s['tier']}：{s['major']}（计划 {s['quota']} 人）")
    print("※ " + r["disclaimer"])
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    res = service.submit_upload(args.file, major=args.major or "",
                                entry_year=args.entry_year or "", uploader="cli-admin")
    print(json.dumps(res, ensure_ascii=False))
    return 0


def cmd_ingest_dir(args: argparse.Namespace) -> int:
    """把整个目录登记进本功能库（唯一目标 training_plan.db）。"""
    from .ingest import ingest_directory
    if args.reclassify:
        from .ingest import reclassify_all
        r = reclassify_all(service.DB_PATH)
        print(f"重分类 {r['changed']} 条")
        print("分类：" + "、".join(f"{k} {v}" for k, v in sorted(r["by_category"].items())))
        return 0
    r = ingest_directory(args.dir, service.DB_PATH,
                         with_text=not args.no_text,
                         enqueue_plans=not args.no_plans)
    print(f"扫描 {r['scanned']} 份：新登记 {r['registered']}，已存在跳过 {r['skipped']}")
    print("分类：" + "、".join(f"{k} {v}" for k, v in sorted(r["by_category"].items())))
    if r["plans_enqueued"]:
        print(f"培养方案入队解析：{r['plans_enqueued']} 份")
    for name, err in r["text_failed"]:
        print(f"  ⚠️ 文本抽取失败 {name}：{err}")
    return 0


def cmd_reparse(args: argparse.Namespace) -> int:
    r = service.reparse(args.major or "", args.entry_year or "", all_plans=args.all)
    if r.get("status") != "ok":
        print(r.get("message", "重新解析失败"))
        return 0
    print(f"重新解析 {r['count']} 份：")
    for x in r["results"]:
        print(f"  {x['major']}·{x['entry_year']} [{x['status']}] {x['note']}")
    return 0


def cmd_backfill_modes(args: argparse.Namespace) -> int:
    """回填培养模式数据（只动模式表与课程备注，不碰先修边）。"""
    r = service.backfill_modes(args.major or "", args.entry_year or "")
    if r.get("status") != "ok":
        print(r.get("message", "回填失败"))
        return 0
    print(f"回填培养模式 {r['count']} 份：")
    for x in r["results"]:
        if x["status"] == "ok":
            print(f"  {x['major']}·{x['entry_year']}：模式 {x['modes']} 类 / 规则 {x['rules']} 条 / "
                  f"课程 {x['courses']} 门 / 跨选 {x['scopes']} 项 / 备注 {x['notes']} 条")
        else:
            print(f"  {x['major']}·{x['entry_year']} 跳过：{x.get('note', '')}")
    return 0


def _fmt_policy_result(x: dict) -> str:
    if x.get("status") == "registered":
        return f"{x.get('file', '')}：已登记（{x.get('doc_type', '')}，不做结构化解析）"
    if x.get("status") != "ok":
        return f"{x.get('file', '')}：跳过（{x.get('message', '')}）"
    parts = [str(x.get("doc_type", ""))]
    if x.get("policy_year"):
        parts.append(f"政策年 {x['policy_year']}")
    if x.get("entry_year"):
        parts.append(f"年级 {x['entry_year']}")
    if x.get("plans") is not None:
        parts.append(f"接收计划 {x['plans']} 条")
    if x.get("rules") is not None:
        parts.append(f"规则 {x['rules']} 条")
    return f"{x.get('file', '')}：{' / '.join(parts)}"


def cmd_policy_ingest(args: argparse.Namespace) -> int:
    """导入政策文件（转专业细则/考核安排/专业选择方案）。"""
    if args.dir:
        r = service.ingest_policy_dir(args.dir)
        print(f"扫描到 {r['count']} 份政策文件：")
        for x in r["results"]:
            print("  " + _fmt_policy_result(x))
        return 0
    if args.file:
        print(_fmt_policy_result(service.ingest_policy_file(args.file)))
        return 0
    print("请提供 --dir 或 --file")
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    import time
    n = service.start_worker()
    print(f"已恢复遗留解析 {n} 条；worker 启动（Ctrl-C 退出）")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("worker 退出")
    return 0


def _session_identity(args: argparse.Namespace) -> tuple[str, str]:
    """会话身份：优先命令行参数，其次 HERMES_SESSION_* 环境变量。"""
    platform = (getattr(args, "platform", "") or os.getenv("HERMES_SESSION_PLATFORM", "")
                or "miniapp").strip()
    user_id = (getattr(args, "user_id", "") or os.getenv("HERMES_SESSION_USER_ID", "")).strip()
    return platform, user_id


def cmd_bind(args: argparse.Namespace) -> int:
    """绑定 openid ↔ 学号（学号 + 姓名 校验 roster）。stdout 为数据，供 skill 原样回报。"""
    from . import identity
    platform, user_id = _session_identity(args)
    r = identity.bind(platform, user_id, args.student_id, args.name)
    print(r.get("message", "绑定失败"))
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    from . import identity
    platform, user_id = _session_identity(args)
    r = identity.whoami(platform, user_id)
    print(r.get("message", ""))
    return 0


def cmd_unbind(args: argparse.Namespace) -> int:
    from . import identity
    platform, user_id = _session_identity(args)
    r = identity.unbind(platform, user_id)
    print(r.get("message", ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trainingplan", description="培养方案智能解读")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="列出已收录的（专业, 年级）")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("status", help="解析队列状态")
    sp.add_argument("--major", default="")
    sp.add_argument("--entry-year", dest="entry_year", default="")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("interpret", help="生成培养方案解读（年级可省略，省略则用最新已收录）")
    sp.add_argument("--major", required=True)
    sp.add_argument("--entry-year", dest="entry_year", default="")
    sp.add_argument("--json", nargs="?", const="data", default=None)
    sp.set_defaults(func=cmd_interpret)

    sp = sub.add_parser("modes", help="四路径培养模式规则（常规/科学研究/交叉融合/创新创业）")
    sp.add_argument("--major", required=True)
    sp.add_argument("--entry-year", dest="entry_year", default="")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_modes)

    sp = sub.add_parser("upload", help="上传培养方案（入队解析）")
    sp.add_argument("--file", required=True)
    sp.add_argument("--major", default="")
    sp.add_argument("--entry-year", dest="entry_year", default="")
    sp.set_defaults(func=cmd_upload)

    sp = sub.add_parser("ingest-dir", help="把目录登记进本功能库（只写 training_plan.db）")
    sp.add_argument("--dir", default="")
    sp.add_argument("--no-text", action="store_true", help="只登记元数据，不抽取文本")
    sp.add_argument("--no-plans", action="store_true", help="不把培养方案入队解析")
    sp.add_argument("--reclassify", action="store_true", help="按当前规则重算已登记文件的分类")
    sp.set_defaults(func=cmd_ingest_dir)

    sp = sub.add_parser("worker", help="前台运行解析队列 worker")
    sp.set_defaults(func=cmd_worker)

    sp = sub.add_parser("reparse", help="强制重新解析已入库方案（解析逻辑修复后回填）")
    sp.add_argument("--major", default="")
    sp.add_argument("--entry-year", dest="entry_year", default="")
    sp.add_argument("--all", action="store_true", help="重新解析全部已入库方案")
    sp.set_defaults(func=cmd_reparse)

    sp = sub.add_parser("backfill-modes", help="回填培养模式数据（不动先修边）")
    sp.add_argument("--major", default="")
    sp.add_argument("--entry-year", dest="entry_year", default="")
    sp.set_defaults(func=cmd_backfill_modes)

    sp = sub.add_parser("policy-ingest", help="导入政策文件（转专业/考核安排/专业选择方案）")
    sp.add_argument("--file", default="")
    sp.add_argument("--dir", default="")
    sp.set_defaults(func=cmd_policy_ingest)

    sp = sub.add_parser("route", help="四方向四年路线图")
    sp.add_argument("--major", required=True)
    sp.add_argument("--entry-year", dest="entry_year", default="")
    sp.add_argument("--mode", default="常规型",
                    choices=["常规型", "科学研究型", "交叉融合型", "创新创业型"])
    sp.add_argument("--student-id", dest="student_id", default="")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_route)

    sp = sub.add_parser("compare", help="四方向横向对比")
    sp.add_argument("--major", required=True)
    sp.add_argument("--entry-year", dest="entry_year", default="")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("simulate", help="转专业模拟（可抵扣/需补修/压力/红线）")
    sp.add_argument("--from-major", dest="from_major", default="")
    sp.add_argument("--to-major", dest="to_major", required=True)
    sp.add_argument("--entry-year", dest="entry_year", default="")
    sp.add_argument("--student-id", dest="student_id", default="")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_simulate)

    sp = sub.add_parser("select", help="专业选择（分流）模拟")
    sp.add_argument("--entry-year", dest="entry_year", default="")
    sp.add_argument("--student-id", dest="student_id", default="")
    sp.add_argument("--choices", default="", help="逗号分隔的志愿专业（可选）")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_select)

    sp = sub.add_parser("simulate-minor", help="辅修模拟（预留，待教务提供教学计划）")
    sp.add_argument("--major", default="")
    sp.add_argument("--minor", default="")
    sp.add_argument("--entry-year", dest="entry_year", default="")
    sp.add_argument("--student-id", dest="student_id", default="")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_simulate_minor)

    sp = sub.add_parser("bind", help="绑定当前会话（openid）↔ 学号（学号 + 姓名 校验 roster）")
    sp.add_argument("--student-id", dest="student_id", required=True, help="学号")
    sp.add_argument("--name", required=True, help="姓名（用于与学籍名单校验）")
    sp.add_argument("--platform", default="", help="默认取 HERMES_SESSION_PLATFORM")
    sp.add_argument("--user-id", dest="user_id", default="", help="默认取 HERMES_SESSION_USER_ID")
    sp.set_defaults(func=cmd_bind)

    sp = sub.add_parser("whoami", help="显示当前会话的学号绑定状态")
    sp.add_argument("--platform", default="")
    sp.add_argument("--user-id", dest="user_id", default="")
    sp.set_defaults(func=cmd_whoami)

    sp = sub.add_parser("unbind", help="解绑当前会话的学号")
    sp.add_argument("--platform", default="")
    sp.add_argument("--user-id", dest="user_id", default="")
    sp.set_defaults(func=cmd_unbind)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
