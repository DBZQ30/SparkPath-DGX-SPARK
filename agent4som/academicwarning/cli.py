"""skill 调用入口：python -m academicwarning.cli upload <path> | check [--grade 2023级]
| precheck [--grade 2023级]"""
import argparse
import os
import sys

from .service import (is_admin, precheck_selection, run_selection_check,
                      upload_file)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="academicwarning")
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upload", help="上传数据文件")
    up.add_argument("path")
    up.add_argument("--hint", default="")
    up.add_argument("--user", default="")
    up.add_argument("--platform", default="wecom")
    ck = sub.add_parser("check", help="运行选课合理性检查")
    ck.add_argument("--grade", default="",
                    help='年级分区，如 "2023级"；空 = 用选课文件自身年级')
    pre = sub.add_parser("precheck", help="选课检查数据齐全性预检（只读，无副作用）")
    pre.add_argument("--grade", default="",
                     help='年级分区，如 "2023级"')
    args = ap.parse_args(argv)

    if args.cmd == "upload":
        print(upload_file(args.path, chat_hint=args.hint,
                          uploader=args.user, platform=args.platform))
        return 0

    # check / precheck 共用权限校验：命令自身从会话身份判定 admin/owner
    platform = os.getenv("HERMES_SESSION_PLATFORM", "").strip()
    user_id = os.getenv("HERMES_SESSION_USER_ID", "").strip()
    if platform and not is_admin(platform, user_id):
        print("无权限：仅管理员可触发选课检查")
        return 1

    if args.cmd == "precheck":
        ready, pending, note = precheck_selection(grade=args.grade)
        print(note)
        # 末行机器可读标志供 skill 分派措辞（人读文本在其上方）：
        #   READY        = 齐备，可执行 check
        #   PENDING      = 不齐备，但含解析中/排队中项 → 稍后再试
        #   INCOMPLETE   = 不齐备且为真缺失/解析失败 → 请补传
        flag = "READY" if ready else ("PENDING" if pending else "INCOMPLETE")
        print(f"[precheck] {flag}")
        return 0   # 预检为只读查询：缺数据是提示而非错误

    _, note = run_selection_check(grade=args.grade)
    print(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
