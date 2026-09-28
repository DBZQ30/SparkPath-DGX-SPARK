#!/usr/bin/env python3
"""Hermes cron 包装脚本：以 agent4som venv 解释器拉起教务通知同步真脚本。

背景：Hermes cron 只允许脚本位于 ``~/.hermes/scripts/``，且解释器固定为网关
venv（``sys.executable``）；而真脚本依赖 agent4som 的包与 venv。因此这里用
``os.execv`` 直接替换自身进程，而不是 ``subprocess.run``：
  - 退出码自然透传（cron 的 ``last_status`` 由退出码决定）；
  - cron 3600s 超时只会 SIGKILL 单个 pid，若用 subprocess 会留下孤儿的真脚本
    继续持有 flock，导致后续定时运行全部静默跳过。
"""

import os
import sys

AGENT4SOM_REPO = os.environ.get("AGENT4SOM_REPO") or os.path.expanduser("~/SparkPath-DGX-SPARK/agent4som")
VENV_PYTHON = os.path.join(AGENT4SOM_REPO, "venv", "bin", "python")
SYNC_SCRIPT = os.path.join(AGENT4SOM_REPO, "scripts", "sync_jxtz.py")

os.chdir(AGENT4SOM_REPO)
os.execv(VENV_PYTHON, [VENV_PYTHON, SYNC_SCRIPT] + sys.argv[1:])
