# hermes_overlay —— 本项目对 Hermes Agent 的定制

> 上游 Hermes Agent 框架（`agent4som-hermesagent/hermes-agent/`，约 940MB）**不纳入本仓库版本控制**，
> 需按 `agent4som-hermesagent/README.md` 的方式单独安装。
> 本目录保存**本项目对该框架的全部自研改动**，部署时覆盖/打到框架源码上。

## 目录

```
hermes_overlay/
├── tools/
│   ├── query_kb.py            # RAG 检索工具 knowledge_search（接入 knowledge_base）
│   └── knowledge_ingest.py    # 知识入库工具（写权限门 + 配额 + 版本）
└── patches/
    └── hermes-local.patch     # 对上游文件的本地改动（gateway / tui_gateway）
```

## 部署方式

安装好上游框架后，在 `agent4som/` 下执行：

```bash
bash scripts/deploy_tools.sh          # 复制 tools/ 并给 toolsets.py 打 rag 工具集补丁
git apply --directory=... hermes_overlay/patches/hermes-local.patch   # 或手工应用补丁
```

`deploy_tools.sh` 会：
1. 把 `hermes_overlay/tools/*.py` 复制到 `$HERMES_HOME/hermes-agent/tools/`；
2. 给 `$HERMES_HOME/hermes-agent/toolsets.py` 补上 `rag` 工具集定义（幂等）。

## `hermes-local.patch` 内容

| 文件 | 改动 |
|------|------|
| `gateway/run.py` | 增加 `on_agent_activity` 事件旁路（opt-in），供小程序展示「执行轨迹」 |
| `gateway/platforms/base.py` | 配套的适配器活动回调支持 |
| `tui_gateway/server.py` | 新增 `skill.activate` 事件（技能激活可视化） |

## 与上游的其它差异（仅记录，不需补丁）

- **移除** `plugins/platforms/wecom/*` 与相关测试：企业微信入口已弃用（2026-09）。
- **移除** `tools/nas_backup_restore.py`：NAS 备份链路已下线。
- **新增测试** `tests/test_miniapp_activity.py`、`tests/tools/test_query_kb_step_back.py`。
