# 测试用例补齐——全生命周期文档 + 单元/集成/安全测试矩阵

> 版本: 1.0 · 日期: 2026-09-26 · 状态: **已实现**
> 关联: [`tests/security/`](../../tests/security/)、[仓库根 README「开发全生命周期」](../../../README.md#10-开发全生命周期)、[04-security 权限总览](../04-security/user-role-tool-permission-design.md)
> 涉及仓库: `agent4som/`（后端 pytest）、`miniprogram-framework-frontend/`（jest）

## 1. 背景与目标

测试基线盘点（2026-09-26 之前）暴露三类问题：

1. **基建**：10 个用例失败均为环境耦合（缺 `xlwt`、依赖真实 reranker 服务、chroma 单例、
   `gateway.config` 导入失败中断收集），套件无法全绿；
2. **覆盖缺口**：`management_flow/service.py`（522 行）、`sqlite_metadata.py`（1690 行）、
   `academicwarning/export.py`、`miniapp_proxy/app.py`、`gateway/hooks/kb_init/handler.py`、
   `knowledge_base/bootstrap.py`、`utils/service_manager.py`、`llamaindex/*`、
   `scripts/sync_jxtz.py` 等零测试；`trainingplan/api.py`（含 HMAC 图片签名）无 API 层测试；
3. **安全**：已有 ACL filter / role_store / quota / Chroma where 注入等散点测试，但缺
   认证负面用例、授权决策矩阵、审计防篡改、上传文件名穿越、secrets 扫描等通用规范类别。
   另 `tests/verification/upgrade_patch_check.sh` 检查项停留在 WeCom 时代（企业微信入口
   2026-09 弃用后全部失效）且从未在 README 提及。

**目标**：可全绿的离线基础套件 + 补齐零覆盖大文件测试 + 按 OWASP 类别组织的
`tests/security/` 套件 + lint/coverage 工具链 + 根 README 全生命周期章节。

## 2. 基建修复（marker 分层）

`pyproject.toml` `[tool.pytest.ini_options]` 注册 marker：

| marker | 语义 |
|--------|------|
| `integration` | 需本机常驻服务（chroma-server / GPU 推理） |
| `network` | 需外网或校园网 |
| `security` | `tests/security/` 套件（本身离线可跑） |
| `samples` | 依赖 gitignored 真实样本（仅开发机；见 [009](009-test-data-layering.md)） |

离线开发机标准命令：`python -m pytest tests/ -m "not integration and not network"`。

环境耦合用例的处理：reranker 测试改注入 `QWEN3_RERANKER_URL` + mock HTTP；
chroma 单例测试打 `integration`；roster 测试依赖 `xlwt`（补进 requirements-dev.txt）。

## 3. 单元/集成测试补齐矩阵

| 新增测试文件 | 被测对象 | 覆盖要点 |
|---|---|---|
| `tests/management_flow/test_service.py` | `management_flow/service.py` | 审核状态流转、guards 决策集成、命令/通知输出、非管理员拒绝 |
| `tests/knowledge_base/repository/test_sqlite_metadata.py` | `sqlite_metadata.py` | 各 DAO CRUD、教师/管理员认证与线索状态机（tmp_path SQLite） |
| `tests/academicwarning/test_export.py` | `export.py` | xlsx 输出回读校验（openpyxl） |
| `tests/miniapp_proxy/test_app.py` | `miniapp_proxy/app.py` | 路径折叠/前缀剥离全分支、转发 502/header 过滤（MockTransport） |
| `tests/trainingplan/test_trainingplan_api.py` | `trainingplan/api.py` | X-API-Key、各查询端点、HMAC 图片双通道鉴权 |
| `tests/doccenter/test_doccenter_api.py` | `doccenter/api.py` | 上传→解析→适用性矩阵→删除全链路 |
| `tests/knowledge_base/core/test_audit_chain.py` | `audit_logger.py` | 链式哈希功能面 |
| `tests/gateway/hooks/test_kb_init.py` | `gateway/hooks/kb_init/handler.py` | 启动钩子幂等、单例初始化、嵌入降级组合 |
| `tests/knowledge_base/test_bootstrap.py` | `bootstrap.py` | 工厂单例、env 缺失降级 |
| `tests/knowledge_base/utils/test_service_manager.py` | `utils/service_manager.py` | chroma-server 生命周期、PID 探测 |
| `tests/knowledge_base/test_sync_jxtz.py` | `scripts/sync_jxtz.py` | 页面解析、增量比对、重试分类、文件锁、JSONL（requests stub） |
| `tests/hermes_overlay/test_knowledge_ingest.py` | `hermes_overlay/tools/knowledge_ingest.py` | 文件名归一化、scope 派生、越权拒绝、工具护栏 |
| `tests/knowledge_base/llamaindex/test_readers.py` 等 | `llamaindex/*` | Excel 规则单元、元数据提取（离线） |

文件路径加载模式：`gateway` 包会被 `tests/conftest.py` 注入的 `~/.hermes` 路径遮蔽，
被测模块用 `importlib.util.spec_from_file_location` 按文件加载。

## 4. 安全测试套件（`tests/security/`）

按 OWASP 类别组织（`conftest.py` 提供角色种子与 role_store 隔离夹具，全离线）：

| 文件 | 类别 | 关键用例 |
|------|------|----------|
| `test_authentication.py` | 认证 | 三个 API 缺/错/空 key → 401；伪造 token、过期 exp、篡改 major/year 拒绝；有效 HMAC 签名放行 |
| `test_authorization.py` | 授权 | 角色×操作决策矩阵（含 `can_assign_role` 拒绝设 owner）；scope 写权限矩阵；跨用户个人库隔离 |
| `test_input_validation.py` | 输入校验 | 超配额/错误类型/空文件/系统目录/FIFO 拒收；文件名穿越归一化；查询参数模糊不 5xx |
| `test_injection.py` | 注入 | SQLite 参数化（`'`/`;--` 载荷）、BM25/向量特殊字符、Chroma `where` 检索链路入口 |
| `test_audit_tamper.py` | 审计完整性 | 正序写链 verify 通过；改 detail/删行 → 校验失败 |
| `test_secrets.py` | 密钥管理 | git 跟踪文件 secret 模式扫描；`.env` 入 gitignore；`.env.example` 占位符 |
| `test_session_isolation.py` | 会话隔离 | QueryContext 跨线程/Task；工具缓存角色泄漏回归（对应 `docs/04-security/tool-defs-cache-role-bleed.md`） |

已知未修复问题（`docs/04-security/tool-dispatch-authz-bypass.md`，网关执行层越权）
的既有测试保持 skip 并注明原因，本轮不修框架层问题本身。

## 5. 前端 jest（`tests/unit/`）

- `trace.test.js`：`formatClock`/`formatDuration`/`answerKey`/`stepOf`/`summarize`（分拍归并、多 skill 去重、reflect 判定）；
- `instance-keys.test.js`：存储键按 INSTANCE_ID 派生、实例隔离；
- `api.test.js`：URL 派生完整性（尾斜杠剥离、空 BASE → 空串、无重复/双斜杠）;
- `trace-demo.test.js`：录播数据契约不变性（t 单调、事件字段、tool 配对、summary 兼容）；
- `build.test.js`：实例组装合并语义、dry-run 不落盘。

机制：`tests/setup.js` 注入 wx 桩；`moduleNameMapper` 把 `config/instance`（构建产物）
映射到确定性夹具 `tests/fixtures/instance.js`；`transform: {}` 关闭 babel
（规避 miniprogram-ci 传递依赖的旧版 @babel/core 与 jest 插件的版本冲突）。

## 6. 工具链与文档

- `requirements-dev.txt`：pytest、pytest-cov、ruff、mypy、xlwt；
- `pyproject.toml`：`[tool.ruff]`（保守 E/F/W，ignore 列表附原因注释）、
  `[tool.coverage.run]`（source 限业务包）；
- `tests/verification/upgrade_patch_check.sh`：重写为检查当前 hermes_overlay 部署
  （工具文件、rag toolset 标记、`hermes-local.patch` 关键改动、kb_init 钩子），
  替换已失效的 WeCom 时代检查项；
- 根 README 新增 §10「开发全生命周期」（环境初始化/构建/测试/发布/补丁升级），
  agent4som README §7 扩为 marker/安全套件/lint 三小节，前端 README §6 补 jest。

## 7. 验证矩阵

| 检查 | 命令 | 结果（2026-09-26） |
|------|------|------|
| 后端离线套件 | `python -m pytest tests/ -m "not integration and not network"` | 当前基线见根 README（离线套件 1000 passed / 36 skipped）；下表 757 passed / 105 skipped 为 2026-09-26 快照 |
| 安全套件 | `python -m pytest tests/security/ -v` | 全过 |
| Lint | `ruff check .` | 0 error |
| 前端 | `npm install && npm run switch && npm test` | validate + validate-ui + 59 jest 用例全绿 |
| 升级检查 | `bash tests/verification/upgrade_patch_check.sh` | 全过（需 hermes-agent 在位的环境） |
