#!/usr/bin/env python3
"""把培养方案批量上传到「培养方案智能解读」服务（走小程序同款 API）。

用途：管理员批量导入（4 专业 × N 年级）时，不必在小程序里一份份点。
等价于小程序「培养方案管理」页逐份上传，落库到本功能自建的
`data/training_plan.db`（**不写 warning.db、不写公共知识库**）。

用法：
  python -m trainingplan.bulk_upload data/SmartGuide/2023版*专业培养方案.docx
  python -m trainingplan.bulk_upload --dir data/SmartGuide --pattern '*专业培养方案.docx'
  python -m trainingplan.bulk_upload --dir ... --wait      # 等待队列跑完并打印结果

环境：需要 .env 已加载（或显式传 --base-url / --api-key）。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time
import urllib.request
import uuid
from typing import Any


def _post_file(base: str, key: str, path: str, major: str = "", entry_year: str = "") -> dict[str, Any]:
    """multipart/form-data POST（不依赖 requests，避免额外安装）。"""
    name = os.path.basename(path)
    boundary = "----tp" + uuid.uuid4().hex
    with open(path, "rb") as fh:
        data = fh.read()
    parts: list[bytes] = []

    def field(k: str, v: str):
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        )

    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n".encode()
    )
    parts.append(data)
    parts.append(b"\r\n")
    field("major", major)
    field("entry_year", entry_year)
    field("orig_name", name)
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/plan/upload",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-API-Key": key,
        },
    )
    import json
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def _get(base: str, key: str, path: str) -> dict[str, Any]:
    import json
    req = urllib.request.Request(f"{base.rstrip('/')}{path}", headers={"X-API-Key": key})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trainingplan.bulk_upload")
    p.add_argument("files", nargs="*", help="培养方案 docx 路径")
    p.add_argument("--dir", default="", help="目录（与 --pattern 配合）")
    p.add_argument("--pattern", default="*培养方案.docx", help="目录内匹配模式")
    p.add_argument("--base-url", default=os.environ.get("TRAINING_PLAN_API_BASE", "http://127.0.0.1:8009"))
    p.add_argument("--api-key", default=os.environ.get("TRAINING_PLAN_API_KEY", ""))
    p.add_argument("--wait", action="store_true", help="等待解析队列跑完")
    p.add_argument("--poll-interval", type=int, default=10)
    args = p.parse_args(argv)

    if not args.api_key:
        print("缺少 API Key：设置 TRAINING_PLAN_API_KEY 或传 --api-key", file=sys.stderr)
        return 2

    files = list(args.files)
    if args.dir:
        files += sorted(glob.glob(os.path.join(args.dir, args.pattern)))
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        print("没有找到待上传文件", file=sys.stderr)
        return 2

    print(f"待上传 {len(files)} 份 → {args.base_url}")
    ok = 0
    for f in files:
        try:
            r = _post_file(args.base_url, args.api_key, f)
            status = r.get("status")
            mark = "✅" if status in ("queued", "done") else "❌"
            print(f"  {mark} {os.path.basename(f)} → {r.get('message') or r}")
            ok += status in ("queued", "done")
        except Exception as exc:
            print(f"  ❌ {os.path.basename(f)} → {type(exc).__name__}: {exc}")

    print(f"已提交 {ok}/{len(files)}")

    if args.wait:
        print("等待解析队列…")
        while True:
            try:
                s = _get(args.base_url, args.api_key, "/api/plan/status?major=&entry_year=")
            except Exception as exc:
                print(f"  状态查询失败：{exc}")
                break
            summary = s.get("summary", {})
            print(f"  总 {summary.get('total')} · 完成 {summary.get('done')} · "
                  f"解析中 {summary.get('parsing')} · 排队 {summary.get('queued')} · "
                  f"失败 {summary.get('failed')}")
            if summary.get("queued", 0) == 0 and summary.get("parsing", 0) == 0:
                for it in s.get("items", []):
                    if it.get("state") == "done":
                        print(f"    ✅ {it['major']}·{it['entry_year']}：{it.get('note', '')}")
                    elif it.get("state") == "failed":
                        print(f"    ❌ {it['major']}·{it['entry_year']}：{it.get('error', '')}")
                break
            time.sleep(args.poll_interval)

    return 0


if __name__ == "__main__":
    sys.exit(main())
